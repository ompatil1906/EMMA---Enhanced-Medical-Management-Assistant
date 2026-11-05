/* EMMA voice UI — shared types + transport contract.

   Two transports implement the same interface:

   * `gateway-transport` — live: streams the browser mic to the real
     `/ws/exotel` endpoint (provider role, simulation mode). Honest by design:
     it never emits agent speech, thinking, or speaking states.
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
  /** echoed frames received back */
  framesOut: number;
  /** rolling average send→echo round-trip, ms */
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
  | /** demo speech envelope, 0..1; live transports never emit this */
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
