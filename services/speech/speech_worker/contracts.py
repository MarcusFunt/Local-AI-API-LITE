from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STT_ALIAS = "ddu-stt"
STT_MODEL_ID = "Systran/faster-whisper-small"
STT_MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
TTS_ALIAS = "ddu-tts"
TTS_MODEL_ID = "rhasspy/piper-voices"
TTS_MODEL_REVISION = "375a0fe641dea077c2a47b4e9a056d6da521eed3"
SpeechLanguage = Literal["da", "en"]
TTS_VOICE_IDS: dict[SpeechLanguage, str] = {
    "da": "da_DK-talesyntese-medium",
    "en": "en_US-ljspeech-medium",
}

MAX_AUDIO_BYTES = 25_000_000
MAX_AUDIO_SECONDS = 120
MAX_TEXT_CHARACTERS = 4_000
MAX_STT_REQUEST_BYTES = MAX_AUDIO_BYTES + 1_048_576
MAX_TTS_REQUEST_BYTES = 64_000
INFERENCE_TIMEOUT_SECONDS = 180
MAX_OUTSTANDING_REQUESTS = 2
SAMPLE_RATE = 16_000


class SpeechError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class MissingArtifactError(Exception):
    """Raised when the fixed, offline model or voice files are unavailable."""


class AudioTooLongError(Exception):
    """Raised after bounded decoding crosses the accepted audio duration."""


class QueueFullError(Exception):
    """Raised when the worker already has its maximum outstanding requests."""


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input: str = Field(min_length=1, max_length=MAX_TEXT_CHARACTERS)
    language: SpeechLanguage
    response_format: Literal["wav"] = "wav"
