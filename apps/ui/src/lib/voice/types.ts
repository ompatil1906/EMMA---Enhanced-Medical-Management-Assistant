/* EMMA voice UI — shared types + transport contract.

   Two transports implement the same interface:

   * `gateway-transport` — live: streams the browser mic to the real
     `/ws/exotel` speech pipeline (Azure STT → controller → Azure TTS) and
     plays the returned PCM audio. It renders the gateway's own transcript,
     assistant text, and state control events.
   * `livekit-transport` — live: connects the browser mic to a LiveKit room
     running a LiveKit agent (Deepgram Flux STT → OpenAI GPT-6 Luna →
     controller → Qdrant RAG → NestJS backend → ElevenLabs TTS). Requires
     `VOICE_TRANSPORT=livekit` and LiveKit credentials on the gateway.
   * `demo-transport` — a clearly-labelled visual preview that walks the
     listening → thinking → speaking cycle with SAMPLE dialogue. It never
     pretends a real call or an AI conversation took place. */

/** The typed voice state machine shown by the UI. */
export type VoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error";

export type VoiceMode = "live" | "demo";

export interface VoiceMetrics {
  /** media frames sent to the gateway */
  framesIn: number;
  /** assistant audio frames received from the gateway */
  framesOut: number;
  /** echo-mode round-trip average, ms; null for the speech pipeline */
  rttMsAvg: number | null;
  closeCode: number | null;
}

export type TranscriptKind = "partial" | "final" | "agent";

export interface TranscriptLine {
  id: string;
  kind: TranscriptKind;
  text: string;
  /** true → content is a labelled sample (demo mode), never a real turn */
  sample: boolean;
}

export interface VoiceNoticeLine {
  id: string;
  text: string;
  sample: boolean;
}

export type VoiceEvent =
  | { type: "state"; state: VoiceState }
  | { type: "transcript"; line: TranscriptLine }
  | { type: "notice"; line: VoiceNoticeLine }
  | { type: "metrics"; metrics: VoiceMetrics }
  | /** output speech envelope, 0..1 (demo and live playback) */
    { type: "output-level"; level: number }
  | { type: "error"; message: string }
  | { type: "ended"; reason: "user" | "done" | "error" };

export interface VoiceSessionContext {
  onEvent(event: VoiceEvent): void;
  /** Smoothed mic level (0..1) from the shared audio pipeline; 0 without mic. */
  micLevel(): number;
}

export interface VoiceTransport {
  readonly mode: VoiceMode;
  /** Précis of what this transport actually is, shown in the UI. */
  readonly label: string;
  /** Begin the session. Must not throw — report through events instead. */
  start(ctx: VoiceSessionContext): void;
  /** Graceful stop (end call); safe to call before/after start. */
  stop(): void;
}
