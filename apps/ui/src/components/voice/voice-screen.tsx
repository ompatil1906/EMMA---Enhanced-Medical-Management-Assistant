"use client";

import { useEffect, useRef, useState } from "react";
import { openAudioPipeline, type AudioPipelineHandle } from "@/lib/voice/audio-pipeline";
import { createDemoTransport } from "@/lib/voice/demo-transport";
import { createGatewayTransport } from "@/lib/voice/gateway-transport";
import type {
  TranscriptLine,
  VoiceEvent,
  VoiceMode,
  VoiceNoticeLine,
  VoiceSessionContext,
  VoiceState,
  VoiceTransport,
} from "@/lib/voice/types";
import { Brand } from "@/components/voice/brand";
import { OrbStage } from "@/components/voice/orb-stage";
import { VoiceControls } from "@/components/voice/controls";
import { TranscriptBlock } from "@/components/voice/transcript";

/* The voice screen — the single surface of the app. It owns:
     · the typed state machine (VoiceState),
     · the shared microphone pipeline (level metering + 20 ms PCM frames),
     · one active transport (real gateway or labelled demo),
     · quiet transcript + notice state.

   Honesty invariant: the live gateway is an echo bridge — the UI reports
   "echo verified" and never shows thinking/speaking/agent text in live mode.
   Thinking, speaking and agent lines exist only in demo mode, where every
   line is labelled as a sample and a permanent badge marks demo mode. */

const GATEWAY_URL =
  process.env.NEXT_PUBLIC_VOICE_GATEWAY_URL ?? "ws://localhost:8000/ws/exotel";

const LEVEL_INTERVAL_MS = 80; // ~12.5 Hz — the orb's springs smooth the rest

const STATUS_TEXT: Record<VoiceState, string> = {
  idle: "Ready to talk",
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
  error: "Connection interrupted",
};

function helperFor(state: VoiceState, demo: boolean): string {
  switch (state) {
    case "idle":
      return "Tap to start a conversation";
    case "connecting":
      return demo ? "Preparing demo preview…" : "Reaching the gateway…";
    case "listening":
      return demo
        ? "Sample dialogue in play — your mic drives the orb"
        : "Speak naturally — tap the red button to end";
    case "thinking":
      return "One moment…";
    case "speaking":
      return demo ? "Sample speech — tap skip to interrupt" : "Speaking…";
    case "error":
      return "The gateway could not be reached";
  }
}

export function VoiceScreen() {
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [demo, setDemo] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [notice, setNotice] = useState<VoiceNoticeLine | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [inputLevel, setInputLevel] = useState(0);
  const [outputLevel, setOutputLevel] = useState(0);
  const [muted, setMuted] = useState(false);

  const voiceStateRef = useRef<VoiceState>("idle");
  const busyRef = useRef(false);
  const pipelineRef = useRef<AudioPipelineHandle | null>(null);
  const transportRef = useRef<VoiceTransport | null>(null);
  const levelTimerRef = useRef<number | null>(null);

  const setVoiceStateTracked = (next: VoiceState) => {
    voiceStateRef.current = next;
    setVoiceState(next);
  };

  const stopLevelLoop = () => {
    if (levelTimerRef.current !== null) {
      window.clearInterval(levelTimerRef.current);
      levelTimerRef.current = null;
    }
  };

  const startLevelLoop = () => {
    if (levelTimerRef.current !== null) return;
    levelTimerRef.current = window.setInterval(() => {
      const level = pipelineRef.current?.level() ?? 0;
      setInputLevel(level);
    }, LEVEL_INTERVAL_MS);
  };

  const endSession = async (reason: "user" | "done" | "error") => {
    if (!busyRef.current) return;
    busyRef.current = false;
    stopLevelLoop();

    transportRef.current?.stop();
    transportRef.current = null;

    const pipeline = pipelineRef.current;
    pipelineRef.current = null;
    await pipeline?.close();

    setInputLevel(0);
    setOutputLevel(0);
    setMuted(false);
    setDemo(false);

    setVoiceStateTracked(reason === "error" ? "error" : "idle");
  };

  const onEvent = (event: VoiceEvent) => {
    switch (event.type) {
      case "state": {
        setVoiceStateTracked(event.state);
        break;
      }
      case "transcript": {
        setTranscript((previous) => {
          // A final line supersedes any pending partial transcription.
          const base =
            event.line.kind === "partial"
              ? previous
              : previous.filter((line) => line.kind !== "partial");
          return [...base, event.line].slice(-6);
        });
        break;
      }
      case "notice": {
        setNotice(event.line);
        break;
      }
      case "metrics": {
        setNotice({
          id: "metrics",
          text: `echo verified · ${event.metrics.framesOut} frames · ⌀ ${event.metrics.rttMsAvg ?? "—"} ms`,
          sample: false,
        });
        break;
      }
      case "output-level": {
        setOutputLevel(event.level);
        break;
      }
      case "error": {
        setErrorMessage(event.message);
        break;
      }
      case "ended": {
        void endSession(event.reason);
        break;
      }
    }
  };

  const startSession = async (mode: VoiceMode) => {
    if (busyRef.current) return;
    busyRef.current = true;

    setVoiceStateTracked("connecting");
    setErrorMessage(null);
    setNotice(null);
    setTranscript([]);
    setInputLevel(0);
    setOutputLevel(0);
    setMuted(false);
    setDemo(mode === "demo");

    if (mode === "live") {
      let livePipeline: AudioPipelineHandle;
      try {
        livePipeline = await openAudioPipeline();
      } catch {
        pipelineRef.current = null;
        setErrorMessage(
          "Microphone access was declined — allow the mic and try again",
        );
        busyRef.current = false;
        setVoiceStateTracked("error");
        return;
      }
      pipelineRef.current = livePipeline;
      beginSession(createGatewayTransport({ url: GATEWAY_URL, pipeline: livePipeline }));
    } else {
      let demoPipeline: AudioPipelineHandle | null = null;
      try {
        demoPipeline = await openAudioPipeline();
        pipelineRef.current = demoPipeline;
      } catch {
        // The demo preview runs without the mic too — the orb simply loses
        // mic reactivity, which is fine for a labelled visual preview.
      }
      beginSession(createDemoTransport());
    }
  };

  /** Shared tail of startSession once a transport exists. */
  const beginSession = (transport: VoiceTransport) => {
    transportRef.current = transport;
    startLevelLoop();

    const ctx: VoiceSessionContext = {
      onEvent,
      micLevel: () => pipelineRef.current?.level() ?? 0,
    };
    transport.start(ctx);
  };

  const toggleMute = () => {
    const pipeline = pipelineRef.current;
    if (!pipeline) return;
    const next = !pipeline.isMuted();
    pipeline.setMuted(next);
    setMuted(next);
  };

  // Release mic + socket on unmount (route change / HMR teardown).
  useEffect(() => {
    return () => {
      stopLevelLoop();
      transportRef.current?.stop();
      transportRef.current = null;
      const pipeline = pipelineRef.current;
      pipelineRef.current = null;
      void pipeline?.close();
    };
  }, []);

  const finalNotice: VoiceNoticeLine | null =
    voiceState === "error" && errorMessage
      ? { id: "error", text: errorMessage, sample: false }
      : notice;

  return (
    <main className="emma-screen relative flex min-h-svh flex-col overflow-hidden bg-[#050505] text-foreground">
      <div aria-hidden className="emma-veil pointer-events-none absolute inset-0" />

      <div className="relative z-10 flex min-h-svh flex-col">
        <div
          className="flex justify-center"
          style={{ paddingTop: "max(1.75rem, env(safe-area-inset-top))" }}
        >
          <Brand />
        </div>

        <section className="min-h-0 flex-1">
          <OrbStage
            voiceState={voiceState}
            inputLevel={inputLevel}
            outputLevel={outputLevel}
            statusText={STATUS_TEXT[voiceState]}
            demo={demo}
          />
        </section>

        <div
          className="flex flex-col items-center gap-6"
          style={{ paddingBottom: "max(1.6rem, env(safe-area-inset-bottom))" }}
        >
          <TranscriptBlock
            lines={transcript}
            notice={finalNotice}
            noticeTone={voiceState === "error" ? "error" : "neutral"}
          />

          <VoiceControls
            voiceState={voiceState}
            muted={muted}
            demo={demo}
            onStart={() => void startSession("live")}
            onEnd={() => void endSession("user")}
            onToggleMute={toggleMute}
            onRetry={() => void startSession("live")}
            onDemo={() => void startSession("demo")}
            onInterrupt={() => void endSession("user")}
            helper={helperFor(voiceState, demo)}
          />
        </div>
      </div>
    </main>
  );
}
