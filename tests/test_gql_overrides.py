from __future__ import annotations

from copy import deepcopy

from constants import GQL_QUERIES
from gql_overrides import apply_overrides, sync_ops_hashes, validate_overrides


def test_validate_rejects_bad_hash():
    data = {
        "version": 1,
        "queries": {
            "Inventory": "not-a-hash",
            "Campaigns": "c" * 64,
        },
    }
    cleaned = validate_overrides(data)
    assert cleaned is not None
    assert "Inventory" not in cleaned["queries"]
    assert cleaned["queries"]["Campaigns"] == "c" * 64


def test_validate_ignores_unknown_query_keys():
    data = {
        "version": 1,
        "queries": {"NotARealQuery": "a" * 64},
    }
    cleaned = validate_overrides(data)
    assert cleaned is not None
    assert cleaned["queries"] == {}


def test_apply_overrides_updates_hash():
    key = "Inventory"
    original = GQL_QUERIES[key]["extensions"]["persistedQuery"]["sha256Hash"]
    new_hash = ("ab" * 32)
    try:
        applied = apply_overrides({"version": 1, "queries": {key: new_hash}})
        assert key in applied
        assert (
            GQL_QUERIES[key]["extensions"]["persistedQuery"]["sha256Hash"] == new_hash
        )
    finally:
        GQL_QUERIES[key]["extensions"]["persistedQuery"]["sha256Hash"] = original


def test_sync_ops_hashes_updates_deepcopy():
    key = "Campaigns"
    original = GQL_QUERIES[key]["extensions"]["persistedQuery"]["sha256Hash"]
    new_hash = ("cd" * 32)
    ops = deepcopy(GQL_QUERIES[key])
    try:
        apply_overrides({"version": 1, "queries": {key: new_hash}})
        assert ops["extensions"]["persistedQuery"]["sha256Hash"] == original
        sync_ops_hashes(ops)
        assert ops["extensions"]["persistedQuery"]["sha256Hash"] == new_hash
    finally:
        GQL_QUERIES[key]["extensions"]["persistedQuery"]["sha256Hash"] = original
