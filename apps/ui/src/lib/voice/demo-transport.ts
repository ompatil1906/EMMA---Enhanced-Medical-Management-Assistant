"use client";

/* Demo transport — a visual pre-viewer ONLY.

   Walks the listening → thinking → speaking cycle with clearly-labelled
   SAMPLE dialogue so the static states of the interface can be seen without
   a running gateway. It does NOT require the microphone and it never fakes a
   real call or an AI conversation: every line it emits is flagged as a sample
   and the UI keeps a permanent "DEMO MODE" badge while it runs. */

import type {
  VoiceEvent,
  VoiceSessionContext,
  VoiceTransport,
} from "@/lib/voice/types";

const SAMPLE_LINES = [
  {
    final: "I'd like to request an appointment.",
    partial: "I'd like to request…",
  },
  {
    final: "Can we move my cardiology follow-up to next Tuesday?",
    partial: "Can we move my cardiology follow-up…",
  },
];

const SAMPLE_AGENT =
  "Hello, you're speaking with EMMA. How can I help you today?";

function id(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2)}`;
}

export function createDemoTransport(): VoiceTransport {
  let timers: number[] = [];
  let envelopeTimer: number | null = null;
  let retired = false;

  const clearAll = () => {
    for (const timer of timers) window.clearTimeout(timer);
    timers = [];
    if (envelopeTimer !== null) {
      window.clearInterval(envelopeTimer);
      envelopeTimer = null;
    }
  };

  const later = (fn: () => void, ms: number) => {
    timers.push(window.setTimeout(fn, ms));
  };

  const start = (ctx: VoiceSessionContext) => {
    if (retired) return;
    const emit = (event: VoiceEvent) => {
      if (!retired) ctx.onEvent(event);
    };

    clearAll();

    emit({
      type: "notice",
      line: {
        id: id("notice"),
        text: "Demo preview — sample dialogue, no call and no AI on the line",
        sample: true,
      },
    });

    later(() => emit({ type: "state", state: "listening" }), 700);

    const dialogue = SAMPLE_LINES[
      Math.floor(Math.random() * SAMPLE_LINES.length)
    ] ?? SAMPLE_LINES[0];

    later(() => {
      emit({
        type: "transcript",
        line: { id: id("t"), kind: "partial", text: dialogue.partial, sample: true },
      });
    }, 1200);
    later(() => {
      emit({
        type: "transcript",
        line: { id: id("t"), kind: "final", text: dialogue.final, sample: true },
      });
    }, 2600);

    later(() => emit({ type: "state", state: "thinking" }), 3400);
    later(() => emit({ type: "state", state: "speaking" }), 4700);
    later(() => {
      emit({
        type: "transcript",
        line: {
          id: id("t"),
          kind: "agent",
          text: SAMPLE_AGENT,
          sample: true,
        },
      });

      // Synthetic envelope so the orb visibly "talks".
      const t0 = performance.now();
      envelopeTimer = window.setInterval(() => {
        const t = (performance.now() - t0) / 1000;
        const level =
          0.42 +
          0.3 * Math.sin(t * 5.1) +
          0.18 * Math.sin(t * 11.3 + 1.4) +
          0.08 * Math.sin(t * 23.7);
        emit({
          type: "output-level",
          level: Math.max(0, Math.min(1, level)),
        });
      }, 80);
    }, 5000);

    later(() => {
      if (envelopeTimer !== null) {
        window.clearInterval(envelopeTimer);
        envelopeTimer = null;
      }
      emit({ type: "output-level", level: 0 });
      emit({
        type: "notice",
        line: {
          id: id("notice"),
          text: "Demo preview finished — start a real session with the gateway when ready",
          sample: false,
        },
      });
      emit({ type: "ended", reason: "done" });
    }, 8600);
  };

  const stop = () => {
    clearAll();
    retired = true;
  };

  return { mode: "demo", label: "Demo preview", start, stop };
}
