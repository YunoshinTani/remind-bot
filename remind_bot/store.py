from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS schedules (
                day TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assignments (
                day TEXT PRIMARY KEY, numbers TEXT NOT NULL,
                collected_at TEXT, collected_by TEXT
            );
            CREATE TABLE IF NOT EXISTS notifications (
                day TEXT NOT NULL REFERENCES assignments(day),
                kind TEXT NOT NULL, status TEXT NOT NULL,
                message_id TEXT UNIQUE, reason TEXT,
                PRIMARY KEY(day, kind)
            );
            CREATE TABLE IF NOT EXISTS manual_notifications (
                request_id TEXT PRIMARY KEY,
                day TEXT NOT NULL REFERENCES assignments(day),
                kind TEXT NOT NULL CHECK(kind IN ('morning', 'lunch')),
                status TEXT NOT NULL, message_id TEXT UNIQUE, reason TEXT,
                requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
                previous_numbers TEXT
            );
        """)

    def close(self):
        self.connection.close()

    def cached_schedule(self, day: date):
        return self.connection.execute("SELECT * FROM schedules WHERE day=?", (day.isoformat(),)).fetchone()

    def save_schedule(self, day: date, payload: dict, now: datetime):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO schedules VALUES (?, ?, ?)",
                (day.isoformat(), json.dumps(payload), now.isoformat()),
            )

    def assignment(self, day: date):
        return self.connection.execute("SELECT * FROM assignments WHERE day=?", (day.isoformat(),)).fetchone()

    def ensure_assignment(self, day: date, numbers: tuple[int, ...]):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO assignments(day, numbers) VALUES (?, ?)",
                (day.isoformat(), json.dumps(numbers)),
            )
        return self.assignment(day)

    def collect(self, day: date, user_id: int, now: datetime) -> bool:
        with self.connection:
            result = self.connection.execute(
                "UPDATE assignments SET collected_at=?, collected_by=? WHERE day=? AND collected_at IS NULL",
                (now.isoformat(), str(user_id), day.isoformat()),
            )
        return result.rowcount == 1

    def notification(self, day: date, kind: str):
        return self.connection.execute(
            "SELECT * FROM notifications WHERE day=? AND kind=?", (day.isoformat(), kind)
        ).fetchone()

    def claim(self, day: date, kind: str) -> bool:
        # 送信前に確定しておく。送信中の異常終了でも再送を避ける。
        with self.connection:
            result = self.connection.execute(
                "INSERT OR IGNORE INTO notifications(day, kind, status) VALUES (?, ?, 'sending')",
                (day.isoformat(), kind),
            )
        return result.rowcount == 1

    def finish(self, day: date, kind: str, status: str, *, message_id: int | None = None, reason: str | None = None):
        with self.connection:
            self.connection.execute(
                "UPDATE notifications SET status=?, message_id=?, reason=? WHERE day=? AND kind=?",
                (status, str(message_id) if message_id is not None else None, reason, day.isoformat(), kind),
            )

    def skip(self, day: date, kind: str, reason: str):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO notifications(day, kind, status, reason) VALUES (?, ?, 'skipped', ?)",
                (day.isoformat(), kind, reason),
            )

    def day_for_message(self, message_id: int) -> date | None:
        row = self.connection.execute(
            "SELECT day FROM notifications WHERE message_id=? AND status='sent' "
            "UNION SELECT day FROM manual_notifications WHERE message_id=? AND status='sent'",
            (str(message_id), str(message_id)),
        ).fetchone()
        return date.fromisoformat(row["day"]) if row else None

    def messages_for_day(self, day: date) -> list[int]:
        return [int(row[0]) for row in self.connection.execute(
            "SELECT message_id FROM notifications WHERE day=? AND status='sent' AND kind IN ('morning','lunch') "
            "UNION SELECT message_id FROM manual_notifications WHERE day=? AND status='sent'",
            (day.isoformat(), day.isoformat()),
        )]

    def manual_notification(self, request_id: str):
        return self.connection.execute(
            "SELECT * FROM manual_notifications WHERE request_id=?", (request_id,)
        ).fetchone()

    def claim_manual(self, day: date, kind: str, request_id: str, user_id: int, now: datetime,
                     numbers: tuple[int, ...], previous: tuple[int, ...]) -> bool:
        # 手動送信・担当訂正・通常枠の抑止を同じトランザクションで確定する。
        with self.connection:
            inserted = self.connection.execute(
                "INSERT OR IGNORE INTO manual_notifications "
                "(request_id,day,kind,status,requested_by,requested_at,previous_numbers) VALUES (?,?,?,'sending',?,?,?)",
                (request_id, day.isoformat(), kind, str(user_id), now.isoformat(),
                 json.dumps(previous) if numbers != previous else None),
            )
            if inserted.rowcount != 1:
                return False
            row = self.assignment(day)
            if row is None or row["collected_at"] is not None or tuple(json.loads(row["numbers"])) != previous:
                raise ValueError("確認後に当番または回収状況が変わりました。")
            if numbers != previous:
                self.connection.execute("UPDATE assignments SET numbers=? WHERE day=?",
                                        (json.dumps(numbers), day.isoformat()))
            # 自動送信済みの記録は保持する。未送信なら同じ種類の自動通知を止める。
            self.connection.execute(
                "INSERT OR IGNORE INTO notifications(day,kind,status,reason) VALUES (?,?,'skipped','manual')",
                (day.isoformat(), kind),
            )
        return True

    def finish_manual(self, request_id: str, status: str, *, message_id: int | None = None,
                      reason: str | None = None):
        with self.connection:
            self.connection.execute(
                "UPDATE manual_notifications SET status=?,message_id=?,reason=? WHERE request_id=?",
                (status, str(message_id) if message_id is not None else None, reason, request_id),
            )

    def uncertain_kinds(self, day: date) -> list[str]:
        uncertain = {row["kind"] for row in self.uncertain_notifications() if row["day"] == day.isoformat()}
        return [kind for kind in ("morning", "lunch", "dismissal") if kind in uncertain]

    def uncertain_notifications(self):
        return self.connection.execute(
            "SELECT day, kind, status FROM notifications WHERE status IN ('sending','uncertain') "
            "UNION SELECT day, kind, status FROM manual_notifications WHERE status IN ('sending','uncertain')"
        ).fetchall()
