"""
TON on-chain polling via TonAPI.
Static receive address + unique payment_memo matching.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("bpcommentary_ton")

TONAPI_BASE = "https://tonapi.io/v2"
PAYMENT_WALLET = os.getenv(
    "TON_PAYMENT_WALLET",
    "UQAW6okaS3s0NxEbv0HW7LVyhmrvUrG-foXlZBB4ace8s334",
).strip()
TONAPI_KEY = os.getenv("TONAPI_KEY", "").strip() or os.getenv("TON_API_KEY", "").strip()

NANOTON = 1_000_000_000
SINGLE_MIN_TON = 50.0
MONTHLY_MIN_TON = 200.0


@dataclass
class IncomingTransfer:
    tx_hash: str
    amount_ton: float
    comment: str
    sender: str
    timestamp: int


def nano_to_ton(nano: int) -> float:
    return nano / NANOTON


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if TONAPI_KEY:
        headers["Authorization"] = f"Bearer {TONAPI_KEY}"
    return headers


def classify_amount(amount_ton: float) -> Optional[str]:
    """
    >= 200 TON → monthly
    >= 50 and < 200 TON → single
    Extra above the tier is sponsorship; never refunded.
    """
    if amount_ton >= MONTHLY_MIN_TON:
        return "monthly"
    if amount_ton >= SINGLE_MIN_TON:
        return "single"
    return None


async def fetch_incoming_transfers(limit: int = 30) -> list[IncomingTransfer]:
    if not PAYMENT_WALLET:
        return []

    url = f"{TONAPI_BASE}/accounts/{PAYMENT_WALLET}/events"
    params = {"limit": limit, "initiator": "false"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=_headers(), params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("TonAPI events error: %s", exc)
        return []

    transfers: list[IncomingTransfer] = []
    for event in data.get("events") or []:
        if event.get("in_progress"):
            continue
        event_id = str(event.get("event_id") or "")
        timestamp = int(event.get("timestamp") or 0)
        for action in event.get("actions") or []:
            if action.get("status") not in (None, "ok", "OK"):
                continue
            payload = action.get("TonTransfer") or {}
            if not payload:
                continue
            recipient = (payload.get("recipient") or {}).get("address") or ""
            # Accept if recipient is our wallet (TonAPI may use raw form)
            comment = (payload.get("comment") or "").strip()
            try:
                amount_nano = int(payload.get("amount") or 0)
            except (TypeError, ValueError):
                continue
            if amount_nano <= 0:
                continue
            sender = (payload.get("sender") or {}).get("address") or ""
            tx_hash = event_id
            transfers.append(
                IncomingTransfer(
                    tx_hash=tx_hash,
                    amount_ton=nano_to_ton(amount_nano),
                    comment=comment,
                    sender=sender,
                    timestamp=timestamp,
                )
            )
    return transfers
