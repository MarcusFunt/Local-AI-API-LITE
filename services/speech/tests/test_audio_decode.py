from __future__ import annotations

import io
import wave

import pytest
from speech_worker.audio import InvalidAudioError, decode_audio_bounded
from speech_worker.contracts import AudioTooLongError


def make_wav(seconds: int) -> io.BytesIO:
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 16_000 * seconds)
    audio.seek(0)
    return audio


def test_decoder_converts_valid_audio_to_bounded_mono_float_samples():
    pytest.importorskip("av")
    pytest.importorskip("numpy")

    samples, duration = decode_audio_bounded(make_wav(1))

    assert samples.shape == (16_000,)
    assert samples.dtype.name == "float32"
    assert duration == 1.0


def test_decoder_stops_when_decoded_audio_exceeds_duration_limit():
    pytest.importorskip("av")
    pytest.importorskip("numpy")

    with pytest.raises(AudioTooLongError):
        decode_audio_bounded(make_wav(2), max_seconds=1)


def test_decoder_rejects_malformed_audio_without_echoing_input_details():
    pytest.importorskip("av")
    pytest.importorskip("numpy")

    with pytest.raises(InvalidAudioError):
        decode_audio_bounded(io.BytesIO(b"private malformed audio"))
