"""Unit tests for core logic and global registry behavior."""

from __future__ import annotations

import pandas as pd

from src.core_logic import (
    append_sent_email_registry,
    apply_strict_discovery_dedup,
    is_valid_email,
    load_sent_email_registry,
    save_sent_email_registry,
)


def test_registry_save_load_roundtrip(tmp_path) -> None:
    registry_file = tmp_path / "sent_emails_registry.json"
    save_sent_email_registry({"A@EXAMPLE.COM", "b@example.com"}, registry_file)

    loaded = load_sent_email_registry(registry_file)
    assert loaded == {"a@example.com", "b@example.com"}


def test_registry_append_adds_only_new(tmp_path) -> None:
    registry_file = tmp_path / "sent_emails_registry.json"
    save_sent_email_registry({"first@example.com"}, registry_file)

    added = append_sent_email_registry(
        ["first@example.com", "SECOND@example.com", "second@example.com"],
        registry_file,
    )

    assert added == 1
    assert load_sent_email_registry(registry_file) == {"first@example.com", "second@example.com"}


def test_email_cleaning_rejects_trash_patterns() -> None:
    blacklist = (
        "sentry.io",
        "preact@10.29.1.js",
        "example.com",
    )

    assert not is_valid_email("logger@sentry.io", blacklist)
    assert not is_valid_email("preact@10.29.1.js", blacklist)
    assert not is_valid_email("test@example.com", blacklist)
    assert is_valid_email("jobs@realhotel.no", blacklist)


def test_dedup_keeps_unique_email_and_domain() -> None:
    df = pd.DataFrame(
        [
            {
                "target_company": "Alpha Hotels AS",
                "website": "https://alpha.hotel.no",
                "email": "contact@hotel.no",
                "target_city": "Oslo",
                "custom_note": "note a",
            },
            {
                "target_company": "Beta Guesthouse by Chain",
                "website": "https://beta.hotel.no",
                "email": "jobs@hotel.no",
                "target_city": "Oslo",
                "custom_note": "note b",
            },
            {
                "target_company": "Gamma Inn",
                "website": "https://gamma.example.org",
                "email": "contact@gamma.org",
                "target_city": "Bergen",
                "custom_note": "note c",
            },
            {
                "target_company": "Gamma Inn",
                "website": "https://gamma.example.org",
                "email": "contact@gamma.org",
                "target_city": "Bergen",
                "custom_note": "note d",
            },
        ]
    )

    result = apply_strict_discovery_dedup(df)

    # one from hotel.no domain and one from gamma.org domain
    assert len(result) == 2
    assert set(result["email"].tolist()) == {"contact@hotel.no", "contact@gamma.org"}
