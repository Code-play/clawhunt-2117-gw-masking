"""Placeholder assignment, the system note and input guards."""

from __future__ import annotations

import pytest

from app.masking import InvalidMaskingInput, MaskingService, PlaceholderCollisionError, SYSTEM_NOTE

from .conftest import EMAIL_A, IDNO_A, PHONE_A, PHONE_B


def test_criterion_1_same_phone_three_times_gets_one_placeholder(masking: MaskingService) -> None:
    """Acceptance criterion 1: one message, three occurrences, one placeholder."""

    messages = [
        {
            "role": "user",
            "content": f"我留的是 {PHONE_A}，紧急联系也是 {PHONE_A}，回拨 {PHONE_A}。",
        }
    ]

    result = masking.mask(messages, "req-criterion-1")

    content = result.messages[-1]["content"]
    assert isinstance(content, str)
    assert content.count("[[PHONE_1]]") == 3
    assert PHONE_A not in content
    assert result.masked_entity_count == 3
    assert result.distinct_values == 1
    assert result.placeholder_kinds == {"[[PHONE_1]]": "PHONE"}


def test_sequence_follows_first_appearance_per_type(masking: MaskingService) -> None:
    messages = [
        {"role": "user", "content": f"手机 {PHONE_A}，邮箱 {EMAIL_A}"},
        {"role": "user", "content": f"另一个手机 {PHONE_B}，身份证 {IDNO_A}"},
    ]

    result = masking.mask(messages, "req-sequence")

    # No system message existed, so the note is inserted at index 0.
    first, second = result.messages[-2]["content"], result.messages[-1]["content"]
    assert "[[PHONE_1]]" in first and "[[EMAIL_1]]" in first
    assert "[[PHONE_2]]" in second and "[[IDNO_1]]" in second
    assert result.masked_entity_count == 4
    assert result.distinct_values == 4


def test_repeated_value_across_messages_reuses_token(masking: MaskingService) -> None:
    messages = [
        {"role": "system", "content": "You are a gateway assistant."},
        {"role": "user", "content": f"first {PHONE_A}"},
        {"role": "assistant", "content": "noted"},
        {"role": "user", "content": f"again {PHONE_A}"},
    ]

    result = masking.mask(messages, "req-reuse-across")

    assert "[[PHONE_1]]" in result.messages[1]["content"]
    assert "[[PHONE_1]]" in result.messages[3]["content"]
    assert result.distinct_values == 1
    assert result.masked_entity_count == 2


def test_sequence_restarts_for_a_new_request(masking: MaskingService) -> None:
    first = masking.mask([{"role": "user", "content": PHONE_A}], "req-restart-a")
    second = masking.mask([{"role": "user", "content": PHONE_B}], "req-restart-b")

    assert "[[PHONE_1]]" in first.messages[-1]["content"]
    assert "[[PHONE_1]]" in second.messages[-1]["content"]


def test_system_note_is_appended_to_existing_system_message(masking: MaskingService) -> None:
    messages = [
        {"role": "system", "content": "You are a careful assistant."},
        {"role": "user", "content": f"call me on {PHONE_A}"},
    ]

    result = masking.mask(messages, "req-note-append")

    system_content = result.messages[0]["content"]
    assert isinstance(system_content, str)
    assert system_content.startswith("You are a careful assistant.")
    assert system_content.rstrip().endswith(SYSTEM_NOTE)
    assert len(result.messages) == 2


def test_system_message_is_created_when_absent(masking: MaskingService) -> None:
    result = masking.mask([{"role": "user", "content": "hello"}], "req-note-create")

    assert result.messages[0]["role"] == "system"
    assert result.messages[0]["content"] == SYSTEM_NOTE
    assert result.masked_entity_count == 0


def test_text_parts_are_masked_and_other_parts_preserved(masking: MaskingService) -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"phone {PHONE_A}"},
                {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
            ],
        }
    ]

    result = masking.mask(messages, "req-parts")

    parts = result.messages[-1]["content"]
    assert parts[0]["text"] == "phone [[PHONE_1]]"
    assert parts[1] == {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}


def test_input_with_reserved_placeholder_is_rejected(masking: MaskingService) -> None:
    """Counter-example: a token typed by the user cannot be distinguished from ours."""

    messages = [{"role": "user", "content": "please keep [[PHONE_9]] unchanged"}]

    with pytest.raises(PlaceholderCollisionError) as excinfo:
        masking.mask(messages, "req-collision")

    assert PHONE_A not in str(excinfo.value)
    assert "[[PHONE_9]]" not in str(excinfo.value)


def test_malformed_messages_are_rejected(masking: MaskingService) -> None:
    with pytest.raises(InvalidMaskingInput):
        masking.mask([], "req-empty")  # type: ignore[arg-type]
    with pytest.raises(InvalidMaskingInput):
        masking.mask([{"content": "no role"}], "req-no-role")  # type: ignore[list-item]


def test_invalid_request_id_is_rejected(masking: MaskingService) -> None:
    with pytest.raises(InvalidMaskingInput):
        masking.mask([{"role": "user", "content": "hi"}], "bad id with spaces")
    with pytest.raises(InvalidMaskingInput):
        masking.mask([{"role": "user", "content": "hi"}], "short")
