"""Core business logic for registry, email hygiene, and deduplication.

This module contains pure functions used across UI and tests to keep the
application maintainable, testable, and production-ready.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from .templates import (
    COMPANY_BY_CHAIN_PATTERN,
    COMPANY_CLEANUP_TERMS,
    SENT_EMAILS_REGISTRY_FILE,
)

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalize_email(value: str) -> str:
    """Normalize an email value for stable comparisons.

    Args:
        value: Raw email string.

    Returns:
        The normalized email in lowercase with surrounding whitespace removed.
    """
    return (value or "").strip().lower()


def is_valid_email(email: str, blacklist: tuple[str, ...]) -> bool:
    """Validate an extracted email against syntax and blacklist rules.

    Args:
        email: Candidate email string.
        blacklist: Blocked substrings and patterns.

    Returns:
        True when email is valid and not blocked; otherwise False.
    """
    normalized = normalize_email(email)
    if not normalized or not EMAIL_PATTERN.fullmatch(normalized):
        return False
    if any(pattern in normalized for pattern in blacklist):
        return False

    local_part, _, domain = normalized.partition("@")
    if not local_part or not domain:
        return False
    if local_part in {"noreply", "no-reply", "donotreply", "do-not-reply"}:
        return False
    if domain.endswith((".js", ".png", ".jpg", ".jpeg", ".webp", ".svg")):
        return False
    return True


def load_sent_email_registry(registry_path: str | Path = SENT_EMAILS_REGISTRY_FILE) -> set[str]:
    """Load the persistent global sent-email registry.

    Args:
        registry_path: Path to the registry JSON file.

    Returns:
        Set of normalized email addresses.
    """
    path = Path(registry_path)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
        return set()

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        path.write_text("[]", encoding="utf-8")
        return set()

    if not isinstance(loaded, list):
        path.write_text("[]", encoding="utf-8")
        return set()

    return {normalize_email(item) for item in loaded if isinstance(item, str) and normalize_email(item)}


def save_sent_email_registry(
    emails: set[str],
    registry_path: str | Path = SENT_EMAILS_REGISTRY_FILE,
) -> None:
    """Persist a normalized sent-email registry to disk.

    Args:
        emails: Set of email addresses to persist.
        registry_path: Path to the registry JSON file.
    """
    normalized = sorted({normalize_email(email) for email in emails if normalize_email(email)})
    Path(registry_path).write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def append_sent_email_registry(
    new_emails: list[str],
    registry_path: str | Path = SENT_EMAILS_REGISTRY_FILE,
) -> int:
    """Append new emails to the global registry and return count of additions.

    Args:
        new_emails: Newly exported emails.
        registry_path: Path to the registry JSON file.

    Returns:
        Number of unique new emails added.
    """
    existing = load_sent_email_registry(registry_path)
    before_count = len(existing)
    existing.update(normalize_email(email) for email in new_emails if normalize_email(email))
    save_sent_email_registry(existing, registry_path)
    return len(existing) - before_count


def clean_company_name(
    value: str,
    cleanup_terms: tuple[str, ...] = COMPANY_CLEANUP_TERMS,
    by_chain_pattern: str = COMPANY_BY_CHAIN_PATTERN,
) -> str:
    """Normalize company names for stable comparison and deduplication.

    Args:
        value: Raw company name.
        cleanup_terms: Regex terms to remove.
        by_chain_pattern: Regex pattern for trailing chain branding.

    Returns:
        Cleaned company name.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return ""

    for pattern in cleanup_terms:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(by_chain_pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*[-,]\s*$", "", cleaned)
    return cleaned.strip() or value.strip()


def extract_root_domain(website: str) -> str:
    """Extract root domain from URL for branch-level deduplication.

    Args:
        website: Website URL.

    Returns:
        Root domain when possible, otherwise empty string.
    """
    hostname = (urlparse(website).hostname or "").lower().strip(".")
    if not hostname:
        return ""

    labels = [label for label in hostname.split(".") if label]
    if len(labels) <= 2:
        return hostname

    second_level_markers = {"co", "com", "org", "net", "gov", "edu"}
    if len(labels[-1]) == 2 and labels[-2] in second_level_markers and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def apply_strict_discovery_dedup(df: pd.DataFrame) -> pd.DataFrame:
    """Apply strict deduplication by email and root domain.

    Args:
        df: Discovery DataFrame with at least email, website, and target_company.

    Returns:
        Deduplicated DataFrame.
    """
    if df.empty:
        return df

    normalized = df.copy()
    normalized["email"] = normalized["email"].astype(str).map(normalize_email)
    normalized["target_company"] = normalized["target_company"].astype(str).map(clean_company_name)
    normalized["root_domain"] = normalized["website"].astype(str).map(extract_root_domain)

    normalized = normalized[normalized["email"].str.len() > 0]
    normalized = normalized.drop_duplicates(subset=["email"], keep="first")

    with_domain = normalized[normalized["root_domain"].str.len() > 0].drop_duplicates(
        subset=["root_domain"],
        keep="first",
    )
    without_domain = normalized[normalized["root_domain"].str.len() == 0]

    merged = pd.concat([with_domain, without_domain], axis=0).sort_index()
    return merged.drop(columns=["root_domain"])
