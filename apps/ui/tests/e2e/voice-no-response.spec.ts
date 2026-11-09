import { test, expect } from "@playwright/test";

import { beginTest, endTest, stopAgentWorker, type TestHandle } from "./helpers/harness";

/* Day 18E: "the browser connects but the assistant never answers".

   Root cause found in production-like conditions: the LiveKit room was fine but
   no EMMA agent worker was running, and the UI announced
   "Connected to EMMA voice agent" purely because the *room* connected. A caller
   then waited forever for a greeting that could never arrive.

   These scenarios pin the corrected contract:

   A. no agent in the room  → UI must say it is waiting, never claim readiness
   B. microphone denied     → actionable error, no fake "listening" state
   C. agent leaves mid-call → UI reports the degraded state instead of listening

   Mock providers only; the agent worker is started by the harness.
*/

test.describe("Day 18E voice no-response regressions", () => {
  test("A: connected room without an agent must not claim the assistant is ready", async () => {
    // Deliberately no agent worker: this is the exact failure users hit.
    const h: TestHandle = await beginTest({ startAgent: false });
    try {
      await h.page.getByPlaceholder("Sarah").fill("Sarah");
      await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
      await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
      await h.page.getByRole("button", { name: "Call Now" }).click();

      // The room connects, so the browser does reach LiveKit…
      await expect(h.page.getByText(/Connected to EMMA/)).toBeVisible({ timeout: 15_000 });

      // …but the assistant is absent, and the UI must say so rather than lie.
      await expect(
        h.page.getByText("waiting for the voice assistant to join"),
        "UI must not claim readiness when no agent joined"
      ).toBeVisible({ timeout: 15_000 });
      await expect(h.page.getByText("Connected to EMMA voice agent")).toHaveCount(0);
      await expect(h.page.getByText("Listening", { exact: true })).toHaveCount(0);
      await expect(h.page.getByText("Speaking", { exact: true })).toHaveCount(0);

      // Server-side truth: the caller joined, the agent never did.
      const participants = h.probe
        .eventsOfType("participant")
        .map((event) => String(event.identity));
      expect(participants.some((identity) => identity.startsWith("emma-caller-"))).toBe(
        true
      );
      expect(participants.some((identity) => identity.startsWith("agent-"))).toBe(false);
    } finally {
      await endTest(h);
    }
  });

  test("B: denied microphone surfaces an actionable error and never fakes listening", async () => {
    const h = await beginTest({
      initScript: () => {
        // Deny capture deterministically (headless Chromium's fake-UI flag would
        // otherwise auto-allow, so overriding is the only reliable simulation).
        Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
          configurable: true,
          value: () =>
            Promise.reject(new DOMException("Permission denied", "NotAllowedError")),
        });
      },
    });
    try {
      await h.page.getByPlaceholder("Sarah").fill("Sarah");
      await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
      await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
      await h.page.getByRole("button", { name: "Call Now" }).click();

      await expect(h.page.getByText(/microphone/i).first()).toBeVisible({
        timeout: 15_000,
      });
      await expect(h.page.getByText("Listening", { exact: true })).toHaveCount(0);
      await expect(h.page.getByText("Connected to EMMA voice agent")).toHaveCount(0);
    } finally {
      await endTest(h);
    }
  });

  test("C: agent leaving mid-call is reported as degraded, not as listening", async () => {
    const h = await beginTest();
    try {
      await h.page.getByPlaceholder("Sarah").fill("Sarah");
      await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
      await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
      await h.page.getByRole("button", { name: "Call Now" }).click();
      await expect(h.page.getByText("Connected to EMMA voice agent")).toBeVisible({
        timeout: 15_000,
      });

      // Kill the agent worker: LiveKit removes the participant.
      await stopAgentWorker();

      await expect(
        h.page.getByText(/voice assistant disconnected|Disconnected/i).first()
      ).toBeVisible({ timeout: 30_000 });
      await expect(h.page.getByText("Listening", { exact: true })).toHaveCount(0);
    } finally {
      await endTest(h);
    }
  });
});
