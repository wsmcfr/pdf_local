"""API keys live in the OS credential store, never in SQLite."""

from __future__ import annotations

import keyring

SERVICE = "local-kb"


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def provider_key_name(provider_id: str) -> str:
    return f"provider:{provider_id}"
