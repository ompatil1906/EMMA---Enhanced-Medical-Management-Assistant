import Image from "next/image";

/** Official QuantumLoopAI wordmark (white variant, from quantumloopai.com). */
export function Brand() {
  return (
    <header
      className="flex select-none flex-col items-center gap-3"
      aria-label="QuantumLoopAI — EMMA voice assistant"
    >
      <Image
        src="/ql-logo.svg"
        alt="QuantumLoopAI"
        width={186}
        height={23}
        priority
        draggable={false}
        className="h-[22px] w-auto opacity-90"
      />
      <p className="text-[10px] font-medium uppercase tracking-[0.38em] text-white/40">
        Emma · Voice Assistant
      </p>
    </header>
  );
}
