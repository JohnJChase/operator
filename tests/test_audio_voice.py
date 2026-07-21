"""Piper voice and WAV level checks."""

import array
from pathlib import Path

import pytest

from operator_os.audio import VOICE_MODELS, resample_s16_mono, resolve_voice


def test_named_hfc_female_resolves_when_present():
    path = VOICE_MODELS["hfc_female"]
    if not path.is_file():
        pytest.skip("onnx weights not installed (just setup-voices)")
    assert resolve_voice("hfc_female") == path
    assert resolve_voice("en_US-hfc_female-medium") == path


def test_missing_voice_raises():
    with pytest.raises(FileNotFoundError):
        resolve_voice("/tmp/definitely-missing-piper-voice.onnx")


def test_absolute_path_voice(tmp_path):
    onnx = tmp_path / "toy.onnx"
    onnx.write_bytes(b"x")
    assert resolve_voice(str(onnx)) == onnx


def test_resample_doubles_length_for_half_rate():
    # 4 samples at 8000 Hz -> 8 samples at 16000 Hz
    src = array.array("h", [0, 1000, 0, -1000]).tobytes()
    out = resample_s16_mono(src, 8000, 16000)
    dst = array.array("h")
    dst.frombytes(out)
    assert len(dst) == 8


def test_resample_same_rate_is_noop():
    src = array.array("h", [1, 2, 3, 4]).tobytes()
    assert resample_s16_mono(src, 16000, 16000) == src


def test_analyze_wav_levels_ok():
    from operator_os.audio import _write_tone_wav, analyze_wav_levels

    path = _write_tone_wav((440.0,), 16000, 0.2)
    try:
        levels = analyze_wav_levels(path)
        assert levels.verdict == "OK"
        assert levels.ok
        assert levels.peak_dbfs < -1.0
    finally:
        path.unlink(missing_ok=True)


def test_crossbar_click_is_short_and_hot():
    from operator_os.audio import _TONE_AMP, build_crossbar_click

    rate = 16000
    pcm = build_crossbar_click(rate, seed=1)
    samples = array.array("h")
    samples.frombytes(pcm)
    # Includes leading silence for device latency (~100ms) + body + tail.
    assert 0.20 * rate <= len(samples) <= 0.35 * rate
    peak = max(abs(s) for s in samples)
    assert peak > int(_TONE_AMP * 32767 * 1.4)


def test_analyze_existing_mic_recording_if_present():
    from operator_os.audio import analyze_wav_levels

    path = Path("data/recordings/mic-test.wav")
    if not path.is_file():
        pytest.skip("no mic-test.wav yet")
    levels = analyze_wav_levels(path)
    assert levels.duration_s > 0
    assert levels.sample_rate_hz == 16000
    assert levels.verdict in ("OK", "WARN", "BAD")
