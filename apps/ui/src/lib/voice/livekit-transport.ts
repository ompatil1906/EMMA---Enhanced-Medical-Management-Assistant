"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */
/* LiveKit WebRTC transport for the EMMA voice UI (Day 13).

   Bridges the browser microphone to the LiveKit voice agent and plays the
   assistant's audio back through Web Audio. Implements the same
   `VoiceTransport` interface as `gateway-transport`, so the UI can select
   between WebSocket and LiveKit via `VOICE_TRANSPORT`.

   Flow:

       browser mic → LiveKit room → LiveKit agent → Deepgram STT →
       emergency guard → OpenAI LLM → ConversationController → Qdrant RAG →
       NestJS backend → ElevenLabs TTS → LiveKit room → browser speaker

   The LiveKit SDK (`livekit-client`) is loaded dynamically so this module
   imports cleanly even when the package is not installed. When LiveKit is
   unavailable, `start()` reports an error event rather than crashing.

   Credentials: the browser receives a short-lived JWT from the
   `/api/voice/token` endpoint. API keys never reach the client.
*/

import type {
  TranscriptKind,
  VoiceCaller,
  VoiceEvent,
  VoiceMetrics,
  VoiceSessionContext,
  VoiceState,
  VoiceTransport,
} from "@/lib/voice/types";

type LiveKitClientModule = any;

const SAMPLE_RATE = 16_000;

/* LiveKit agent participant identities. `agent-<job_id>` is what the LiveKit
   Agents worker joins as; `emma-agent-` is the legacy `LiveKitAgent` identity. */
const AGENT_IDENTITY_PREFIXES = ["agent-", "emma-agent-"] as const;

function isAgentIdentity(identity: unknown): boolean {
  const value = String(identity ?? "");
  return AGENT_IDENTITY_PREFIXES.some((prefix) => value.startsWith(prefix));
}

/* Development-only, non-secret playback diagnostics (Day 18E).
   The Day 17 tests could only prove the remote track was *attached*; these
   counters prove the browser actually received audible samples. Exposed on
   `window.__emmaVoiceDiag` for tests and a dev console — never sent anywhere. */
interface RemoteAudioDiagnostics {
  attached: boolean;
  playing: boolean;
  frames: number;
  peakRms: number;
  lastRms: number;
}

function publishDiagnostics(diag: RemoteAudioDiagnostics): void {
  if (typeof window !== "undefined") {
    (window as unknown as { __emmaVoiceDiag?: RemoteAudioDiagnostics }).__emmaVoiceDiag = diag;
  }
}

function randomId(prefix: string): string {
  const uuid =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2);
  return `${prefix}-${uuid}`;
}

async function loadLiveKitClient(): Promise<LiveKitClientModule | null> {
  if (typeof window === "undefined") return null;
  try {
    // Dynamically import so the module loads without `livekit-client`
    // installed.
        const mod: any = await import("livekit-client");
    return mod;
  } catch {
    return null;
  }
}

interface LiveKitTransportOptions {
  /** Base URL of the FastAPI gateway (for the token endpoint). */
  baseUrl: string;
  /** Room name to join. */
  roomName: string;
  /** Intake identity from the form (Day 19). Sent once with the token request so
      the agent can greet the caller by name; the first name is the only field
      that reaches the room. */
  caller?: VoiceCaller | null;
}

export function createLiveKitTransport(
  options: LiveKitTransportOptions,
): VoiceTransport {
  const { baseUrl, roomName, caller } = options;

    let room: any = null;
    let clientMod: LiveKitClientModule | null = null;
  let active = false;
  let closing = false;
  const framesSent: number[] = [0];
  const framesReceived: number[] = [0];
  let monitorTimer: number | null = null;
  let remoteAudioTimer: number | null = null;
  let remoteAudioCtx: AudioContext | null = null;
  let remoteAudioEl: HTMLMediaElement | null = null;
  let agentPresent = false;
  /* An agent participant alone is NOT proof the assistant is alive: a stale
     job from a previous call can still be listed in the room. Readiness is
     the agent's first audio track (the greeting). If none arrives, say so
     instead of sitting on "Listening" with nobody listening. */
  let readinessTimer: number | null = null;
  const clearReadinessTimer = () => {
    if (readinessTimer !== null) {
      window.clearTimeout(readinessTimer);
      readinessTimer = null;
    }
  };

  const markAgentAudio = () => {
    clearReadinessTimer();
    if (agentPresent) return;
    agentPresent = true;
    setState("listening");
    emit({
      type: "notice",
      line: {
        id: randomId("notice"),
        text: "Connected to EMMA voice agent",
        sample: false,
      },
    });
  };

  const announceAgentReady = () => {
    if (agentPresent) return;
    setState("connecting");
    emit({
      type: "notice",
      line: {
        id: randomId("notice"),
        text: "Connected to EMMA — waiting for the assistant to speak…",
        sample: false,
      },
    });
    if (readinessTimer !== null) return;
    readinessTimer = window.setTimeout(() => {
      readinessTimer = null;
      if (!active || closing || agentPresent) return;
      emit({
        type: "error",
        message:
          "The voice assistant is not responding (no audio). End the call and start a new one.",
      });
      setState("error");
      emit({ type: "ended", reason: "error" });
      stop();
    }, 8000);
  };


  let speechTicks = 0;
  const remoteDiag: RemoteAudioDiagnostics = {
    attached: false,
    playing: false,
    frames: 0,
    peakRms: 0,
    lastRms: 0,
  };
    let micTrack: any = null;
    let ctx: VoiceSessionContext | null = null;

  const emit = (event: VoiceEvent) => ctx?.onEvent(event);

  const setState = (next: VoiceState) => {
    emit({ type: "state", state: next });
  };

  const emitMetrics = (closeCode: number | null) => {
    const metrics: VoiceMetrics = {
      framesIn: framesSent[0],
      framesOut: framesReceived[0],
      rttMsAvg: null,
      closeCode,
    };
    emit({ type: "metrics", metrics });
  };

  const stopMonitor = () => {
    if (monitorTimer !== null) {
      window.clearInterval(monitorTimer);
      monitorTimer = null;
    }
    speechTicks = 0;
  };

  const startMonitor = () => {
    stopMonitor();
    monitorTimer = window.setInterval(() => {
      if (!active) return;
      // Output level comes from the remote audio track via the room.
      emit({ type: "output-level", level: 0 });
    }, 60);
  };

  const cleanupAudio = () => {
    clearReadinessTimer();
    stopMonitor();
    if (remoteAudioTimer !== null) {
      window.clearInterval(remoteAudioTimer);
      remoteAudioTimer = null;
    }
    if (remoteAudioCtx) {
      void remoteAudioCtx.close().catch(() => {});
      remoteAudioCtx = null;
    }
    if (remoteAudioEl) {
      try {
        remoteAudioEl.pause();
        remoteAudioEl.remove();
      } catch {
        /* ignore */
      }
      remoteAudioEl = null;
    }
    micTrack = null;
    room = null;
    clientMod = null;
    emit({ type: "output-level", level: 0 });
  };

  const start = (session: VoiceSessionContext) => {
    ctx = session;
    if (active) return;
    active = true;
    closing = false;
    speechTicks = 0;

    // Dynamically load the LiveKit client so we never hard-depend on it.
    void loadLiveKitClient().then((mod) => {
      clientMod = mod;
      if (mod === null) {
        emit({
          type: "error",
          message: "LiveKit client is not available in this build",
        });
        setState("error");
        emit({ type: "ended", reason: "error" });
        return;
      }
      void connect();
    });
  };

  async function connect() {
    if (!active || closing) return;

    try {
      // Mint a token from the gateway.
      const tokenRes = await fetch(`${baseUrl}/api/voice/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room_name: roomName,
          caller: caller ?? undefined,
        }),
      });

      if (!tokenRes.ok) {
        let detail = "LiveKit token request failed";
        try {
          const body = await tokenRes.json();
          detail = (body as { detail?: string }).detail || detail;
        } catch {
          /* ignore parse errors */
        }
        emit({ type: "error", message: detail });
        setState("error");
        emit({ type: "ended", reason: "error" });
        return;
      }

      const tokenBody = (await tokenRes.json()) as {
        token?: string;
        room?: string;
        practice_id?: string;
        livekit_url?: string | null;
      };
      const token = tokenBody.token;
      if (!token) {
        emit({
          type: "error",
          message: "LiveKit token request returned no token",
        });
        setState("error");
        emit({ type: "ended", reason: "error" });
        return;
      }

      // livekit-client's Room.connect(url, token) requires the server URL
      // explicitly; the JWT does not carry it. The token endpoint returns it
      // (non-secret, e.g. ws://127.0.0.1:7880 for local dev).
      const serverUrl = tokenBody.livekit_url;
      if (!serverUrl) {
        emit({
          type: "error",
          message: "LiveKit token response did not include the server URL",
        });
        setState("error");
        emit({ type: "ended", reason: "error" });
        return;
      }

      const RoomCtor = clientMod!.Room ?? clientMod!.default?.Room;
      room = new RoomCtor();

      // Room event handlers.
      room.on("connected", () => {
        if (!active || closing) return;
        startMonitor();

        // Publish the microphone track straight away: the agent needs it as
        // soon as it joins.
        void publishMic();

        // Day 18E: a room connection is NOT a voice session. Only claim the
        // assistant is ready when an agent participant is actually present —
        // otherwise a caller would sit waiting for a reply that can never come.
        const participants = Array.from(
          (room?.remoteParticipants ?? new Map()).values()
        ) as Array<{ identity?: unknown }>;
        if (participants.some((participant) => isAgentIdentity(participant?.identity))) {
          announceAgentReady();
        } else {
          setState("connecting");
          emit({
            type: "notice",
            line: {
              id: randomId("notice"),
              text: "Connected to EMMA — waiting for the voice assistant to join…",
              sample: false,
            },
          });
        }
      });

      room.on("participantConnected", (participant: { identity?: unknown }) => {
        if (!active || closing) return;
        if (isAgentIdentity(participant?.identity)) announceAgentReady();
      });

      room.on("participantDisconnected", (participant: { identity?: unknown }) => {
        if (!active || closing) return;
        if (!isAgentIdentity(participant?.identity)) return;
        // The assistant left mid-call: say so instead of appearing to listen.
        agentPresent = false;
        setState("error");
        emit({
          type: "error",
          message:
            "The voice assistant disconnected before finishing. End the call and try again.",
        });
      });

      room.on("disconnected", () => {
        if (!active) return;
        cleanupAudio();
        active = false;
        emitMetrics(1000);
        emit({ type: "ended", reason: "user" });
      });

      room.on("trackPublished", (publication: any) => {
        if (!active || closing) return;
        // Remote audio track from the agent — play it back.
        const track = publication.track;
        if (track && track.kind === "audio") {
          void playAudioTrack(track);
        }
      });

      // Tracks the agent published BEFORE this browser joined arrive as
      // `trackSubscribed` (auto-subscription), not `trackPublished`.
      room.on("trackSubscribed", (track: any) => {
        if (!active || closing) return;
        if (track && track.kind === "audio") {
          void playAudioTrack(track);
        }
      });

      room.on("dataReceived", (payload: any) => {
        if (!active) return;
        try {
          const message = JSON.parse(
            typeof payload === "string" ? payload : new TextDecoder().decode(payload),
          );
          handleControlMessage(message);
        } catch {
          /* ignore malformed data messages */
        }
      });

      await room.connect(serverUrl, token);
    } catch {
      if (!active || closing) return;
      emit({
        type: "error",
        message: "LiveKit connection failed",
      });
      setState("error");
      emit({ type: "ended", reason: "error" });
    }
  }

  async function publishMic() {
    if (!room || !clientMod) return;
    try {
      // Request microphone access.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: SAMPLE_RATE,
        },
      });

      const track = stream.getAudioTracks()[0];
      micTrack = track;

      // Publish the track to the room.
      await room.localParticipant.publishTrack(track, {
        name: "microphone",
      });

      // Start the audio-level monitor for barge-in detection.
      startAudioLevelMonitor(track);
    } catch {
      if (!active || closing) return;
      emit({
        type: "error",
        message: "Microphone access declined — please allow microphone permissions",
      });
      setState("error");
      emit({ type: "ended", reason: "error" });
    }
  }

  function startAudioLevelMonitor(track: MediaStreamTrack) {
    stopMonitor();
    const AudioContext:
      | typeof window.AudioContext
      | undefined =
      window.AudioContext ||
      (window as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();
    // Callers may pass either a native MediaStreamTrack or a LiveKit track
    // wrapper (which exposes the native track via `mediaStreamTrack`).
    const native = (track as any)?.mediaStreamTrack ?? track;
    const source = ctx.createMediaStreamSource(new MediaStream([native]));
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);

    monitorTimer = window.setInterval(() => {
      if (!active) return;
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      const level = Math.max(0, Math.min(1, rms * 6));
      emit({ type: "output-level", level: 0 }); // output level is remote audio

      // Barge-in detection: local mic level while agent is speaking.
      if (level >= 0.12) {
        speechTicks += 1;
        if (speechTicks >= 2) {
          speechTicks = 0;
          stopMicTrack();
          if (room && room.localParticipant) {
            void room.localParticipant.publishData(
              new TextEncoder().encode(JSON.stringify({ event: "barge-in" })),
            );
          }
        }
      } else {
        speechTicks = 0;
      }
    }, 60);
  }

  function stopMicTrack() {
    if (micTrack) {
      try {
        micTrack.stop();
      } catch {
        /* ignore */
      }
      micTrack = null;
    }
  }

  async function playAudioTrack(track: any) {
    // LiveKit hands us a RemoteAudioTrack wrapper, not a native
    // MediaStreamTrack, so `new MediaStream([track])` would throw. Use the
    // SDK's `attach()`, which binds the underlying MediaStreamTrack to an
    // <audio> element for us.
    try {
      const el = (typeof track?.attach === "function" ? track.attach() : null) as
        | HTMLMediaElement
        | null;
      if (!el) return;
      el.autoplay = true;
      el.style.display = "none";
      el.dataset.emmaRemoteAudio = "true";
      document.body.appendChild(el);
      remoteAudioEl = el;
      // Count for the end-of-call metrics event; emitting metrics here would
      // replace the "Connected to EMMA voice agent" notice.
      framesReceived[0] += 1;
      markAgentAudio();
      remoteDiag.attached = true;
      publishDiagnostics(remoteDiag);

      // Measured playback evidence: sample the element's audio graph so tests
      // can assert non-zero output rather than just "an element exists".
      const native = track?.mediaStreamTrack ?? track;
      if (native) {
        try {
          const ctx = new AudioContext();
          remoteAudioCtx = ctx;
          const source = ctx.createMediaStreamSource(new MediaStream([native]));
          const analyser = ctx.createAnalyser();
          analyser.fftSize = 1024;
          source.connect(analyser);
          const buffer = new Float32Array(analyser.fftSize);
          remoteAudioTimer = window.setInterval(() => {
            if (!active) return;
            analyser.getFloatTimeDomainData(buffer);
            let sum = 0;
            for (let i = 0; i < buffer.length; i += 1) sum += buffer[i] * buffer[i];
            const rms = Math.sqrt(sum / buffer.length);
            remoteDiag.lastRms = rms;
            if (rms > remoteDiag.peakRms) remoteDiag.peakRms = rms;
            if (rms > 0.001) remoteDiag.frames += 1;
            remoteDiag.playing = !el.paused;
            publishDiagnostics(remoteDiag);
          }, 60);
        } catch {
          /* diagnostics are best-effort; playback must still work */
        }
      }
      try {
        await el.play();
      } catch {
        // Autoplay may be blocked until user interaction; the element is
        // already attached so playback starts on the first gesture.
      }
    } catch {
      /* never let playback errors break the call */
    }
  }

  function handleControlMessage(message: {
    event: string;
    text?: string;
    kind?: string;
    state?: string;
    [key: string]: unknown;
  }) {
    if (!active) return;
    const event = message.event;
    switch (event) {
      case "transcript": {
        if (typeof message.text !== "string" || !message.text.trim()) return;
        const kind: TranscriptKind =
          message.kind === "partial" ? "partial" : "final";
        emit({
          type: "transcript",
          line: {
            id: randomId("t"),
            kind,
            text: message.text,
            sample: false,
          },
        });
        return;
      }
      case "assistant": {
        if (typeof message.text !== "string" || !message.text.trim()) return;
        emit({
          type: "transcript",
          line: {
            id: randomId("a"),
            kind: "agent",
            text: message.text,
            sample: false,
          },
        });
        return;
      }
      case "state": {
        const mapped: Record<string, VoiceState> = {
          listening: "listening",
          thinking: "thinking",
          speaking: "speaking",
        };
        const next = mapped[message.state ?? ""];
        if (next) setState(next);
        return;
      }
      case "barge-in": {
        // Client-side: stop local playback (handled by the room disconnecting
        // the audio track on agent cancel).
        return;
      }
      default:
        return;
    }
  }

  const stop = () => {
    if (!active) {
      emit({ type: "ended", reason: "user" });
      return;
    }
    closing = true;
    stopMicTrack();
    stopMonitor();
    if (room) {
      try {
        room.disconnect();
      } catch {
        /* ignore */
      }
    }
    cleanupAudio();
    active = false;
    emit({ type: "ended", reason: "user" });
  };

  return { mode: "live" as const, label: "EMMA LiveKit", start, stop };
}
