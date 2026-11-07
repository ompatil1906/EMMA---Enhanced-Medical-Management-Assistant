"use client";

import { useEffect, useState } from "react";
import { PhoneOff, Mic, MicOff, Volume2, Grid, UserPlus, Video } from "lucide-react";
import type { VoiceState } from "@/lib/voice/types";
import type { IntakeFormData } from "./intake-form";

interface PhoneCallUIProps {
  voiceState: VoiceState;
  muted: boolean;
  callerData: IntakeFormData;
  outputLevel?: number;
  inputLevel?: number;
  onEnd: () => void;
  onToggleMute: () => void;
}

export function PhoneCallUI({
  voiceState,
  muted,
  callerData,
  outputLevel = 0,
  inputLevel = 0,
  onEnd,
  onToggleMute,
}: PhoneCallUIProps) {
  const [seconds, setSeconds] = useState(0);

  // Live in-call timer
  useEffect(() => {
    if (voiceState === "error") return;
    const interval = setInterval(() => {
      setSeconds((s) => s + 1);
    }, 1000);
    return () => clearInterval(interval);
  }, [voiceState]);

  const formatTimer = (totalSeconds: number) => {
    const mins = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };

  const getStatusSubtitle = () => {
    switch (voiceState) {
      case "connecting":
        return "Calling receptionist...";
      case "listening":
        return `Connected · ${formatTimer(seconds)}`;
      case "thinking":
        return "Processing request...";
      case "speaking":
        return `EMMA Speaking · ${formatTimer(seconds)}`;
      case "error":
        return "Call Disconnected";
      default:
        return formatTimer(seconds);
    }
  };

  // Activity waveform height generator based on volume level
  const activeLevel = voiceState === "speaking" ? outputLevel : inputLevel;
  const barH = (base: number) =>
    Math.max(4, Math.min(22, base + activeLevel * 24));

  return (
    <div className="flex h-full flex-col justify-between bg-gradient-to-b from-[#16171b] via-[#0d0e10] to-[#060608] px-4 pb-7 pt-12 text-white">
      {/* ── Caller & Assistant Header ── */}
      <div className="flex flex-col items-center gap-1 text-center">
        {/* Practice identity */}
        <p className="text-[10px] font-medium tracking-wider uppercase text-white/50">
          St. Jude Medical · EMMA AI
        </p>

        {/* Patient Name */}
        <h3 className="text-[16px] font-semibold tracking-tight text-white">
          {callerData.firstName} {callerData.lastName}
        </h3>

        {/* Status / Timer */}
        <p className="text-[11px] font-medium text-emerald-400">
          {getStatusSubtitle()}
        </p>

        {/* Patient inquiry chip */}
        <div className="mt-1 max-w-[200px] truncate rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-0.5 text-[9px] text-white/50">
          {callerData.reason || "Appointment Booking"}
        </div>
      </div>

      {/* ── Center Avatar with Live Audio Waveforms ── */}
      <div className="relative my-auto flex flex-col items-center justify-center">
        {/* Glowing pulse rings when speaking/listening (subtle monochrome white) */}
        {(voiceState === "speaking" || voiceState === "listening") && (
          <>
            <div className="absolute h-24 w-24 animate-ping rounded-full bg-white/[0.08]" />
            <div className="absolute h-20 w-20 animate-pulse rounded-full bg-white/[0.12]" />
          </>
        )}

        <div className="relative z-10 flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-tr from-[#383a42] via-[#26272e] to-[#1a1b20] shadow-[0_0_25px_rgba(255,255,255,0.08)] ring-1 ring-white/20">
          <span className="text-[18px] font-bold text-white">
            {callerData.firstName ? callerData.firstName[0].toUpperCase() : "E"}
          </span>
        </div>

        {/* Dynamic 5-bar voice waveform (pure white monochrome) */}
        <div className="mt-3 flex items-center gap-1 h-6">
          <div
            className="w-1 rounded-full bg-white/60 transition-all duration-75"
            style={{ height: `${barH(4)}px` }}
          />
          <div
            className="w-1 rounded-full bg-white/80 transition-all duration-75"
            style={{ height: `${barH(10)}px` }}
          />
          <div
            className="w-1 rounded-full bg-white transition-all duration-75"
            style={{ height: `${barH(16)}px` }}
          />
          <div
            className="w-1 rounded-full bg-white/80 transition-all duration-75"
            style={{ height: `${barH(10)}px` }}
          />
          <div
            className="w-1 rounded-full bg-white/60 transition-all duration-75"
            style={{ height: `${barH(4)}px` }}
          />
        </div>
      </div>

      {/* ── iOS In-Call Action Grid (2 rows x 3 cols) ── */}
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-3 gap-y-3 px-2">
          {/* Mute */}
          <button
            type="button"
            onClick={onToggleMute}
            className="flex flex-col items-center gap-1 group"
          >
            <div
              className={`flex h-11 w-11 items-center justify-center rounded-full transition ${
                muted
                  ? "bg-white text-black shadow-lg shadow-white/20"
                  : "bg-white/[0.08] text-white hover:bg-white/[0.15]"
              }`}
            >
              {muted ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </div>
            <span className="text-[9px] text-white/60">
              {muted ? "unmute" : "mute"}
            </span>
          </button>

          {/* Keypad */}
          <div className="flex flex-col items-center gap-1 opacity-50 cursor-not-allowed">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-white/[0.08] text-white">
              <Grid className="h-4 w-4" />
            </div>
            <span className="text-[9px] text-white/60">keypad</span>
          </div>

          {/* Speaker */}
          <div className="flex flex-col items-center gap-1">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-black shadow-lg shadow-white/20">
              <Volume2 className="h-4 w-4" />
            </div>
            <span className="text-[9px] text-white/60">speaker</span>
          </div>

          {/* Add Call */}
          <div className="flex flex-col items-center gap-1 opacity-40 cursor-not-allowed">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-white/[0.08] text-white">
              <UserPlus className="h-4 w-4" />
            </div>
            <span className="text-[9px] text-white/60">add</span>
          </div>

          {/* FaceTime */}
          <div className="flex flex-col items-center gap-1 opacity-40 cursor-not-allowed">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-white/[0.08] text-white">
              <Video className="h-4 w-4" />
            </div>
            <span className="text-[9px] text-white/60">video</span>
          </div>

          {/* End Call (Centered in bottom row or distinct below) */}
          <button
            type="button"
            onClick={onEnd}
            className="flex flex-col items-center gap-1 group"
          >
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-red-600 text-white shadow-[0_4px_16px_rgba(220,38,38,0.5)] transition hover:bg-red-500 hover:scale-105 active:scale-95">
              <PhoneOff className="h-4 w-4" />
            </div>
            <span className="text-[9px] text-red-300 font-medium">end</span>
          </button>
        </div>
      </div>
    </div>
  );
}
