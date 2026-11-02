import type { DashboardStatus, Profile, SpeechProfile } from "../api";
import { OperationPanel } from "./OperationPanel";
import { RetrievalOperationsPanel } from "./RetrievalOperationsPanel";

interface ModelsPageProps {
  status: DashboardStatus | null;
  profiles: Profile[];
  speechProfiles: SpeechProfile[];
  csrfToken: string;
}

function StateBadge({ state }: { state: string }) {
  const key = state.toLowerCase().replaceAll(" ", "-");
  return <span className={`state-badge state-${key}`}><span className="state-dot" />{state}</span>;
}

function ProfileCard({ profile, active, index }: { profile: Profile; active: boolean; index: number }) {
  return (
    <article className={`profile-card ${active ? "profile-card-active" : ""}`}>
      <div className="profile-card-top">
        <div className={`profile-index profile-index-${index}`}>0{index + 1}</div>
        <div className="profile-tags"><span className="validated-tag"><span />VALIDATED</span>{active && <span className="active-tag">ACTIVE</span>}</div>
      </div>
      <h3>{profile.name}</h3>
      <p>{profile.description}</p>
      <div className="profile-model">{profile.model_id}</div>
      <div className="profile-specs">
        <div><span>CONTEXT</span><strong>{profile.max_model_len.toLocaleString()}</strong></div>
        <div><span>MAX SEQUENCES</span><strong>{profile.max_num_seqs}</strong></div>
        <div><span>GPU MEMORY TARGET</span><strong>{Math.round(profile.gpu_memory_utilization * 100)}%</strong></div>
      </div>
      <div className="profile-revision"><span>REASONING PARSER</span><code>{profile.reasoning_parser}</code></div>
      <div className="profile-revision"><span>REVISION</span><code>{profile.model_revision.slice(0, 12)}…</code></div>
      <div className="profile-image"><span>PINNED ENGINE IMAGE</span><code>{profile.image_ref.split("@")[1]?.slice(0, 20) ?? profile.image_ref}</code></div>
    </article>
  );
}

function SpeechProfileCard({ profile }: { profile: SpeechProfile }) {
  return (
    <section className="speech-profile-card" aria-label={`${profile.name} profile`}>
      <div className="speech-profile-heading">
        <div><span className="eyebrow">{profile.language} · {profile.compose_profile}</span><h3>{profile.name}</h3></div>
        <span className="speech-profile-unverified">IMPLEMENTATION UNVERIFIED</span>
      </div>
      <p>{profile.description}</p>
      <div className="speech-service-grid">
        <div className="speech-service">
          <span className="eyebrow">SPEECH TO TEXT · {profile.stt_alias}</span>
          <strong>{profile.stt_model_id}</strong>
          <span>{profile.stt_runtime}</span>
          <code>{profile.stt_model_revision}</code>
        </div>
        <div className="speech-service">
          <span className="eyebrow">TEXT TO SPEECH · {profile.tts_alias}</span>
          <strong>{profile.tts_voice_id}</strong>
          <span>{profile.tts_runtime}</span>
          <code>{profile.tts_model_revision}</code>
        </div>
      </div>
      <div className="speech-profile-limits">
        <span>CPU only</span>
        <span>STT {profile.stt_cpu_limit} CPU · {profile.stt_memory_limit}</span>
        <span>TTS {profile.tts_cpu_limit} CPU · {profile.tts_memory_limit}</span>
        <span>Upload limit {Math.round(profile.max_audio_bytes / 1_000_000)} MB · {profile.max_audio_seconds}s</span>
        <span>Runtime license: STT {profile.stt_license} · TTS {profile.tts_runtime_license}</span>
        <span>Voice dataset: {profile.tts_voice_dataset_license}</span>
      </div>
    </section>
  );
}

export function ModelsPage({ status, profiles, speechProfiles, csrfToken }: ModelsPageProps) {
  const activeProfileId = status?.active_profile?.id;
  const configuration = status?.inference.configuration;
  return (
    <div className="page-content models-content">
      <section className="intro-panel">
        <span className="eyebrow">PINNED CONFIGURATIONS</span>
        <h2>Validated on this host</h2>
        <p>Choose a validated local profile and manage inference with fixed, authenticated controls. No arbitrary commands or prompt content are accepted.</p>
      </section>
      <OperationPanel profiles={profiles} csrfToken={csrfToken} />
      <RetrievalOperationsPanel csrfToken={csrfToken} workers={status?.model_services ?? []} />
      <section className="observed-config panel">
        <div className="panel-heading"><div><span className="eyebrow">OBSERVED AT RUNTIME</span><h2>Current model configuration</h2></div><StateBadge state={status?.inference.state ?? "Unknown"} /></div>
        {configuration ? <div className="observed-grid">
          <div className="observed-model"><span className="eyebrow">MODEL IDENTIFIER</span><strong>{configuration.model_id}</strong></div>
          <div><span className="eyebrow">REVISION</span><strong className="mono">{configuration.model_revision ?? "Unavailable"}</strong></div>
          <div><span className="eyebrow">SERVED ALIAS</span><strong>{configuration.model_alias ?? "Unavailable"}</strong></div>
          <div><span className="eyebrow">REASONING PARSER</span><strong>{configuration.reasoning_parser ?? "Unavailable"}</strong></div>
          <div><span className="eyebrow">PROFILE MATCH</span><strong>{status?.active_profile?.name ?? "No validated match"}</strong></div>
          <div className="observed-image"><span className="eyebrow">PINNED ENGINE IMAGE</span><strong className="mono">{configuration.image_ref ?? "Unavailable"}</strong></div>
        </div> : <p className="empty-state">No inference container configuration is available. The controller can still report host telemetry while inference is stopped.</p>}
      </section>
      <div className="section-heading profile-heading"><div><span className="eyebrow">PROFILE REGISTRY</span><h2>Host-tested options</h2></div><span className="section-note">Stable API alias: ddu-narrator</span></div>
      <div className="profile-grid">
        {profiles.map((profile, index) => <ProfileCard key={profile.id} profile={profile} active={activeProfileId === profile.id} index={index} />)}
        {profiles.length === 0 && <div className="empty-state panel">The versioned profile registry is unavailable.</div>}
      </div>
      <section className="speech-profiles-section" aria-labelledby="speech-profiles-title">
        <div className="section-heading profile-heading">
          <div><span className="eyebrow">SPEECH PROFILE REGISTRY</span><h2 id="speech-profiles-title">Speech services</h2></div>
          <span className="section-note">Private aliases: ddu-stt · ddu-tts</span>
        </div>
        <div className="speech-profile-grid">
          {speechProfiles.map((profile) => <SpeechProfileCard key={profile.id} profile={profile} />)}
          {speechProfiles.length === 0 && <div className="empty-state panel">The speech profile registry is unavailable.</div>}
        </div>
      </section>
      <div className="profile-boundary"><span className="boundary-icon">i</span><span>Only fixed, validated profile IDs are accepted. Controls remain unavailable until an operator configures a stable local Compose project. A deliberate Stop remains the desired state after dashboard restarts.</span></div>
    </div>
  );
}
