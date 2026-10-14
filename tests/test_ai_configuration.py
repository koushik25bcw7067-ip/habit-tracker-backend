from app.config import Settings


def test_gemini_defaults_do_not_use_placeholder_credentials():
    assert Settings.model_fields["ai_api_key"].default == ""
    assert Settings.model_fields["ai_base_url"].default == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert Settings.model_fields["ai_model"].default == "gemini-3.8-flash"


def test_ai_api_url_is_accepted_as_legacy_base_url_alias():
    settings = Settings(
        _env_file=None,
        AI_API_URL="https://example.test/v1/chat/completions",
    )
    assert settings.ai_base_url == "https://example.test/v1/chat/completions"
