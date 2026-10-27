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
