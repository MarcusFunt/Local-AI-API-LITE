/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SpeechProfile } from "../api";
import { ModelsPage } from "./ModelsPage";

afterEach(() => cleanup());

describe("speech profile registry", () => {
  it("shows the pinned STT and TTS services without marking them host-validated", () => {
    const profile: SpeechProfile = {
      id: "danish-speech-cpu",
      name: "Danish + English speech / CPU",
      language: "Danish (da) / English (en)",
      stt_alias: "ddu-stt",
      stt_model_id: "Systran/faster-whisper-small",
      stt_model_revision: "536b0662742c02347bc0e980a01041f333bce120",
      stt_runtime: "faster-whisper 1.2.1 / CTranslate2 CPU int8",
      stt_license: "MIT",
      tts_alias: "ddu-tts",
      tts_model_id: "rhasspy/piper-voices",
      tts_model_revision: "375a0fe641dea077c2a47b4e9a056d6da521eed3",
      tts_voice_id: "da: da_DK-talesyntese-medium; en: en_US-ljspeech-medium",
      tts_runtime: "piper-tts 1.8.0 / ONNX Runtime CPU",
      tts_runtime_license: "GPL-3.0-or-later",
      tts_voice_dataset_license: "Danish talesyntese: CC0; English LJ Speech: public domain (per pinned voice model cards)",
      compose_project: "local-ai-api-lite-speech",
      compose_profile: "danish-speech",
      cpu_only: true,
      stt_cpu_limit: 4,
      stt_memory_limit: "4g",
      tts_cpu_limit: 1,
      tts_memory_limit: "1g",
      max_audio_seconds: 120,
      max_audio_bytes: 25000000,
      max_text_characters: 4000,
      validation_state: "implementation-unverified",
      description: "Pinned local Danish and English transcription and speech synthesis.",
    };

    render(<ModelsPage status={null} profiles={[]} speechProfiles={[profile]} csrfToken="" />);

    expect(screen.getByRole("region", { name: "Danish + English speech / CPU profile" })).toBeTruthy();
    const card = screen.getByRole("region", { name: "Danish + English speech / CPU profile" });
    expect(card.textContent).toContain("ddu-stt");
    expect(card.textContent).toContain("ddu-tts");
    expect(screen.getByText("IMPLEMENTATION UNVERIFIED")).toBeTruthy();
    expect(screen.getByText("faster-whisper 1.2.1 / CTranslate2 CPU int8")).toBeTruthy();
    expect(card.textContent).toContain("da_DK-talesyntese-medium");
    expect(card.textContent).toContain("Danish (da) / English (en)");
    expect(card.textContent).toContain("en_US-ljspeech-medium");
    expect(card.textContent).toContain("375a0fe641dea077c2a47b4e9a056d6da521eed3");
    expect(card.textContent).toContain("Voice dataset: Danish talesyntese: CC0; English LJ Speech: public domain");
    expect(card.textContent).toContain("Runtime license: STT MIT · TTS GPL-3.0-or-later");
  });
});
