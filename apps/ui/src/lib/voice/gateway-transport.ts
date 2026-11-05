"use client";

/* Live transport — the browser plays the provider role against the real
   EMMA voice gateway (`/ws/exotel`, simulation mode):

     connect → connected → start{media_format} → media{chunk} every 20 ms
     → echoed media{payload} replies (counted + RTT-measured) → stop → close 1000

   Honesty rules (echo gateway, no AI behind it yet):
   * it never emits thinking/speaking states or agent text;
   * the UI speaks of "echo verified", never of agent speech;
   * transcripts come only from the browser's own speech engine, if present,
     so every transcript line is genuinely what the user said.

   Browser limitation: WebSocket custom headers are impossible, so this works
   against a gateway without WS_AUTH_TOKEN (the default local configuration). */

import type {
  VoiceEvent,
  VoiceMetrics,
  VoiceSessionContext,
  VoiceState,
  VoiceTransport,
} from "@/lib/voice/types";
import type { AudioPipelineHandle } from "@/lib/voice/audio-pipeline";
import { createTranscriber, type TranscriberHandle } from "@/lib/voice/speech";

const FRAME_MS = 20;

const CLOSE_REASONS: Record<number, string> = {
  1003: "Protocol error — the gateway rejected our frames",
  1008: "Gateway refused connection (auth). Browsers cannot send X-WS-Token headers — unset WS_AUTH_TOKEN for local tests",
  1009: "A message exceeded the gateway size limit",
  1011: "Gateway internal error",
  1013: "Gateway session limit reached",
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

export interface GatewayTransportOptions {
  /** WebSocket endpoint, e.g. ws://localhost:8000/ws/exotel (no query params). */
  url: string;
  pipeline: AudioPipelineHandle;
}

export function createGatewayTransport(
  options: GatewayTransportOptions,
): VoiceTransport {
  const { url, pipeline } = options;

  let ws: WebSocket | null = null;
  let active = false;
  let closing = false;
  let framesSent = 0;
  let framesReceived = 0;
  let rttSum = 0;
  let rttCount = 0;
  /** FIFO of perf.now()-style timestamps, one per in-flight echo. */
  const sentAt: number[] = [];
  let unsubscribeFrames: (() => void) | null = null;
  let offMute: (() => void) | null = null;
  let transcriber: TranscriberHandle | null = null;
  let ctx: VoiceSessionContext | null = null;

  const emit = (event: VoiceEvent) => ctx?.onEvent(event);

  const setState = (next: VoiceState) => {
    emit({ type: "state", state: next });
  };

  const emitMetrics = (closeCode: number | null) => {
    const metrics: VoiceMetrics = {
      framesIn: framesSent,
      framesOut: framesReceived,
      rttMsAvg: rttCount > 0 ? Math.round(rttSum / rttCount) : null,
      closeCode,
    };
    emit({ type: "metrics", metrics });
  };

  const cleanupAudio = () => {
    unsubscribeFrames?.();
    unsubscribeFrames = null;
    offMute?.();
    offMute = null;
    transcriber?.stop();
    transcriber = null;
  };

  const start = (session: VoiceSessionContext) => {
    ctx = session;
    if (active) return;
    active = true;
    closing = false;

    const urlWithRate = url.includes("?")
      ? `${url}&sample-rate=16000`
      : `${url}?sample-rate=16000`;

    let socket: WebSocket;
    try {
      socket = new WebSocket(urlWithRate);
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
            sample_rate: 16000,
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
        sentAt.push(performance.now());
        socket.send(
          JSON.stringify({ event: "media", chunk, timestamp }),
        );
      });

      offMute = pipeline.onMuteChange((muted) => {
        if (muted) {
          transcriber?.stop();
          transcriber = null;
        } else if (transcriber === null && active) {
          transcriber = createTranscriber(onTranscript);
        }
      });

      // Best-effort transcription of the user's own speech (browser engine).
      transcriber = createTranscriber(onTranscript);
      if (transcriber === null) {
        emit({
          type: "notice",
          line: {
            id: randomId("notice"),
            text: "Live transcription is not available in this browser — audio is still streaming and echoed",
            sample: false,
          },
        });
      }
    };

    socket.onmessage = (raw) => {
      if (!active) return;
      let reply: unknown;
      try {
        reply = JSON.parse(String(raw.data)) as unknown;
      } catch {
        return;
      }
      const media = reply as { event?: string; media?: { payload?: string } };
      if (media.event === "media" && media.media?.payload) {
        framesReceived += 1;
        const startAt = sentAt.shift();
        if (startAt !== undefined) {
          rttSum += performance.now() - startAt;
          rttCount += 1;
        }
        if (framesReceived % 25 === 0) emitMetrics(null);
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
            text: `Stream ended — ${framesSent} frames echoed, closed cleanly (1000)`,
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

  const onTranscript = (text: string, final: boolean) => {
    if (!active) return;
    emit({
      type: "transcript",
      line: {
        id: randomId("t"),
        kind: final ? "final" : "partial",
        text,
        sample: false,
      },
    });
  };

  const stop = () => {
    if (!active) {
      // Never opened / already gone: just make sure the UI unblocks.
      emit({ type: "ended", reason: "user" });
      return;
    }
    closing = true;
    pipeline.setMuted(false);
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
