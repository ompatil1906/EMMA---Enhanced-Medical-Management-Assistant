"use strict";

import * as child_process from "child_process";
import * as fs from "fs";
import * as path from "path";
import type { ChildProcess } from "child_process";
import { loadLiveKitEnv } from "./env";

/* Day 17 test helper: manages the LiveKit agent worker process.

   One worker is started for the whole acceptance suite (persisted across tests
   via module-level state in `harness.ts`) and rooms are purged between tests;
   the worker auto-dispatches one job per room creation. Restarting the worker
   is only necessary when a test needs a different `MOCK_STT_SCRIPT` (test E).

   Killing workers with plain `proc.kill()` leaves the multiprocessing job
   child registered with LiveKit, so `killStaleAgentWorkers` uses `pkill -9`
   on the whole process tree.
*/

export interface AgentWorkerHandle {
  proc: ChildProcess;
  logPath: string;
  stop(): Promise<void>;
  log(): string;
}

/** Kill every LiveKit agent-worker process (parent + job children) so a fresh
   worker owns the next room dispatch. Returns true if any process was killed. */
export function killStaleAgentWorkers(): boolean {
  try {
    child_process.execFileSync("pkill", ["-9", "-f", "app.integrations.livekit.agent"], {
      stdio: "ignore",
      timeout: 5_000,
    });
    return true; // pkill exit 0 => at least one process matched
  } catch {
    return false; // exit 1 => nothing matched
  }
}

/** Start the agent worker with the given mock-STT script and wait until it has
   registered with LiveKit. */
export async function startAgentWorker({
  mockSttScript,
  mockSttUtteranceMs,
  cwd,
  logPath,
}: {
  mockSttScript?: string;
  mockSttUtteranceMs?: number;
  cwd: string;
  logPath: string;
}): Promise<AgentWorkerHandle> {
  // Kill any stale agent processes from previous runs so the HTTP port is free.
  killStaleAgentWorkers();
  const settledUntil = Date.now() + 2000;
  while (Date.now() < settledUntil) {
    /* intentional sync settle */
  }

  // Truncate the log file so `waitForAgentRegistered` only sees entries from
  // this worker instance (not stale entries from previous test runs).
  try {
    fs.writeFileSync(logPath, "");
  } catch {
    /* ignore */
  }

  const PYTHON = path.join(cwd, ".venv/bin/python");
  const env = { ...process.env };
  if (mockSttScript !== undefined) {
    env.MOCK_STT_SCRIPT = mockSttScript;
  }
  if (mockSttUtteranceMs !== undefined) {
    env.MOCK_STT_UTTERANCE_MS = String(mockSttUtteranceMs);
  }
  env.STT_PROVIDER = env.STT_PROVIDER ?? "mock";
  env.TTS_PROVIDER = env.TTS_PROVIDER ?? "mock";
  env.LLM_PROVIDER = env.LLM_PROVIDER ?? "mock";
  env.EMBEDDING_PROVIDER = env.EMBEDDING_PROVIDER ?? "mock";
  env.VECTOR_STORE_PROVIDER = env.VECTOR_STORE_PROVIDER ?? "mock";
  env.PRACTICE_ID = env.PRACTICE_ID ?? "demo-practice-001";
  env.VOICE_TRANSPORT = env.VOICE_TRANSPORT ?? "livekit";

  const { livekitUrl, apiKey, apiSecret } = loadLiveKitEnv();
  env.LIVEKIT_URL = livekitUrl;
  env.LIVEKIT_API_KEY = apiKey;
  env.LIVEKIT_API_SECRET = apiSecret;

  const proc = child_process.spawn(PYTHON, ["-m", "app.integrations.livekit.agent", "dev"], {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  const logStream = fs.createWriteStream(logPath, { flags: "a" });
  proc.stdout?.on("data", (chunk) => logStream.write(chunk));
  proc.stderr?.on("data", (chunk) => logStream.write(chunk));

  let stopped = false;
  const handle: AgentWorkerHandle = {
    proc,
    logPath,
    stop: async () => {
      if (stopped) return;
      stopped = true;
      try {
        // SIGTERM lets the Python worker close its LiveKit WebSocket cleanly so
        // the server deregisters it immediately (SIGKILL leaves the server
        // waiting ~14s for a heartbeat, which breaks dispatch for the next test).
        proc.kill("SIGTERM");
      } catch {
        /* ignore */
      }
      // Wait for the process to exit so the HTTP port is released before the
      // next worker tries to bind it.
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => resolve(), 5000);
        const onExit = () => {
          clearTimeout(timer);
          resolve();
        };
        proc.once("exit", onExit);
      });
      try {
        logStream.end();
      } catch {
        /* ignore */
      }
    },
    log() {
      try {
        return fs.readFileSync(logPath, "utf8");
      } catch {
        return "";
      }
    },
  };

  // Wait until the worker has registered with LiveKit.
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (/registered worker/.test(handle.log())) return handle;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `LiveKit agent worker did not register within 20s. Log:\n${handle.log()}`
  );
}

/** Wait until the worker's registration line appears in its log. */
export async function waitForAgentRegistered(
  handle: AgentWorkerHandle,
  { timeoutMs = 15_000 }: { timeoutMs?: number } = {}
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const log = handle.log();
    if (/registered worker/.test(log)) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `LiveKit agent worker did not register within ${timeoutMs}ms. Log:\n${handle.log()}`
  );
}
