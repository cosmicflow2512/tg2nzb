"""Helpers for NZB attachments: detection, password, job name, keyword filter."""
import re

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def attachment_name(msg) -> str:
    f = getattr(msg, "file", None)
    return (getattr(f, "name", None) or "") if f else ""


def is_nzb_attachment(msg, extensions: list[str]) -> bool:
    if not getattr(msg, "document", None):
        return False
    name = attachment_name(msg).lower()
    if any(name.endswith(ext) for ext in extensions):
        return True
    mime = (getattr(msg.file, "mime_type", "") or "").lower()
    return "nzb" in mime


def looks_like_nzb(content: bytes) -> bool:
    head = content[:8192].lower()
    return b"<nzb" in head


def extract_password(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        try:
            m = re.search(pat, text or "")
        except re.error:
            continue
        if m:
            pw = (m.group(1) if m.groups() else m.group(0)).strip()
            if pw:
                return pw
    return ""


def caption_title(text: str) -> str:
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if len(ln) >= 3:
            return ln
    return ""


def sanitize(name: str) -> str:
    cleaned = _UNSAFE.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return cleaned[:200]


def strip_ext(name: str, extensions: list[str]) -> str:
    low = name.lower()
    for ext in sorted(extensions, key=len, reverse=True):
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def keyword_hit(text: str, words: list[str]) -> str:
    """Return the first keyword that occurs as a whole word (case-insensitive), else ""."""
    for w in words:
        if w and re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            return w
    return ""


def rich_message_text(rich: dict | None) -> str:
    """Flatten Telegram's rich_message (PageBlock* structure from TLObject.to_dict())."""
    skip = {"_", "url", "photos", "documents", "file_reference", "bytes",
            "photo_id", "webpage_id", "access_hash", "sizes", "video_sizes"}

    def collect(obj) -> str:
        if isinstance(obj, dict):
            if obj.get("_") == "TextPlain":
                return obj.get("text") or ""
            return "".join(collect(v) for k, v in obj.items() if k not in skip)
        if isinstance(obj, (list, tuple)):
            return "".join(collect(v) for v in obj)
        return ""

    if not rich:
        return ""
    return "\n".join(t for t in (collect(b) for b in rich.get("blocks", [])) if t.strip())
