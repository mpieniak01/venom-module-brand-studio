from __future__ import annotations

import os
import time
from dataclasses import dataclass
from threading import Lock

import httpx

from venom_module_brand_studio.api.schemas import BrandStudioAuditEntry


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class BrandStudioAuditPublishConfig:
    enabled: bool
    core_base_url: str
    timeout_seconds: float
    source: str
    ingest_token: str


class BrandStudioAuditPublisher:
    def __init__(self, config: BrandStudioAuditPublishConfig) -> None:
        self.config = config
        self._client_lock = Lock()
        self._state_lock = Lock()
        self._client: httpx.Client | None = None
        self._failure_count = 0
        self._suspended_until = 0.0

    @classmethod
    def from_env(cls) -> "BrandStudioAuditPublisher":
        default_enabled = not bool(os.getenv("PYTEST_CURRENT_TEST"))
        return cls(
            BrandStudioAuditPublishConfig(
                enabled=_env_flag("BRAND_STUDIO_AUDIT_PUBLISH_ENABLED", default=default_enabled),
                core_base_url=(
                    (os.getenv("BRAND_STUDIO_AUDIT_CORE_BASE_URL") or "").strip()
                    or "http://127.0.0.1:8000"
                ),
                timeout_seconds=max(
                    0.1,
                    _env_float("BRAND_STUDIO_AUDIT_TIMEOUT_SECONDS", default=0.8),
                ),
                source=(os.getenv("BRAND_STUDIO_AUDIT_SOURCE") or "").strip()
                or "module.brand_studio",
                ingest_token=(os.getenv("BRAND_STUDIO_AUDIT_INGEST_TOKEN") or "").strip(),
            )
        )

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self.config.timeout_seconds)
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass

    def publish_entry(self, entry: BrandStudioAuditEntry) -> bool:
        if not self.config.enabled:
            return False
        with self._state_lock:
            if time.monotonic() < self._suspended_until:
                return False

        source = self._resolve_source(entry)
        context = (entry.details or "").strip() or entry.payload_hash
        payload = {
            "id": f"{source}:{entry.id}",
            "source": source,
            "action": entry.action,
            "actor": entry.actor,
            "status": entry.status,
            "context": context,
            "details": {
                "module_event_id": entry.id,
                "module_payload_hash": entry.payload_hash,
                "module_details": entry.details,
            },
            "timestamp": entry.timestamp.isoformat(),
        }
        headers = {}
        if self.config.ingest_token:
            headers["X-Venom-Audit-Token"] = self.config.ingest_token

        url = f"{self.config.core_base_url.rstrip('/')}/api/v1/audit/stream"
        try:
            response = self._get_client().post(url, json=payload, headers=headers)
            response.raise_for_status()
            with self._state_lock:
                self._failure_count = 0
                self._suspended_until = 0.0
            return True
        except Exception:
            with self._state_lock:
                self._failure_count += 1
                backoff = min(60.0, float(2 ** min(self._failure_count, 6)))
                self._suspended_until = time.monotonic() + backoff
            return False

    def _resolve_source(self, entry: BrandStudioAuditEntry) -> str:
        details_l = (entry.details or "").lower()
        if entry.action.startswith("queue.") and (
            details_l.startswith("github:") or ":github" in details_l
        ):
            return "core.technical.github_publish"
        return self.config.source
