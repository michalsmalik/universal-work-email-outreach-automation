"""Discovery and scraping business logic for the outreach workflow."""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests import RequestException

from src.ai_handler import GEMINI_GENERATION_CONFIG, generate_custom_note_with_diagnostics
from src import templates as templates_module
from src.core_logic import apply_strict_discovery_dedup, clean_company_name, is_valid_email

REQUEST_TIMEOUT = int(getattr(templates_module, "REQUEST_TIMEOUT", 12))
MAX_SUBPAGE_CHECKS = int(getattr(templates_module, "MAX_SUBPAGE_CHECKS", 20))
GOOGLE_PLACES_TEXT_SEARCH_URL = str(
    getattr(
        templates_module,
        "GOOGLE_PLACES_TEXT_SEARCH_URL",
        "https://maps.googleapis.com/maps/api/place/textsearch/json",
    )
)
GOOGLE_PLACE_DETAILS_URL = str(
    getattr(
        templates_module,
        "GOOGLE_PLACE_DETAILS_URL",
        "https://maps.googleapis.com/maps/api/place/details/json",
    )
)
DEFAULT_HTTP_USER_AGENT = str(
    getattr(
        templates_module,
        "DEFAULT_HTTP_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
)
CONTACT_LINK_KEYWORDS = tuple(
    getattr(
        templates_module,
        "CONTACT_LINK_KEYWORDS",
        (
            "contact",
            "about",
            "team",
            "company",
            "imprint",
            "booking",
            "reservation",
            "kontakt",
            "reservasjon",
        ),
    )
)
DISCOVERY_CATEGORY_TERMS = list(
    getattr(
        templates_module,
        "DISCOVERY_CATEGORY_TERMS",
        [
            "Hotels",
            "Guesthouses",
            "Pensions",
            "Inns",
            "Resorts",
            "Motels",
            "Hostels",
            "Lodges",
            "Bed and Breakfast",
            "Accommodation",
        ],
    )
)
DISCOVERY_AREA_MODIFIERS = list(
    getattr(
        templates_module,
        "DISCOVERY_AREA_MODIFIERS",
        [
            "city center",
            "downtown",
            "old town",
            "near airport",
            "near train station",
            "waterfront",
            "mountain",
            "budget",
            "family",
            "boutique",
        ],
    )
)
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ACCOMMODATION_TYPE_TERMS: dict[str, list[str]] = {
    "Hotels": ["hotel"],
    "Camping": ["camping", "campingplass"],
    "Fjellstue": ["fjellstue"],
    "Gjestegård": ["gjestegård"],
    "Rorbu": ["rorbu"],
    "Hostels": ["hostel"],
    "Resorts": ["resort"],
}
QUOTED_NORWEGIAN_TERMS = {"fjellstue", "gjestegård", "rorbu", "campingplass"}


def _collect_accommodation_terms(accommodation_types: list[str], fallback_term: str) -> list[str]:
    terms: list[str] = []
    for item in accommodation_types:
        normalized = str(item).strip()
        if not normalized:
            continue
        candidates = ACCOMMODATION_TYPE_TERMS.get(normalized, [normalized.lower()])
        for term in candidates:
            cleaned = str(term).strip().lower()
            if cleaned and cleaned not in terms:
                terms.append(cleaned)

    if terms:
        return terms

    fallback = (fallback_term or "").strip().lower() or "hotel"
    return [fallback]


def _format_query_term(term: str) -> str:
    if term in QUOTED_NORWEGIAN_TERMS:
        return f'"{term}"'
    return term


def normalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        return f"https://{value}"
    return value


def fetch_page(url: str) -> str | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": DEFAULT_HTTP_USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.text
    except RequestException:
        return None


def find_contact_links(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        text = anchor.get_text(" ", strip=True).lower()
        lowered_href = href.lower()

        if any(keyword in lowered_href or keyword in text for keyword in CONTACT_LINK_KEYWORDS):
            full_url = urljoin(base_url, href)
            if full_url.startswith(("http://", "https://")) and full_url not in links:
                links.append(full_url)

    return links


def extract_emails_from_website(url: str, blacklist: tuple[str, ...]) -> list[str]:
    normalized_url = normalize_url(url)
    if not normalized_url:
        return []

    queue = [normalized_url]
    visited: set[str] = set()
    found: set[str] = set()
    checks = 0

    while queue and checks < MAX_SUBPAGE_CHECKS:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        checks += 1

        html = fetch_page(current)
        if html is None:
            continue

        candidates = {match.lower() for match in EMAIL_PATTERN.findall(html)}
        found.update(candidate for candidate in candidates if is_valid_email(candidate, blacklist))
        if found:
            return sorted(found)

        for link in find_contact_links(current, html):
            if link not in visited and link not in queue:
                queue.append(link)

    return sorted(found)


def parse_city_from_components(address_components: list[dict[str, Any]], fallback_city: str) -> str:
    ordered_types = (
        "locality",
        "postal_town",
        "administrative_area_level_2",
        "administrative_area_level_1",
    )
    for city_type in ordered_types:
        for component in address_components:
            types = component.get("types", [])
            if city_type in types:
                city = str(component.get("long_name", "")).strip()
                if city:
                    return city
    return fallback_city.strip()


def fetch_place_details(place_id: str, maps_api_key: str) -> dict[str, Any]:
    params = {
        "key": maps_api_key,
        "place_id": place_id,
        "fields": "name,website,address_components",
    }
    try:
        response = requests.get(GOOGLE_PLACE_DETAILS_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except RequestException:
        return {"name": "", "website": "", "address_components": []}

    if payload.get("status") != "OK":
        return {"name": "", "website": "", "address_components": []}

    result = payload.get("result", {})
    components = result.get("address_components", [])
    if not isinstance(components, list):
        components = []

    return {
        "name": str(result.get("name", "")).strip(),
        "website": str(result.get("website", "")).strip(),
        "address_components": components,
    }


def places_text_search(query: str, maps_api_key: str, max_results: int) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    next_page_token: str | None = None

    while len(candidates) < max_results:
        if next_page_token:
            payload: dict[str, Any] = {}
            for attempt in range(6):
                time.sleep(2 + attempt)
                response = requests.get(
                    GOOGLE_PLACES_TEXT_SEARCH_URL,
                    params={"key": maps_api_key, "pagetoken": next_page_token},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    break
                payload = data
                if payload.get("status") != "INVALID_REQUEST":
                    break
        else:
            response = requests.get(
                GOOGLE_PLACES_TEXT_SEARCH_URL,
                params={"key": maps_api_key, "query": query},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()

        status = str(payload.get("status", ""))
        if status not in {"OK", "ZERO_RESULTS"}:
            break

        for item in payload.get("results", []):
            place_id = str(item.get("place_id", "")).strip()
            name = str(item.get("name", "")).strip()
            if place_id and name:
                candidates.append(
                    {
                        "place_id": place_id,
                        "name": name,
                        "formatted_address": str(item.get("formatted_address", "")).strip(),
                    }
                )
            if len(candidates) >= max_results:
                break

        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break

    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        if item["place_id"] in seen:
            continue
        seen.add(item["place_id"])
        unique.append(item)

    return unique[:max_results]


def build_discovery_dataframe(
    city: str,
    business_type: str,
    country: str,
    query_template: str,
    maps_api_key: str,
    gemini_api_key: str,
    blacklist: tuple[str, ...],
    blocked_emails: set[str],
    max_results: int,
    use_ai_personalization: bool,
    accommodation_types: list[str] | None = None,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    diagnostics_out: dict[str, Any] | None = None,
) -> pd.DataFrame:
    selected_types = accommodation_types if accommodation_types is not None else [business_type]
    accommodation_terms = _collect_accommodation_terms(selected_types, fallback_term=business_type)
    or_clause = " OR ".join(_format_query_term(term) for term in accommodation_terms)
    location = city.strip()
    if country.strip():
        location = f"{location}, {country.strip()}"

    base_query = f"{or_clause} in {location}"
    category_terms = [*_collect_accommodation_terms(selected_types, fallback_term=business_type), *DISCOVERY_CATEGORY_TERMS]
    area_modifiers = DISCOVERY_AREA_MODIFIERS

    query_variants: list[str] = [base_query]
    for term in category_terms:
        query_variants.append(f"{_format_query_term(term)} in {location}")
    for term in category_terms:
        for modifier in area_modifiers:
            query_variants.append(f"{_format_query_term(term)} {modifier} in {location}")
    for token in "abcdefghijklmnopqrstuvwxyz":
        query_variants.append(f"{or_clause} in {location} {token}")

    deduped_queries: list[str] = []
    seen_queries: set[str] = set()
    for raw_query in query_variants:
        cleaned = raw_query.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen_queries:
            continue
        seen_queries.add(key)
        deduped_queries.append(cleaned)

    rows: list[dict[str, str]] = []
    seen_place_ids: set[str] = set()
    ai_notes = 0
    fallback_notes = 0
    fallback_reasons: dict[str, int] = {}
    ai_note_index = 0

    valid_count = 0
    per_query_limit = max(20, min(max_results * 2, 40))
    max_query_attempts = max(20, min(max_results * 3, 60))
    query_plan = deduped_queries[:max_query_attempts]

    def _update_status(message: str) -> None:
        if on_status is not None:
            on_status(message)

    def _update_progress(value: float) -> None:
        if on_progress is not None:
            on_progress(value)

    def _should_stop() -> bool:
        return bool(should_stop and should_stop())

    for query_index, search_query in enumerate(query_plan, start=1):
        if _should_stop():
            break
        if valid_count >= max_results:
            break

        _update_status(f"Searching places ({query_index}/{len(query_plan)}): {search_query}")
        try:
            candidates = places_text_search(
                query=search_query,
                maps_api_key=maps_api_key,
                max_results=per_query_limit,
            )
        except RequestException:
            continue

        for candidate in candidates:
            if _should_stop():
                break
            if valid_count >= max_results:
                break

            place_id = candidate["place_id"]
            if place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            details = fetch_place_details(place_id, maps_api_key)
            company = details["name"] or candidate["name"]
            website = (details["website"] or "").strip()
            if website and not website.startswith(("http://", "https://")):
                website = f"https://{website}"
            if not website:
                continue

            city_name = parse_city_from_components(details["address_components"], fallback_city=city)

            _update_status(f"Scanning website for emails: {company}")
            emails = extract_emails_from_website(website, blacklist)
            email = (emails[0] if emails else "").strip().lower()
            if not is_valid_email(email, blacklist):
                continue
            if email in blocked_emails:
                continue

            cleaned_company = clean_company_name(company)

            custom_note = f"Your location and concept in {city_name} make {cleaned_company or company} stand out in a memorable way."
            note_source = "fallback"
            note_reason = "ai-disabled"

            if use_ai_personalization and gemini_api_key:
                if ai_note_index > 0:
                    time.sleep(4)

                ai_note_number = ai_note_index + 1
                _update_status(
                    f"Generating AI note {ai_note_number}/{max_results} for {cleaned_company or company}..."
                )
                try:
                    custom_note, note_source, note_reason = generate_custom_note_with_diagnostics(
                        company=cleaned_company or company,
                        city=city_name,
                        gemini_api_key=gemini_api_key,
                        use_ai_personalization=use_ai_personalization,
                    )
                except Exception as exc:
                    note_source = "fallback"
                    note_reason = f"ai-exception:{type(exc).__name__}"
                    custom_note = (
                        f"Your location and concept in {city_name} make {cleaned_company or company} "
                        "stand out in a memorable way."
                    )
                ai_note_index += 1

            if note_source == "ai":
                ai_notes += 1
            else:
                fallback_notes += 1
                fallback_reasons[note_reason] = fallback_reasons.get(note_reason, 0) + 1

            rows.append(
                {
                    "target_company": cleaned_company or company,
                    "website": website,
                    "email": email,
                    "target_city": city_name,
                    "custom_note": custom_note,
                }
            )
            valid_count += 1
            _update_progress(valid_count / max(max_results, 1))

            if valid_count < max_results and valid_count % 10 == 0:
                _update_status(f"Collected {valid_count}/{max_results} valid rows so far...")

        if _should_stop():
            break

    if _should_stop():
        _update_status(f"Discovery stopped. Collected {valid_count}/{max_results} valid rows.")
    elif valid_count < max_results:
        _update_status(f"Search exhausted. Collected {valid_count}/{max_results} valid rows.")

    diagnostics = {
        "ai_enabled": bool(use_ai_personalization),
        "api_key_present": bool(gemini_api_key.strip()),
        "generation_config": dict(GEMINI_GENERATION_CONFIG),
        "ai_notes": ai_notes,
        "fallback_notes": fallback_notes,
        "fallback_reasons": fallback_reasons,
    }
    if diagnostics_out is not None:
        diagnostics_out.update(diagnostics)

    return apply_strict_discovery_dedup(pd.DataFrame(rows))
