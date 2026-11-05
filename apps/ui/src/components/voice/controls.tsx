"use client";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  LoaderCircle,
  Mic,
  MicOff,
  PhoneOff,
  RefreshCcw,
  SkipForward,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "cn";
import type { VoiceState } from "@/lib/voice/types";

/* Minimal control area: one large circular call button, small companions for
   mute / demo interruption, a retry action, and a quiet helper line. */

interface ControlsProps {
  voiceState: VoiceState;
  muted: boolean;
  demo: boolean;
  onStart(): void;
  onEnd(): void;
  onToggleMute(): void;
  onRetry(): void;
  onDemo(): void;
  onInterrupt(): void;
  helper: string;
}

const PRIMARY_BASE =
  "size-16 rounded-full border transition-all duration-300 active:scale-95";

const SECONDARY_BASE =
  "size-11 rounded-full border transition-all duration-300 active:scale-95";

function IconButton(props: {
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  ariaPressed?: boolean;
  /** Tooltip + aria label live on the trigger; the icon is decorative. */
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            aria-label={props.label}
            onClick={props.onClick}
            disabled={props.disabled}
            aria-pressed={props.ariaPressed}
            className={props.className}
          >
            {props.children}
          </Button>
        }
      />
      <TooltipContent>{props.label}</TooltipContent>
    </Tooltip>
  );
}

export function VoiceControls({
  voiceState,
  muted,
  demo,
  onStart,
  onEnd,
  onToggleMute,
  onRetry,
  onDemo,
  onInterrupt,
  helper,
}: ControlsProps) {
  const showMute = voiceState === "listening";
  const showPrimary =
    voiceState === "listening" ||
    voiceState === "speaking" ||
    voiceState === "thinking";

  return (
    <TooltipProvider>
      <div className="flex flex-col items-center gap-4">
        <div className="flex items-center gap-5">
          {voiceState === "idle" || voiceState === "connecting" ? (
            <IconButton
              label={voiceState === "idle" ? "Start conversation" : "Connecting…"}
              onClick={voiceState === "idle" ? onStart : undefined}
              disabled={voiceState !== "idle"}
              className={cn(
                PRIMARY_BASE,
                "border-white/12 bg-white/[0.07] text-white/85 hover:bg-white/[0.12]",
              )}
            >
              {voiceState === "idle" ? (
                <Mic className="size-6" aria-hidden />
              ) : (
                <LoaderCircle className="size-6 animate-spin" aria-hidden />
              )}
            </IconButton>
          ) : null}

          {showMute ? (
            <IconButton
              label={muted ? "Unmute microphone" : "Mute microphone"}
              onClick={onToggleMute}
              ariaPressed={muted}
              className={cn(
                SECONDARY_BASE,
                muted
                  ? "border-white/12 bg-white/[0.05] text-white/45"
                  : "border-white/12 bg-white/[0.07] text-white/75 hover:bg-white/[0.12]",
              )}
            >
              {muted ? (
                <MicOff className="size-4.5" aria-hidden />
              ) : (
                <Mic className="size-4.5" aria-hidden />
              )}
            </IconButton>
          ) : null}

          {voiceState === "speaking" && demo ? (
            <IconButton
              label="Interrupt speech"
              onClick={onInterrupt}
              className={cn(
                SECONDARY_BASE,
                "border-white/12 bg-white/[0.07] text-white/75 hover:bg-white/[0.12]",
              )}
            >
              <SkipForward className="size-4.5" aria-hidden />
            </IconButton>
          ) : null}

          {showPrimary ? (
            <IconButton
              label="End conversation"
              onClick={onEnd}
              className={cn(
                PRIMARY_BASE,
                "border-red-400/25 bg-red-500/[0.12] text-red-300 hover:bg-red-500/20",
              )}
            >
              <PhoneOff className="size-6" aria-hidden />
            </IconButton>
          ) : null}

          {voiceState === "error" ? (
            <IconButton
              label="Retry connection"
              onClick={onRetry}
              className={cn(
                PRIMARY_BASE,
                "border-white/12 bg-white/[0.07] text-white/85 hover:bg-white/[0.12]",
              )}
            >
              <RefreshCcw className="size-6" aria-hidden />
            </IconButton>
          ) : null}
        </div>

        <p
          aria-live="polite"
          className={cn(
            "text-[12.5px] transition-colors duration-500",
            voiceState === "error" ? "text-red-300/80" : "text-white/45",
            !helper && "opacity-0",
          )}
        >
          {helper || "·"}
        </p>

        {voiceState === "idle" || voiceState === "error" ? (
          <Button
            variant="ghost"
            onClick={onDemo}
            className="h-auto px-3 py-1 text-[11px] text-white/35 hover:text-white/65"
          >
            Preview demo mode
          </Button>
        ) : null}
      </div>
    </TooltipProvider>
  );
}
