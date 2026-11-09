import { test, expect } from "@playwright/test";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { runTest, stopAgentWorker, type TestHandle } from "../e2e/helpers/harness";

/* Day 18: OPT-IN live-provider browser acceptance.

   Real Chromium → real self-hosted LiveKit → agent → real Deepgram STT →
   ConversationController → mock LLM → real ElevenLabs TTS → LiveKit → real
   browser playback.

   Never runs in the default suite: it is excluded in `playwright.config.js`
   and additionally skips unless both gates below are set.

     EMMA_RUN_LIVE_TESTS=1                 # live tests are opt-in
     EMMA_LIVE_CREDENTIALS_ROTATED=1       # operator attests keys were rotated
     EMMA_LIVE_PROVIDER_SPEND_APPROVED=1   # operator authorises the bounded spend

   Cost control: the phrase is synthesised locally (macOS `say`) into a
   speech + 15 s silence fixture so Chromium's looping fake microphone yields
   exactly ONE utterance per cycle. One call = 1 Deepgram session (≤15 s) and
   at most 2 ElevenLabs requests (greeting + one answer), inside the Day 18
   caps of 3 sessions / 3 requests.

   Run (services started with real providers, see
   docs/LIVE_PROVIDER_ACCEPTANCE.md):

     cd apps/ui && EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
       EMMA_LIVE_PROVIDER_SPEND_APPROVED=1 \
       npx playwright test -c playwright.live.config.js --reporter=list
*/

const LIVE = process.env.EMMA_RUN_LIVE_TESTS === "1";
const ROTATED = process.env.EMMA_LIVE_CREDENTIALS_ROTATED === "1";
const SPEND_APPROVED = process.env.EMMA_LIVE_PROVIDER_SPEND_APPROVED === "1";

const VOICE_GATEWAY = path.resolve(__dirname, "../../voice-gateway");
const PYTHON = path.join(VOICE_GATEWAY, ".venv/bin/python");

function buildOneShotFixture(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "emma-live-fixture-"));
  const out = path.join(dir, "gp_appointment_oneshot.wav");
  execFileSync(
    PYTHON,
    [
      "-m",
      "tools.make_speech_fixture",
      "--out",
      out,
      "--trailing-silence-ms",
      "15000",
    ],
    { cwd: VOICE_GATEWAY, stdio: "pipe", timeout: 120_000 }
  );
  expect(fs.statSync(out).size).toBeGreaterThan(100_000);
  return out;
}

interface LiveGuardBlock {
  enabled: boolean;
  applicable: boolean;
  attestation?: { credentials_rotated: boolean; spend_approved: boolean; ok: boolean };
  ledger_available: boolean;
  run_id: string | null;
  usage: Record<string, number> | null;
  limits: Record<string, number> | null;
  remaining: Record<string, number> | null;
  exhausted: boolean | null;
  configuration_ready: boolean;
  error: string | null;
}

/** Read the runtime guard block — the SAME counters the harness uses (Day 18E). */
async function readLiveGuard(): Promise<LiveGuardBlock> {
  const response = await fetch("http://localhost:8000/health/ready");
  const body = (await response.json()) as { checks?: Record<string, unknown> };
  const guard = (body.checks ?? {}).live_provider_guard as LiveGuardBlock | undefined;
  expect(guard, "gateway must expose checks.live_provider_guard").toBeTruthy();
  return guard as LiveGuardBlock;
}

test.describe("Day 18 live providers (Deepgram STT + ElevenLabs TTS)", () => {
  test.skip(!LIVE, "live acceptance disabled (set EMMA_RUN_LIVE_TESTS=1)");
  test.skip(
    !ROTATED,
    "credential rotation not attested (set EMMA_LIVE_CREDENTIALS_ROTATED=1 after rotating the exposed keys)"
  );
  test.skip(
    !SPEND_APPROVED,
    "provider spend not authorised (set EMMA_LIVE_PROVIDER_SPEND_APPROVED=1 after reviewing balances/pricing)"
  );

  test.setTimeout(240_000);

  test("browser conversation with real STT and real TTS", async () => {
    // Preflight: refuse to spend credits if the gateway is still on mocks.
    const readiness = await fetch("http://localhost:8000/health/ready");
    const body = (await readiness.json()) as { checks?: Record<string, unknown> };
    const checks = body.checks ?? {};
    expect(
      checks.stt_provider,
      "gateway must run STT_PROVIDER=deepgram for the live run"
    ).toBe("deepgram");
    expect(
      checks.tts_provider,
      "gateway must run TTS_PROVIDER=elevenlabs for the live run"
    ).toBe("elevenlabs");
    expect(checks.llm_provider, "the LLM must stay mocked in Day 18").toBe("mock");
    expect(checks.speech_provider_configured).toBe(true);

    // Day 18E: the runtime guard is the shared ledger. It must be armed,
    // attested, healthy and have capacity left before we spend anything.
    const guardBefore = await readLiveGuard();
    expect(guardBefore.enabled, "EMMA_LIVE_GUARD_ENABLED must be on").toBe(true);
    expect(guardBefore.applicable, "live providers must be selected").toBe(true);
    expect(guardBefore.attestation?.ok, "user attestations must be present").toBe(true);
    expect(guardBefore.ledger_available, "shared ledger must be available").toBe(true);
    expect(guardBefore.configuration_ready, "guard configuration must be ready").toBe(true);
    expect(guardBefore.error).toBeNull();
    expect(guardBefore.remaining?.deepgram_sessions ?? 0).toBeGreaterThanOrEqual(1);
    expect(guardBefore.remaining?.elevenlabs_requests ?? 0).toBeGreaterThanOrEqual(1);

    const micFile = buildOneShotFixture();

    await runTest(
      async (h: TestHandle) => {
        await h.page.getByPlaceholder("Sarah").fill("Sarah");
        await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
        await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
        await h.page.getByRole("button", { name: "Call Now" }).click();
        await expect(h.page.getByText("Connected to EMMA voice agent")).toBeVisible({
          timeout: 30_000,
        });

        // Real Deepgram transcript must cross the data channel and reach the UI.
        const transcript = await h.probe.waitFor(
          (e) =>
            e.event === "data" &&
            e.data_event === "transcript" &&
            /appointment|book|hello/i.test(String(e.text ?? "")),
          { timeoutMs: 90_000 }
        );
        expect(
          transcript,
          `no Deepgram transcript observed; probe events: ${JSON.stringify(h.probe.events())}`
        ).not.toBeNull();
        await expect(h.page.getByText(/appointment/i).first()).toBeVisible({
          timeout: 30_000,
        });

        // Mock LLM produced a reply that ElevenLabs synthesised and the agent
        // published: an `assistant` control event plus audible room frames.
        const assistant = await h.probe.waitFor(
          (e) => e.event === "data" && e.data_event === "assistant",
          { timeoutMs: 90_000 }
        );
        expect(assistant).not.toBeNull();
        const audible = await h.probe.waitFor(
          (e) =>
            e.event === "audio_progress" &&
            typeof e.nonzero_frames === "number" &&
            e.nonzero_frames > 0,
          { timeoutMs: 90_000 }
        );
        expect(audible, "no audible agent audio reached the room").not.toBeNull();

        // Browser playback of the real TTS audio.
        const playback = await h.page.evaluate(async () => {
          const el = document.querySelector<HTMLAudioElement>(
            "audio[data-emma-remote-audio]"
          );
          if (!el) return { attached: false, track: false, playing: false };
          const stream = el.srcObject as MediaStream | null;
          await new Promise((resolve) => setTimeout(resolve, 800));
          return {
            attached: true,
            track: !!stream && stream.getAudioTracks().length > 0,
            playing: !el.paused,
          };
        });
        expect(playback.attached).toBe(true);
        expect(playback.track).toBe(true);
        expect(playback.playing).toBe(true);

        // End the call promptly: the fake microphone loops, and every extra
        // utterance would spend another ElevenLabs request.
        await h.page.getByRole("button", { name: "End Conversation" }).click();
        await expect(h.page.getByPlaceholder("Sarah")).toBeVisible({ timeout: 15_000 });

        // Day 18E evidence: the SAME persistent ledger the harness uses recorded
        // this browser session (proves shared accounting, not a test-only copy).
        const guardAfter = await readLiveGuard();
        expect(guardAfter.run_id).toBe(guardBefore.run_id);
        expect(guardAfter.usage?.deepgram_sessions ?? 0).toBeGreaterThan(
          guardBefore.usage?.deepgram_sessions ?? 0
        );
        expect(guardAfter.usage?.elevenlabs_requests ?? 0).toBeGreaterThan(
          guardBefore.usage?.elevenlabs_requests ?? 0
        );
        // The greeting plus at most one answer: never more than 2 in this run.
        const ttsUsed =
          (guardAfter.usage?.elevenlabs_requests ?? 0) -
          (guardBefore.usage?.elevenlabs_requests ?? 0);
        expect(ttsUsed).toBeGreaterThanOrEqual(1);
        expect(ttsUsed).toBeLessThanOrEqual(2);
        // Invariant: the shared caps were never exceeded. `exhausted` becoming
        // true after the last allowed request is expected on a full run.
        expect(guardAfter.usage?.elevenlabs_requests ?? 0).toBeLessThanOrEqual(
          guardAfter.limits?.max_elevenlabs_requests ?? 0
        );
        expect(guardAfter.usage?.deepgram_sessions ?? 0).toBeLessThanOrEqual(
          guardAfter.limits?.max_deepgram_sessions ?? 0
        );

        // The live path must also be console-clean (same bar as Day 17).
        expect(h.capturedErrors).toEqual([]);
      },
      { micFile }
    );
  });
});

test.afterAll(async () => {
  await stopAgentWorker();
});
