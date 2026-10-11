# tg2nzb

Telegram channel → SABnzbd bridge. Picks up `.nzb` files posted as attachments
in Telegram channels and uploads them to SABnzbd.

Derived from [tg2jd](https://github.com/cosmicflow2512/TG2JDPS5): same Telegram
side (Telethon userbot, backfill, SQLite dedup, Pushover, heartbeat healthcheck,
PUID/PGID), the JDownloader part replaced by the SABnzbd API.

## How it works

1. Telethon listens to `NewMessage` on the configured channels (userbot, your
   own account, no admin rights needed).
2. A message counts as NZB when it carries a document whose filename ends in
   one of `file_extensions` (default `.nzb`) or whose MIME type contains `nzb`.
   Everything else is marked processed and ignored.
3. Optional keyword filter (`include_keywords` / `exclude_keywords`) on
   filename + caption.
4. The attachment is downloaded into memory (`download_media(msg, file=bytes)`)
   and checked for an `<nzb` element.
5. A password is taken from the caption via `password_patterns`
   (default: lines like `Passwort: xyz`, `PW = xyz`).
6. Upload via `POST /api?mode=addfile` (multipart, field `name`) with
   `cat`, `priority`, `pp`, `script`, `password` and optionally `nzbname`.
   Success requires an `nzo_id` in the response.
7. Pushover notification, message marked processed.

Telegram download errors and SAB errors are **not** marked processed, so the
next container start (backfill) retries them. Invalid NZBs, filtered posts and
non-NZB messages are marked and never retried.

SABnzbd API reference: <https://sabnzbd.org/wiki/configuration/5.1/api>

## Setup on Unraid

The image is built by GitHub Actions on every push to `main` and published as
`ghcr.io/cosmicflow2512/tg2nzb:latest` (also `:sha-<short>` and, for `v*` tags,
`:<version>`).

1. Copy `tg2nzb.xml` to `/boot/config/plugins/dockerMan/templates-user/my-tg2nzb.xml`.
2. Docker → Add Container → template `tg2nzb`, fill in Telegram API ID/hash,
   channels, SAB key and category, Apply.
3. Telegram login (once): `docker attach tg2nzb`, enter phone number, the code
   from the Telegram app and the 2FA password if set; detach with Ctrl+P, Ctrl+Q.
4. Updates: Docker tab → "check for updates" → apply.

Local build instead: `docker build -t tg2nzb:latest .` and set the template
repository to `tg2nzb:latest`.

The Telegram session is a separate login from tg2jd. Running two clients on the
same session file in parallel is not supported by Telethon — log in fresh.

## SABnzbd key

The **NZB key** (SABnzbd → Config → General) is sufficient: it can only add
jobs. With the full **API key** tg2nzb also reads `get_cats` at startup and
warns about categories that don't exist in SAB.

## Destination folder

There is no download path in tg2nzb. The folder, post-processing and script
come from the SAB **category**: `sabnzbd.category` globally,
`sabnzbd.channel_categories` per channel (keys: `@username`, title or chat id).

## Environment variables

| YAML key                     | Env var                  |
| ---------------------------- | ------------------------ |
| `telegram.api_id`            | `TG_API_ID`              |
| `telegram.api_hash`          | `TG_API_HASH`            |
| `telegram.channels`          | `TG_CHANNELS` (comma-separated) |
| `telegram.session_dir`       | `TG_SESSION_DIR`         |
| `sabnzbd.url`                | `SAB_URL`                |
| `sabnzbd.api_key`            | `SAB_API_KEY`            |
| `sabnzbd.category`           | `SAB_CATEGORY`           |
| `sabnzbd.priority`           | `SAB_PRIORITY`           |
| `sabnzbd.pp`                 | `SAB_PP`                 |
| `sabnzbd.script`             | `SAB_SCRIPT`             |
| `sabnzbd.job_name`           | `JOB_NAME` (`filename`/`caption`) |
| `sabnzbd.max_nzb_mb`         | `MAX_NZB_MB`             |
| `include_keywords`           | `INCLUDE_KEYWORDS`       |
| `exclude_keywords`           | `EXCLUDE_KEYWORDS`       |
| `backfill_limit`             | `BACKFILL_LIMIT`         |
| `backfill_max_age_hours`     | `BACKFILL_MAX_AGE_HOURS` |
| `pushover.token` / `.user`   | `PUSHOVER_TOKEN` / `PUSHOVER_USER` |
| `state_db`                   | `STATE_DB`               |

Empty env values count as unset (unfilled Unraid template fields). Per-channel
maps and `password_patterns` are config.yaml only.

## Known limits

- Albums: Telegram sends each file of an album as its own message, the caption
  usually only on the first one. A password in the caption therefore only
  reaches the first NZB of an album.
- Posts that only link to an NZB (no attachment) are ignored.

## License

MIT
