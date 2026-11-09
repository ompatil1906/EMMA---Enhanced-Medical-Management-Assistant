"use client";

/* Shared microphone pipeline — one getUserMedia, one AudioContext, used by
   both the orb (level metering) and the live transport (20 ms PCM frames).

   * Level: AnalyserNode RMS, smoothed by the caller's poll cadence.
   * Frames: an AudioWorklet (loaded from a Blob URL — no bundler involvement)
     emits one Float32Array per 20 ms; the main thread resamples it to the
     gateway's 16 kHz wire rate and converts to Int16.
   * Muting happens inside the worklet, so no silence frames ever reach the
     wire. */

const WIRE_RATE = 16_000;
const FRAME_MS = 20;
const WIRE_FRAME_SAMPLES = (WIRE_RATE * FRAME_MS) / 1000; // 320 samples @16k

export type WireFrameHandler = (frame: Int16Array) => void;

export interface AudioPipelineHandle {
  readonly wireRate: number;
  readonly audioContext?: AudioContext;
  /** Smoothed RMS level 0..1 (call at 10–15 Hz). */
  level(): number;
  setMuted(muted: boolean): void;
  isMuted(): boolean;
  onMuteChange(handler: (muted: boolean) => void): () => void;
  /** 20 ms / 320-sample Int16 frames at the wire rate. */
  subscribe(handler: WireFrameHandler): () => void;
  close(): Promise<void>;
}

const WORKLET_SRC = `
class EmmaCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._pending = [];
    this._target = Math.round(sampleRate * 0.02);
    this._muted = false;
    this.port.onmessage = (e) => {
      if (typeof e.data === "object" && e.data !== null && "mute" in e.data) {
        this._muted = Boolean(e.data.mute);
      }
    };
  }
  process(inputs) {
    const input = inputs[0];
    const ch = input && input[0];
    if (!ch) return true;
    if (this._muted) {
      this._pending.length = 0;
      return true;
    }
    for (let i = 0; i < ch.length; i++) this._pending.push(ch[i]);
    while (this._pending.length >= this._target) {
      const out = new Float32Array(this._target);
      for (let j = 0; j < this._target; j++) out[j] = this._pending[j];
      this._pending = this._pending.slice(this._target);
      this.port.postMessage(out);
    }
    return true;
  }
}
registerProcessor("emma-capture", EmmaCaptureProcessor);
`;

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

function downsampleInt16(
  input: Float32Array,
  fromRate: number,
  outSamples: number,
): Int16Array {
  const out = new Int16Array(outSamples);
  const ratio = fromRate / WIRE_RATE;
  for (let i = 0; i < outSamples; i++) {
    const t = i * ratio;
    const i0 = Math.floor(t);
    const frac = t - i0;
    const a = i0 < input.length ? input[i0] : 0;
    const b = i0 + 1 < input.length ? input[i0 + 1] : a;
    const v = a + (b - a) * frac;
    out[i] = Math.max(-32_768, Math.min(32_767, Math.round(v * 32_767)));
  }
  return out;
}

/** Open mic + capture graph. Call from a user gesture (autoplay policies). */
export async function openAudioPipeline(): Promise<AudioPipelineHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const ctx = new (getAudioCtor())();
  try {
    await ctx.resume();
  } catch {
    /* resume may be blocked; capture still works in most cases */
  }

  const workletUrl = URL.createObjectURL(
    new Blob([WORKLET_SRC], { type: "text/javascript" }),
  );

  let closed = false;
  let muted = false;
  let smooth = 0;

  const muteHandlers = new Set<(m: boolean) => void>();
  const frameHandlers = new Set<WireFrameHandler>();

  let source: MediaStreamAudioSourceNode | null = null;
  let node: AudioWorkletNode | null = null;
  let analyser: AnalyserNode | null = null;

  const teardown = async () => {
    if (closed) return;
    closed = true;
    if (node) {
      node.port.onmessage = null;
      try {
        node.disconnect();
      } catch {
        /* already gone */
      }
    }
    try {
      source?.disconnect();
    } catch {
      /* already gone */
    }
    for (const track of stream.getTracks()) track.stop();
    URL.revokeObjectURL(workletUrl);
    await ctx.close().catch(() => undefined);
  };

  try {
    await ctx.audioWorklet.addModule(workletUrl);
    source = ctx.createMediaStreamSource(stream);
    node = new AudioWorkletNode(ctx, "emma-capture", {
      numberOfOutputs: 0,
    });
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;

    source.connect(analyser);
    source.connect(node);

    const levelBuf = new Float32Array(analyser.fftSize);

    node.port.onmessage = (event: MessageEvent) => {
      const block = event.data as Float32Array;
      if (closed || muted || !(block instanceof Float32Array)) return;
      const frame = downsampleInt16(block, ctx.sampleRate, WIRE_FRAME_SAMPLES);
      for (const handler of frameHandlers) handler(frame);
    };

    node.port.postMessage({ mute: false });

    return {
      wireRate: WIRE_RATE,
      audioContext: ctx,
      level() {
        if (closed || !analyser) return 0;
        analyser.getFloatTimeDomainData(levelBuf);
        let sum = 0;
        for (let i = 0; i < levelBuf.length; i++) {
          sum += levelBuf[i] * levelBuf[i];
        }
        const rms = Math.sqrt(sum / levelBuf.length);
        const target = Math.max(0, Math.min(1, rms * 6));
        // Asymmetric smoothing: fast attack, slow release (per ~80 ms poll).
        smooth += (target - smooth) * (target > smooth ? 0.45 : 0.16);
        return muted ? 0 : smooth;
      },
      setMuted(next: boolean) {
        if (muted === next) return;
        muted = next;
        node?.port.postMessage({ mute: next });
        for (const handler of muteHandlers) handler(next);
      },
      isMuted: () => muted,
      onMuteChange(handler: (m: boolean) => void) {
        muteHandlers.add(handler);
        return () => {
          muteHandlers.delete(handler);
        };
      },
      subscribe(handler: WireFrameHandler) {
        frameHandlers.add(handler);
        return () => {
          frameHandlers.delete(handler);
        };
      },
      close: teardown,
    };
  } catch (error) {
    await teardown();
    throw error;
  }
}
