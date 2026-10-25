import ast
import asyncio
import io
from fractions import Fraction
from types import SimpleNamespace
from typing import Mapping, cast
from unittest.mock import AsyncMock, Mock

import av
import numpy as np
import pytest
from conftest import controls, installer, launcher


def definition(source, name, namespace):
    tree = ast.parse(source)
    item = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    tree.body = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        item,
    ]
    exec(compile(ast.fix_missing_locations(tree), "upstream", "exec"), namespace)
    return namespace[name]


def request_type(upstream):
    tree = ast.parse((upstream / "entrypoints/openai/protocol/videos.py").read_bytes())
    excluded = {"vllm_omni.entrypoints.openai.image_api_utils", "vllm_omni.inputs.data"}
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module in excluded)
    ]
    namespace = {"__name__": "fixture_protocol"}
    exec(compile(tree, "upstream_protocol", "exec"), namespace)
    return namespace["VideoGenerationRequest"]


@pytest.mark.parametrize("sound", [None, True, False, "false"])
def test_real_serving_method_preserves_audio_default_and_silent_output(upstream, sound):
    source = installer.patched((upstream / installer.TARGET).read_bytes())
    frames = np.full((4, 192, 320, 3), 96, dtype=np.uint8)
    audio = np.zeros(32000, dtype=np.float32)
    params = SimpleNamespace(
        extra_args={},
        boundary_ratio=None,
        num_inference_steps=4,
        guidance_scale=1,
        guidance_scale_2=None,
        seed=1101,
    )
    generate = AsyncMock(return_value=object())
    service = SimpleNamespace(
        _resolve_default_sampling_params=lambda: params,
        _request_fps_provided=lambda request: True,
        _request_num_frames_provided=lambda request: False,
        _resolve_video_generation_defaults=lambda request: None,
        _apply_lora=lambda *args: None,
        _run_generation=generate,
        _extract_multimodal_output=lambda result: {},
        _extract_video_outputs=lambda result: [frames],
        _extract_audio_outputs=lambda result, expected_count: [audio],
        _extract_action_outputs=lambda result, expected_count: [None],
        _resolve_audio_sample_rate=lambda result: 32000,
        _resolve_fps=lambda result: 24,
        _resolve_video_fps_multiplier=lambda result: 1,
        _extract_stage_durations=lambda result: {},
        _extract_peak_memory_mb=lambda result: 0,
    )
    request = request_type(upstream)(
        prompt="A red square",
        num_inference_steps=4,
        extra_params={"task": "t2va"},
        **({} if sound is None else {"generate_sound": sound}),
    )
    run = definition(
        source,
        "_run_and_extract",
        {
            "OmniTextPrompt": dict,
            "VideoGenerationArtifacts": SimpleNamespace,
            "logger": Mock(),
            "Mapping": Mapping,
        },
    )
    result = asyncio.run(run(service, request, "trial"))
    assert generate.await_args.args[1].num_inference_steps == 4
    silent = sound is False or sound == "false"
    assert (result.audios[0] is None) == silent
    mux = definition(
        (upstream / "diffusion/utils/media_utils.py").read_bytes(),
        "mux_video_audio_bytes",
        {
            "io": io,
            "av": av,
            "np": np,
            "cast": cast,
            "Fraction": Fraction,
        },
    )
    encoded = mux(frames, result.audios[0], fps=24, audio_sample_rate=32000)
    with av.open(io.BytesIO(encoded)) as container:
        assert len(container.streams.audio) == (0 if silent else 1)
        decoded = [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
    assert len(decoded) == 4
    # The trial uses upstream's unwatermarked muxer, matching watermark=false.
    assert np.abs(decoded[0].astype(float) - frames[0]).mean() < 5


def test_patch_refuses_source_drift(upstream):
    with pytest.raises(ValueError, match="unexpected"):
        installer.patched((upstream / installer.TARGET).read_bytes() + b"\n")


@pytest.mark.parametrize("layout", ["mono", "channels_first", "channels_last", "silent"])
def test_full_fifteen_second_output_passes_existing_duration_limit(upstream, layout):
    frames = np.full((362, 64, 64, 3), 96, dtype=np.uint8)
    audio = np.zeros((2, 486400), dtype=np.float32)
    if layout == "mono":
        audio = audio[0]
    elif layout == "channels_last":
        audio = audio.T
    elif layout == "silent":
        audio = None
    videos, audios = controls.bound_media([frames], [audio], fps=24, sample_rate=32000)
    assert len(videos[0]) == 360
    mux = definition(
        (upstream / "diffusion/utils/media_utils.py").read_bytes(),
        "mux_video_audio_bytes",
        {
            "io": io,
            "av": av,
            "np": np,
            "cast": cast,
            "Fraction": Fraction,
        },
    )
    with av.open(io.BytesIO(mux(videos[0], audios[0], fps=24, audio_sample_rate=32000))) as c:
        video = c.streams.video[0]
        assert video.frames == 360
        assert video.duration * video.time_base == 15
        assert max(s.duration * s.time_base for s in c.streams) <= 15


def test_preencoded_output_is_rejected_instead_of_slicing_mp4_bytes():
    with pytest.raises(ValueError, match="decoded frames"):
        controls.bound_media([b"mp4"], [None], fps=24, sample_rate=32000)


def test_launch_uses_local_adapter_and_fixed_gpu_group():
    argv = launcher.command("/tinfoil/mpk/mpk-" + "a" * 64)
    assert "--num-gpus=4" in argv
    assert "--tensor-parallel-size=4" in argv
    assert "--task-type=fl2va" in argv
    assert "--diffusion-attention-backend=FASTVIDEO_VSA" in argv
    assert "--lora-path=/opt/fasth3/adapter_model.safetensors" in argv
    for model in ["MiniMaxAI/MiniMax-H3", "/tmp/model", "/tinfoil/mpk/mpk-../bad"]:
        with pytest.raises(ValueError):
            launcher.command(model)
