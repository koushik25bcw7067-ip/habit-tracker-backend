import json

import pytest

from app.services import theme_service as ts
from app.services.theme_presets import PRESETS as RAW


def test_all_presets_validate_and_are_distinct():
    assert len(ts.PRESETS) >= 7
    seen = set()
    for key, p in ts.PRESETS.items():
        assert p["schema_version"] == 2
        assert set(ts.LINE_KEYS) <= set(p["lines"])
        # legacy v1 fields still present for old clients
        assert p["accent_color"] == p["colors"]["primary"] and p["font"] == p["fonts"]["display"]
        assert p["mascot_name"] and p["mascot_emoji"] and isinstance(p["dialogues"], list) and p["dialogues"]
        sig = (p["colors"]["primary"], p["colors"]["background"], p["fonts"]["display"], p["mascot"]["accessory"])
        assert sig not in seen, f"{key} duplicates another preset"
        seen.add(sig)


def test_required_worlds_exist():
    for k in ("mahishmati", "grimoire", "minimal", "cloud", "coding", "motivation", "classic"):
        assert k in ts.PRESETS
    assert ts.PRESETS["mahishmati"]["vocab"]["add_task_prompt"] == "What mission shall we conquer today?"
    assert ts.PRESETS["grimoire"]["vocab"]["add_task_prompt"] == "Write your next mission in the grimoire."


def test_sanitize_blocks_injection_unknown_fonts_and_urls():
    raw = json.loads(json.dumps(RAW["classic"]))
    raw["fonts"] = {"display": "x; background:url(//evil)", "body": "Comic Sans"}
    raw["mascot"]["name"] = "<script>alert(1)</script>"
    raw["mascot"]["accessory"] = "laser"
    raw["effects"]["texture"] = "url(http://x)"
    raw["lines"]["idle"] = ["visit http://evil.com now", "<b>ok</b> {bogus} fine {name}", "x" * 400]
    raw["vocab"]["habit"] = "javascript:alert(1)"
    t = ts.sanitize_theme(raw)
    assert t["fonts"] == {"display": "Plus Jakarta Sans", "body": "Inter"}
    assert t["mascot"]["accessory"] == "cap" and "<" not in t["mascot"]["name"]
    assert t["effects"]["texture"] == "none"
    assert t["vocab"]["habit"] == "Routine"
    assert "javascript:" not in t["vocab"]["habit"]
    assert all("http" not in l and "<" not in l and "{bogus}" not in l and len(l) <= ts.MAX_LINE_LEN for l in t["lines"]["idle"])
    assert any("{name}" in l for l in t["lines"]["idle"])


def test_sanitize_rejects_bad_colours():
    raw = json.loads(json.dumps(RAW["classic"]))
    raw["colors"]["primary"] = "red"
    with pytest.raises(ValueError):
        ts.sanitize_theme(raw)


def test_legacy_v1_theme_upgrades():
    legacy = {"accent_color": "#8b5cf6", "font": "Poppins", "mascot_name": "Moony", "mascot_emoji": "🌙", "dialogues": ["hi", "there"]}
    t = ts.sanitize_theme(legacy)
    assert t["colors"]["primary"] == "#8b5cf6" and t["fonts"]["display"] == "Poppins" and t["mascot"]["name"] == "Moony"
    assert t["lines"]["idle"] == ["hi", "there"]


def test_fallback_deterministic_valid_and_public_shape():
    a, b = ts.fallback_theme("A peaceful celestial world"), ts.fallback_theme("a peaceful celestial world ")
    assert a["colors"] == b["colors"] and a["source"] == "fallback"
    assert a["mascot"]["name"] == "Mira" and a["mascot"]["accessory"] == "cap"
    ts.sanitize_theme(a)
    assert ts.fallback_theme("Matrix")["colors"] != a["colors"]


def test_attack_on_titan_customization_uses_a_specific_curated_world(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "configured")
    ts._cache.clear()

    theme = ts.generate_custom("Attack on Titan")

    assert theme["source"] == "curated"
    assert theme["label"] == "Attack on Titan"
    assert theme["mode"] == "dark"
    assert theme["colors"]["background"] == "#191d1b"
    assert theme["fonts"]["display"] == "Oswald"
    assert theme["vocab"]["habit"] == "Routine"
    assert "routine" in theme["lines"]["habit_created"][0].lower()
    assert ts.generate_custom("Attack of Titan")["colors"] == theme["colors"]
    assert ts.generate_custom("Shingeki no Kyojin")["colors"] == theme["colors"]


def test_mentalist_request_gets_a_specific_curated_mystery_theme(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "configured")
    ts._cache.clear()

    theme = ts.generate_custom("The Mentalist")

    assert theme["source"] == "curated"
    assert theme["label"] == "The Mentalist"
    assert theme["mode"] == "dark"
    assert theme["colors"]["primary"] == "#a92e45"
    assert theme["colors"]["background"] == "#191719"
    assert theme["fonts"]["display"] == "Playfair Display"
    assert theme["mascot"]["accessory"] == "glasses"
    assert "investigative-mystery" in theme["description"]
    assert ts.generate_custom("The Mentalist series")["colors"] == theme["colors"]


def test_doraemon_request_uses_blue_white_red_palette_even_without_ai(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "")
    ts._cache.clear()

    theme = ts.generate_custom("Doraemon")

    assert theme["source"] == "curated"
    assert theme["label"] == "Doraemon-inspired"
    assert theme["mode"] == "light"
    assert theme["colors"]["primary"] == "#1687d4"
    assert theme["colors"]["secondary"] == "#e63946"
    assert theme["colors"]["background"] == "#f5faff"
    assert theme["fonts"]["display"] == "Fredoka"
    assert theme["effects"]["radius"] == "round"
    assert "green" not in theme["description"].lower()


def test_doraemon_theme_keeps_explicit_user_palette_preferences(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "")
    ts._cache.clear()

    theme = ts.generate_custom("Doraemon but make it a purple dark theme")

    assert theme["source"] == "curated"
    assert theme["mode"] == "dark"
    assert theme["colors"]["primary"] == ts._hsl_to_hex(275, 64, 57)
    assert theme["colors"]["background"] == "#191719"


def test_theme_generation_preserves_explicit_color_and_mode_preferences(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "configured")
    ts._cache.clear()

    theme = ts.generate_custom("The Mentalist with a purple palette in light mode")

    assert theme["source"] == "curated"
    assert theme["mode"] == "light"
    assert theme["colors"]["primary"] == ts._hsl_to_hex(275, 64, 44)
    assert theme["mascot"]["outfit"] == theme["colors"]["primary"]
    assert theme["colors"]["background"] == "#f8f6f3"
    assert theme["colors"]["text"] == "#211b1b"


def test_ai_theme_keeps_explicit_color_and_mode_preferences(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "k")
    ts._cache.clear()
    ai = json.loads(json.dumps(RAW["mahishmati"]))
    monkeypatch.setattr(ts.httpx, "Client", _fake_client(json.dumps(ai)))

    theme = ts.generate_custom("A forest world with a purple palette in light mode")

    assert theme["source"] == "ai"
    assert theme["mode"] == "light"
    assert theme["colors"]["primary"] == ts._hsl_to_hex(275, 64, 44)
    assert theme["colors"]["background"] == "#f8f6f3"


def test_theme_wording_uses_routines_without_changing_private_data_keys():
    raw = json.loads(json.dumps(RAW["classic"]))
    raw["vocab"]["habit"] = "Spell"
    raw["vocab"]["habits"] = "Spells"
    raw["lines"]["habit_done"] = ["Habit done!"]

    theme = ts.sanitize_theme(raw)

    assert theme["vocab"]["habit"] == "Routine"
    assert theme["vocab"]["habits"] == "Routines"
    assert theme["lines"]["habit_done"] == ["Routine done!"]


def test_generate_without_key_uses_uncached_fallback(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "")
    ts._cache.clear()
    first = ts.generate_custom("Interstellar")
    assert first["source"] == "fallback" and first["accent_color"]
    assert ts.generate_custom("interstellar")["colors"] == first["colors"]
    assert "interstellar" not in ts._cache


def _fake_client(content):
    class R:
        def raise_for_status(self): ...
        def json(self): return {"choices": [{"message": {"content": content}}]}
    class C:
        def __enter__(self): return self
        def __exit__(self, *a): ...
        def post(self, *a, **k): return R()
    return lambda **k: C()


def test_generate_ai_success_is_sanitized(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "k")
    ts._cache.clear()
    ai = json.loads(json.dumps(RAW["mahishmati"]))
    ai["lines"]["task_done"] = ["<i>Victory</i> {task}!"]
    monkeypatch.setattr(ts.httpx, "Client", _fake_client("```json\n" + json.dumps(ai) + "\n```"))
    t = ts.generate_custom("epic kingdom")
    assert t["source"] == "ai" and t["fonts"]["display"] == "Cinzel"
    assert "<" not in t["lines"]["task_done"][0]


def test_generate_invalid_ai_output_falls_back(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "k")
    ts._cache.clear()
    monkeypatch.setattr(ts.httpx, "Client", _fake_client("sorry, no json"))
    assert ts.generate_custom("Naruto")["source"] == "fallback"
    ts._cache.clear()
    monkeypatch.setattr(ts.httpx, "Client", _fake_client(json.dumps({"colors": {"primary": "nope"}})))
    assert ts.generate_custom("Zelda")["source"] == "fallback"


def test_generate_provider_error_falls_back(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "k")
    ts._cache.clear()
    class Boom:
        def __enter__(self): raise RuntimeError("down")
        def __exit__(self, *a): ...
    monkeypatch.setattr(ts.httpx, "Client", lambda **k: Boom())
    assert ts.generate_custom("Naruto")["source"] == "fallback"


def test_custom_world_prompt_preserves_arbitrary_reference_and_preferences():
    prompt = ts._build_prompt("A cozy Ghibli-inspired seaside town in teal, light mode")

    assert "A cozy Ghibli-inspired seaside town in teal, light mode" in prompt
    assert "cartoon, anime, movie, series, show, game, book" in ts.SYSTEM_PROMPT
    assert "do not replace it with an unrelated generic theme" in ts.SYSTEM_PROMPT
    assert "Preserve every explicit user preference" in prompt
    assert "the named world/reference from the request" in prompt


def test_provider_fallback_is_not_cached_and_a_retry_regenerates(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "configured")
    ts._cache.clear()

    class Boom:
        def __enter__(self):
            raise RuntimeError("provider unavailable")

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(ts.httpx, "Client", lambda **_kwargs: Boom())
    first = ts.generate_custom("A cozy Ghibli seaside town")
    assert first["source"] == "fallback"
    assert "a cozy ghibli seaside town" not in ts._cache

    ai = json.loads(json.dumps(RAW["cloud"]))
    monkeypatch.setattr(ts.httpx, "Client", _fake_client(json.dumps(ai)))
    retry = ts.generate_custom("A cozy Ghibli seaside town")
    assert retry["source"] == "ai"
    assert retry["label"] == ai["label"]
    assert "a cozy ghibli seaside town" in ts._cache


def test_persona_prompt_mentions_world_but_keeps_facts_rule():
    p = ts.persona_for_prompt(ts.PRESETS["grimoire"])
    assert "Wisp" in p and "never facts" in p
    assert ts.persona_for_prompt(None) == ""
