"""
SQLite database for BPC bot: users, subscriptions, payments, chat history.
Single-file DB, no external service needed.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "bpc.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
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

            CREATE INDEX IF NOT EXISTS idx_payments_chat ON payments(chat_id);
            CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
            CREATE INDEX IF NOT EXISTS idx_chat_chat ON chat_history(chat_id);
        """)


@dataclass
class User:
    chat_id: int
    username: Optional[str]
    first_name: Optional[str]
    created_at: int
    subscription_expires_at: int
    total_paid_ton: float
    analysis_count: int

    @property
    def is_subscribed(self) -> bool:
        return self.subscription_expires_at > int(time.time())

    @property
    def days_remaining(self) -> int:
        if not self.is_subscribed:
            return 0
        return max(0, (self.subscription_expires_at - int(time.time())) // 86400)


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
        return User(**dict(row))


def update_user_subscription(chat_id: int, days: int = 30) -> User:
    """Add subscription days. If currently subscribed, extend from expiry; otherwise from now."""
    now = int(time.time())
    with get_db() as conn:
        row = conn.execute("SELECT subscription_expires_at FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        current_expiry = row["subscription_expires_at"] if row else 0
        base = max(current_expiry, now)
        new_expiry = base + days * 86400
        conn.execute(
            "UPDATE users SET subscription_expires_at = ? WHERE chat_id = ?",
            (new_expiry, chat_id),
        )
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return User(**dict(row))


def add_payment(chat_id: int, order_id: str, amount_ton: float, payment_type: str = "single") -> int:
    now = int(time.time())
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO payments (chat_id, order_id, amount_ton, status, created_at, type) VALUES (?, ?, ?, 'pending', ?, ?)",
            (chat_id, order_id, amount_ton, now, payment_type),
        )
        return cursor.lastrowid


def confirm_payment(order_id: str, tx_hash: str = "", sender: str = "") -> Optional[dict]:
    now = int(time.time())
    with get_db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE payments SET status = 'confirmed', tx_hash = ?, sender_address = ?, confirmed_at = ? WHERE order_id = ?",
            (tx_hash, sender, now, order_id),
        )
        # Update user total paid
        conn.execute(
            "UPDATE users SET total_paid_ton = total_paid_ton + ? WHERE chat_id = ?",
            (row["amount_ton"], row["chat_id"]),
        )
        return dict(row)


def increment_analysis_count(chat_id: int):
    with get_db() as conn:
        conn.execute("UPDATE users SET analysis_count = analysis_count + 1 WHERE chat_id = ?", (chat_id,))


def add_chat_message(chat_id: int, role: str, content: str):
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
        # Reverse to chronological order
        return [dict(r) for r in reversed(rows)]


def clear_chat_history(chat_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
