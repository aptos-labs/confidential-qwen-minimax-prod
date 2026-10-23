"""Exercise the Guard adapter's input boundary without loading a GPU model."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from moderation_worker.models import Guard
from paid_gateway.moderation_contract import ALLOW, ModerationFailure


def prepared_guard(monkeypatch, input_length):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext))
    input_ids = SimpleNamespace(shape=(1, input_length))
    attention_mask = object()
    encoded = MagicMock()
    encoded.__getitem__.return_value = input_ids
    encoded.to.return_value = {"input_ids": input_ids, "attention_mask": attention_mask}
    output = MagicMock()
    output.__getitem__.return_value = [123, SimpleNamespace(item=Mock(return_value=9))]
    guard = Guard()
    guard.healthy = True
    guard.tokenizer = SimpleNamespace(
        apply_chat_template=Mock(return_value=encoded),
        eos_token_id=9,
        decode=Mock(return_value="Safety: Safe\nCategories: None"),
    )
    guard.model = SimpleNamespace(device="cuda:0", generate=Mock(return_value=output))
    return guard, encoded, output


@pytest.mark.parametrize("input_length", [8193, 16384])
def test_guard_accepts_expanded_context_without_truncation(monkeypatch, input_length):
    guard, encoded, output = prepared_guard(monkeypatch, input_length)
    prompt = "A benign synthetic prompt whose tokenized length is controlled by the fixture."

    assert guard._check(prompt) == ALLOW

    guard.tokenizer.apply_chat_template.assert_called_once_with(
        [{"role": "user", "content": prompt}],
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    encoded.to.assert_called_once_with("cuda:0")
    guard.model.generate.assert_called_once_with(
        **encoded.to.return_value,
        max_new_tokens=128,
        do_sample=False,
        max_time=30,
        pad_token_id=9,
    )
    output.__getitem__.assert_called_once_with((0, slice(input_length, None)))


def test_guard_rejects_over_limit_before_gpu_transfer(monkeypatch):
    guard, encoded, _ = prepared_guard(monkeypatch, 16385)

    with pytest.raises(ModerationFailure):
        guard._check("A benign synthetic prompt over the token budget.")

    encoded.to.assert_not_called()
    guard.model.generate.assert_not_called()
