"""Closed, content-free moderation wire contract shared with the private worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "minimax-h3-2026-10-21-v1"
AUP_CATEGORIES = frozenset(f"aup_{n:02d}" for n in range(2, 21) if n not in {12})
POLICY_CATEGORIES = AUP_CATEGORIES | {
    "sexual_content",
    "guard_unsafe",
    "guard_controversial",
}
FAILURE = "moderation_unavailable"


class ModerationFailure(Exception):
    def __init__(self) -> None:
        super().__init__(FAILURE)


def strict_object(raw: bytes | str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ModerationFailure
            result[key] = value
        return result

    def invalid(_: str) -> None:
        raise ModerationFailure

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(value, dict):
            raise ModerationFailure
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ModerationFailure from exc


@dataclass(frozen=True)
class Verdict:
    decision: str
    category: str

    def __post_init__(self) -> None:
        if not (
            (self.decision == "allow" and self.category == "none")
            or (self.decision == "block" and self.category in POLICY_CATEGORIES | {FAILURE})
        ):
            raise ModerationFailure

    def wire(self) -> dict[str, str]:
        return {"decision": self.decision, "category": self.category}

    @classmethod
    def parse(cls, raw: bytes | str, *, model: bool = False) -> Verdict:
        value = strict_object(raw)
        if set(value) != {"decision", "category"} or not all(
            isinstance(v, str) for v in value.values()
        ):
            raise ModerationFailure
        result = cls(value["decision"], value["category"])
        if model and result.category not in AUP_CATEGORIES | {"sexual_content", "none"}:
            raise ModerationFailure
        return result


ALLOW = Verdict("allow", "none")
UNAVAILABLE = Verdict("block", FAILURE)
