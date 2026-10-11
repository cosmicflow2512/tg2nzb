"""Config loading from YAML + ENV overrides."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PASSWORD_PATTERNS = [
    r"(?im)^\s*(?:passwor[dt]|pw|pass)\s*[:=]\s*(\S.*?)\s*$",
]


def _as_list(v) -> list[str]:
    """Accept a YAML list or a comma-separated string (ENV)."""
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


@dataclass
class Config:
    tg_api_id: int = 0
    tg_api_hash: str = ""
    channels: list = field(default_factory=list)
    session_dir: str = "/data/session"

    sab_url: str = ""                       # e.g. http://192.168.0.20:8080 (with url_base if set)
    sab_api_key: str = ""                   # NZB key is enough (only adds jobs)
    sab_category: str = ""                  # "" = SAB default
    channel_categories: dict = field(default_factory=dict)
    sab_priority: int = -100                # -100 = default of category
    sab_pp: int = -1                        # -1 = default of category
    sab_script: str = ""                    # "" = default of category
    job_name: str = "filename"              # filename | caption
    password_patterns: list = field(default_factory=lambda: list(DEFAULT_PASSWORD_PATTERNS))
    file_extensions: list = field(default_factory=lambda: [".nzb"])
    max_nzb_mb: float = 50

    include_keywords: list = field(default_factory=list)
    exclude_keywords: list = field(default_factory=list)
    channel_include_keywords: dict = field(default_factory=dict)
    channel_exclude_keywords: dict = field(default_factory=dict)

    backfill_limit: int = 20
    channel_backfill_limits: dict = field(default_factory=dict)
    backfill_max_age_hours: float = 0
    channel_backfill_max_age_hours: dict = field(default_factory=dict)

    pushover_token: str = ""
    pushover_user: str = ""

    state_db: str = "/data/state.db"

    @classmethod
    def load(cls, path: str = "/config/config.yaml") -> "Config":
        p = Path(path)
        data = {}
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        def env(key: str, default):
            """ENV wins, but an empty value (unfilled Unraid template field) counts as unset."""
            v = os.getenv(key)
            return v if v is not None and v.strip() != "" else default

        tg = data.get("telegram", {}) or {}
        sab = data.get("sabnzbd", {}) or {}
        po = data.get("pushover", {}) or {}

        cfg = cls(
            tg_api_id=int(env("TG_API_ID", tg.get("api_id", 0))),
            tg_api_hash=env("TG_API_HASH", tg.get("api_hash", "")),
            channels=tg.get("channels", []) or [],
            session_dir=env("TG_SESSION_DIR", tg.get("session_dir", "/data/session")),
            sab_url=str(env("SAB_URL", sab.get("url", ""))).rstrip("/"),
            sab_api_key=env("SAB_API_KEY", sab.get("api_key", "")),
            sab_category=env("SAB_CATEGORY", sab.get("category", "")) or "",
            channel_categories=sab.get("channel_categories", {}) or {},
            sab_priority=int(env("SAB_PRIORITY", sab.get("priority", -100))),
            sab_pp=int(env("SAB_PP", sab.get("pp", -1))),
            sab_script=env("SAB_SCRIPT", sab.get("script", "")) or "",
            job_name=str(env("JOB_NAME", sab.get("job_name", "filename"))).strip().lower(),
            password_patterns=sab.get("password_patterns", DEFAULT_PASSWORD_PATTERNS) or [],
            file_extensions=[e.lower() for e in _as_list(sab.get("file_extensions", [".nzb"]))],
            max_nzb_mb=float(env("MAX_NZB_MB", sab.get("max_nzb_mb", 50))),
            include_keywords=_as_list(env("INCLUDE_KEYWORDS", data.get("include_keywords", []))),
            exclude_keywords=_as_list(env("EXCLUDE_KEYWORDS", data.get("exclude_keywords", []))),
            channel_include_keywords={k: _as_list(v) for k, v in (data.get("channel_include_keywords", {}) or {}).items()},
            channel_exclude_keywords={k: _as_list(v) for k, v in (data.get("channel_exclude_keywords", {}) or {}).items()},
            backfill_limit=int(env("BACKFILL_LIMIT", data.get("backfill_limit", 20))),
            channel_backfill_limits=data.get("channel_backfill_limits", {}) or {},
            backfill_max_age_hours=float(env("BACKFILL_MAX_AGE_HOURS", data.get("backfill_max_age_hours", 0)) or 0),
            channel_backfill_max_age_hours=data.get("channel_backfill_max_age_hours", {}) or {},
            pushover_token=env("PUSHOVER_TOKEN", po.get("token", "")),
            pushover_user=env("PUSHOVER_USER", po.get("user", "")),
            state_db=env("STATE_DB", data.get("state_db", "/data/state.db")),
        )

        env_channels = os.getenv("TG_CHANNELS")
        if env_channels and env_channels.strip():
            cfg.channels = [c.strip() for c in env_channels.split(",") if c.strip()]

        cfg.channels = [
            int(c) if isinstance(c, str) and c.lstrip("-").isdigit() else c
            for c in cfg.channels
        ]

        cfg._validate()
        Path(cfg.session_dir).mkdir(parents=True, exist_ok=True)
        Path(cfg.state_db).parent.mkdir(parents=True, exist_ok=True)
        return cfg

    def _validate(self):
        missing = []
        if not self.tg_api_id:
            missing.append("telegram.api_id / TG_API_ID")
        if not self.tg_api_hash:
            missing.append("telegram.api_hash / TG_API_HASH")
        if not self.channels:
            missing.append("telegram.channels / TG_CHANNELS")
        if not self.sab_url:
            missing.append("sabnzbd.url / SAB_URL")
        if not self.sab_api_key:
            missing.append("sabnzbd.api_key / SAB_API_KEY")
        if missing:
            raise RuntimeError("Missing required config: " + ", ".join(missing))
        if self.job_name not in ("filename", "caption"):
            raise RuntimeError(f"sabnzbd.job_name must be 'filename' or 'caption', got '{self.job_name}'")
