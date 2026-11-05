"use client";

import { cn } from "cn";
import type { TranscriptLine, VoiceNoticeLine } from "@/lib/voice/types";

/* Minimal conversation text — no chat window, no bubbles. The most recent
   lines read clearly; older ones fade. Fixed-height slot keeps the orb and
   controls rock-steady while text streams in. */

const MAX_LINES = 4;

const KIND_STYLE: Record<TranscriptLine["kind"], string> = {
  partial: "text-white/45 italic",
  final: "text-white/85",
  agent: "text-white/95",
};

interface TranscriptBlockProps {
  lines: TranscriptLine[];
  notice: VoiceNoticeLine | null;
  noticeTone: "neutral" | "error";
}

export function TranscriptBlock({ lines, notice, noticeTone }: TranscriptBlockProps) {
  const visible = lines.slice(-MAX_LINES);

  return (
    <div
      aria-live="polite"
      className="mx-auto flex w-full max-w-xl flex-col items-center gap-2 px-6"
    >
      <div className="flex min-h-[104px] flex-col items-center justify-end gap-1.5 text-center">
        {visible.map((line, fromEnd) => {
          const age = visible.length - 1 - fromEnd;
          const opacity =
            age === 0 ? "opacity-100" : age === 1 ? "opacity-70" : age === 2 ? "opacity-45" : "opacity-25";
          return (
            <p
              key={line.id}
              className={cn(
                "max-w-full text-balance text-[13.5px] leading-relaxed transition-opacity duration-700",
                KIND_STYLE[line.kind],
                opacity,
              )}
            >
              {line.sample ? (
                <span className="mr-1.5 align-[1px] rounded-full border border-white/12 bg-white/[0.05] px-1.5 py-px text-[8.5px] font-medium tracking-[0.2em] text-white/40 uppercase">
                  Sample
                </span>
              ) : null}
              {line.text}
            </p>
          );
        })}
      </div>

      {notice ? (
        <p
          className={cn(
            "pb-1 text-center text-[11px] leading-relaxed",
            noticeTone === "error" ? "text-red-400/80" : "text-white/40",
          )}
        >
          {notice.text}
        </p>
      ) : null}
    </div>
  );
}
