from __future__ import annotations

import json
import os
from dataclasses import dataclass
from threading import Lock

import httpx


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


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class LLMGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrandStudioLLMConfig:
    enabled: bool
    core_base_url: str
    timeout_seconds: float
    max_tokens: int
    temperature: float
    auto_start_local_server: bool


class BrandStudioLLMClient:
    def __init__(self, config: BrandStudioLLMConfig) -> None:
        self.config = config
        self._client_lock = Lock()
        self._client: httpx.Client | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @classmethod
    def from_env(cls) -> "BrandStudioLLMClient":
        return cls(
            BrandStudioLLMConfig(
                enabled=_env_flag("BRAND_STUDIO_LLM_ENABLED", default=False),
                core_base_url=(
                    (os.getenv("BRAND_STUDIO_LLM_CORE_BASE_URL") or "").strip()
                    or "http://127.0.0.1:8000"
                ),
                timeout_seconds=max(
                    1.0, _env_float("BRAND_STUDIO_LLM_TIMEOUT_SECONDS", default=25.0)
                ),
                max_tokens=max(64, _env_int("BRAND_STUDIO_LLM_MAX_TOKENS", default=800)),
                temperature=max(
                    0.0,
                    min(2.0, _env_float("BRAND_STUDIO_LLM_TEMPERATURE", default=0.3)),
                ),
                auto_start_local_server=_env_flag(
                    "BRAND_STUDIO_LLM_AUTO_START_LOCAL_SERVER",
                    default=True,
                ),
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

    def generate_text(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        session_id: str | None = None,
    ) -> str:
        if not self.config.enabled:
            raise LLMGenerationError("llm_disabled")

        payload: dict[str, object] = {
            "content": prompt,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if session_id:
            payload["session_id"] = session_id
        try:
            return self._stream_completion(payload)
        except LLMGenerationError as exc:
            # Best-effort auto-recovery path for local runtimes (ollama/vllm).
            if not self.config.auto_start_local_server:
                raise
            if not self._should_try_auto_start(exc):
                raise
            if not self._try_auto_start_local_server():
                raise
            return self._stream_completion(payload)

    def _stream_completion(self, payload: dict[str, object]) -> str:
        url = f"{self.config.core_base_url.rstrip('/')}/api/v1/llm/simple/stream"
        chunks: list[str] = []
        current_event = ""
        try:
            client = self._get_client()
            with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line.split(":", 1)[1].strip()
                    if current_event == "content":
                        if not data:
                            continue
                        packet = json.loads(data)
                        text = packet.get("text")
                        if isinstance(text, str) and text:
                            chunks.append(text)
                        continue
                    if current_event == "error":
                        error_msg = "llm_stream_error"
                        if data:
                            try:
                                packet = json.loads(data)
                                if isinstance(packet, dict):
                                    candidate = packet.get("message")
                                    if isinstance(candidate, str) and candidate.strip():
                                        error_msg = candidate
                            except Exception:
                                pass
                        raise LLMGenerationError(error_msg)
                    if current_event == "done":
                        break
        except httpx.HTTPError as exc:
            raise LLMGenerationError(f"llm_http_error:{exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMGenerationError("llm_stream_decode_error") from exc

        content = "".join(chunks).strip()
        if not content:
            raise LLMGenerationError("llm_empty_response")
        return content

    def _should_try_auto_start(self, exc: Exception) -> bool:
        detail = str(exc).lower()
        return (
            "connection" in detail
            or "timeout" in detail
            or "http 503" in detail
            or "llm_http_error" in detail
        )

    def _try_auto_start_local_server(self) -> bool:
        base = self.config.core_base_url.rstrip("/")
        active_url = f"{base}/api/v1/system/llm-servers/active"
        try:
            timeout = min(self.config.timeout_seconds, 10.0)
            client = self._get_client()
            active = client.get(active_url, timeout=timeout)
            active.raise_for_status()
            payload = active.json() if active.content else {}
            server_name = self._resolve_local_server_name(payload)
            if server_name not in {"ollama", "vllm"}:
                return False
            start_url = f"{base}/api/v1/system/llm-servers/{server_name}/start"
            start_resp = client.post(start_url, timeout=timeout)
            return start_resp.status_code < 400
        except Exception:
            return False

    def _resolve_local_server_name(self, payload: dict) -> str | None:
        raw = payload.get("active_server")
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"ollama", "vllm"}:
                return normalized
            if normalized == "local":
                endpoint = payload.get("active_endpoint")
                if isinstance(endpoint, str):
                    endpoint_l = endpoint.lower()
                    if "11434" in endpoint_l or "ollama" in endpoint_l:
                        return "ollama"
                    if "vllm" in endpoint_l or ":8000" in endpoint_l or ":8001" in endpoint_l:
                        return "vllm"
        return None
