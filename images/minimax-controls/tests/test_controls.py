import ast
import asyncio
import io
import json
from fractions import Fraction
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import av
import numpy as np
import pytest
from conftest import ARTIFACT, installer


def test_install_preserves_unmodified_sources_and_is_idempotent(package):
    manifest = json.loads((ARTIFACT / "manifest.json").read_text())
    original = {name: (package / name).read_bytes() for name in manifest["preserved_files"]}
    installer.install(package)
    patched = {name: (package / name).read_bytes() for name in manifest["patches"]}
    for name, data in patched.items():
        assert installer.sha256(data) == manifest["patches"][name]["patched_sha256"]
    installer.install(package)
    assert all((package / name).read_bytes() == data for name, data in patched.items())
    assert all((package / name).read_bytes() == data for name, data in original.items())


@pytest.mark.parametrize(
    "name",
    [
        installer.TARGET,
        "diffusion/utils/media_utils.py",
        "diffusion/utils/commercial_use.py",
        "entrypoints/openai/protocol/videos.py",
    ],
)
def test_source_drift_is_rejected_before_any_patch(package, name):
    (package / name).write_bytes((package / name).read_bytes() + b"\n# changed\n")
    before = {
        p: (package / p).read_bytes() for p in (installer.TARGET, installer.WATERMARK_TARGET)
    }
    with pytest.raises(ValueError, match="source drift"):
        installer.install(package)
    assert all((package / p).read_bytes() == data for p, data in before.items())


def test_install_rejects_symlink_and_leaves_destination_unchanged(package, tmp_path):
    path = package / installer.TARGET
    external = tmp_path / "external.py"
    path.rename(external)
    before = external.read_bytes()
    path.symlink_to(external)
    with pytest.raises(ValueError, match="noncanonical"):
        installer.install(package)
    assert external.read_bytes() == before


def load_definition(path, name, namespace):
    tree = ast.parse(path.read_bytes())
    definition = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            definition,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def load_request_model(package):
    """Use the pinned Pydantic wire schema, including actual defaults/field presence."""
    path = package / "entrypoints/openai/protocol/videos.py"
    tree = ast.parse(path.read_bytes())
    # These two GPU-package imports supply optional size/quality helpers. Neither
    # is used by this request; all schema and Pydantic validation execute normally.
    excluded = {
        "vllm_omni.entrypoints.openai.image_api_utils",
        "vllm_omni.inputs.data",
    }
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module in excluded)
    ]
    namespace = {"__name__": "fixture_video_protocol"}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["VideoGenerationRequest"]


@pytest.mark.parametrize(
    "sound,silent", [(False, True), (True, False), (None, False), ("false", True), ("0", True)]
)
@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("setting", [None, "true", "false"])
def test_real_server_control_flow_and_mux_obey_watermark(
    installed, sound, silent, count, setting, monkeypatch
):
    """Execute the patched serving method with synthetic generation, then real MP4 encoding."""
    frames = np.full((4, 192, 320, 3), 96, dtype=np.uint8)
    audio = np.zeros(32000, dtype=np.float32)
    videos = [frames] * count
    audios = [audio] * count
    generation = AsyncMock(return_value=object())
    params = SimpleNamespace(
        extra_args={},
        boundary_ratio=None,
        num_inference_steps=50,
        guidance_scale=1,
        guidance_scale_2=None,
        seed=1101,
    )
    service = SimpleNamespace(
        _resolve_default_sampling_params=lambda: params,
        _request_fps_provided=lambda request: True,
        _apply_lora=lambda *args: None,
        _run_generation=generation,
        _extract_multimodal_output=lambda result: {},
        _extract_video_outputs=lambda result: videos,
        _extract_audio_outputs=lambda result, expected_count: audios,
        _extract_action_outputs=lambda result, expected_count: [None] * count,
        _resolve_audio_sample_rate=lambda result: 32000,
        _resolve_fps=lambda result: 24,
        _resolve_video_fps_multiplier=lambda result: 1,
        _extract_stage_durations=lambda result: {},
        _extract_peak_memory_mb=lambda result: 0,
    )
    request = load_request_model(installed)(
        prompt="A red square",
        num_outputs_per_prompt=count,
        extra_params={"task": "ref2va"},
        **({} if sound is None else {"generate_sound": sound}),
    )
    if sound is None:
        assert request.generate_sound is False
        assert "generate_sound" not in request.model_fields_set
    method = load_definition(
        installed / installer.TARGET,
        "_run_and_extract",
        {"OmniTextPrompt": dict, "VideoGenerationArtifacts": SimpleNamespace, "logger": Mock()},
    )
    images = [object() for _ in range(9)]
    artifacts = asyncio.run(
        method(service, request, "fixture", reference_image=SimpleNamespace(data=images))
    )
    assert artifacts.videos is videos and artifacts.output_fps == 24
    assert generation.await_args.args[0]["multi_modal_data"]["image"] is images
    assert len(artifacts.audios) == count
    assert (
        all(value is None for value in artifacts.audios) if silent else artifacts.audios is audios
    )

    if setting is not None:
        monkeypatch.setenv("CCS_MINIMAX_WATERMARK_ENABLED", setting)
    watermark = {"np": np}
    exec((installed / "diffusion/utils/commercial_use.py").read_bytes(), watermark)
    mux = load_definition(
        installed / "diffusion/utils/media_utils.py",
        "mux_video_audio_bytes",
        {
            "io": io,
            "av": av,
            "np": np,
            "Fraction": Fraction,
            "cast": cast,
            "apply_commercial_use_watermark": watermark["apply_commercial_use_watermark"],
        },
    )
    raw = mux(artifacts.videos[0], artifacts.audios[0], fps=24, audio_sample_rate=32000)
    with av.open(io.BytesIO(raw)) as container:
        assert len(container.streams.video) == 1
        assert len(container.streams.audio) == (0 if silent else 1)
        decoded = [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
    assert len(decoded) == len(frames)
    expected = watermark["apply_commercial_use_watermark"](frames[0])
    # H.264 is lossy; compare decoded pixels against the requested output.
    assert np.abs(decoded[0].astype(float) - expected).mean() < 5
    if setting == "false":
        assert np.array_equal(expected, frames[0])
    else:
        assert np.abs(decoded[0].astype(float) - frames[0]).sum() > 10000


@pytest.mark.parametrize("setting", ["", "TRUE", "False", "0", "1", "off"])
def test_invalid_watermark_setting_fails_at_module_startup(installed, setting, monkeypatch):
    monkeypatch.setenv("CCS_MINIMAX_WATERMARK_ENABLED", setting)
    with pytest.raises(ValueError, match="CCS_MINIMAX_WATERMARK_ENABLED"):
        exec((installed / installer.WATERMARK_TARGET).read_bytes(), {})


def test_watermark_setting_is_captured_at_startup_and_preserves_frame_timestamps(
    installed, monkeypatch
):
    monkeypatch.setenv("CCS_MINIMAX_WATERMARK_ENABLED", "false")
    namespace = {}
    exec((installed / installer.WATERMARK_TARGET).read_bytes(), namespace)
    monkeypatch.setenv("CCS_MINIMAX_WATERMARK_ENABLED", "true")
    pixels = np.full((192, 320, 3), 96, dtype=np.uint8)
    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
    frame.pts = 7
    frame.time_base = Fraction(1, 24)
    result = namespace["watermark_video_frame"](frame)
    assert np.array_equal(result.to_ndarray(format="rgb24"), pixels)
    assert result.pts == frame.pts and result.time_base == frame.time_base
