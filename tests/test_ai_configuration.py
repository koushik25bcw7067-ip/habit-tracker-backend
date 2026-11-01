import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services.ai_service import AIService


def test_gemini_defaults_do_not_use_placeholder_credentials():
    assert Settings.model_fields["ai_api_key"].default == ""
    assert Settings.model_fields["ai_model"].default == "gemini-3.5-flash-lite"


def test_ai_model_is_loaded_from_environment_name():
    settings = Settings(
        _env_file=None,
        AI_MODEL="gemini-test-override",
    )
    assert settings.ai_model == "gemini-test-override"


def test_gemini_api_key_is_optional_and_accepts_environment_name():
    assert Settings(_env_file=None).ai_api_key == ""
    settings = Settings(_env_file=None, AI_API_KEY="gemini-test-key")
    assert settings.ai_api_key == "gemini-test-key"


def test_ai_service_uses_native_gemini_generate_content_protocol(monkeypatch):
    from app.services import ai_service

    monkeypatch.setattr(ai_service.settings, "ai_api_key", "gemini-test-key")
    monkeypatch.setattr(ai_service.settings, "ai_model", "gemini-3.5-flash-lite")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "Hello there."}]}}
                ]
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(ai_service.httpx, "Client", Client)
    service = AIService(db=None, user_id=1)
    reply = service._request_ai(
        [
            {"role": "system", "content": "Act naturally."},
            {"role": "user", "content": "Hi."},
            {"role": "assistant", "content": "Hello."},
            {"role": "user", "content": "How are you?"},
        ],
        temperature=0.25,
    )

    assert reply == "Hello there."
    assert captured["url"].endswith(
        "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    )
    assert captured["headers"] == {
        "x-goog-api-key": "gemini-test-key",
        "Content-Type": "application/json",
    }
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == (
        "Act naturally."
    )
    assert [item["role"] for item in captured["json"]["contents"]] == [
        "user",
        "model",
        "user",
    ]


def test_ai_chat_surfaces_gemini_provider_failures(monkeypatch):
    from app.services import ai_service

    monkeypatch.setattr(ai_service.settings, "ai_api_key", "gemini-test-key")
    monkeypatch.setattr(ai_service.settings, "ai_model", "gemini-3.5-flash-lite")
    request = httpx.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/test",
    )
    response = httpx.Response(404, request=request)

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            raise httpx.HTTPStatusError(
                "provider not found",
                request=request,
                response=response,
            )

    monkeypatch.setattr(ai_service.httpx, "Client", Client)
    service = AIService(db=None, user_id=1)
    monkeypatch.setattr(service, "verified_context", lambda _days: {})

    with pytest.raises(HTTPException) as error:
        service.chat([{"role": "user", "content": "Hello"}])

    assert error.value.status_code == 502
    assert error.value.detail == "Gemini request failed (HTTP 404)."
