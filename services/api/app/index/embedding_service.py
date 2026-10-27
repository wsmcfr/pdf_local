"""Embedding abstraction with local, external, auto and FTS fallback modes.

The service is deliberately synchronous: the indexer and retriever run in
worker/sync contexts. External providers expose an ``async`` ``embed`` method,
so it is driven with :func:`asyncio.run` from :meth:`EmbeddingService.embed`.
Calling this service from inside a running event loop is not supported; the
provider call then fails with a logged warning and callers fall back to FTS.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from ..providers import registry
from ..providers.factory import create_provider
from .embeddings import (
    MODEL_NAME,
    LocalModelUnavailable,
    encode,
    local_model_available,
)

logger = logging.getLogger(__name__)

MODES = ("local", "external", "auto", "fts")
DEFAULT_MODE = "local"

ExternalEmbed = Callable[[list[str]], Any]
ProviderFactory = Callable[..., Any]


def _valid_vectors(result: Any, expected: int) -> list[list[float]] | None:
    if not isinstance(result, list) or not result:
        return None
    if len(result) != expected:
        return None
    vectors: list[list[float]] = []
    for vector in result:
        if not isinstance(vector, (list, tuple)) or not vector:
            return None
        vectors.append([float(value) for value in vector])
    return vectors


class EmbeddingService:
    """Resolve embedding vectors according to ``settings["embedding_mode"]``.

    ``external_embed`` and ``provider_factory`` are injectable so tests never
    load BGE-M3, hit the network, or read real credentials.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Mapping[str, Any] | None,
        local_encode: Callable[[list[str]], Any] = encode,
        external_embed: ExternalEmbed | None = None,
        provider_factory: ProviderFactory = create_provider,
    ) -> None:
        self.conn = conn
        self.settings = settings if settings is not None else {}
        self.local_encode = local_encode
        self.external_embed = external_embed
        self.provider_factory = provider_factory

    # -- settings helpers --------------------------------------------------

    def _setting(self, key: str, default: str = "") -> str:
        try:
            value = self.settings.get(key, default)  # type: ignore[union-attr]
        except AttributeError:
            value = default
        if value is None:
            return default
        return str(value)

    @property
    def mode(self) -> str:
        mode = self._setting("embedding_mode", DEFAULT_MODE).strip().lower()
        return mode if mode in MODES else DEFAULT_MODE

    @property
    def provider_id(self) -> str:
        return self._setting("embedding_provider_id").strip()

    @property
    def model(self) -> str:
        return self._setting("embedding_model").strip()

    # -- main API ----------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return one vector per text, or ``None`` when only FTS is possible."""
        if not texts:
            return []

        mode = self.mode
        if mode == "fts":
            return None

        if mode in ("local", "auto"):
            vectors = self._local(texts)
            if vectors is not None:
                return vectors
            if mode == "local":
                return None

        if mode in ("external", "auto"):
            return self._external(texts)

        return None

    def info(self) -> dict:
        """Describe the current configuration without exposing any secret."""
        mode = self.mode
        provider_id = self.provider_id
        model = self.model
        info: dict = {
            "mode": mode,
            "provider_id": provider_id,
            "model": model,
            "local_model": MODEL_NAME,
            "ok": True,
            "status": "ready",
        }

        if mode == "fts":
            info["status"] = "fts"
        elif mode == "external":
            configured = bool(provider_id) and bool(model) and self._provider_row() is not None
            info["ok"] = configured
            info["status"] = "ready" if configured else "not_configured"
        elif mode == "local":
            available = local_model_available()
            info["ok"] = available
            info["status"] = "ready" if available else "not_downloaded"
        elif mode == "auto":
            info["status"] = "auto" if local_model_available() else "auto_fts"
        return info

    def test(self, text: str) -> dict:
        """Embed a single string and report a safe, key-free result."""
        vectors = self.embed([text])
        if not vectors or not vectors[0]:
            reason = (
                "LOCAL_MODEL_NOT_DOWNLOADED"
                if self.mode == "local" and not local_model_available()
                else "EMBEDDING_UNAVAILABLE"
            )
            return {
                "ok": False,
                "mode": self.mode,
                "fallback": "fts",
                "reason": reason,
            }
        return {"ok": True, "mode": self.mode, "dimension": len(vectors[0])}

    # -- local -------------------------------------------------------------

    def _local(self, texts: list[str]) -> list[list[float]] | None:
        try:
            result = self.local_encode(texts)
        except LocalModelUnavailable:
            logger.warning(
                "local embedding model is not downloaded; using external/FTS fallback"
            )
            return None
        except Exception:  # noqa: BLE001 - fall back to external/FTS
            logger.warning("local embedding failed", exc_info=True)
            return None

        vectors = _valid_vectors(result, len(texts))
        if vectors is None:
            logger.warning("local embedding returned no usable vectors")
        return vectors

    # -- external ----------------------------------------------------------

    def _external(self, texts: list[str]) -> list[list[float]] | None:
        if self.external_embed is not None:
            try:
                return self._resolve(
                    self.external_embed(texts), len(texts), source="external"
                )
            except Exception:  # noqa: BLE001 - fall back to FTS
                logger.warning("external embedding failed", exc_info=True)
                return None

        provider_id = self.provider_id
        if not provider_id:
            logger.warning("external embedding provider is not configured")
            return None

        row = self._provider_row()
        if row is None:
            logger.warning(
                "external embedding provider %s does not exist", provider_id
            )
            return None

        try:
            api_key = registry.load_key(provider_id)
            config = registry.config_from_row(row, api_key)
        except Exception:  # noqa: BLE001 - never echo keyring errors/secrets
            logger.warning("failed to load embedding provider config", exc_info=True)
            return None

        model = self.model
        if model:
            config.model = model

        try:
            provider = self.provider_factory(config)
            return self._resolve(
                provider.embed(texts), len(texts), source="external"
            )
        except Exception:  # noqa: BLE001 - fall back to FTS
            logger.warning("external embedding request failed", exc_info=True)
            return None

    def _resolve(self, value: Any, expected: int, source: str) -> list[list[float]] | None:
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        vectors = _valid_vectors(value, expected)
        if vectors is None:
            logger.warning("%s embedding returned no usable vectors", source)
        return vectors

    def _provider_row(self):
        provider_id = self.provider_id
        if not provider_id:
            return None
        return self.conn.execute(
            "SELECT * FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
