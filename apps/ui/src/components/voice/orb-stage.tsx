"use client";

import { Shdr21, type Shdr21Props } from "@/components/ui/shdr-21";
import { cn } from "cn";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { VoiceState } from "@/lib/voice/types";

/* The centre of the screen. The Shdr21 volumetric cloud is driven by the
   voice-state machine + real audio:

   voice state → engine state
     idle / connecting → "idle"   (engine's own calm synthesis)
     listening         → engine "idle" with volumes.input = real mic RMS
     thinking          → "thinking"
     speaking          → "speaking" with volumes.output = speech envelope
     error             → engine paused (animation stops)

   Sizing is quantized (280/340/420/480) so a resize never re-creates the
   WebGL canvas on every pixel of a window drag; the halos + status sit in
   this wrapper so the orb file stays pristine. */

const SIZE_BUCKETS = [280, 340, 420, 480] as const;

function bucketFor(width: number, height: number): number {
  const target = Math.min(width * 0.82, height * 0.6, 480);
  let chosen: number = SIZE_BUCKETS[0];
  for (const bucket of SIZE_BUCKETS) {
    if (bucket <= target) chosen = bucket;
  }
  return chosen;
}

interface OrbStageProps {
  voiceState: VoiceState;
  inputLevel: number;
  outputLevel: number;
  statusText: string;
  demo: boolean;
}

export function OrbStage({
  voiceState,
  inputLevel,
  outputLevel,
  statusText,
  demo,
}: OrbStageProps) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<number>(280);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      setSize(bucketFor(box.width, box.height));
    });
    observer.observe(stage);
    return () => {
      observer.disconnect();
    };
  }, []);

  let engineState: Shdr21Props["state"] = "idle";
  let paused = false;
  let volumes: Shdr21Props["volumes"] | undefined;

  switch (voiceState) {
    case "listening": {
      engineState = "idle";
      volumes = { input: inputLevel };
      break;
    }
    case "speaking": {
      engineState = "speaking";
      volumes = { output: outputLevel };
      break;
    }
    case "thinking": {
      engineState = "thinking";
      break;
    }
    case "error": {
      paused = true;
      break;
    }
    default: {
      engineState = "idle";
    }
  }

  const activeGlow =
    voiceState === "listening" || voiceState === "speaking"
      ? 1 - (voiceState === "listening" ? inputLevel : outputLevel) * 0.35
      : voiceState === "error"
        ? 0.35
        : 0.8;

  const haloStyle: CSSProperties = {
    width: size * 1.62,
    height: size * 1.62,
    opacity: activeGlow,
  };

  return (
    <div
      ref={stageRef}
      className="relative flex h-full w-full flex-col items-center justify-center"
    >
      <div className="relative grid place-items-center">
        {/* Soft diffused light behind the cloud — two restrained layers. */}
        <div
          aria-hidden
          className={cn(
            "emma-halo pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full",
          )}
          style={{
            ...haloStyle,
            background:
              "radial-gradient(closest-side, rgba(255,213,163,0.10), rgba(255,213,163,0.03) 55%, transparent 72%)",
          }}
        />
        <div
          aria-hidden
          className="emma-halo-slow pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{
            width: size * 2.1,
            height: size * 2.1,
            background:
              "radial-gradient(closest-side, rgba(90,105,190,0.07), transparent 70%)",
          }}
        />

        <Shdr21
          state={engineState}
          size={size}
          volumes={volumes}
          paused={paused}
          ariaLabel={`EMMA voice orb — ${statusText}`}
          className="relative"
        />
      </div>

      <div className="mt-9 flex items-center gap-3">
        <p
          aria-live="polite"
          className={cn(
            "text-[13px] font-medium tracking-[0.22em] uppercase transition-colors duration-500",
            voiceState === "error" ? "text-red-400/90" : "text-white/60",
          )}
        >
          {statusText}
        </p>
        {demo ? (
          <span className="rounded-full border border-white/12 bg-white/[0.04] px-2.5 py-1 text-[9px] font-medium tracking-[0.28em] text-white/50 uppercase">
            Demo
          </span>
        ) : null}
      </div>
    </div>
  );
}
