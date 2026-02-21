from __future__ import annotations

import json
import os
from dataclasses import dataclass

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


class BrandStudioLLMClient:
    def __init__(self, config: BrandStudioLLMConfig) -> None:
        self.config = config

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
                temperature=_env_float("BRAND_STUDIO_LLM_TEMPERATURE", default=0.3),
            )
        )

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

        url = f"{self.config.core_base_url.rstrip('/')}/api/v1/llm/simple/stream"
        payload: dict[str, object] = {
            "content": prompt,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if session_id:
            payload["session_id"] = session_id

        chunks: list[str] = []
        current_event = ""

        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
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
                            packet = json.loads(data)
                            text = packet.get("text")
                            if isinstance(text, str) and text:
                                chunks.append(text)
                            continue
                        if current_event == "error":
                            error_msg = "llm_stream_error"
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
