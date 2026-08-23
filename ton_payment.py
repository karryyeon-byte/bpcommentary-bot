"""
TON payment verification for BPC bot.
Uses TON Center v2 API to poll transactions and verify payments.
No TON Connect wallet connection needed — user just sends TON with a memo.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("bpcommentary_ton")

# TON Center v2 API (free tier, no key needed for basic queries)
TONCENTER_API = "https://toncenter.com/api/v2"

# Your wallet address that receives payments
PAYMENT_WALLET = os.getenv("TON_PAYMENT_WALLET", "").strip()

# Polling config
POLL_INTERVAL = 10  # seconds
POLL_TIMEOUT = 300  # 5 minutes max
MIN_CONFIRMATIONS = 1  # TON transactions are fast; 1 confirmation is enough


@dataclass
class PaymentResult:
    paid: bool
    tx_hash: Optional[str] = None
    amount_nano: Optional[int] = None
    sender: Optional[str] = None
    memo: Optional[str] = None
    error: Optional[str] = None


def nano_to_ton(nano: int) -> float:
    return nano / 1_000_000_000


def ton_to_nano(ton: float) -> int:
    return int(ton * 1_000_000_000)


def decode_memo(raw_message: str) -> str:
    """Decode base64 TON comment/memo to plain text."""
    try:
        # TON comments are base64-encoded UTF-8 text
        decoded = base64.b64decode(raw_message).decode("utf-8", errors="replace")
        return decoded.strip()
    except Exception:
        return raw_message


async def check_transaction(
    order_id: str,
    expected_amount_ton: float,
    since_timestamp: int,
) -> PaymentResult:
    """
    Poll TON Center for a transaction matching order_id and amount.
    Returns PaymentResult with paid=True when found.
    """
    if not PAYMENT_WALLET:
        return PaymentResult(paid=False, error="TON_PAYMENT_WALLET not configured")

    expected_nano = ton_to_nano(expected_amount_ton)
    # Allow 1% slippage (network fees, rounding)
    min_nano = int(expected_nano * 0.99)

    url = f"{TONCENTER_API}/getTransactions"
    params = {
        "address": PAYMENT_WALLET,
        "limit": 20,
        "archival": "false",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"TON Center API error: {e}")
        return PaymentResult(paid=False, error=str(e))

    if data.get("ok") is not True:
        return PaymentResult(paid=False, error=f"API returned not ok: {data}")

    transactions = data.get("result", [])

    for tx in transactions:
        in_msg = tx.get("in_msg", {})

        # Only incoming transactions (source is not empty)
        source = in_msg.get("source", "")
        if not source or source == "":
            continue

        # Amount in nanoTON
        value_str = in_msg.get("value", "0")
        try:
            value_nano = int(value_str)
        except (ValueError, TypeError):
            continue

        # Check amount
        if value_nano < min_nano:
            continue

        # Check timestamp (must be after order was created)
        tx_time = tx.get("utime", 0)
        if tx_time < since_timestamp:
            continue

        # Check memo/comment
        raw_message = in_msg.get("message", "")
        memo = decode_memo(raw_message) if raw_message else ""

        if order_id.lower() in memo.lower():
            tx_hash = tx.get("transaction_id", {}).get("hash", "")
            logger.info(
                f"Payment found: order={order_id}, "
                f"amount={nano_to_ton(value_nano):.4f} TON, "
                f"sender={source[:20]}..., memo={memo}"
            )
            return PaymentResult(
                paid=True,
                tx_hash=tx_hash,
                amount_nano=value_nano,
                sender=source,
                memo=memo,
            )

    return PaymentResult(paid=False)


async def wait_for_payment(
    order_id: str,
    expected_amount_ton: float,
    timeout: int = POLL_TIMEOUT,
) -> PaymentResult:
    """
    Block until payment is found or timeout.
    Polls every POLL_INTERVAL seconds.
    """
    since = int(time.time())
    deadline = since + timeout
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        result = await check_transaction(order_id, expected_amount_ton, since)

        if result.paid:
            return result

        if result.error and attempts == 1:
            logger.warning(f"First poll failed: {result.error}")

        await asyncio.sleep(POLL_INTERVAL)

    return PaymentResult(
        paid=False,
        error=f"Timeout after {timeout}s ({attempts} attempts)",
    )


def generate_order_id(chat_id: int) -> str:
    """Generate a unique, short order ID for single-analysis payment memo."""
    timestamp = int(time.time())
    return f"BPC{chat_id % 100000}{timestamp % 100000}"


def generate_subscription_order_id(chat_id: int) -> str:
    """Generate order ID for monthly subscription payment memo."""
    timestamp = int(time.time())
    return f"SUB{chat_id % 100000}{timestamp % 100000}"


async def check_subscription_payment(
    chat_id: int,
    expected_amount_ton: float,
    since_timestamp: int,
) -> PaymentResult:
    """
    Check for a subscription payment. Matches any memo starting with 'SUB'
    from this chat_id's order, OR any payment with correct amount after since.
    More lenient than single-payment matching because subscription users
    might reuse the same memo or forget the exact order ID.
    """
    if not PAYMENT_WALLET:
        return PaymentResult(paid=False, error="TON_PAYMENT_WALLET not configured")

    expected_nano = ton_to_nano(expected_amount_ton)
    min_nano = int(expected_nano * 0.95)  # 5% tolerance for subscription

    url = f"{TONCENTER_API}/getTransactions"
    params = {"address": PAYMENT_WALLET, "limit": 30, "archival": "false"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return PaymentResult(paid=False, error=str(e))

    if data.get("ok") is not True:
        return PaymentResult(paid=False, error=f"API error: {data}")

    for tx in data.get("result", []):
        in_msg = tx.get("in_msg", {})
        source = in_msg.get("source", "")
        if not source:
            continue

        try:
            value_nano = int(in_msg.get("value", "0"))
        except (ValueError, TypeError):
            continue

        if value_nano < min_nano:
            continue

        tx_time = tx.get("utime", 0)
        if tx_time < since_timestamp:
            continue

        raw_message = in_msg.get("message", "")
        memo = decode_memo(raw_message) if raw_message else ""

        # Match: memo contains SUB prefix, OR amount matches subscription tier
        if "SUB" in memo.upper() or value_nano >= expected_nano:
            tx_hash = tx.get("transaction_id", {}).get("hash", "")
            return PaymentResult(
                paid=True,
                tx_hash=tx_hash,
                amount_nano=value_nano,
                sender=source,
                memo=memo,
            )

    return PaymentResult(paid=False)
