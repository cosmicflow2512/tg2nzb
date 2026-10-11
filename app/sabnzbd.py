"""SABnzbd API client.

API reference: https://sabnzbd.org/wiki/configuration/5.1/api
- mode=addfile: POST multipart/form-data, NZB in field `name` (or `nzbfile`)
- optional: nzbname, password, cat, script, priority, pp
- returns {"status": true, "nzo_ids": [...]}
- the NZB key is sufficient for adding jobs; version needs no key
"""
import logging
import time

import requests

log = logging.getLogger("tg2nzb.sab")


class SabError(RuntimeError):
    pass


class SabClient:
    def __init__(self, url: str, api_key: str, timeout: float = 30):
        self.api = f"{url.rstrip('/')}/api"
        self.api_key = api_key
        self.timeout = timeout

    def version(self) -> str:
        r = requests.get(self.api, params={"mode": "version", "output": "json"}, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("version", "?")

    def categories(self) -> list[str] | None:
        """Needs the full API key; returns None if only the NZB key is configured."""
        r = requests.get(self.api, params={"mode": "get_cats", "output": "json", "apikey": self.api_key},
                         timeout=self.timeout)
        if r.status_code == 403:  # NZB key: no read access
            return None
        r.raise_for_status()
        data = r.json()
        if "categories" not in data:
            return None
        return data["categories"]

    def add_file(self, filename: str, content: bytes, *, nzbname: str = "", password: str = "",
                 cat: str = "", script: str = "", priority: int = -100, pp: int = -1) -> list[str]:
        """Upload an NZB. Returns the nzo_ids. Raises SabError after 3 failed attempts."""
        data = {
            "mode": "addfile",
            "output": "json",
            "apikey": self.api_key,
            "priority": str(priority),
            "pp": str(pp),
        }
        if nzbname:
            data["nzbname"] = nzbname
        if password:
            data["password"] = password
        if cat:
            data["cat"] = cat
        if script:
            data["script"] = script

        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                # Parameters in the query string, NZB as multipart body.
                r = requests.post(
                    self.api,
                    params=data,
                    files={"name": (filename, content, "application/x-nzb")},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                body = r.json()
                ids = body.get("nzo_ids") or []
                # SAB can report status=true without a job; no nzo_id means nothing was queued.
                if not body.get("status") or not ids:
                    raise SabError(body.get("error") or f"no job created: {body}")
                return ids
            except SabError as e:
                # API-level rejection (wrong key, invalid NZB): retrying won't help.
                if "key" in str(e).lower():
                    raise
                last_err = e
            except Exception as e:
                last_err = e
            log.warning("addfile attempt %d/3 for '%s' failed: %s", attempt, filename, last_err)
            time.sleep(2 * attempt)
        raise SabError(f"addfile failed for '{filename}': {last_err}")
