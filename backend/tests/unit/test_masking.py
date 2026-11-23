from __future__ import annotations

from app.security.masking import mask_pii


def test_masks_common_pii() -> None:
    text = (
        "Contact alice@example.com or 13800138000. ID 110101199001011234, card 4111 1111 1111 1111."
    )

    masked = mask_pii(text)

    assert "alice@example.com" not in masked
    assert "13800138000" not in masked
    assert "110101199001011234" not in masked
    assert "4111 1111 1111 1111" not in masked
    assert "[EMAIL]" in masked
    assert "[PHONE]" in masked
    assert "[ID]" in masked
    assert "[CARD]" in masked


def test_leaves_normal_text_alone() -> None:
    assert mask_pii("hello world") == "hello world"
