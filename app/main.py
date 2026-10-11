"""
tg2nzb - Telegram channel → SABnzbd bridge.
Listens to configured channels, downloads posted .nzb attachments and uploads
them to SABnzbd via mode=addfile.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from telethon import TelegramClient, events

from config import Config
from nzb import (attachment_name, is_nzb_attachment, looks_like_nzb, extract_password,
                 caption_title, sanitize, strip_ext, keyword_hit, rich_message_text)
from pushover import PushoverClient
from sabnzbd import SabClient
from storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tg2nzb")
logging.getLogger("telethon").setLevel(logging.WARNING)


def message_text(msg) -> str:
    """Visible text/caption of a message, including Telegram's rich_message format."""
    if not msg:
        return ""
    text = msg.raw_text or ""
    if text:
        return text
    rich = getattr(msg, "rich_message", None)
    if rich is not None:
        try:
            return rich_message_text(rich.to_dict())
        except Exception as e:
            log.warning("rich_message could not be parsed: %s", e)
    return ""


def topic_id(msg, chat) -> int | None:
    """Forum topic of a message. None for chats without topics; 1 = General topic.
    Topic messages carry reply_to.forum_topic; the topic id is reply_to_top_id, or
    reply_to_msg_id when the message is not a reply inside the topic."""
    if not getattr(chat, "forum", False):
        return None
    rt = getattr(msg, "reply_to", None)
    if rt is not None and getattr(rt, "forum_topic", False):
        return getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)
    return 1


def _lookup(overrides: dict, raw, chat, chat_id):
    """Per-channel override lookup. Keys: identifier as written in channels:,
    @username, username, title, numeric chat_id (int or str). Returns (found, value)."""
    if not overrides:
        return False, None
    username = getattr(chat, "username", None)
    candidates = [
        raw,
        str(raw) if raw is not None else None,
        username,
        f"@{username}" if username else None,
        getattr(chat, "title", None),
        chat_id,
        str(chat_id) if chat_id is not None else None,
    ]
    for key in candidates:
        if key is not None and key in overrides:
            return True, overrides[key]
    return False, None


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.storage = Storage(cfg.state_db)
        self.sab = SabClient(cfg.sab_url, cfg.sab_api_key)
        self.push = PushoverClient(cfg.pushover_token, cfg.pushover_user) \
            if cfg.pushover_token and cfg.pushover_user else None
        self.client = TelegramClient(
            str(Path(cfg.session_dir) / "userbot"),
            api_id=cfg.tg_api_id,
            api_hash=cfg.tg_api_hash,
        )
        # Maps the resolved chat id back to the identifier from channels:, for overrides.
        self._raw_by_id: dict[int, object] = {}

    async def start(self):
        log.info("Connecting to Telegram ...")
        await self.client.start(phone=lambda: input("Phone number (incl. country code): "))
        me = await self.client.get_me()
        log.info("Logged in as %s (id=%s)", me.username or me.first_name, me.id)

        await self._check_sab()
        self._start_heartbeat()
        await self._backfill()

        self.client.add_event_handler(self._on_message, events.NewMessage(chats=self.cfg.channels))
        log.info("Listening on channels: %s", ", ".join(map(str, self.cfg.channels)))
        await self.client.run_until_disconnected()

    async def _check_sab(self):
        log.info("Checking SABnzbd at %s ...", self.cfg.sab_url)
        try:
            ver = await asyncio.to_thread(self.sab.version)
            log.info("SABnzbd %s reachable", ver)
        except Exception as e:
            # Not fatal: uploads retry and failed messages stay unprocessed.
            log.error("SABnzbd not reachable (%s) — continuing, uploads will retry", e)
            return
        wanted = {c for c in [self.cfg.sab_category, *self.cfg.channel_categories.values()] if c}
        if not wanted:
            return
        try:
            cats = await asyncio.to_thread(self.sab.categories)
        except Exception as e:
            log.warning("Could not read SABnzbd categories: %s", e)
            return
        if cats is None:
            log.info("Category check skipped (NZB key has no read access)")
            return
        unknown = sorted(wanted - set(cats))
        if unknown:
            log.warning("Categories not defined in SABnzbd: %s (available: %s)", unknown, cats)

    def _start_heartbeat(self):
        hb_path = Path(self.cfg.state_db).parent / "heartbeat"

        def loop():
            while True:
                try:
                    hb_path.write_text(str(int(time.time())))
                except Exception as e:
                    log.warning("heartbeat write failed: %s", e)
                time.sleep(15)

        threading.Thread(target=loop, name="heartbeat", daemon=True).start()
        log.info("Heartbeat thread started → %s", hb_path)

    async def _on_message(self, event):
        chat = await event.get_chat()
        chat_name = getattr(chat, "title", None) or getattr(chat, "username", None) or str(event.chat_id)
        await self._process_message(event.message, chat, event.chat_id, chat_name, backfill=False)

    async def _backfill(self):
        if self.cfg.backfill_limit <= 0 and not self.cfg.channel_backfill_limits:
            log.info("Backfill disabled (backfill_limit=0, no per-channel overrides)")
            return

        log.info("Backfill: starting per-channel scan ...")
        total = 0
        dialogs_loaded = False
        for ch in self.cfg.channels:
            try:
                chat = await self.client.get_entity(ch)
            except Exception as e:
                # Numeric IDs only resolve once the access_hash is cached; loading dialogs fills it.
                # https://docs.telethon.dev/en/stable/concepts/entities.html
                if dialogs_loaded:
                    log.error("Backfill: cannot resolve channel %s: %s", ch, e)
                    continue
                log.info("Backfill: channel %s not in entity cache, loading dialogs ...", ch)
                try:
                    await self.client.get_dialogs()
                    dialogs_loaded = True
                    chat = await self.client.get_entity(ch)
                except Exception as e2:
                    dialogs_loaded = True
                    log.error("Backfill: cannot resolve channel %s (is the account a member?): %s", ch, e2)
                    continue

            chat_id = getattr(chat, "id", None)
            chat_name = getattr(chat, "title", None) or getattr(chat, "username", None) or str(ch)

            found, limit = _lookup(self.cfg.channel_backfill_limits, ch, chat, chat_id)
            limit = int(limit if found else self.cfg.backfill_limit)
            if limit <= 0:
                log.info("Backfill [%s]: skipped (limit=0)", chat_name)
                continue

            found, max_age = _lookup(self.cfg.channel_backfill_max_age_hours, ch, chat, chat_id)
            max_age = float((max_age if found else self.cfg.backfill_max_age_hours) or 0)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age) if max_age > 0 else None
            log.info("Backfill [%s]: scanning last %d message(s)%s ...", chat_name, limit,
                     f", max age {max_age:g}h" if cutoff else "")

            found, topics = _lookup(self.cfg.channel_topics, ch, chat, chat_id)
            topics = topics if found else self.cfg.topics
            recent, count, already = [], 0, 0
            try:
                if topics and getattr(chat, "forum", False):
                    # Forum with topic filter: scan each wanted topic on its own, so `limit`
                    # counts messages of that topic, not of the whole group.
                    # Topic messages are read via messages.getReplies (Telethon: reply_to=topic id).
                    seen = set()
                    for t in topics:
                        n = 0
                        try:
                            async for msg in self.client.iter_messages(chat, limit=limit, reply_to=t):
                                if cutoff and msg.date and msg.date < cutoff:
                                    break
                                if msg.id not in seen:
                                    seen.add(msg.id)
                                    recent.append(msg)
                                    n += 1
                        except Exception as e:
                            log.error("Backfill [%s]: topic %s could not be read: %s", chat_name, t, e)
                        log.info("Backfill [%s]: topic %s → %d message(s)", chat_name, t, n)
                    recent.sort(key=lambda m: m.id, reverse=True)
                else:
                    # Newest `limit` messages (Telethon default: newest first), processed oldest first.
                    async for msg in self.client.iter_messages(chat, limit=limit):
                        if cutoff and msg.date and msg.date < cutoff:
                            break  # older posts are not marked, a larger max age picks them up later
                        recent.append(msg)
                for msg in reversed(recent):
                    cid = msg.chat_id or chat_id
                    if self.storage.is_processed(cid, msg.id):
                        already += 1
                        continue
                    if await self._process_message(msg, chat, cid, chat_name, backfill=True, raw=ch):
                        count += 1
            except Exception as e:
                log.exception("Backfill [%s]: iteration failed: %s", chat_name, e)

            total += count
            log.info("Backfill [%s]: %d in window, %d already processed, %d NZB(s) queued",
                     chat_name, len(recent), already, count)

        log.info("Backfill complete: %d NZB(s) queued in SABnzbd", total)

    async def _process_message(self, msg, chat, chat_id: int, chat_name: str,
                               *, backfill: bool = False, raw=None) -> bool:
        """Download an NZB attachment and queue it in SABnzbd. Returns True on success.
        Messages are marked processed when queued or deliberately skipped; transient
        failures (download, SAB unreachable) stay unmarked so the next start retries."""
        if self.storage.is_processed(chat_id, msg.id):
            return False
        cfg = self.cfg
        topic = topic_id(msg, chat)
        tag = f"[{chat_name}/{msg.id}]" if topic is None else f"[{chat_name}/topic {topic}/{msg.id}]"

        found, topics = _lookup(cfg.channel_topics, raw, chat, chat_id)
        topics = topics if found else cfg.topics
        if topics and topic is not None and topic not in topics:
            log.info("%s topic %s not in %s, skipping", tag, topic, topics)
            self.storage.mark_processed(chat_id, msg.id)
            return False

        if not is_nzb_attachment(msg, cfg.file_extensions):
            if getattr(msg, "document", None):
                f = getattr(msg, "file", None)
                log.info("%s document is not an NZB (name=%r, mime=%r), skipping", tag,
                         getattr(f, "name", None), getattr(f, "mime_type", None))
            elif getattr(msg, "media", None):
                log.info("%s media %s, no document, skipping", tag, type(msg.media).__name__)
            else:
                log.info("%s text only, skipping: %r", tag, (message_text(msg) or "")[:80])
            self.storage.mark_processed(chat_id, msg.id)
            return False

        filename = sanitize(attachment_name(msg)) or f"tg2nzb_{msg.id}.nzb"
        text = message_text(msg)
        log.info("%s %s NZB: %s", tag, "Backfill" if backfill else "Live", filename)

        # keyword filter on filename + caption
        haystack = f"{filename}\n{text}"
        found, include = _lookup(cfg.channel_include_keywords, raw, chat, chat_id)
        include = include if found else cfg.include_keywords
        found, exclude = _lookup(cfg.channel_exclude_keywords, raw, chat, chat_id)
        exclude = exclude if found else cfg.exclude_keywords
        if include and not keyword_hit(haystack, include):
            log.info("%s no include keyword %s, skipping", tag, include)
            self.storage.mark_processed(chat_id, msg.id)
            return False
        hit = keyword_hit(haystack, exclude)
        if hit:
            log.info("%s exclude keyword '%s', skipping", tag, hit)
            self.storage.mark_processed(chat_id, msg.id)
            return False

        size = getattr(msg.file, "size", 0) or 0
        if size > cfg.max_nzb_mb * 1024 * 1024:
            log.warning("%s attachment is %.1f MB (> max_nzb_mb=%g), skipping", tag, size / 1048576, cfg.max_nzb_mb)
            self.storage.mark_processed(chat_id, msg.id)
            return False

        try:
            content = await self.client.download_media(msg, file=bytes)
        except Exception as e:
            log.exception("%s download from Telegram failed: %s", tag, e)
            return False
        if not content or not looks_like_nzb(content):
            log.warning("%s attachment is not a valid NZB (no <nzb> element), skipping", tag)
            self.storage.mark_processed(chat_id, msg.id)
            return False

        password = extract_password(text, cfg.password_patterns)
        if cfg.job_name == "caption" and caption_title(text):
            nzbname = sanitize(caption_title(text))
        else:
            nzbname = ""  # SAB uses the NZB filename
        display = nzbname or strip_ext(filename, cfg.file_extensions)

        found, cat = _lookup(cfg.channel_categories, raw, chat, chat_id)
        cat = (cat if found else cfg.sab_category) or ""

        try:
            ids = await asyncio.to_thread(
                self.sab.add_file, filename, content,
                nzbname=nzbname, password=password, cat=cat,
                script=cfg.sab_script, priority=cfg.sab_priority, pp=cfg.sab_pp,
            )
        except Exception as e:
            log.error("%s SABnzbd upload failed: %s", tag, e)
            if self.push and not backfill:
                await asyncio.to_thread(self.push.send, title="tg2nzb: SABnzbd upload failed",
                                        message=f"{chat_name}\n{display}\n{e}", priority=1)
            return False

        log.info("%s queued in SABnzbd: '%s' (cat=%s, password=%s, nzo=%s)",
                 tag, display, cat or "<default>", "yes" if password else "no", ",".join(ids))

        if self.push:
            await asyncio.to_thread(
                self.push.send,
                title=f"{'📦' if backfill else '📥'} {display[:80]}",
                message=f"From: {chat_name}\nCategory: {cat or 'Default'}\nPassword: {'yes' if password else 'no'}",
                priority=-1 if backfill else 0,
            )

        self.storage.mark_processed(chat_id, msg.id)
        return True


async def amain():
    app = App(Config.load())
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    task = asyncio.create_task(app.start())
    stop_task = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        if t is task and t.exception():
            log.error("Fatal: %s", t.exception())
    log.info("Shutting down")
    await app.client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(0)
