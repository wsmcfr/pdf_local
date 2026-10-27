"""Typed errors raised by provider adapters.

Messages here are safe to surface to the UI / logs: they must never contain
API keys or request headers.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for provider adapter failures."""


class ProviderConfigError(ProviderError):
    """The provider configuration is unusable (unsupported kind, missing URL)."""


class ProviderAuthError(ProviderError):
    """The upstream rejected our credentials (401/403)."""


class ProviderNotFoundError(ProviderError):
    """The upstream endpoint or resource does not exist (404)."""


class ProviderRateLimitError(ProviderError):
    """The upstream is rate limiting us (429)."""


class ProviderTimeoutError(ProviderError):
    """The upstream request timed out."""
