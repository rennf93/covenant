"""Neutral price witness for evidence bundles (Pyth Hermes REST).

The equity an operator commits is self-reported; a witness adds the Pyth
network price for the instrument at commit time so a reader can later
sanity-check the claimed equity trajectory against a neutral source and
detect stale or fabricated witness data. This is an honestly-labeled
cross-check, not proof.

Failure posture: a commit must never fail because the witness failed. Any
problem (no feed id configured, malformed id, network error, timeout,
unparseable payload) returns None and logs exactly one line.

The witness is an UNSIGNED top-level annotation on the bundle, outside the
EIP-712 message: the signed schema stays byte-identical whether or not a
witness was captured.

Layering: leaf (vouch.attest). Stdlib urllib only; no new dependencies.
"""

from __future__ import annotations

import json
import re
import urllib.request

from vouch.logging import get_logger

logger = get_logger(__name__)

HERMES_TIMEOUT_S = 3.0

# Pyth feed ids are 32-byte hex with a 0x prefix.
_FEED_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def fetch_witness(feed_id: str, hermes_url: str) -> dict | None:
    """Latest Pyth price for feed_id via Hermes, assembled into the witness
    dict (or None, never an exception). One HTTP GET, 3s timeout, stdlib only.

    Hermes latest-price endpoint: GET <hermes>/v2/updates/price/latest?ids[]=<id>;
    the response carries the parsed entries under "parsed" (a bare list is
    accepted too, defensively).
    """
    if not feed_id:
        logger.info("pyth witness skipped: COVENANT_PYTH_PRICE_ID not configured")
        return None
    if not _FEED_ID_RE.fullmatch(feed_id):
        logger.warning("pyth witness skipped: COVENANT_PYTH_PRICE_ID is not a 32-byte hex feed id")
        return None
    url = f"{hermes_url.rstrip('/')}/v2/updates/price/latest?ids[]={feed_id}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HERMES_TIMEOUT_S) as resp:  # noqa: S310
            payload = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 - the witness is best-effort by contract
        logger.warning("pyth witness fetch failed: %s", e)
        return None
    witness = assemble_witness(feed_id, payload)
    if witness is None:
        logger.warning("pyth witness skipped: unparseable Hermes response")
    return witness


def assemble_witness(feed_id: str, hermes_response: object) -> dict | None:
    """(feed_id, hermes_response) -> witness dict; pure, total, network-free.

    Accepts either observed response shape: the parsed entries list directly,
    or the full object carrying it under "parsed". Entry shape:
    {id, price: {price: "<int>", conf: "<int>", expo: <int>,
    publish_time: <int>}, ema_price: {...}}. Returns None on anything
    malformed. The human price is price * 10^expo.
    """
    entries = hermes_response
    if isinstance(hermes_response, dict):
        entries = hermes_response.get("parsed")
    if not isinstance(entries, list) or not entries:
        return None
    entry = entries[0]
    if not isinstance(entry, dict):
        return None
    price = entry.get("price")
    if not isinstance(price, dict):
        return None
    raw_price = _as_int(price.get("price"))
    confidence = _as_int(price.get("conf"))
    expo = _as_int(price.get("expo"))
    publish_time = _as_int(price.get("publish_time"))
    if raw_price is None or confidence is None or expo is None or publish_time is None:
        return None
    return {
        "source": "pyth",
        "priceId": feed_id,
        "price": str(raw_price),
        "expo": expo,
        "confidence": str(confidence),
        "publishTime": publish_time,
    }


def _as_int(v: object) -> int | None:
    """Defensive integer coercion: accepts int or exact integer string only
    (never floats, bools or anything else), so a hostile payload cannot
    smuggle in a value that round-trips differently through the bundle."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v):
        return int(v)
    return None
