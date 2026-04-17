from __future__ import annotations

import sys
import types

import app as app_module


def install_fake_genai(monkeypatch, *, text: str | None = None, error: Exception | None = None):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, value: str | None) -> None:
            self.text = value

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FakeModels:
        def list(self):
            return []

        def generate_content(self, *, model: str, contents: str, config: object):
            captured["model_name"] = model
            captured["prompt"] = contents
            captured["generation_config"] = config
            if error is not None:
                raise error
            return FakeResponse(text)

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key
            self.models = FakeModels()

    fake_types_module = types.ModuleType("google.genai.types")
    fake_types_module.GenerateContentConfig = FakeGenerateContentConfig  # type: ignore[attr-defined]

    fake_genai_module = types.ModuleType("google.genai")
    fake_genai_module.Client = FakeClient  # type: ignore[attr-defined]
    fake_genai_module.types = fake_types_module  # type: ignore[attr-defined]

    fake_google_module = types.ModuleType("google")
    fake_google_module.genai = fake_genai_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", fake_google_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)
    return captured


def test_generate_custom_note_uses_ai_output_when_available(monkeypatch) -> None:
    captured = install_fake_genai(monkeypatch, text="The mountain view feels wonderfully calm.")

    note = app_module.generate_custom_note(
        company="Aurora Hotel",
        city="Bergen",
        gemini_api_key="test-key",
        use_ai_personalization=True,
    )

    assert note == "The mountain view feels wonderfully calm."
    assert captured["api_key"] == "test-key"
    assert captured["model_name"] in app_module.DEFAULT_GEMINI_MODEL_CANDIDATES
    assert "Aurora Hotel" in str(captured["prompt"])
    assert "Bergen" in str(captured["prompt"])
    assert getattr(captured["generation_config"], "temperature", None) == 0.8
    assert getattr(captured["generation_config"], "top_p", None) == 0.95
    assert getattr(captured["generation_config"], "top_k", None) == 40
    assert getattr(captured["generation_config"], "max_output_tokens", None) == 120


def test_generate_custom_note_uses_fallback_when_disabled() -> None:
    note = app_module.generate_custom_note(
        company="Aurora Hotel",
        city="Bergen",
        gemini_api_key="test-key",
        use_ai_personalization=False,
    )

    assert note == "Your location and concept in Bergen make Aurora Hotel stand out in a memorable way."


def test_generate_custom_note_uses_fallback_when_ai_fails(monkeypatch) -> None:
    install_fake_genai(monkeypatch, error=RuntimeError("boom"))

    note = app_module.generate_custom_note(
        company="Aurora Hotel",
        city="Bergen",
        gemini_api_key="test-key",
        use_ai_personalization=True,
    )

    assert note == "Your location and concept in Bergen make Aurora Hotel stand out in a memorable way."


def test_build_discovery_dataframe_calls_gemini_once_per_company(monkeypatch) -> None:
    call_args: list[tuple[str, str]] = []

    def fake_places_text_search(query: str, maps_api_key: str, max_results: int):
        return [
            {"place_id": "place-1", "name": "Steien", "formatted_address": ""},
            {"place_id": "place-2", "name": "Fjell View", "formatted_address": ""},
        ]

    def fake_fetch_place_details(place_id: str, maps_api_key: str):
        if place_id == "place-1":
            return {"name": "Steien", "website": "https://steien.example", "address_components": []}
        return {"name": "Fjell View", "website": "https://fjell.example", "address_components": []}

    def fake_extract_emails_from_website(url: str, blacklist: tuple[str, ...]):
        if "steien" in url:
            return ["contact@steien.example"]
        return ["contact@fjell.example"]

    def fake_generate_custom_note_with_diagnostics(
        company: str,
        city: str,
        gemini_api_key: str,
        use_ai_personalization: bool,
    ):
        call_args.append((company, city))
        return f"note for {company} in {city}", "ai", "ok"

    monkeypatch.setattr(app_module, "places_text_search", fake_places_text_search)
    monkeypatch.setattr(app_module, "fetch_place_details", fake_fetch_place_details)
    monkeypatch.setattr(app_module, "extract_emails_from_website", fake_extract_emails_from_website)
    monkeypatch.setattr(
        app_module,
        "generate_custom_note_with_diagnostics",
        fake_generate_custom_note_with_diagnostics,
    )
    monkeypatch.setattr(app_module, "is_valid_email", lambda email, blacklist: True)
    monkeypatch.setattr(app_module, "apply_strict_discovery_dedup", lambda df: df)
    monkeypatch.setattr(app_module.st, "progress", lambda value: types.SimpleNamespace(progress=lambda _: None))
    monkeypatch.setattr(app_module.st, "empty", lambda: types.SimpleNamespace(text=lambda _: None))

    df = app_module.build_discovery_dataframe(
        city="Bergen",
        business_type="Hotels",
        country="Norway",
        query_template="{business_type} in {city}, {country}",
        maps_api_key="test-key",
        gemini_api_key="gemini-key",
        blacklist=(),
        blocked_emails=set(),
        max_results=2,
        use_ai_personalization=True,
    )

    assert len(df) == 2
    assert call_args == [("Steien", "Bergen"), ("Fjell View", "Bergen")]
    assert df["custom_note"].tolist() == ["note for Steien in Bergen", "note for Fjell View in Bergen"]