"""Offline Guard and fixed internal Qwen endpoint; no content-bearing diagnostics."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
from paid_gateway.moderation_contract import ModerationFailure, Verdict, strict_object

from .policy import RUBRIC, guard_verdict

QWEN_URL = "http://vllm-omni-qwen:8000/v1/chat/completions"
MODEL_DIR = "/opt/qwen3guard"
GUARD_MAX_INPUT_TOKENS = 16384


class Guard:
    def __init__(self) -> None:
        self.healthy = False
        self.model: Any = None
        self.tokenizer: Any = None
        self.startup_stage = "starting"

    def load(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.startup_stage = "cuda_availability"
        if not torch.cuda.is_available():
            raise ModerationFailure
        self.startup_stage = "cuda_count"
        if torch.cuda.device_count() != 1:
            raise ModerationFailure
        torch.set_num_threads(2)
        self.startup_stage = "tokenizer_load"
        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_DIR,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.startup_stage = "weights_load"
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_DIR,
            local_files_only=True,
            trust_remote_code=False,
            torch_dtype=torch.bfloat16,
            device_map="cuda:0",
        ).eval()
        self.healthy = True

    def _check(self, prompt: str) -> Verdict:
        import torch

        if not self.healthy:
            raise ModerationFailure
        self.startup_stage = "guard_tokenize"
        encoded = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
        input_length = encoded["input_ids"].shape[1]
        if input_length > GUARD_MAX_INPUT_TOKENS:
            raise ModerationFailure
        self.startup_stage = "guard_transfer"
        encoded = encoded.to(self.model.device)
        self.startup_stage = "guard_generate"
        with torch.inference_mode():
            output = self.model.generate(
                **encoded,
                max_new_tokens=128,
                do_sample=False,
                max_time=30,
                pad_token_id=self.tokenizer.eos_token_id,
            )[0, input_length:]
        self.startup_stage = "guard_completion"
        if not len(output) or output[-1].item() != self.tokenizer.eos_token_id:
            raise ModerationFailure
        self.startup_stage = "guard_verdict"
        verdict = guard_verdict(self.tokenizer.decode(output, skip_special_tokens=True))
        self.startup_stage = "guard_complete"
        return verdict

    async def check(self, prompt: str) -> Verdict:
        task = asyncio.create_task(asyncio.to_thread(self._check, prompt))
        try:
            return await asyncio.shield(task)
        except BaseException:
            # A cancelled CUDA thread may still be executing. Quarantine the instance
            # so a following request cannot overlap it or reuse a late result.
            self.healthy = False
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            raise


class Omni:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(35, connect=5),
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def classify(self, content: list[dict[str, Any]]) -> Verdict:
        payload = {
            "model": "qwen3-omni",
            "stream": False,
            "modalities": ["text"],
            "temperature": 0,
            "max_tokens": 96,
            "messages": [
                {"role": "system", "content": RUBRIC},
                {"role": "user", "content": content},
            ],
        }
        try:
            async with asyncio.timeout(40):
                async with self.client.stream(
                    "POST",
                    QWEN_URL,
                    json=payload,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "").split(";")[0]
                        != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise ModerationFailure
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        body.extend(chunk)
                        if len(body) > 32768:
                            raise ModerationFailure
                    result = strict_object(bytes(body))
                    choices = result["choices"]
                    if not isinstance(choices, list) or len(choices) != 1 or result.get("error"):
                        raise ModerationFailure
                    choice = choices[0]
                    message = choice["message"]
                    if choice["finish_reason"] != "stop" or message.get("role") != "assistant":
                        raise ModerationFailure
                    if any(
                        message.get(k) for k in ("tool_calls", "function_call", "refusal", "audio")
                    ):
                        raise ModerationFailure
                    value = message["content"]
                    if not isinstance(value, str) or len(value) > 1024:
                        raise ModerationFailure
                    return Verdict.parse(value, model=True)
        except (httpx.HTTPError, TimeoutError, KeyError, TypeError, AttributeError) as exc:
            raise ModerationFailure from exc


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def image_part(image: bytes) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        },
    }


def audio_part(audio: bytes) -> dict[str, Any]:
    return {
        "type": "input_audio",
        "input_audio": {
            "format": "wav",
            "data": base64.b64encode(audio).decode("ascii"),
        },
    }
