from __future__ import annotations

import re
import json
import random
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from constants import (
    FORK_OWNER,
    GQL_OVERRIDES_CACHE_PATH,
    GQL_QUERIES,
    ClientType,
    JsonType,
    _resource_path,
)

logger = logging.getLogger("TwitchDrops")

GQL_OVERRIDES_BUNDLED_PATH = _resource_path("gql_overrides.json")
GQL_OVERRIDES_URL = (
    f"https://raw.githubusercontent.com/{FORK_OWNER}/TwitchDropsMiner/master/gql_overrides.json"
)
GQL_OVERRIDES_TTL = timedelta(hours=6)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_USER_AGENTS = 20
_MAX_USER_AGENT_LEN = 512

# Keys we allow overriding — must match GQL_QUERIES
_ALLOWED_QUERY_KEYS = frozenset(GQL_QUERIES.keys())


def _read_json_file(path: Path) -> JsonType | None:
    try:
        with path.open("r", encoding="utf8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def _cache_is_fresh(path: Path, *, ttl: timedelta = GQL_OVERRIDES_TTL) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    return datetime.now(timezone.utc) - mtime < ttl


def validate_overrides(data: JsonType) -> JsonType | None:
    """
    Return a sanitized overrides object, or None if the payload is unusable.
    Unknown keys are ignored. Invalid hashes/agents are skipped.
    """
    if not isinstance(data, dict):
        return None
    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        return None

    clean: JsonType = {
        "version": version,
        "updated_at": data.get("updated_at", ""),
        "queries": {},
        "android_app": {},
    }
    if not isinstance(clean["updated_at"], str):
        clean["updated_at"] = ""

    raw_queries = data.get("queries") or {}
    if isinstance(raw_queries, dict):
        for key, value in raw_queries.items():
            if key not in _ALLOWED_QUERY_KEYS:
                continue
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                logger.warning("Ignoring invalid GQL override hash for %s", key)
                continue
            clean["queries"][key] = value.lower()

    raw_android = data.get("android_app") or {}
    if isinstance(raw_android, dict):
        agents = raw_android.get("user_agents")
        if isinstance(agents, list):
            cleaned_agents: list[str] = []
            for agent in agents[:_MAX_USER_AGENTS]:
                if (
                    isinstance(agent, str)
                    and 0 < len(agent) <= _MAX_USER_AGENT_LEN
                    and "\n" not in agent
                    and "\r" not in agent
                ):
                    cleaned_agents.append(agent)
            if cleaned_agents:
                clean["android_app"]["user_agents"] = cleaned_agents

    return clean


def apply_overrides(data: JsonType) -> list[str]:
    """
    Apply sanitized overrides onto in-memory GQL_QUERIES / ANDROID_APP.
    Returns the list of query keys that were updated.
    """
    sanitized = validate_overrides(data)
    if sanitized is None:
        return []

    applied: list[str] = []
    queries: dict[str, str] = sanitized.get("queries") or {}
    for key, sha256 in queries.items():
        query = GQL_QUERIES.get(key)
        if query is None:
            continue
        try:
            current = query["extensions"]["persistedQuery"]["sha256Hash"]
        except (KeyError, TypeError):
            continue
        if current == sha256:
            continue
        query["extensions"]["persistedQuery"]["sha256Hash"] = sha256
        applied.append(key)

    android = sanitized.get("android_app") or {}
    agents = android.get("user_agents")
    if isinstance(agents, list) and agents:
        ClientType.ANDROID_APP.USER_AGENT = random.choice(agents)

    return applied


def sync_ops_hashes(ops: JsonType | list[JsonType]) -> None:
    """Update sha256Hash on already-built GQL ops from current GQL_QUERIES."""
    items = ops if isinstance(ops, list) else [ops]
    by_name = {
        q.get("operationName"): q["extensions"]["persistedQuery"]["sha256Hash"]
        for q in GQL_QUERIES.values()
        if isinstance(q.get("operationName"), str)
    }
    for op in items:
        if not isinstance(op, dict):
            continue
        name = op.get("operationName")
        if not isinstance(name, str) or name not in by_name:
            continue
        try:
            op["extensions"]["persistedQuery"]["sha256Hash"] = by_name[name]
        except (KeyError, TypeError):
            continue


def load_cached_or_bundled() -> JsonType | None:
    """Prefer a fresh disk cache, otherwise the bundled defaults file."""
    if GQL_OVERRIDES_CACHE_PATH.exists():
        cached = _read_json_file(GQL_OVERRIDES_CACHE_PATH)
        if cached is not None:
            sanitized = validate_overrides(cached)
            if sanitized is not None:
                return sanitized
    bundled = _read_json_file(Path(GQL_OVERRIDES_BUNDLED_PATH))
    if bundled is None:
        return None
    return validate_overrides(bundled)


def save_cache(data: JsonType) -> None:
    sanitized = validate_overrides(data)
    if sanitized is None:
        return
    try:
        with GQL_OVERRIDES_CACHE_PATH.open("w", encoding="utf8") as file:
            json.dump(sanitized, file, indent=4, sort_keys=True)
            file.write("\n")
    except OSError:
        logger.warning("Unable to write GQL overrides cache", exc_info=True)


def should_fetch_remote(*, force: bool = False) -> bool:
    if force:
        return True
    return not _cache_is_fresh(GQL_OVERRIDES_CACHE_PATH)


async def fetch_remote_overrides(session: Any) -> JsonType | None:
    """
    Download overrides from the fork's GitHub raw URL.
    `session` is an aiohttp.ClientSession (passed to avoid circular imports).
    """
    try:
        from aiohttp import ClientTimeout

        async with session.get(
            GQL_OVERRIDES_URL, timeout=ClientTimeout(total=20)
        ) as response:
            if response.status != 200:
                logger.warning(
                    "GQL overrides fetch failed: HTTP %s", response.status
                )
                return None
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "json" not in content_type and "text" not in content_type:
                # GitHub raw may use text/plain — still attempt parse
                if "octet" in content_type:
                    return None
            text = await response.text()
    except Exception:
        logger.warning("GQL overrides fetch raised", exc_info=True)
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("GQL overrides payload is not valid JSON")
        return None
    return validate_overrides(data)


def operation_name_from_ops(ops: JsonType | list[JsonType]) -> str:
    items = ops if isinstance(ops, list) else [ops]
    names: list[str] = []
    for op in items:
        if isinstance(op, dict):
            name = op.get("operationName")
            if isinstance(name, str) and name:
                names.append(name)
    return ", ".join(names) if names else "unknown"
