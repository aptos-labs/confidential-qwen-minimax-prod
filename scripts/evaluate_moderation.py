"""Run a labeled corpus INSIDE the CVM; emit aggregate quality/latency only."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

import httpx
from paid_gateway.moderation_contract import FAILURE, Verdict


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)]


def corpus_file(root: Path, name: str) -> Path:
    file = (root / name).resolve()
    if not file.is_relative_to(root.resolve()) or not file.is_file():
        raise ValueError("invalid corpus path")
    return file


async def evaluate(manifest: Path) -> dict[str, Any]:
    cases = json.loads(manifest.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("nonempty corpus required")
    results: dict[str, Any] = {
        "cases": 0,
        "misses": 0,
        "false_blocks": 0,
        "errors": 0,
        "category_mismatches": 0,
        "expected_blocks": 0,
        "expected_allows": 0,
        "by_category": {},
    }
    latency: dict[str, list[float]] = {"pre": [], "post": []}
    async with httpx.AsyncClient(timeout=245, trust_env=False, follow_redirects=False) as client:
        for case in cases:
            stage = case["stage"]
            expected = Verdict.parse(json.dumps(case["expected"]))
            if stage not in latency or expected.category == FAILURE:
                raise ValueError("invalid labeled case")
            body_file = corpus_file(manifest.parent, case["file"])
            if body_file.stat().st_size > (64 if stage == "pre" else 128) * 1024**2:
                raise ValueError("oversized corpus file")
            results["cases"] += 1
            results["expected_blocks" if expected.decision == "block" else "expected_allows"] += 1
            category = results["by_category"].setdefault(
                expected.category, {"cases": 0, "misses": 0}
            )
            category["cases"] += 1
            started = time.monotonic()
            try:
                response = await client.post(
                    "http://moderation-worker:8090/" + stage,
                    content=body_file.read_bytes(),
                    headers={
                        "Content-Type": case["content_type"],
                        "X-Audio-Expected": "true"
                        if case.get("audio_expected", True)
                        else "false",
                    },
                )
                if response.status_code != 200 or len(response.content) > 1024:
                    raise ValueError("moderation failed")
                actual = Verdict.parse(response.content)
                if actual.category == FAILURE:
                    raise ValueError("moderation failed")
                miss = expected.decision == "block" and actual.decision == "allow"
                results["misses"] += int(miss)
                category["misses"] += int(miss)
                results["false_blocks"] += int(
                    expected.decision == "allow" and actual.decision == "block"
                )
                results["category_mismatches"] += int(actual.category != expected.category)
            except Exception:
                results["errors"] += 1
            latency[stage].append(time.monotonic() - started)
    results["latency_seconds"] = {
        stage: {f"p{p}": percentile(values, p / 100) for p in (50, 95, 99)}
        for stage, values in latency.items()
    }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--max-miss-rate", type=float, required=True)
    parser.add_argument("--max-false-block-rate", type=float, required=True)
    args = parser.parse_args()
    if not all(0 <= value <= 1 for value in (args.max_miss_rate, args.max_false_block_rate)):
        parser.error("thresholds must be between zero and one")
    try:
        result = asyncio.run(evaluate(args.manifest))
    except Exception:
        raise SystemExit("Evaluation failed; no corpus content emitted.") from None
    print(json.dumps(result, sort_keys=True))
    # Both positive and negative cases are mandatory; errors never count as detections.
    passed = (
        result["expected_blocks"] > 0
        and result["expected_allows"] > 0
        and result["errors"] == 0
        and result["misses"] / result["expected_blocks"] <= args.max_miss_rate
        and result["false_blocks"] / result["expected_allows"] <= args.max_false_block_rate
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
