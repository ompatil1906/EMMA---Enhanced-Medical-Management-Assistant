'use client';

import { forwardRef, type ComponentPropsWithoutRef, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Wifi, Signal } from 'lucide-react';

const CHASSIS_ASPECT_RATIO = 70.6 / 146.6;

type PhoneMockupCardProps = Readonly<
  ComponentPropsWithoutRef<'div'> & {
    visibleRatio?: number;
    showDynamicIsland?: boolean;
    currentTime?: string;
  }
>;

function SideControls() {
  return (
    <>
      {/* Silent / Action switch */}
      <div
        className="absolute -left-[2.5px] top-[14.5%] h-[3.2%] w-[2.5px] rounded-l-sm bg-[#3a3b42] shadow-[inset_1px_0_1px_rgba(255,255,255,0.2)]"
        aria-hidden="true"
      />
      {/* Volume Up */}
      <div
        className="absolute -left-[2.5px] top-[20.5%] h-[7.2%] w-[2.5px] rounded-l-sm bg-[#3a3b42] shadow-[inset_1px_0_1px_rgba(255,255,255,0.2)]"
        aria-hidden="true"
      />
      {/* Volume Down */}
      <div
        className="absolute -left-[2.5px] top-[29.8%] h-[7.2%] w-[2.5px] rounded-l-sm bg-[#3a3b42] shadow-[inset_1px_0_1px_rgba(255,255,255,0.2)]"
        aria-hidden="true"
      />
      {/* Power / Siri button */}
      <div
        className="absolute -right-[2.5px] top-[22.5%] h-[11.5%] w-[2.5px] rounded-r-sm bg-[#3a3b42] shadow-[inset_-1px_0_1px_rgba(255,255,255,0.2)]"
        aria-hidden="true"
      />
    </>
  );
}

function MockupScreen({
  children,
  showDynamicIsland,
  currentTime = '9:41',
}: Readonly<{
  children: ReactNode;
  showDynamicIsland: boolean;
  currentTime?: string;
}>) {
  return (
    <div className="relative h-full w-full overflow-hidden rounded-[2.35rem] bg-[#0c0d11]">
      {/* Inner Screen */}
      <div className="relative flex h-full w-full flex-col overflow-hidden bg-[#0c0d11]">
        
        {/* iOS Status Bar (above screen content) */}
        <div className="pointer-events-none absolute left-0 right-0 top-0 z-30 flex h-11 items-center justify-between px-6 text-white select-none">
          {/* Time */}
          <span className="text-[11px] font-semibold tracking-tight text-white/90">
            {currentTime}
          </span>

          {/* Dynamic Island */}
          {showDynamicIsland && (
            <div
              className="absolute left-1/2 top-[9px] z-30 flex h-[21px] w-[74px] -translate-x-1/2 items-center justify-between rounded-full bg-black px-2 shadow-[0_0_1px_1px_rgba(255,255,255,0.08)]"
              aria-hidden="true"
            >
              {/* Camera sensor dot */}
              <div className="h-2 w-2 rounded-full bg-[#14151f] ring-1 ring-white/10" />
              {/* Mic / speaker sensor */}
              <div className="h-1.5 w-1.5 rounded-full bg-emerald-500/80 animate-pulse" />
            </div>
          )}

          {/* Cellular, WiFi & Battery icons */}
          <div className="flex items-center gap-1.5 text-white/80">
            <Signal className="h-2.5 w-2.5" />
            <Wifi className="h-2.5 w-2.5" />
            {/* Battery glyph */}
            <div className="flex items-center gap-0.5">
              <div className="flex h-2.5 w-4 items-center justify-start rounded-[3px] border border-white/60 p-[1px]">
                <div className="h-full w-full rounded-[1px] bg-white" />
              </div>
              <div className="h-1 w-[1px] rounded-r-[1px] bg-white/60" />
            </div>
          </div>
        </div>

        {/* Screen Children content */}
        <div className="relative flex-1 overflow-hidden">{children}</div>

        {/* iOS Home Indicator Bar */}
        <div
          className="pointer-events-none absolute bottom-[6px] left-1/2 z-30 h-[3.5px] w-[34%] -translate-x-1/2 rounded-full bg-white/40 shadow-sm"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

export const PhoneMockupCard = forwardRef<HTMLDivElement, PhoneMockupCardProps>(
  (
    {
      className,
      children,
      visibleRatio = 1,
      showDynamicIsland = true,
      currentTime = '9:41',
      style,
      ...props
    },
    ref,
  ) => {
    const ratio = Math.min(1, Math.max(0, visibleRatio));
    const isCropped = ratio < 1;

    // Dark Titanium frame with delicate bevel ring and deep ambient glow
    const phoneFrameClassName = cn(
      'relative rounded-[2.65rem] p-[3px] transition-all',
      'bg-gradient-to-b from-[#323339] via-[#1c1d22] to-[#121316]',
      'ring-1 ring-white/10 shadow-[0_25px_60px_-15px_rgba(0,0,0,0.95),0_0_20px_rgba(255,255,255,0.03)]',
    );

    if (!isCropped) {
      return (
        <div
          ref={ref}
          data-slot="phone-mockup-card"
          className={cn(phoneFrameClassName, 'aspect-[70.6/146.6]', className)}
          style={style}
          {...props}
        >
          <SideControls />
          <MockupScreen showDynamicIsland={showDynamicIsland} currentTime={currentTime}>
            {children}
          </MockupScreen>
        </div>
      );
    }

    return (
      <div
        ref={ref}
        data-slot="phone-mockup-card"
        data-visible-ratio={ratio}
        className={cn('relative overflow-hidden', className)}
        style={{ aspectRatio: `${CHASSIS_ASPECT_RATIO / ratio}`, ...style }}
        {...props}
      >
        <div className="absolute inset-0 left-0.5 right-0.5 overflow-hidden">
          <div
            className={cn(phoneFrameClassName, 'w-full shrink-0')}
            style={{ height: `${100 / ratio}%` }}
          >
            <MockupScreen showDynamicIsland={showDynamicIsland} currentTime={currentTime}>
              {children}
            </MockupScreen>
          </div>
        </div>
        <div
          className="pointer-events-none absolute left-0.5 top-0 z-10 w-full shrink-0"
          style={{ height: `${100 / ratio}%` }}
        >
          <SideControls />
        </div>
      </div>
    );
  },
);

PhoneMockupCard.displayName = 'PhoneMockupCard';
