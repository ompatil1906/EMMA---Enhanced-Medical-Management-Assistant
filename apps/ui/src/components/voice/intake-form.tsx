"use client";

import { useState } from "react";
import { PhoneCall, User, Phone, ShieldCheck, CalendarCheck } from "lucide-react";

export interface IntakeFormData {
  firstName: string;
  lastName: string;
  mobile: string;
  reason: string;
}

interface IntakeFormProps {
  onCallNow: (data: IntakeFormData) => void;
}

const QUICK_REASONS = [
  "Book Appointment",
  "Repeat Prescription",
  "Lab / Test Results",
  "Urgent GP Callback",
];

export function IntakeForm({ onCallNow }: IntakeFormProps) {
  const [form, setForm] = useState<IntakeFormData>({
    firstName: "",
    lastName: "",
    mobile: "",
    reason: "Book Appointment",
  });

  const [errors, setErrors] = useState<Partial<IntakeFormData>>({});

  const validate = () => {
    const e: Partial<IntakeFormData> = {};
    if (!form.firstName.trim()) e.firstName = "First name required";
    if (!form.lastName.trim()) e.lastName = "Last name required";
    if (!/^\+?[\d\s\-()]{7,16}$/.test(form.mobile.trim()))
      e.mobile = "Valid phone required";
    if (!form.reason.trim()) e.reason = "Reason required";
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (validate()) {
      onCallNow(form);
    }
  };

  return (
    <div className="flex h-full flex-col justify-between bg-gradient-to-b from-[#141416] via-[#0d0d0f] to-[#08080a] px-5 pb-6 pt-12 text-white">
      {/* ── Top Clinic Header ── */}
      <div className="flex flex-col items-center gap-1.5 pt-1 text-center">
        <div className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.05] px-2.5 py-0.5 text-[9px] font-medium tracking-wider uppercase text-white/70">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          Reception Online
        </div>

        <h2 className="mt-1 text-[15px] font-bold tracking-tight text-white">
          St. Jude Health Partners
        </h2>
        <p className="text-[10.5px] text-white/50 leading-tight">
          Connect directly to EMMA AI medical assistant
        </p>
      </div>

      {/* ── Form Body ── */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-2.5 my-auto">
        {/* Name Fields (First + Last) */}
        <div className="grid grid-cols-2 gap-2">
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-1 text-[9.5px] font-medium uppercase tracking-wider text-white/45">
              <User className="h-2.5 w-2.5" /> First
            </label>
            <input
              type="text"
              value={form.firstName}
              onChange={(e) => {
                setForm((f) => ({ ...f, firstName: e.target.value }));
                if (errors.firstName) setErrors((e) => ({ ...e, firstName: undefined }));
              }}
              placeholder="Sarah"
              className={`w-full rounded-xl border bg-white/[0.04] px-3 py-2 text-[11px] text-white placeholder-white/20 outline-none transition focus:border-white/30 focus:bg-white/[0.08] ${errors.firstName ? "border-red-500/50" : "border-white/10"
                }`}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-1 text-[9.5px] font-medium uppercase tracking-wider text-white/45">
              <User className="h-2.5 w-2.5" /> Last
            </label>
            <input
              type="text"
              value={form.lastName}
              onChange={(e) => {
                setForm((f) => ({ ...f, lastName: e.target.value }));
                if (errors.lastName) setErrors((e) => ({ ...e, lastName: undefined }));
              }}
              placeholder="Jenkins"
              className={`w-full rounded-xl border bg-white/[0.04] px-3 py-2 text-[11px] text-white placeholder-white/20 outline-none transition focus:border-white/30 focus:bg-white/[0.08] ${errors.lastName ? "border-red-500/50" : "border-white/10"
                }`}
            />
          </div>
        </div>

        {/* Mobile Number Field */}
        <div className="flex flex-col gap-1">
          <label className="flex items-center gap-1 text-[9.5px] font-medium uppercase tracking-wider text-white/45">
            <Phone className="h-2.5 w-2.5" /> Mobile Number
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[11px] text-white/40">
              🇬🇧 +44
            </span>
            <input
              type="tel"
              value={form.mobile.replace(/^\+44\s?/, "")}
              onChange={(e) => {
                setForm((f) => ({ ...f, mobile: "+44 " + e.target.value.trim() }));
                if (errors.mobile) setErrors((e) => ({ ...e, mobile: undefined }));
              }}
              placeholder="7700 900077"
              className={`w-full rounded-xl border bg-white/[0.04] py-2 pl-14 pr-3 text-[11px] text-white placeholder-white/20 outline-none transition focus:border-white/30 focus:bg-white/[0.08] ${errors.mobile ? "border-red-500/50" : "border-white/10"
                }`}
            />
          </div>
        </div>

        {/* Reason for Call */}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-1 text-[9.5px] font-medium uppercase tracking-wider text-white/45">
              <CalendarCheck className="h-2.5 w-2.5" /> Reason for Visit
            </label>
            <span className="text-[9px] text-white/40 font-medium">Quick select</span>
          </div>

          {/* Quick Category Chips */}
          <div className="grid grid-cols-2 gap-1.5">
            {QUICK_REASONS.map((r) => {
              const active = form.reason === r;
              return (
                <button
                  type="button"
                  key={r}
                  onClick={() => setForm((f) => ({ ...f, reason: r }))}
                  className={`rounded-lg px-2 py-1.5 text-left text-[9.5px] font-medium transition ${active
                    ? "border border-white/40 bg-white/15 text-white shadow-sm"
                    : "border border-white/5 bg-white/[0.03] text-white/60 hover:bg-white/[0.06] hover:text-white/80"
                    }`}
                >
                  {r}
                </button>
              );
            })}
          </div>

          {/* Additional details */}
          <input
            type="text"
            value={form.reason}
            onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
            placeholder="Or type specific symptoms/request..."
            className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[10px] text-white placeholder-white/20 outline-none transition focus:border-white/30 focus:bg-white/[0.08]"
          />
        </div>

        {/* ── Call Now Action Button (Sleek monochrome white CTA) ── */}
        <button
          type="submit"
          className="group relative mt-1 flex w-full items-center justify-center gap-2 overflow-hidden rounded-2xl bg-gradient-to-r from-white via-[#f2f2f5] to-[#e4e4e7] py-3 text-[12px] font-bold text-black shadow-[0_8px_25px_-5px_rgba(255,255,255,0.2)] transition-all hover:scale-[1.02] hover:shadow-[0_10px_30px_-5px_rgba(255,255,255,0.3)] active:scale-[0.98]"
        >
          <PhoneCall className="h-3.5 w-3.5 text-black" />
          <span>Call Now</span>
        </button>
      </form>

      {/* ── Footer Trust Tag ── */}
      <div className="flex items-center justify-center gap-1.5 pb-2 text-[9px] text-white/35">
        <ShieldCheck className="h-3 w-3 text-emerald-400/80" />
        <span>NHS & HIPAA Compliant · Voice Encrypted</span>
      </div>
    </div>
  );
}
