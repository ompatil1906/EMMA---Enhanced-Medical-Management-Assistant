"use client";

/* Browser playback for the gateway's outbound audio.

   The gateway sends base64 PCM16 mono chunks (the verified Exotel `media`
   envelope). Chunks arrive faster than real time, so they are scheduled
   back-to-back on one AudioContext timeline. `stop()` cancels everything
   immediately — used by acoustic barge-in and session teardown. */

export interface PcmPlayerHandle {
  /** Queue one base64 PCM16 mono chunk at the player's sample rate. */
  playBase64(payload: string): void;
  /** True while scheduled audio is still audible or pending. */
  isPlaying(): boolean;
  /** Smoothed output RMS 0..1 (call at 10–15 Hz). */
  level(): number;
  /** Cancel all scheduled and audible audio immediately. */
  stop(): void;
  close(): Promise<void>;
}

type WebkitAudioContextCtor = { new (): AudioContext };

function getAudioCtor(): typeof AudioContext {
  const w = window as unknown as {
    AudioContext?: typeof AudioContext;
    webkitAudioContext?: WebkitAudioContextCtor;
  };
  const ctor = w.AudioContext ?? w.webkitAudioContext;
  if (!ctor) throw new Error("Web Audio is not available in this browser");
  return ctor;
}

function base64ToInt16(payload: string): Int16Array {
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  // Copy into an aligned buffer; PCM16 wire format is little-endian.
  const samples = new Int16Array(Math.floor(bytes.length / 2));
  for (let i = 0; i < samples.length; i++) {
    const lo = bytes[i * 2];
    const hi = bytes[i * 2 + 1];
    const value = (hi << 8) | lo;
    samples[i] = value >= 0x8000 ? value - 0x10000 : value;
  }
  return samples;
}

export interface PcmPlayerOptions {
  sampleRate: number;
  audioContext?: AudioContext;
}

export function createPcmPlayer(options: PcmPlayerOptions): PcmPlayerHandle {
  const { sampleRate, audioContext } = options;

  let ctx: AudioContext | null = audioContext ?? null;
  let master: GainNode | null = null;
  let analyser: AnalyserNode | null = null;
  let levelBuf: Float32Array<ArrayBuffer> | null = null;
  let nextStartTime = 0;
  let smooth = 0;
  let closed = false;
  const ownsContext = !audioContext;

  const active = new Set<AudioBufferSourceNode>();

  const initGraph = (targetCtx: AudioContext) => {
    master = targetCtx.createGain();
    master.gain.value = 1;
    analyser = targetCtx.createAnalyser();
    analyser.fftSize = 2048;
    levelBuf = new Float32Array(analyser.fftSize);
    master.connect(analyser);
    analyser.connect(targetCtx.destination);
  };

  if (ctx !== null) {
    initGraph(ctx);
    if (ctx.state === "suspended") {
      void ctx.resume().catch(() => undefined);
    }
  }

  const ensureContext = (): AudioContext | null => {
    if (closed) return null;
    if (ctx === null) {
      try {
        ctx = new (getAudioCtor())();
        initGraph(ctx);
      } catch {
        ctx = null;
        return null;
      }
    }
    if (ctx.state === "suspended") {
      void ctx.resume().catch(() => undefined);
    }
    return ctx;
  };

  const playBase64 = (payload: string) => {
    if (closed || !payload) return;
    const context = ensureContext();
    if (context === null || master === null) return;

    let samples: Int16Array;
    try {
      samples = base64ToInt16(payload);
    } catch {
      return;
    }
    if (samples.length === 0) return;

    const buffer = context.createBuffer(1, samples.length, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) {
      channel[i] = samples[i] / 32_768;
    }

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(master);

    // Small lead absorbs network jitter without adding audible gaps.
    const startAt = Math.max(context.currentTime + 0.02, nextStartTime);
    nextStartTime = startAt + buffer.duration;
    active.add(source);
    source.onended = () => {
      active.delete(source);
      try {
        source.disconnect();
      } catch {
        /* already gone */
      }
    };
    source.start(startAt);
  };

  const isPlaying = () => {
    if (closed || ctx === null) return false;
    return active.size > 0 || nextStartTime > ctx.currentTime + 0.02;
  };

  const level = () => {
    if (closed || analyser === null || levelBuf === null || !isPlaying()) {
      smooth += (0 - smooth) * 0.25;
      return smooth;
    }
    analyser.getFloatTimeDomainData(levelBuf);
    let sum = 0;
    for (let i = 0; i < levelBuf.length; i++) {
      sum += levelBuf[i] * levelBuf[i];
    }
    const rms = Math.sqrt(sum / levelBuf.length);
    const target = Math.max(0, Math.min(1, rms * 6));
    smooth += (target - smooth) * (target > smooth ? 0.5 : 0.2);
    return smooth;
  };

  const stop = () => {
    for (const source of active) {
      try {
        source.onended = null;
        source.stop();
        source.disconnect();
      } catch {
        /* already stopped */
      }
    }
    active.clear();
    nextStartTime = 0;
    smooth = 0;
  };

  const close = async () => {
    if (closed) return;
    stop();
    closed = true;
    const context = ctx;
    ctx = null;
    if (master !== null) {
      try {
        master.disconnect();
      } catch {
        /* ignore */
      }
    }
    if (analyser !== null) {
      try {
        analyser.disconnect();
      } catch {
        /* ignore */
      }
    }
    master = null;
    analyser = null;
    levelBuf = null;
    if (ownsContext && context !== null) await context.close().catch(() => undefined);
  };

  return { playBase64, isPlaying, level, stop, close };
}
