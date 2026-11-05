"use client";

/* Optional browser speech recognition (Web Speech API), used to fill the
   transcript while the user talks in live mode. Feature-detected and typed
   through a minimal local interface so builds do not depend on lib.dom
   shipping SpeechRecognition types. Never throws — transcript is best-effort
   and everything else keeps working without it. */

interface MiniSpeechAlternative {
  transcript: string;
}

interface MiniSpeechResult {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: MiniSpeechAlternative;
}

interface MiniSpeechEvent {
  resultIndex: number;
  results: { readonly length: number; [index: number]: MiniSpeechResult };
}

interface MiniRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: MiniSpeechEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
}

type MiniRecognitionCtor = new () => MiniRecognition;

function findCtor(): MiniRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  const candidate = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (typeof candidate !== "function") return null;
  const ctor = candidate as MiniRecognitionCtor;
  try {
    const probe = new ctor();
    if (
      typeof probe.start !== "function" ||
      typeof probe.abort !== "function"
    ) {
      return null;
    }
    probe.abort();
    return ctor;
  } catch {
    return null;
  }
}

export interface TranscriberHandle {
  stop(): void;
}

/**
 * Start listening for user speech.
 *
 * @param onTranscript called with partial text while speaking, final on pause
 * @returns a handle, or null when the browser has no speech engine
 */
export function createTranscriber(
  onTranscript: (text: string, final: boolean) => void,
): TranscriberHandle | null {
  const Ctor = findCtor();
  if (!Ctor) return null;

  let active = true;
  let stoppedByMute = false;
  let recognition: MiniRecognition | null = null;

  const spawn = () => {
    if (!active) return;
    const instance = new Ctor();
    recognition = instance;
    instance.lang = navigator.language || "en-GB";
    instance.continuous = true;
    instance.interimResults = true;

    instance.onresult = (event) => {
      let partial = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result) continue;
        const alternative = result[0];
        if (!alternative) continue;
        const text = alternative.transcript.trim();
        if (!text) continue;
        if (result.isFinal) onTranscript(text, true);
        else partial += (partial ? " " : "") + text;
      }
      if (partial) onTranscript(partial, false);
    };
    instance.onerror = (event) => {
      // 'no-speech' between phrases is normal; keep going,
      // permission problems end the engine permanently.
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        active = false;
      }
    };
    instance.onend = () => {
      recognition = null;
      if (active) {
        // Engines stop silently after each result; keep the session alive.
        window.setTimeout(() => {
          if (active && !stoppedByMute && !recognition) spawn();
        }, 250);
      }
    };

    try {
      instance.start();
    } catch {
      recognition = null;
    }
  };

  spawn();

  return {
    stop() {
      active = false;
      stoppedByMute = true;
      const instance = recognition;
      recognition = null;
      if (instance) {
        instance.onresult = null;
        instance.onerror = null;
        instance.onend = null;
        try {
          instance.abort();
        } catch {
          /* already ended */
        }
      }
    },
  };
}
