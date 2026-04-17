"""Gemini AI helpers for custom note generation."""

from __future__ import annotations

import random
import re

import streamlit as st

from src import templates as templates_module

DEFAULT_GEMINI_MODEL_CANDIDATES = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
)
GEMINI_TEMPERATURE = 0.8
GEMINI_TOP_P = 0.95
GEMINI_TOP_K = 40
GEMINI_MAX_OUTPUT_TOKENS = 120
GEMINI_STOP_SEQUENCES = ["\n"]
GEMINI_GENERATION_CONFIG = {
    "temperature": GEMINI_TEMPERATURE,
    "top_p": GEMINI_TOP_P,
    "top_k": GEMINI_TOP_K,
    "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
    "stop_sequences": GEMINI_STOP_SEQUENCES,
}
GEMINI_NOTE_FOCI = tuple(getattr(templates_module, "GEMINI_NOTE_FOCI", ("architecture", "nature", "vibe")))
GEMINI_NOTE_PROMPT_TEMPLATE = str(
    getattr(
        templates_module,
        "GEMINI_NOTE_PROMPT_TEMPLATE",
        "Write exactly one short compliment sentence in English (max 15 words). "
        "Focus on the {focus} of {company} in {city}. "
        "Use high variety in sentence structures and phrasing. "
        "Avoid repetitive openings and never use patterns like 'Your setting in...'. "
        "Do not mention the same angle twice across different outputs.",
    )
)


def generate_custom_note(
    company: str,
    city: str,
    gemini_api_key: str,
    use_ai_personalization: bool,
) -> str:
    note, _, _ = generate_custom_note_with_diagnostics(
        company=company,
        city=city,
        gemini_api_key=gemini_api_key,
        use_ai_personalization=use_ai_personalization,
    )
    return note


def generate_custom_note_with_diagnostics(
    company: str,
    city: str,
    gemini_api_key: str,
    use_ai_personalization: bool,
) -> tuple[str, str, str]:
    fallback = f"Your location and concept in {city} make {company} stand out in a memorable way."
    if not use_ai_personalization or not gemini_api_key:
        reason = "ai-disabled" if not use_ai_personalization else "missing-api-key"
        return fallback, "fallback", reason

    focus = random.choice(GEMINI_NOTE_FOCI) if GEMINI_NOTE_FOCI else "vibe"
    prompt = GEMINI_NOTE_PROMPT_TEMPLATE.format(target_company=company, target_city=city, focus=focus)
    prompt += (
        " Return ONLY the sentence itself. Do not use bullet points, numbering (1., 2.), "
        "or introductory phrases. Ensure the sentence is grammatically complete and ends with a period. "
        "Write exactly one full sentence. Do not start a second one."
    )

    def _normalize_note(text: str) -> str:
        cleaned = (text or "").strip()
        cleaned = re.sub(r"^\s*(?:[-*]\s+|\d+[.)]\s+)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
        first_sentence = parts[0].strip() if parts else ""
        if first_sentence and first_sentence[-1] in "!?":
            first_sentence = first_sentence[:-1].rstrip() + "."
        return first_sentence

    try:
        from google import genai  # type: ignore[reportMissingImports]
        from google.genai import types  # type: ignore[reportMissingImports]

        client = genai.Client(api_key=gemini_api_key)
        discovered_models: list[str] = []
        list_models_error = ""
        try:
            for listed_model in client.models.list():
                raw_name = str(getattr(listed_model, "name", "")).strip()
                if not raw_name:
                    continue
                normalized = raw_name.replace("models/", "", 1)
                if "gemini" in normalized.lower():
                    discovered_models.append(normalized)
        except Exception as exc:
            list_models_error = f"list-models-failed:{type(exc).__name__}"

        candidate_models: list[str] = []
        candidate_models.extend(model for model in discovered_models if model)
        candidate_models.extend(DEFAULT_GEMINI_MODEL_CANDIDATES)

        deduped_candidates: list[str] = []
        seen: set[str] = set()
        for model_name in candidate_models:
            key = str(model_name).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_candidates.append(str(model_name).strip())

        errors: list[str] = []
        for model_name in deduped_candidates:
            for attempt in range(2):
                try:
                    attempt_prompt = prompt
                    if attempt == 1:
                        attempt_prompt += (
                            " Your previous answer was incomplete. Return one complete sentence ending with a period."
                        )

                    response = client.models.generate_content(
                        model=model_name,
                        contents=attempt_prompt,
                        config=types.GenerateContentConfig(
                            temperature=GEMINI_TEMPERATURE,
                            top_p=GEMINI_TOP_P,
                            top_k=GEMINI_TOP_K,
                            max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                            stop_sequences=GEMINI_STOP_SEQUENCES,
                        ),
                    )
                    text = _normalize_note((getattr(response, "text", "") or ""))
                    if text and text.endswith("."):
                        st.session_state["gemini_last_model_diagnostics"] = {
                            "discovered_models": discovered_models,
                            "candidate_models": deduped_candidates,
                        }
                        return text, "ai", f"model:{model_name}"
                    errors.append(f"{model_name}: incomplete-sentence")
                except Exception as exc:
                    errors.append(f"{model_name}: {type(exc).__name__}")
                    break

        reason = "all-models-failed"
        if list_models_error:
            reason = f"{reason} ({list_models_error})"
        if errors:
            reason = f"{reason} ({'; '.join(errors[:3])})"
        st.session_state["gemini_last_model_diagnostics"] = {
            "discovered_models": discovered_models,
            "candidate_models": deduped_candidates,
            "errors": errors[:5],
            "list_models_error": list_models_error,
        }
        return fallback, "fallback", reason
    except Exception as exc:
        message = str(exc).strip()
        lowered = message.lower()
        if "api key" in lowered or "permission" in lowered or "unauthorized" in lowered:
            reason = f"invalid-api-key-or-permission: {message or type(exc).__name__}"
        else:
            reason = f"{type(exc).__name__}: {message or 'unknown-error'}"
        return fallback, "fallback", reason
