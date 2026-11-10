"use client";

import Image from "next/image";

export function QuantumLoopLogo({
  className,
  height = 22,
  width = 154,
}: {
  className?: string;
  height?: number;
  width?: number;
}) {
  return (
    <div className={`flex items-center gap-2.5 ${className ?? ""}`}>
      <Image
        src="/ql-logo.svg"
        alt="QuantumLoopAI"
        width={width}
        height={height}
        priority
        draggable={false}
        className={`h-[${height}px] w-auto opacity-95`}
      />
    </div>
  );
}
