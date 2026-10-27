"""Build runtime provider configuration from database rows and the keyring."""

from __future__ import annotations

import json
from collections.abc import Mapping

from . import secret_store
from .base import ProviderConfig


def load_key(provider_id: str) -> str | None:
    return secret_store.get_secret(secret_store.provider_key_name(provider_id))


def config_from_row(
    row: Mapping | object,
    api_key: str | None = None,
) -> ProviderConfig:
    data = dict(row)
    raw_extra = data.get("extra_json") or "{}"
    try:
        extra = json.loads(raw_extra)
    except (TypeError, ValueError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}

    return ProviderConfig(
        id=str(data.get("id") or ""),
        name=str(data.get("name") or ""),
        kind=str(data.get("kind") or ""),
        base_url=str(data.get("base_url") or ""),
        model=str(data.get("model") or ""),
        api_key=api_key,
        extra=extra,
    )


def load_config(row: Mapping | object) -> ProviderConfig:
    data = dict(row)
    return config_from_row(data, load_key(str(data.get("id") or "")))


def select_provider(conn, settings: Mapping | object | None) -> object | None:
    """Return the provider row the gateway should use, or ``None``.

    An explicitly configured ``llm_provider_id`` wins; otherwise the first
    enabled provider is used. The row is returned as-is so callers can pass it
    to :func:`load_config`.
    """
    data = dict(settings or {})
    provider_id = str(data.get("llm_provider_id") or "").strip()
    if provider_id:
        row = conn.execute(
            "SELECT * FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if row is not None:
            return row
    return conn.execute(
        "SELECT * FROM providers WHERE enabled = 1 "
        "ORDER BY created_at ASC, id ASC LIMIT 1"
    ).fetchone()
