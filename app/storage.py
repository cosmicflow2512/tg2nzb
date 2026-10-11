"""SQLite-backed dedup store."""
import sqlite3
import threading
import time
from pathlib import Path


class Storage:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS processed (
                chat_id INTEGER NOT NULL,
                msg_id  INTEGER NOT NULL,
                ts      INTEGER NOT NULL,
                PRIMARY KEY (chat_id, msg_id)
            )"""
        )
        self._conn.commit()

    def is_processed(self, chat_id: int, msg_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM processed WHERE chat_id=? AND msg_id=?",
                (chat_id, msg_id),
            )
            return cur.fetchone() is not None

    def mark_processed(self, chat_id: int, msg_id: int):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed (chat_id, msg_id, ts) VALUES (?, ?, ?)",
                (chat_id, msg_id, int(time.time())),
            )
            self._conn.commit()
