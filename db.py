"""
SQLite database for BPC bot: users, subscriptions, payments, chat history.
"""

from __future__ import annotations

import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "bpc.db"

MEMO_ALPHABET = string.ascii_uppercase + string.digits


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at INTEGER NOT NULL,
                subscription_expires_at INTEGER DEFAULT 0,
                total_paid_ton REAL DEFAULT 0,
                analysis_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                order_id TEXT NOT NULL UNIQUE,
                amount_ton REAL NOT NULL,
                tx_hash TEXT,
                sender_address TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                confirmed_at INTEGER,
                type TEXT NOT NULL DEFAULT 'single',
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            );

            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            );

            CREATE TABLE IF NOT EXISTS processed_txs (
                tx_hash TEXT PRIMARY KEY,
                payment_memo TEXT,
                chat_id INTEGER,
                amount_ton REAL,
                processed_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_payments_chat ON payments(chat_id);
            CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
            CREATE INDEX IF NOT EXISTS idx_chat_chat ON chat_history(chat_id);
            """
        )
        _ensure_column(conn, "users", "expiry_date", "INTEGER DEFAULT 0")
        _ensure_column(conn, "users", "single_unlock", "INTEGER DEFAULT 0")
        _ensure_column(conn, "payments", "payment_memo", "TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_memo "
            "ON payments(payment_memo) WHERE payment_memo IS NOT NULL"
        )
        conn.execute(
            "UPDATE users SET expiry_date = subscription_expires_at "
            "WHERE IFNULL(expiry_date, 0) = 0 AND IFNULL(subscription_expires_at, 0) > 0"
        )
        conn.execute(
            "UPDATE payments SET payment_memo = order_id "
            "WHERE payment_memo IS NULL OR payment_memo = ''"
        )


@dataclass
class User:
    chat_id: int
    username: Optional[str]
    first_name: Optional[str]
    created_at: int
    subscription_expires_at: int
    total_paid_ton: float
    analysis_count: int
    expiry_date: int = 0
    single_unlock: int = 0

    @property
    def is_subscribed(self) -> bool:
        now = int(time.time())
        return max(self.expiry_date or 0, self.subscription_expires_at or 0) > now

    @property
    def has_single_unlock(self) -> bool:
        return int(self.single_unlock or 0) > 0

    @property
    def can_run_deep_audit(self) -> bool:
        return self.is_subscribed or self.has_single_unlock

    @property
    def days_remaining(self) -> int:
        if not self.is_subscribed:
            return 0
        expiry = max(self.expiry_date or 0, self.subscription_expires_at or 0)
        return max(0, (expiry - int(time.time())) // 86400)


def _user_from_row(row: sqlite3.Row) -> User:
    data = dict(row)
    return User(
        chat_id=data["chat_id"],
        username=data.get("username"),
        first_name=data.get("first_name"),
        created_at=data["created_at"],
        subscription_expires_at=data.get("subscription_expires_at") or 0,
        total_paid_ton=data.get("total_paid_ton") or 0,
        analysis_count=data.get("analysis_count") or 0,
        expiry_date=data.get("expiry_date") or 0,
        single_unlock=data.get("single_unlock") or 0,
    )


def get_or_create_user(chat_id: int, username: str = "", first_name: str = "") -> User:
    now = int(time.time())
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (chat_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, username, first_name, now),
            )
            row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return _user_from_row(row)


def set_monthly_expiry(chat_id: int, days: int = 30) -> User:
    """Set expiry_date to now + 30 days (does not stack)."""
    expiry = int(time.time()) + days * 86400
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET expiry_date = ?, subscription_expires_at = ?, single_unlock = 0 "
            "WHERE chat_id = ?",
            (expiry, expiry, chat_id),
        )
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return _user_from_row(row)


def grant_single_unlock(chat_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE users SET single_unlock = 1 WHERE chat_id = ?", (chat_id,))


def consume_single_unlock(chat_id: int) -> None:
    """Destroy one-shot deep-audit permission after the report is delivered."""
    with get_db() as conn:
        conn.execute("UPDATE users SET single_unlock = 0 WHERE chat_id = ?", (chat_id,))


def update_user_subscription(chat_id: int, days: int = 30) -> User:
    return set_monthly_expiry(chat_id, days=days)


def _new_memo(conn: sqlite3.Connection) -> str:
    for _ in range(40):
        memo = "".join(secrets.choice(MEMO_ALPHABET) for _ in range(8))
        exists = conn.execute(
            "SELECT 1 FROM payments WHERE payment_memo = ?", (memo,)
        ).fetchone()
        if not exists:
            return memo
    raise RuntimeError("Could not generate a unique payment_memo")


def get_or_create_pending_memo(chat_id: int, display_amount: float, payment_type: str) -> str:
    """
    One pending memo per user. Clicking 单次 / 包月 reuses the same code
    and only updates the displayed amount/type on the summons.
    """
    now = int(time.time())
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE chat_id = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE payments SET amount_ton = ?, type = ? WHERE id = ?",
                (display_amount, payment_type, row["id"]),
            )
            return row["payment_memo"] or row["order_id"]

        memo = _new_memo(conn)
        conn.execute(
            "INSERT INTO payments "
            "(chat_id, order_id, payment_memo, amount_ton, status, created_at, type) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (chat_id, memo, memo, display_amount, now, payment_type),
        )
        return memo


def get_pending_by_memo(memo: str) -> Optional[dict]:
    needle = (memo or "").strip().upper()
    if not needle:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE status = 'pending' AND UPPER(payment_memo) = ?",
            (needle,),
        ).fetchone()
        return dict(row) if row else None


def find_pending_memo_in_comment(comment: str) -> Optional[dict]:
    text = (comment or "").upper()
    if not text:
        return None
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE status = 'pending' AND payment_memo IS NOT NULL"
        ).fetchall()
        for row in rows:
            memo = (row["payment_memo"] or "").upper()
            if memo and memo in text:
                return dict(row)
    return None


def tx_already_processed(tx_hash: str) -> bool:
    if not tx_hash:
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_txs WHERE tx_hash = ?", (tx_hash,)
        ).fetchone()
        return row is not None


def mark_tx_processed(
    tx_hash: str, payment_memo: str, chat_id: int, amount_ton: float
) -> None:
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_txs "
            "(tx_hash, payment_memo, chat_id, amount_ton, processed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tx_hash, payment_memo, chat_id, amount_ton, now),
        )


def confirm_payment(
    order_id: str,
    tx_hash: str = "",
    sender: str = "",
    actual_amount: Optional[float] = None,
    payment_type: Optional[str] = None,
) -> Optional[dict]:
    now = int(time.time())
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE order_id = ? OR payment_memo = ?",
            (order_id, order_id),
        ).fetchone()
        if row is None:
            return None
        credited = actual_amount if actual_amount is not None else row["amount_ton"]
        new_type = payment_type or row["type"]
        conn.execute(
            "UPDATE payments SET status = 'confirmed', tx_hash = ?, sender_address = ?, "
            "confirmed_at = ?, amount_ton = ?, type = ? WHERE id = ?",
            (tx_hash, sender, now, credited, new_type, row["id"]),
        )
        conn.execute(
            "UPDATE users SET total_paid_ton = total_paid_ton + ? WHERE chat_id = ?",
            (credited, row["chat_id"]),
        )
        return dict(row)


def add_payment(chat_id: int, order_id: str, amount_ton: float, payment_type: str = "single") -> int:
    now = int(time.time())
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO payments "
            "(chat_id, order_id, payment_memo, amount_ton, status, created_at, type) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (chat_id, order_id, order_id, amount_ton, now, payment_type),
        )
        return cursor.lastrowid


def increment_analysis_count(chat_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET analysis_count = analysis_count + 1 WHERE chat_id = ?",
            (chat_id,),
        )


def add_chat_message(chat_id: int, role: str, content: str) -> None:
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO chat_history (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, now),
        )


def get_chat_history(chat_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def clear_chat_history(chat_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
