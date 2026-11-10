"use client";

/* Live transport — the browser plays the provider role against the real
   EMMA voice gateway (`/ws/exotel`, speech pipeline):

     connect → connected → start{media_format} → media{chunk} every 20 ms
     → Azure STT / Gemini / controller / Azure TTS
     → control events (transcript/assistant/state/barge-in) + media{payload}
     → PCM playback → stop → close 1000

   The gateway only emits control events when the connection opts in with
   `?control-events=1`; the verified Exotel `media` envelope is unchanged.

   Barge-in has two layers:
   * server: the gateway cancels TTS when genuine caller energy arrives while
     speaking (energy-gated, so a continuously streaming mic cannot interrupt
     every reply);
   * browser: this transport stops local playback and sends `barge-in` as soon
     as the mic level indicates the caller is talking over the assistant.

   Browser limitation: WebSocket custom headers are impossible, so this works
   against a gateway without WS_AUTH_TOKEN (the default local configuration). */

import type {
  TranscriptKind,
  VoiceEvent,
  VoiceMetrics,
  VoiceSessionContext,
  VoiceState,
  VoiceTransport,
} from "@/lib/voice/types";
import type { AudioPipelineHandle } from "@/lib/voice/audio-pipeline";
import { createPcmPlayer, type PcmPlayerHandle } from "@/lib/voice/pcm-player";

const FRAME_MS = 20;
const SAMPLE_RATE = 16_000;
const BARGE_IN_LEVEL = 0.40;
const BARGE_IN_TICKS = 4;

const CLOSE_REASONS: Record<number, string> = {
  1003: "Protocol error — the gateway rejected our frames",
  1008: "Gateway refused connection (auth). Browsers cannot send X-WS-Token headers — unset WS_AUTH_TOKEN for local tests",
  1009: "A message exceeded the gateway size limit",
  1011: "Gateway internal error",
  1013: "Gateway session limit reached",
};

const GATEWAY_STATES: Record<string, VoiceState> = {
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
};

function int16ToBase64(frame: Int16Array): string {
  const bytes = new Uint8Array(frame.buffer, frame.byteOffset, frame.byteLength);
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function randomId(prefix: string): string {
  const uuid =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2);
  return `${prefix}-${uuid}`;
}

interface GatewayMessage {
  event?: string;
  media?: { payload?: string };
  kind?: string;
  text?: string;
  state?: string;
}

export interface GatewayTransportOptions {
  /** WebSocket endpoint, e.g. ws://localhost:8000/ws/exotel (no query params). */
  url: string;
  pipeline: AudioPipelineHandle;
  /** Intake first name (Day 19) so EMMA greets the caller by name. Only the
      first name is sent, as a query parameter; nothing else from the form. */
  callerFirstName?: string;
}

export function createGatewayTransport(
  options: GatewayTransportOptions,
): VoiceTransport {
  const { url, pipeline, callerFirstName } = options;

  let ws: WebSocket | null = null;
  let player: PcmPlayerHandle | null = null;
  let active = false;
  let closing = false;
  let framesSent = 0;
  let framesReceived = 0;
  let monitorTimer: number | null = null;
  let speechTicks = 0;
  let unsubscribeFrames: (() => void) | null = null;
  let ctx: VoiceSessionContext | null = null;

  const emit = (event: VoiceEvent) => ctx?.onEvent(event);

  const setState = (next: VoiceState) => {
    emit({ type: "state", state: next });
  };

  const emitMetrics = (closeCode: number | null) => {
    const metrics: VoiceMetrics = {
      framesIn: framesSent,
      framesOut: framesReceived,
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

  const startMonitor = (socket: WebSocket) => {
    stopMonitor();
    monitorTimer = window.setInterval(() => {
      if (!active) return;
      const currentPlayer = player;
      if (currentPlayer === null) return;
      emit({ type: "output-level", level: currentPlayer.level() });

      if (!currentPlayer.isPlaying()) {
        speechTicks = 0;
        return;
      }
      if (pipeline.level() >= BARGE_IN_LEVEL) {
        speechTicks += 1;
        if (speechTicks >= BARGE_IN_TICKS) {
          speechTicks = 0;
          currentPlayer.stop();
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ event: "barge-in" }));
          }
        }
      } else {
        speechTicks = 0;
      }
    }, 60);
  };

  const cleanupAudio = () => {
    stopMonitor();
    unsubscribeFrames?.();
    unsubscribeFrames = null;
    const currentPlayer = player;
    player = null;
    void currentPlayer?.close();
    emit({ type: "output-level", level: 0 });
  };

  const start = (session: VoiceSessionContext) => {
    ctx = session;
    if (active) return;
    active = true;
    closing = false;
    speechTicks = 0;
    player = createPcmPlayer({
      sampleRate: SAMPLE_RATE,
      audioContext: pipeline.audioContext,
    });

    const separator = url.includes("?") ? "&" : "?";
    const nameParam = callerFirstName?.trim()
      ? `&caller-first-name=${encodeURIComponent(callerFirstName.trim())}`
      : "";
    const urlWithOptions = `${url}${separator}sample-rate=${SAMPLE_RATE}&control-events=1${nameParam}`;

    let socket: WebSocket;
    try {
      socket = new WebSocket(urlWithOptions);
    } catch {
      active = false;
      emit({ type: "error", message: "Invalid gateway URL — check configuration" });
      emit({ type: "ended", reason: "error" });
      return;
    }
    ws = socket;
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      if (!active) return;
      emit({
        type: "notice",
        line: {
          id: randomId("notice"),
          text: "Gateway connected — streaming microphone at 16 kHz",
          sample: false,
        },
      });

      socket.send(JSON.stringify({ event: "connected" }));
      socket.send(
        JSON.stringify({
          event: "start",
          stream_sid: randomId("ui-stream"),
          call_sid: randomId("ui-call"),
          media_format: {
            encoding: "PCM",
            sample_rate: SAMPLE_RATE,
            bit_rate: 256000,
          },
        }),
      );

      setState("listening");

      unsubscribeFrames = pipeline.subscribe((frame) => {
        if (closing || socket.readyState !== WebSocket.OPEN) return;
        const chunk = int16ToBase64(frame);
        const timestamp = String(framesSent * FRAME_MS);
        framesSent += 1;
        socket.send(JSON.stringify({ event: "media", chunk, timestamp }));
      });

      startMonitor(socket);
    };

    socket.onmessage = (raw) => {
      if (!active) return;
      let reply: GatewayMessage;
      try {
        reply = JSON.parse(String(raw.data)) as GatewayMessage;
      } catch {
        return;
      }

      switch (reply.event) {
        case "media": {
          const payload = reply.media?.payload;
          if (!payload) return;
          framesReceived += 1;
          player?.playBase64(payload);
          if (framesReceived % 25 === 0) emitMetrics(null);
          return;
        }
        case "transcript": {
          if (typeof reply.text !== "string" || !reply.text.trim()) return;
          const kind: TranscriptKind =
            reply.kind === "partial" ? "partial" : "final";
          emit({
            type: "transcript",
            line: {
              id: randomId("t"),
              kind,
              text: reply.text,
              sample: false,
            },
          });
          return;
        }
        case "assistant": {
          if (typeof reply.text !== "string" || !reply.text.trim()) return;
          emit({
            type: "transcript",
            line: {
              id: randomId("a"),
              kind: "agent",
              text: reply.text,
              sample: false,
            },
          });
          return;
        }
        case "state": {
          const mapped = reply.state ? GATEWAY_STATES[reply.state] : undefined;
          if (mapped) setState(mapped);
          return;
        }
        case "barge-in": {
          player?.stop();
          return;
        }
        default:
          return;
      }
    };

    socket.onerror = () => {
      // A close frame always follows; keep the error text generic.
      emit({ type: "error", message: "Gateway connection failed" });
      setState("error");
      cleanupAudio();
    };

    socket.onclose = (event) => {
      const code = event.code;
      const wasIntentional = closing;
      cleanupAudio();
      active = false;
      emitMetrics(code);
      if (wasIntentional) {
        emit({
          type: "notice",
          line: {
            id: randomId("notice"),
            text: `Stream ended — ${framesReceived} assistant audio frames, closed cleanly (1000)`,
            sample: false,
          },
        });
        emit({ type: "ended", reason: "user" });
        return;
      }
      if (code === 1000) {
        emit({ type: "ended", reason: "user" });
        return;
      }
      emit({
        type: "error",
        message: CLOSE_REASONS[code] ?? `Connection lost (code ${code})`,
      });
      emit({ type: "ended", reason: "error" });
    };
  };

  const stop = () => {
    if (!active) {
      // Never opened / already gone: just make sure the UI unblocks.
      emit({ type: "ended", reason: "user" });
      return;
    }
    closing = true;
    try {
      ws?.send(JSON.stringify({ event: "stop", reason: "stopped" }));
      ws?.close(1000, "client stopped");
    } catch {
      /* socket already going away */
    }
    cleanupAudio();
  };

  return { mode: "live", label: "EMMA gateway", start, stop };
}
