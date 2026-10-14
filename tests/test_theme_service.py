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
    assert t["mascot"]["accessory"] == "none" and "<" not in t["mascot"]["name"]
    assert t["effects"]["texture"] == "none"
    assert "habit" not in t["vocab"]
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
    ts.sanitize_theme(a)
    assert ts.fallback_theme("Matrix")["colors"] != a["colors"]


def test_generate_without_key_uses_fallback_and_caches(monkeypatch):
    monkeypatch.setattr(ts.settings, "ai_api_key", "")
    ts._cache.clear()
    first = ts.generate_custom("Interstellar")
    assert first["source"] == "fallback" and first["accent_color"]
    assert ts.generate_custom("interstellar") == first


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


def test_persona_prompt_mentions_world_but_keeps_facts_rule():
    p = ts.persona_for_prompt(ts.PRESETS["grimoire"])
    assert "Wisp" in p and "never facts" in p
    assert ts.persona_for_prompt(None) == ""
