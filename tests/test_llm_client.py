from __future__ import annotations

import pytest

from venom_module_brand_studio.services.llm_client import (
    BrandStudioLLMClient,
    BrandStudioLLMConfig,
    LLMGenerationError,
)


def _build_client(*, auto_start_local_server: bool = True) -> BrandStudioLLMClient:
    return BrandStudioLLMClient(
        BrandStudioLLMConfig(
            enabled=True,
            core_base_url="http://127.0.0.1:8000",
            timeout_seconds=5.0,
            max_tokens=128,
            temperature=0.2,
            auto_start_local_server=auto_start_local_server,
        )
    )


def test_generate_text_retries_after_auto_start(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(auto_start_local_server=True)
    calls = {"count": 0}

    def fake_stream(_payload):
        calls["count"] += 1
        if calls["count"] == 1:
            raise LLMGenerationError("llm_http_error: connection refused")
        return "Recovered output"

    monkeypatch.setattr(client, "_stream_completion", fake_stream)
    monkeypatch.setattr(client, "_try_auto_start_local_server", lambda: True)

    out = client.generate_text("prompt")
    assert out == "Recovered output"
    assert calls["count"] == 2


def test_generate_text_does_not_retry_when_auto_start_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(auto_start_local_server=False)
    monkeypatch.setattr(
        client,
        "_stream_completion",
        lambda _payload: (_ for _ in ()).throw(LLMGenerationError("llm_http_error")),
    )

    with pytest.raises(LLMGenerationError):
        client.generate_text("prompt")


def test_resolve_local_server_name_from_payload() -> None:
    client = _build_client()
    assert client._resolve_local_server_name({"active_server": "ollama"}) == "ollama"
    assert client._resolve_local_server_name({"active_server": "vllm"}) == "vllm"
    assert (
        client._resolve_local_server_name(
            {"active_server": "local", "active_endpoint": "http://localhost:11434/v1"}
        )
        == "ollama"
    )
    assert (
        client._resolve_local_server_name(
            {"active_server": "local", "active_endpoint": "http://127.0.0.1:8001/v1"}
        )
        == "vllm"
    )


def test_http_client_is_reused_and_recreated_after_close() -> None:
    client = _build_client()
    first = client._get_client()
    second = client._get_client()
    assert first is second
    client.close()
    third = client._get_client()
    assert third is not first
    client.close()


def test_from_env_clamps_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_STUDIO_LLM_ENABLED", "true")
    monkeypatch.setenv("BRAND_STUDIO_LLM_TEMPERATURE", "9.9")
    hot = BrandStudioLLMClient.from_env()
    assert hot.config.temperature == 2.0

    monkeypatch.setenv("BRAND_STUDIO_LLM_TEMPERATURE", "-3")
    cold = BrandStudioLLMClient.from_env()
    assert cold.config.temperature == 0.0
