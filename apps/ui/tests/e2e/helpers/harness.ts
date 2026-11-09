"use strict";

import * as os from "os";
import * as path from "path";
import * as fs from "fs";
import { execFileSync } from "child_process";
import { expect, type Page, type BrowserContext, type Browser } from "@playwright/test";

import { spawnProbe, type ProbeHandle } from "./probe";
import {
  startAgentWorker,
  type AgentWorkerHandle,
} from "./agent-worker";
import { launchVoiceBrowser } from "./browser";

/* Day 17 acceptance-test harness.

   A single LiveKit agent worker is kept running for the whole suite
   (started on first use, restarted only when a scenario needs a different
   `MOCK_STT_SCRIPT`). Rooms are purged between scenarios so each one starts
   with a fresh room → fresh agent dispatch → deterministic behaviour.

   Each scenario:
     1. Ensure the agent worker is running with the scenario's mock-STT script.
     2. Purge the LiveKit room (forces recreation → fresh agent dispatch).
     3. Start the room probe (server-side ground truth).
     4. Launch Chromium with a synthetic mic WAV file and open /voice.

   Teardown closes the browser, probe, and (on suite end) the agent worker.
*/

function findRepoRoot(start: string): string {
  let dir = path.resolve(start);
  while (true) {
    if (fs.existsSync(path.join(dir, ".git"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error("could not locate repo root (no .git directory found)");
    }
    dir = parent;
  }
}

const REPO_ROOT = findRepoRoot(__dirname);
const AGENT_CWD = path.join(REPO_ROOT, "apps/voice-gateway");
const TMP_DIR = os.tmpdir();
const ROOM_NAME = "emma-voice";

let probeCounter = 0;

/* Module-level agent worker. `null` until first use, and reset by
   `stopAgentWorker` at suite end. */
let agentWorker: AgentWorkerHandle | null = null;

/** Ensure an agent worker is running with the given mock-STT script, starting
   or restarting it as necessary, and wait until it is registered. */
async function ensureAgentWorker(script?: string, utteranceMs?: number): Promise<AgentWorkerHandle> {
  if (agentWorker) {
    await agentWorker.stop();
    agentWorker = null;
    // Give LiveKit a moment to notice the SIGTERMed worker's WebSocket has
    // closed before registering a replacement (otherwise LiveKit may still
    // consider the old worker registered and dispatch to a dead connection).
    const settleUntil = Date.now() + 4000;
    while (Date.now() < settleUntil) {
      /* intentional sync settle */
    }
  }
  const logPath = path.join(TMP_DIR, `emma-agent-suite.log`);
  agentWorker = await startAgentWorker({
    mockSttScript: script,
    mockSttUtteranceMs: utteranceMs,
    cwd: AGENT_CWD,
    logPath,
  });
  return agentWorker;
}

export async function purgeRoom(): Promise<void> {
  execFileSync(
    path.join(AGENT_CWD, ".venv/bin/python"),
    ["-m", "tools.room_purge", "--room", ROOM_NAME],
    {
      cwd: AGENT_CWD,
      stdio: "ignore",
      timeout: 20_000,
    }
  );
}

export interface BeginTestOptions {
  /** Mock STT script for the agent worker (undefined = default script). */
  script?: string;
  /** Mock STT utterance trigger (ms). Raised above the default so the first
     scripted line lands after the greeting (pipeline SPEAKING→LISTENING). */
  mockSttUtteranceMs?: number;
  /** Whether the probe should auto-send a barge-in when the agent speaks. */
  autoBargeIn?: boolean;
  /** WAV fixture played as the browser microphone. */
  /** WAV played as the browser microphone: fixture name or absolute path. */
  micFile?: string;
  /** Start (and wait for) an agent worker in the room. Default true. Set false
     to reproduce the Day 18E "browser connects but nothing answers" case. */
  startAgent?: boolean;
  /** Script injected into the page before any app code runs (e.g. to simulate
     a denied microphone). */
  initScript?: () => void;
}

export interface TestHandle {
  page: Page;
  context: BrowserContext;
  browser: Browser;
  probe: ProbeHandle;
  /** Identity of the LiveKit agent participant observed in the room (null when
     the scenario deliberately runs without an agent). */
  agentIdentity: string | null;
  /** Error messages captured from the page console / pageerror. */
  capturedErrors: string[];
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Create the room with a probe in it and wait for the agent to be dispatched
   into it.

   Bounded retry: LiveKit keeps a killed worker registered until its heartbeat
   expires (~14s), and a job dispatched to that dead registration never joins.
   Deleting the room and re-creating it re-runs dispatch against whichever
   worker is registered *now*. This is isolation repair, never a weakened
   assertion: the agent must still be observed in the room before the browser
   is launched. */
async function joinRoomWithAgent(options: BeginTestOptions): Promise<{
  probe: ProbeHandle;
  agentIdentity: string | null;
}> {
  const requireAgent = options.startAgent !== false;
  if (!requireAgent) {
    // No agent worker: create the room, attach the probe, and return as soon as
    // the room exists. The browser will connect to an agentless room — exactly
    // the condition that used to leave a caller waiting forever.
    purgeRoom();
    const probeId = ++probeCounter;
    const probe = spawnProbe({
      room: ROOM_NAME,
      identity: `emma-probe-${probeId}-${process.pid}`,
      seconds: 120,
      outPath: path.join(TMP_DIR, `emma-probe-${process.pid}-${probeId}-${Date.now()}.jsonl`),
      autoBargeIn: !!options.autoBargeIn,
      cwd: AGENT_CWD,
    });
    const connected = await probe.waitFor((e) => e.event === "probe_connected", {
      timeoutMs: 10_000,
    });
    if (!connected) {
      probe.stop();
      throw new Error("probe never connected to LiveKit room");
    }
    return { probe, agentIdentity: null };
  }
  const maxAttempts = 3;
  let lastEvents: ReturnType<ProbeHandle["events"]> = [];

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    purgeRoom();

    const probeId = ++probeCounter;
    const probe = spawnProbe({
      room: ROOM_NAME,
      identity: `emma-probe-${probeId}-${process.pid}`,
      seconds: 120,
      // Unique per test *and* per Playwright worker process: a restarted worker
      // resets the counter, and a still-draining probe from the previous worker
      // must never share (and interleave writes into) this test's log.
      outPath: path.join(TMP_DIR, `emma-probe-${process.pid}-${probeId}-${Date.now()}.jsonl`),
      autoBargeIn: !!options.autoBargeIn,
      cwd: AGENT_CWD,
    });

    // Wait until the probe has joined (room created → agent dispatched) and the
    // agent participant is present in the room.
    const connected = await probe.waitFor((e) => e.event === "probe_connected", {
      timeoutMs: 10_000,
    });
    if (!connected) {
      lastEvents = probe.events();
      probe.stop();
      continue;
    }

    const agentPresent = await probe.waitFor(
      (e) =>
        e.event === "participant" &&
        typeof e.identity === "string" &&
        e.identity.startsWith("agent-"),
      { timeoutMs: 15_000 }
    );
    if (agentPresent) {
      return { probe, agentIdentity: String(agentPresent.identity) };
    }

    lastEvents = probe.events();
    probe.stop();
    // Let the stale registration expire before the next dispatch attempt.
    await sleep(5_000);
  }

  throw new Error(
    `agent participant never joined the room after ${maxAttempts} attempts. ` +
      `Last probe events:\n${lastEvents.map((e) => JSON.stringify(e)).join("\n")}`
  );
}

export async function beginTest(options: BeginTestOptions = {}): Promise<TestHandle> {
  if (options.startAgent !== false) {
    // Ensure the agent worker is running with the scenario's script BEFORE the
    // room is created, so LiveKit dispatches a job to it immediately.
    await ensureAgentWorker(options.script, options.mockSttUtteranceMs);
  } else {
    // Make sure a worker from a previous scenario cannot answer for this one.
    await stopAgentWorker();
  }

  const { probe, agentIdentity } = await joinRoomWithAgent(options);

  const browser = await launchVoiceBrowser({
    fakeAudioCaptureFile: options.micFile ?? "silence.wav",
    headless: true,
  });
  const context = await browser.newContext({ permissions: ["microphone"] });
  if (options.initScript) await context.addInitScript(options.initScript);
  const page = await context.newPage();

  const capturedErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") capturedErrors.push(msg.text());
  });
  page.on("pageerror", (e) => capturedErrors.push(String(e.message ?? e)));

  await page.goto("/voice");
  await expect(page.getByPlaceholder("Sarah")).toBeVisible({ timeout: 15_000 });

  return {
    page,
    context,
    browser,
    probe,
    agentIdentity,
    capturedErrors,
  };
}

export async function endTest(handle: TestHandle): Promise<void> {
  const errors: Array<Promise<void>> = [];
  if (handle.page) errors.push(handle.page.close().catch(() => {}));
  if (handle.context) errors.push(handle.context.close().catch(() => {}));
  if (handle.browser) errors.push(handle.browser.close().catch(() => {}));
  await Promise.all(errors);
  handle.probe.stop();
}

/** Stop the suite-level agent worker (call once, e.g. in afterAll). */
export async function stopAgentWorker(): Promise<void> {
  if (agentWorker) {
    agentWorker.stop();
    agentWorker = null;
  }
}

/** Run one scenario with guaranteed teardown (browser, probe) even if the
   scenario body throws. The suite-level agent worker stays running. */
export async function runTest(
  fn: (h: TestHandle) => Promise<void>,
  options: BeginTestOptions = {}
): Promise<void> {
  const h = await beginTest(options);
  try {
    await fn(h);
  } finally {
    await endTest(h);
  }
}
