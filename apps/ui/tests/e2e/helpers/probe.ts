"use strict";

import * as child_process from "child_process";
import * as fs from "fs";
import * as path from "path";
import type { ChildProcess } from "child_process";

/* Day 17 test helper: spawns the non-product `tools.room_probe` LiveKit
   participant as a background process and exposes its JSONL event log.

   The probe joins the room like a browser participant would and records
   participants, tracks, data messages, and audio-frame observations so
   acceptance tests can assert server-side ground truth (agent present,
   assistant audio flowed, barge-in data message received, etc.).
*/

export interface ProbeEvent {
  ts: number;
  event: string;
  [key: string]: unknown;
}

export class ProbeHandle {
  private proc: ChildProcess;
  private outPath: string;
  private _stopped = false;
  private lines: string[] = [];

  constructor(proc: ChildProcess, outPath: string) {
    this.proc = proc;
    this.outPath = outPath;
    // Drain stdout so the pipe never blocks the probe.
    proc.stdout?.on("data", () => {});
    proc.stderr?.on("data", () => {});
    this._readFile();
  }

  private _readFile(): void {
    try {
      if (fs.existsSync(this.outPath)) {
        const content = fs.readFileSync(this.outPath, "utf8");
        this.lines = content.split(/\r?\n/).filter(Boolean);
      }
    } catch {
      /* file may not exist yet */
    }
  }

  /** Snapshot every parsed JSON line observed so far. */
  events(): ProbeEvent[] {
    this._readFile();
    const out: ProbeEvent[] = [];
    for (const line of this.lines) {
      try {
        out.push(JSON.parse(line) as ProbeEvent);
      } catch {
        /* ignore non-JSON lines (e.g. PROBE_DONE summary) */
      }
    }
    return out;
  }

  /** Return lines matching a given event name. */
  eventsOfType(name: string): ProbeEvent[] {
    return this.events().filter((e) => e.event === name);
  }

  /** Wait (polling) until a line matches `predicate` or `timeoutMs` elapses. */
  async waitFor(
    predicate: (e: ProbeEvent) => boolean,
    { timeoutMs = 30_000, intervalMs = 250 }: { timeoutMs?: number; intervalMs?: number } = {}
  ): Promise<ProbeEvent | null> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      for (const e of this.events()) {
        if (predicate(e)) return e;
      }
      if (this._stopped) break;
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    return null;
  }

  stop(): void {
    if (this._stopped) return;
    this._stopped = true;
    try {
      this.proc.kill("SIGTERM");
    } catch {
      /* ignore */
    }
  }
}

/** Spawn the room probe as a detached background process. */
export function spawnProbe({
  room,
  identity = `emma-probe-${Date.now()}`,
  seconds = 120,
  outPath,
  autoBargeIn = false,
  cwd,
}: {
  room: string;
  identity?: string;
  seconds?: number;
  outPath: string;
  autoBargeIn?: boolean;
  cwd: string;
}): ProbeHandle {
  const PYTHON = path.join(cwd, ".venv/bin/python");
  const args = [
    "-m",
    "tools.room_probe",
    "--room",
    room,
    "--identity",
    identity,
    "--seconds",
    String(seconds),
    "--out",
    outPath,
  ];
  if (autoBargeIn) args.push("--auto-barge-in");

  const proc = child_process.spawn(PYTHON, args, {
    cwd,
    stdio: ["ignore", "pipe", "pipe"],
    detached: false,
  });

  return new ProbeHandle(proc, outPath);
}
