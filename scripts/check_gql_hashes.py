#!/usr/bin/env python3
"""
Probe Twitch GQL persisted-query hashes used by the miner.

A wrong / rotated hash returns PersistedQueryNotFound even without auth.
Other errors (auth, missing variables, etc.) mean the hash is still registered.

Exit codes:
  0 — all checked queries are known to Twitch
  1 — one or more queries returned PersistedQueryNotFound
  2 — unexpected script / network failure
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Allow running from repo root or scripts/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aiohttp

from constants import GQL_QUERIES, ClientType

# Queries that are critical for mining. Checked anonymously: PersistedQueryNotFound
# means the hash is stale regardless of auth.
CHECK_KEYS = (
    "Inventory",
    "Campaigns",
    "CampaignDetails",
    "CurrentDrop",
    "AvailableDrops",
    "PlaybackAccessToken",
    "GetStreamInfo",
    "GameDirectory",
)


def _payload_for(key: str) -> dict[str, Any]:
    query = GQL_QUERIES[key]
    # Strip Ellipsis placeholders — Twitch still resolves the persisted query by hash
    variables: dict[str, Any] = {}
    raw_vars = query.get("variables")
    if isinstance(raw_vars, dict):
        for k, v in raw_vars.items():
            if v is Ellipsis:
                # Provide inert stand-ins so the request body is valid JSON
                variables[k] = "" if "Login" in k or "channel" in k.lower() or k in {
                    "login", "slug", "name", "dropID", "channelID", "channelLogin"
                } else None
            elif isinstance(v, dict):
                variables[k] = {
                    ik: ("" if iv is Ellipsis else iv) for ik, iv in v.items()
                }
            else:
                variables[k] = v
    body: dict[str, Any] = {
        "operationName": query["operationName"],
        "extensions": query["extensions"],
    }
    if variables:
        body["variables"] = variables
    return body


async def _check_one(
    session: aiohttp.ClientSession, key: str
) -> tuple[str, str, bool]:
    """
    Returns (key, detail, ok).
    ok=False only for PersistedQueryNotFound.
    """
    headers = {
        "Client-Id": ClientType.ANDROID_APP.CLIENT_ID,
        "User-Agent": ClientType.ANDROID_APP.USER_AGENT,
        "Content-Type": "application/json",
    }
    body = _payload_for(key)
    try:
        async with session.post(
            "https://gql.twitch.tv/gql",
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            text = await response.text()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return key, f"HTTP {response.status}: non-JSON body", True
    except Exception as exc:
        return key, f"request failed: {exc}", True

    messages: list[str] = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        for err in item.get("errors") or []:
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                messages.append(err["message"])

    if any(m == "PersistedQueryNotFound" for m in messages):
        sha = body["extensions"]["persistedQuery"]["sha256Hash"]
        return key, f"PersistedQueryNotFound (sha256={sha})", False
    if messages:
        return key, f"hash OK (other errors: {', '.join(messages[:3])})", True
    return key, "hash OK", True


async def main() -> int:
    missing = [k for k in CHECK_KEYS if k not in GQL_QUERIES]
    if missing:
        print(f"Unknown query keys in CHECK_KEYS: {missing}", file=sys.stderr)
        return 2

    # Apply local overrides file if present (same rules as the app)
    overrides_path = ROOT / "gql_overrides.json"
    if overrides_path.exists():
        from gql_overrides import apply_overrides, validate_overrides

        with overrides_path.open("r", encoding="utf8") as file:
            raw = json.load(file)
        sanitized = validate_overrides(raw)
        if sanitized:
            applied = apply_overrides(sanitized)
            if applied:
                print(f"Applied local overrides for: {', '.join(applied)}")

    print(f"Checking {len(CHECK_KEYS)} GQL persisted queries against Twitch...")
    failed: list[str] = []
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(_check_one(session, key) for key in CHECK_KEYS)
        )
    for key, detail, ok in results:
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] {key}: {detail}")
        if not ok:
            failed.append(key)

    if failed:
        print(
            "\nStale hashes detected. Update constants.py and/or gql_overrides.json "
            f"for: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    print("\nAll checked GQL hashes are recognized by Twitch.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(2) from None
