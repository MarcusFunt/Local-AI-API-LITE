from __future__ import annotations

import io
import json
import logging
import threading
from collections.abc import Callable
from fractions import Fraction

import av
import httpx

from .errors import ServiceError
from .languages import Language

LOGGER = logging.getLogger("chat_voice.speech")
STT_URL = "http://ddu-stt:8080/v1/audio/transcriptions"
TTS_URL = "http://ddu-tts:8080/v1/audio/speech"
STT_MODEL = "ddu-stt"
TTS_MODEL = "ddu-tts"
MAX_AUDIO_BYTES = 25_000_000
MAX_AUDIO_SECONDS = 120
MAX_AUDIO_FRAMES = 60_000
MAX_TTS_CHARACTERS = 4000
MAX_TRANSCRIPT_CHARACTERS = 4000
MAX_SPEECH_RESPONSE_BYTES = 25_000_000
ALLOWED_AUDIO_TYPES = frozenset(
    {"audio/webm", "audio/wav", "audio/x-wav", "audio/ogg", "audio/mpeg", "audio/mp4"}
)


class SpeechService:
    def __init__(
        self,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.timeout = timeout or httpx.Timeout(connect=3, read=45, write=20, pool=3)
        self._capacity = threading.BoundedSemaphore(1)

    def transcribe(self, audio: bytes, content_type: str, language: Language) -> dict[str, str]:
        if not audio:
            raise ServiceError(422, "audio_empty")
        if len(audio) > MAX_AUDIO_BYTES:
            raise ServiceError(413, "audio_too_large")
        if content_type not in ALLOWED_AUDIO_TYPES:
            raise ServiceError(415, "audio_format_unsupported")
        if not self._capacity.acquire(blocking=False):
            raise ServiceError(429, "speech_saturated")
        try:
            _validate_audio_duration(audio)
            with (
                self.client_factory(timeout=self.timeout, trust_env=False) as client,
                client.stream(
                    "POST",
                    STT_URL,
                    data={"model": STT_MODEL, "language": language, "response_format": "json"},
                    files={"file": ("recording", audio, content_type)},
                    headers={"Accept-Encoding": "identity"},
                ) as response,
            ):
                if response.status_code < 200 or response.status_code >= 300:
                    raise ServiceError(503, "speech_unavailable")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ServiceError(502, "speech_invalid_response")
                content = _read_bounded(response, MAX_SPEECH_RESPONSE_BYTES)
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ServiceError(502, "speech_invalid_response") from None
            transcript = data.get("text") if isinstance(data, dict) else None
            if not isinstance(transcript, str) or len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
                raise ServiceError(502, "speech_invalid_response")
            LOGGER.info("transcription completed model=%s language=%s", STT_MODEL, language)
            return {"text": transcript}
        except ServiceError as exc:
            LOGGER.warning("transcription failed category=%s", exc.category)
            raise
        except httpx.TimeoutException:
            LOGGER.warning("transcription failed category=timeout")
            raise ServiceError(504, "speech_timeout") from None
        except httpx.HTTPError:
            LOGGER.warning("transcription failed category=unavailable")
            raise ServiceError(503, "speech_unavailable") from None
        except (OSError, RuntimeError, ValueError):
            LOGGER.warning("transcription failed category=unavailable")
            raise ServiceError(503, "speech_unavailable") from None
        finally:
            self._capacity.release()

    def synthesize(self, text: str, language: Language) -> tuple[bytes, str]:
        if not isinstance(text, str) or not text.strip():
            raise ServiceError(422, "speech_text_empty")
        if len(text) > MAX_TTS_CHARACTERS:
            raise ServiceError(422, "speech_text_too_long")
        if not self._capacity.acquire(blocking=False):
            raise ServiceError(429, "speech_saturated")
        try:
            with (
                self.client_factory(timeout=self.timeout, trust_env=False) as client,
                client.stream(
                    "POST",
                    TTS_URL,
                    json={
                        "model": TTS_MODEL,
                        "language": language,
                        "input": text,
                        "response_format": "wav",
                    },
                    headers={"Accept": "audio/wav", "Accept-Encoding": "identity"},
                ) as response,
            ):
                if response.status_code < 200 or response.status_code >= 300:
                    raise ServiceError(503, "speech_unavailable")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ServiceError(502, "speech_invalid_response")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in {"audio/wav", "audio/x-wav"}:
                    raise ServiceError(502, "speech_invalid_response")
                audio = _read_bounded(response, MAX_SPEECH_RESPONSE_BYTES)
            if len(audio) < 12 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
                raise ServiceError(502, "speech_invalid_response")
            LOGGER.info("synthesis completed model=%s language=%s", TTS_MODEL, language)
            return audio, "audio/wav"
        except ServiceError as exc:
            LOGGER.warning("synthesis failed category=%s", exc.category)
            raise
        except httpx.TimeoutException:
            LOGGER.warning("synthesis failed category=timeout")
            raise ServiceError(504, "speech_timeout") from None
        except httpx.HTTPError:
            LOGGER.warning("synthesis failed category=unavailable")
            raise ServiceError(503, "speech_unavailable") from None
        except (OSError, RuntimeError, ValueError):
            LOGGER.warning("synthesis failed category=unavailable")
            raise ServiceError(503, "speech_unavailable") from None
        finally:
            self._capacity.release()


def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise ServiceError(502, "upstream_response_too_large")
        except ValueError:
            raise ServiceError(502, "upstream_invalid_response") from None
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise ServiceError(502, "upstream_response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_audio_duration(audio: bytes) -> None:
    """Decode the audio track and stop as soon as the duration or frame budget is exceeded."""
    try:
        av.logging.set_level(av.logging.PANIC)
        with av.open(io.BytesIO(audio), mode="r") as container:
            streams = container.streams.audio
            if len(streams) != 1:
                raise ValueError("Audio must contain one audio stream")
            stream = streams[0]
            stream.codec_context.thread_count = 1
            duration = Fraction(0, 1)
            frame_count = 0
            for frame in container.decode(audio=0):
                frame_count += 1
                if frame_count > MAX_AUDIO_FRAMES:
                    raise ValueError("Audio contains too many decoded frames")
                if frame.sample_rate <= 0 or frame.samples < 0:
                    raise ValueError("Audio frame metadata is invalid")
                duration += Fraction(frame.samples, frame.sample_rate)
                if duration > MAX_AUDIO_SECONDS:
                    raise ServiceError(413, "audio_too_long")
            if frame_count == 0:
                raise ValueError("Audio contains no decoded frames")
    except ServiceError:
        raise
    except Exception:  # noqa: BLE001 - media parser details and embedded metadata remain private.
        raise ServiceError(415, "audio_invalid") from None
