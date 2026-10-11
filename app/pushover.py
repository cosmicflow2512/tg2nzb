"""Pushover notification client. Docs: https://pushover.net/api"""
import logging

import requests

log = logging.getLogger("tg2nzb.push")

_API_URL = "https://api.pushover.net/1/messages.json"


class PushoverClient:
    def __init__(self, token: str, user: str):
        self.token = token
        self.user = user

    def send(self, title: str, message: str, priority: int = 0, url: str | None = None):
        data = {
            "token": self.token,
            "user": self.user,
            "title": title[:250],
            "message": message[:1024],
            "priority": priority,
        }
        if url:
            data["url"] = url[:512]
        try:
            r = requests.post(_API_URL, data=data, timeout=10)
            if r.status_code != 200:
                log.error("Pushover returned %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            log.exception("Pushover request failed: %s", e)
