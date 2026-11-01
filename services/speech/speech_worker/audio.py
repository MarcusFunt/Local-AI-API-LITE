from __future__ import annotations

from typing import BinaryIO

from .contracts import MAX_AUDIO_SECONDS, SAMPLE_RATE, AudioTooLongError


class InvalidAudioError(Exception):
    """Raised for inputs that cannot be decoded as a non-empty audio stream."""


def decode_audio_bounded(
    source: BinaryIO,
    max_seconds: int = MAX_AUDIO_SECONDS,
):
    """Decode audio to mono 16 kHz float samples, stopping at the duration ceiling."""
    import av
    import numpy as np

    source.seek(0)
    chunks = []
    sample_count = 0
    max_samples = max_seconds * SAMPLE_RATE
    try:
        with av.open(source, mode="r") as container:
            streams = container.streams.audio
            if not streams:
                raise InvalidAudioError()
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(streams[0]):
                for converted in resampler.resample(frame):
                    samples = converted.to_ndarray().reshape(-1)
                    sample_count += int(samples.size)
                    if sample_count > max_samples:
                        raise AudioTooLongError()
                    chunks.append(samples.copy())
            for converted in resampler.resample(None):
                samples = converted.to_ndarray().reshape(-1)
                sample_count += int(samples.size)
                if sample_count > max_samples:
                    raise AudioTooLongError()
                chunks.append(samples.copy())
    except (AudioTooLongError, InvalidAudioError):
        raise
    except Exception as exc:
        raise InvalidAudioError() from exc

    if sample_count == 0:
        raise InvalidAudioError()
    audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    return audio, sample_count / SAMPLE_RATE
