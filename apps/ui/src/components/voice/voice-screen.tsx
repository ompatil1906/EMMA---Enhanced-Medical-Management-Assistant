"use client";

import { useEffect, useRef, useState } from "react";
import { openAudioPipeline, type AudioPipelineHandle } from "@/lib/voice/audio-pipeline";
import { createGatewayTransport } from "@/lib/voice/gateway-transport";
import { createLiveKitTransport } from "@/lib/voice/livekit-transport";
import { createDemoTransport } from "@/lib/voice/demo-transport";
import type {
  TranscriptLine,
  VoiceEvent,
  VoiceMode,
  VoiceNoticeLine,
  VoiceSessionContext,
  VoiceState,
  VoiceTransport,
} from "@/lib/voice/types";
import { PhoneMockupCard } from "@/components/ui/phone-mockup";
import { IntakeForm, type IntakeFormData } from "@/components/voice/intake-form";
import { PhoneCallUI } from "@/components/voice/phone-call-ui";
import { OrbStage } from "@/components/voice/orb-stage";
import { TranscriptBlock } from "@/components/voice/transcript";
import { Mic, MicOff, PhoneOff, RefreshCcw } from "lucide-react";
import Image from "next/image";
import { cn } from "@/lib/utils";

const GATEWAY_URL =
  process.env.NEXT_PUBLIC_VOICE_GATEWAY_URL ?? "ws://localhost:8000/ws/exotel";

const VOICE_TRANSPORT =
  process.env.NEXT_PUBLIC_VOICE_TRANSPORT ?? "websocket";

/* LiveKit dispatches one agent job per ROOM. Reusing a fixed room name meant
   every call after the first joined a room that already had (or had had) an
   agent and was never dispatched again — the caller sat on "Listening" with no
   assistant. Each call now gets its own room (also a session-isolation win). */
const LIVEKIT_ROOM_PREFIX = "emma-voice";

function newRoomName(): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
      : Math.random().toString(36).slice(2, 14);
  return `${LIVEKIT_ROOM_PREFIX}-${suffix}`;
}

const LEVEL_INTERVAL_MS = 80;

type PagePhase = "form" | "calling";

export function VoiceScreen() {
  /* ── voice engine state ─────────────────────────────────────────── */
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [demo, setDemo] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [notice, setNotice] = useState<VoiceNoticeLine | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [inputLevel, setInputLevel] = useState(0);
  const [outputLevel, setOutputLevel] = useState(0);
  const [muted, setMuted] = useState(false);

  /* ── page phase ─────────────────────────────────────────────────── */
  const [phase, setPhase] = useState<PagePhase>("form");
  const [callerData, setCallerData] = useState<IntakeFormData>({
    firstName: "",
    lastName: "",
    email: "",
    mobile: "",
    reason: "Book Appointment",
  });

  /* ── refs ───────────────────────────────────────────────────────── */
  const voiceStateRef = useRef<VoiceState>("idle");
  const busyRef = useRef(false);
  const pipelineRef = useRef<AudioPipelineHandle | null>(null);
  const transportRef = useRef<VoiceTransport | null>(null);
  const levelTimerRef = useRef<number | null>(null);
  /* Intake identity must reach the transport synchronously: `setCallerData`
     only lands on the next render, and the session starts immediately. */
  const callerRef = useRef<IntakeFormData | null>(null);

  /* ── helpers ────────────────────────────────────────────────────── */
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
      setInputLevel(pipelineRef.current?.level() ?? 0);
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
    setPhase("form");
  };

  const onEvent = (event: VoiceEvent) => {
    switch (event.type) {
      case "state":
        setVoiceStateTracked(event.state);
        break;
      case "transcript":
        setTranscript((prev) => {
          if (event.line.kind === "partial") {
            const last = prev[prev.length - 1];
            if (last && last.kind === "partial") {
              return [...prev.slice(0, -1), event.line];
            }
            return [...prev, event.line].slice(-6);
          }
          const withoutPartials = prev.filter((l) => l.kind !== "partial");
          return [...withoutPartials, event.line].slice(-6);
        });
        break;
      case "notice":
        setNotice(event.line);
        break;
      case "metrics":
        setNotice({
          id: "metrics",
          text: `live audio · ${event.metrics.framesOut} assistant frames`,
          sample: false,
        });
        break;
      case "output-level":
        setOutputLevel(event.level);
        break;
      case "error":
        setErrorMessage(event.message);
        break;
      case "ended":
        void endSession(event.reason);
        break;
    }
  };

  const beginSession = (transport: VoiceTransport) => {
    transportRef.current = transport;
    startLevelLoop();
    const ctx: VoiceSessionContext = {
      onEvent,
      micLevel: () => pipelineRef.current?.level() ?? 0,
    };
    transport.start(ctx);
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
        setErrorMessage("Microphone access declined — please allow microphone permissions and tap retry");
        busyRef.current = false;
        setVoiceStateTracked("error");
        return;
      }
      pipelineRef.current = livePipeline;

      const caller = callerRef.current;
      let transport: VoiceTransport;
      if (VOICE_TRANSPORT === "livekit") {
        transport = createLiveKitTransport({
          baseUrl: GATEWAY_URL.replace(/^ws/, "http").replace(/^wss/, "https"),
          roomName: newRoomName(),
          caller,
        });
      } else {
        transport = createGatewayTransport({
          url: GATEWAY_URL,
          pipeline: livePipeline,
          callerFirstName: caller?.firstName,
        });
      }
      beginSession(transport);
    } else {
      try {
        pipelineRef.current = await openAudioPipeline();
      } catch {
        // demo preview runs without mic
      }
      beginSession(createDemoTransport());
    }
  };

  const toggleMute = () => {
    const pipeline = pipelineRef.current;
    if (!pipeline) return;
    const next = !pipeline.isMuted();
    pipeline.setMuted(next);
    setMuted(next);
  };

  /* ── user clicks "Call Now" on intake form ──────────────────────── */
  const handleCallNow = (data: IntakeFormData) => {
    callerRef.current = data;
    setCallerData(data);
    setPhase("calling");
    void startSession("live");
  };

  /* ── teardown on unmount ────────────────────────────────────────── */
  useEffect(() => {
    return () => {
      stopLevelLoop();
      transportRef.current?.stop();
      transportRef.current = null;
      const p = pipelineRef.current;
      pipelineRef.current = null;
      void p?.close();
    };
  }, []);

  const finalNotice: VoiceNoticeLine | null =
    voiceState === "error" && errorMessage
      ? { id: "error", text: errorMessage, sample: false }
      : notice;

  const isCalling = phase === "calling";

  const STATUS_TEXT: Record<VoiceState, string> = {
    idle: "Ready",
    connecting: "Connecting…",
    listening: "Listening",
    thinking: "Processing",
    speaking: "Speaking",
    error: "Disconnected",
  };

  return (
    <main className="relative flex min-h-svh w-full flex-col overflow-hidden bg-[#050507] text-white">
      {/* Background subtle obsidian gradient (pure neutral dark, no blue) */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(255,255,255,0.03),transparent_70%)]"
      />

      {/* ── Top Bar: Logo on Left ──────────────────────────────────── */}
      <header
        className="relative z-20 flex w-full items-center justify-between px-6 py-4 md:px-10"
        style={{ paddingTop: "max(1.25rem, env(safe-area-inset-top))" }}
      >
        {/* Left: Brand */}
        <div className="flex select-none items-center">
          <Image
            src="/ql-logo.svg"
            alt="QuantumLoopAI"
            width={154}
            height={22}
            priority
            draggable={false}
            className="h-[20px] w-auto opacity-95"
          />
        </div>

        {/* Right: Quick demo preview button if user wants to test speech simulation */}
        {!isCalling && (
          <button
            onClick={() => {
              setPhase("calling");
              void startSession("demo");
            }}
            className="rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-1.5 text-[11px] font-medium text-white/60 transition hover:border-white/20 hover:bg-white/[0.08] hover:text-white"
          >
            Preview Demo
          </button>
        )}
      </header>

      {/* ── Main Viewport Area ─────────────────────────────────────── */}
      <div className="relative z-10 flex flex-1 flex-col items-center justify-center p-4">

        {/* ── CENTER: Circular Voice Orb Stage (Visible in Calling Phase) ── */}
        <div
          className={cn(
            "flex flex-col items-center justify-center transition-all duration-700 ease-out",
            isCalling
              ? "opacity-100 scale-100 pointer-events-auto"
              : "opacity-0 scale-90 pointer-events-none absolute"
          )}
        >
          {/* Volumetric Orb */}
          <div className="relative h-[320px] w-[320px] md:h-[380px] md:w-[380px] flex items-center justify-center">
            <OrbStage
              voiceState={voiceState}
              inputLevel={inputLevel}
              outputLevel={outputLevel}
              statusText={STATUS_TEXT[voiceState]}
              demo={demo}
            />
          </div>

          {/* Transcript Dialogue */}
          <div className="mt-4 w-full max-w-md px-4">
            <TranscriptBlock
              lines={transcript}
              notice={finalNotice}
              noticeTone={voiceState === "error" ? "error" : "neutral"}
            />
          </div>

          {/* Center Call Controls */}
          <div className="mt-6 flex items-center gap-4">
            {/* Mute toggle */}
            <button
              onClick={toggleMute}
              className={cn(
                "flex h-12 w-12 items-center justify-center rounded-full border transition active:scale-95",
                muted
                  ? "border-amber-400/30 bg-amber-500/20 text-amber-200"
                  : "border-white/15 bg-white/[0.06] text-white hover:bg-white/[0.12]"
              )}
              title={muted ? "Unmute microphone" : "Mute microphone"}
            >
              {muted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
            </button>

            {/* End Call Button */}
            <button
              onClick={() => void endSession("user")}
              className="flex h-12 items-center gap-2 rounded-full border border-red-500/30 bg-red-600/90 px-6 text-[12px] font-semibold text-white shadow-[0_4px_20px_rgba(220,38,38,0.4)] transition hover:bg-red-500 hover:scale-105 active:scale-95"
            >
              <PhoneOff className="h-4 w-4" />
              <span>End Conversation</span>
            </button>

            {/* Retry if error */}
            {voiceState === "error" && (
              <button
                onClick={() => void startSession("live")}
                className="flex h-12 w-12 items-center justify-center rounded-full border border-white/15 bg-white/[0.06] text-white hover:bg-white/[0.12]"
                title="Retry call"
              >
                <RefreshCcw className="h-5 w-5" />
              </button>
            )}
          </div>
        </div>

        {/* ── PHONE MOCKUP (Center in form phase, Bottom-Right in calling phase) ── */}
        <div
          className={cn(
            "transition-all duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]",
            isCalling
              ? "fixed bottom-6 right-6 z-40 w-[240px] md:bottom-8 md:right-8 md:w-[260px] drop-shadow-2xl"
              : "relative w-[305px] sm:w-[325px]"
          )}
        >
          <PhoneMockupCard
            showDynamicIsland
            className={cn(
              "w-full transition-shadow duration-500",
              isCalling && "shadow-[0_25px_60px_-10px_rgba(0,0,0,0.95),0_0_30px_rgba(255,255,255,0.06)]"
            )}
          >
            {!isCalling ? (
              <IntakeForm onCallNow={handleCallNow} />
            ) : (
              <PhoneCallUI
                voiceState={voiceState}
                muted={muted}
                callerData={callerData}
                outputLevel={outputLevel}
                inputLevel={inputLevel}
                onEnd={() => void endSession("user")}
                onToggleMute={toggleMute}
              />
            )}
          </PhoneMockupCard>
        </div>

        {/* Form helper note */}
        {!isCalling && (
          <p className="mt-4 text-center text-[11px] text-white/40 select-none">
            Enter your details above and tap{" "}
            <span className="font-semibold text-white/80">Call EMMA Reception</span> to start
          </p>
        )}
      </div>
    </main>
  );
}
