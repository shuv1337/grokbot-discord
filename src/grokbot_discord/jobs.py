"""Single-process durable correlation. Never replay a possibly accepted remote action."""

from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import time
from pathlib import Path

from grokbot_discord.models import WakePayload


class CallbackRejected(ValueError):
    pass


class CallbackNotReady(RuntimeError):
    """Retry the identical callback later: no delivery claim or send has occurred."""


class JobStore:
    def __init__(
        self,
        path: Path,
        *,
        ttl: int = 300,
        capacity: int = 2,
        clock=time.time,
        max_records: int = 10000,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = open(str(path) + ".lock", "a")
        os.chmod(str(path) + ".lock", 0o600)
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise RuntimeError("job database already in use") from None
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, message TEXT UNIQUE NOT NULL, channel TEXT NOT NULL,
            guild TEXT NOT NULL, author TEXT NOT NULL, bot TEXT NOT NULL,
            created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
            digest TEXT NOT NULL DEFAULT '')""")
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_channel ON jobs(channel, created)")
        # A send interrupted by process death is ambiguous. Do not deliver it twice.
        self.db.execute("UPDATE jobs SET state='uncertain' WHERE state='delivering'")
        self.db.commit()
        self.ttl, self.capacity, self.clock, self.max_records = ttl, capacity, clock, max_records
        self.expire()

    def close(self) -> None:
        self.db.close()
        self._lock.close()

    def expire(self) -> None:
        now = self.clock()
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state='expired' WHERE expires<=? "
                "AND state IN ('waiting','dispatching')",
                (now,),
            )
            self.db.execute(
                "DELETE FROM jobs WHERE created<? AND state NOT IN "
                "('waiting','dispatching','delivering')",
                (now - 7 * 86400,),
            )

    def reserve(self, payload: WakePayload, *, bot_author: bool = False) -> bool:
        self.expire()
        now = self.clock()
        if self.db.execute("SELECT 1 FROM jobs WHERE message=?", (payload.message_id,)).fetchone():
            return False
        count = self.db.execute(
            "SELECT count(*) FROM jobs WHERE channel=? AND state IN "
            "('waiting','dispatching','delivering')",
            (payload.channel_id,),
        )
        if count.fetchone()[0] >= self.capacity:
            return False
        # Bound all traffic, including explicitly granted external bots.
        recent = self.db.execute(
            "SELECT author, created FROM jobs WHERE channel=? AND created>?",
            (payload.channel_id, now - 60),
        ).fetchall()
        bot_recent = bot_author and any(r["author"] == payload.author_id for r in recent)
        if len(recent) >= 10 or bot_recent:
            return False
        if self.db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= self.max_records:
            return False
        with self.db:
            self.db.execute(
                "INSERT INTO jobs "
                "(id,message,channel,guild,author,bot,created,expires,state) "
                "VALUES (?,?,?,?,?,?,?,?,'dispatching')",
                (
                    payload.correlation_id,
                    payload.message_id,
                    payload.channel_id,
                    payload.guild_id,
                    payload.author_id,
                    payload.bot,
                    now,
                    now + self.ttl,
                ),
            )
        return True

    def finish_wake(self, correlation_id: str, state: str = "waiting") -> None:
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state=? WHERE id=? AND state='dispatching'",
                (state, correlation_id),
            )

    def get(self, correlation_id: str):
        self.expire()
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (correlation_id,)).fetchone()

    def validate_reply(self, correlation_id: str, channel: str, message: str, text: str):
        row = self.get(correlation_id)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if row is None or row["channel"] != channel or row["message"] != message:
            raise CallbackRejected("unknown or mismatched correlation")
        if row["state"] == "completed" and row["digest"] == digest:
            return None
        if row["state"] not in ("waiting", "dispatching"):
            raise CallbackRejected("correlation is no longer deliverable")
        return row

    def claim(self, correlation_id: str, channel: str, message: str, text: str):
        row = self.validate_reply(correlation_id, channel, message, text)
        if row is None:
            return None
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state='delivering', digest=? WHERE id=?", (digest, correlation_id)
            )
        return row

    def finish_reply(self, correlation_id: str, *, success: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state=? WHERE id=? AND state='delivering'",
                ("completed" if success else "uncertain", correlation_id),
            )
