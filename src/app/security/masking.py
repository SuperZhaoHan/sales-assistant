from __future__ import annotations

import re

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ID_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
BANK_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)")

REPLACEMENTS = (
    (EMAIL_PATTERN, "[EMAIL]"),
    (ID_CARD_PATTERN, "[ID]"),
    (PHONE_PATTERN, "[PHONE]"),
    (BANK_CARD_PATTERN, "[CARD]"),
)


def mask_pii(text: str) -> str:
    """Mask common PII before sending text to a third-party model.

    This is a best-effort baseline, not a compliance guarantee. Enable it with
    ``llm.pii_masking_enabled`` and extend the patterns for your domain.
    """

    masked = text
    for pattern, replacement in REPLACEMENTS:
        masked = pattern.sub(replacement, masked)
    return masked
