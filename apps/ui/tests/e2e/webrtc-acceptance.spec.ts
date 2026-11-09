import { test, expect } from "@playwright/test";

import { runTest, stopAgentWorker, type BeginTestOptions, type TestHandle } from "./helpers/harness";

/* Day 17: real-browser WebRTC acceptance through self-hosted LiveKit.

   Browser UI → LiveKit → agent → mock STT → ConversationController → mock LLM
   → mock TTS → browser playback. Mock STT/LLM/TTS only; no paid API calls.

   Each scenario runs in full isolation: room purge → agent-worker restart
   (scenario mock-STT script) → room probe (server-side ground truth) →
   Chromium with a synthetic microphone file. Guaranteed teardown.
*/

test.describe("Day 17 WebRTC acceptance (A–G)", () => {

  async function scenario(fn: (h: TestHandle) => Promise<void>, options: BeginTestOptions = {}) {
    await runTest(fn, options);
  }

  const fillAndConnect = async (h: TestHandle) => {
    await h.page.getByPlaceholder("Sarah").fill("Sarah");
    await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
    await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
    await h.page.getByRole("button", { name: "Call Now" }).click();
    await expect(h.page.getByText("Connected to EMMA voice agent")).toBeVisible({
      timeout: 15_000,
    });
  };

  const fillAndCall = async (h: TestHandle) => {
    await fillAndConnect(h);
    await expect(h.page.getByText("Listening")).toBeVisible({ timeout: 15_000 });
  };

  /* Scenarios that only exercise transport/UI behaviour pass a mock-STT
     utterance trigger longer than the test itself. The mock STT derives
     utterances from accumulated caller-audio *duration* (never from content),
     so the browser's always-on fake microphone would otherwise inject a
     scripted turn every ~1s and hold the pipeline in SPEAKING — making
     "settles to Listening" a coin flip rather than an assertion. */
  const noScriptedTurns = { mockSttUtteranceMs: 60_000 } as const;

  // --- A: startup ----------------------------------------------------------

  test("A startup: call connects, the browser settles to Listening, console clean", async () => {
    await scenario(async (h) => {
      await fillAndCall(h);

      // Server-side ground truth: the agent participant was observed in the
      // room by the probe (identity captured during `beginTest`).
      expect(h.agentIdentity ?? "").toMatch(/^agent-/);

      // A: no console / page errors on the happy path.
      expect(h.capturedErrors).toEqual([]);
    }, { ...noScriptedTurns });
  });

  // --- B: auth / room connect ----------------------------------------------

  test("B auth/room connect: browser caller and agent share one room", async () => {
    await scenario(async (h) => {
      await fillAndCall(h);

      // Both the browser caller (emma-caller-...) and the agent (agent-<job_id>)
      // are participants in the same room.
      expect(h.agentIdentity ?? "").toMatch(/^agent-/);
      const caller = await h.probe.waitFor(
        (e) => e.event === "participant" && String(e.identity).startsWith("emma-caller-"),
        { timeoutMs: 15_000 }
      );
      expect(
        caller,
        `probe participants: [${h.probe
          .eventsOfType("participant")
          .map((e) => String(e.identity))
          .join(", ")}]`
      ).not.toBeNull();
    }, { ...noScriptedTurns });
  });

  // --- C: mic transmission + WebRTC stats ----------------------------------

  test("C mic transmission + WebRTC stats: caller audio reaches the room", async () => {
    await scenario(async (h) => {
      await fillAndCall(h);

      // Server-side: the caller's audio track was published and subscribed.
      const callerTrack = await h.probe.waitFor(
        (e) =>
          e.event === "track_subscribed" &&
          String(e.participant).startsWith("emma-caller-") &&
          e.isAudio === true,
        { timeoutMs: 15_000 }
      );
      expect(callerTrack).not.toBeNull();

      // Client-side: the WebRTC API surface is functional in this Chromium
      // build — RTCPeerConnection exists and exposes getStats / getSenders.
      const statsOk = await h.page.evaluate(async () => {
        try {
          if (typeof RTCPeerConnection === "undefined") return false;
          const pc = new RTCPeerConnection();
          const ok =
            typeof pc.getStats === "function" &&
            typeof pc.getSenders === "function";
          pc.close();
          return ok;
        } catch {
          return false;
        }
      });
      expect(statsOk).toBe(true);
    }, { ...noScriptedTurns });
  });

  // --- D: agent audio ------------------------------------------------------

  test("D agent audio: assistant audio (greeting) flows to the browser", async () => {
    await scenario(async (h) => {
      await fillAndCall(h);

      // Server-side ground truth: the room carried audible frames (the greeting
      // is published as a 240 Hz tone at ~-18 dBFS, so a nonzero RMS is real
      // audio and not the near-silent caller microphone).
      const agentAudio = await h.probe.waitFor(
        (e) =>
          e.event === "audio_progress" &&
          typeof e.nonzero_frames === "number" &&
          e.nonzero_frames > 0,
        { timeoutMs: 15_000 }
      );
      expect(agentAudio, `probe events: ${JSON.stringify(h.probe.events())}`).not.toBeNull();

      // Client-side: the assistant's track is attached to an <audio> element
      // and actually playing in the browser (not merely published).
      const playback = await h.page.evaluate(async () => {
        const el = document.querySelector<HTMLAudioElement>(
          "audio[data-emma-remote-audio]"
        );
        if (!el) return { attached: false, track: false, playing: false, time: 0 };
        const stream = el.srcObject as MediaStream | null;
        const track = !!stream && stream.getAudioTracks().length > 0;
        await new Promise((resolve) => setTimeout(resolve, 800));
        return { attached: true, track, playing: !el.paused, time: el.currentTime };
      });
      expect(playback.attached).toBe(true);
      expect(playback.track).toBe(true);
      expect(playback.playing).toBe(true);

      // Day 18E: measurable output. `__emmaVoiceDiag` samples the remote audio
      // graph, so this proves audible samples reached the browser — an attached
      // element with a paused track would have peakRms === 0.
      const diag = await h.page.evaluate(async () => {
        const read = () =>
          (window as unknown as {
            __emmaVoiceDiag?: { peakRms: number; frames: number; playing: boolean };
          }).__emmaVoiceDiag ?? null;
        for (let i = 0; i < 40 && (read()?.peakRms ?? 0) === 0; i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const current = read();
        return current ? { ...current } : null;
      });
      expect(diag, "remote-audio diagnostics must be exposed").not.toBeNull();
      expect(diag?.peakRms ?? 0, "browser playback must contain audible samples").toBeGreaterThan(0);
      expect(diag?.frames ?? 0).toBeGreaterThan(0);
    }, { ...noScriptedTurns });
  });

  // --- E: full round trip (opening hours → RAG abstain) --------------------

  test("E full round trip: opening-hours question → RAG abstain response", async () => {
    await scenario(
      async (h) => {
        await fillAndCall(h);

        // The caller's final transcript (mock STT line 1) appears in the UI.
        await expect(h.page.getByText("opening hours")).toBeVisible({ timeout: 20_000 });

        // The assistant's deterministic RAG-abstain answer appears.
        await expect(
          h.page.getByText("I don't have information about that in the practice records")
        ).toBeVisible({ timeout: 20_000 });

        // Server-side ground truth: both control events crossed the data channel.
        await h.probe.waitFor(
          (e) =>
            e.event === "data" &&
            e.data_event === "transcript" &&
            String(e.text ?? "").includes("opening hours"),
          { timeoutMs: 20_000 }
        );
        await h.probe.waitFor(
          (e) =>
            e.event === "data" &&
            e.data_event === "assistant" &&
            String(e.text ?? "").includes("practice records"),
          { timeoutMs: 20_000 }
        );
      },
      {
        // Script line 1 routes to practice-information; the empty mock vector
        // store makes the pipeline abstain deterministically. Utterance trigger
        // is raised so the first scripted line lands after the greeting
        // (pipeline is LISTENING by then; a final during the greeting would
        // hit an invalid SPEAKING→PROCESSING transition and crash the turn).
        script: "What are your opening hours?,I need a prescription repeat",
        mockSttUtteranceMs: 9000,
      }
    );
  });

  // --- F: mute / unmute / disconnect / recovery ----------------------------

  test("F mute / unmute / disconnect / recovery", async () => {
    await scenario(async (h) => {
      await fillAndCall(h);

      // Mute → button reports unmute.
      await h.page.getByTitle("Mute microphone").click();
      await expect(h.page.getByTitle("Unmute microphone")).toBeVisible({ timeout: 5_000 });

      // Unmute → button reports mute again.
      await h.page.getByTitle("Unmute microphone").click();
      await expect(h.page.getByTitle("Mute microphone")).toBeVisible({ timeout: 5_000 });

      // Disconnect (End Conversation) → back to the form (Ready).
      await h.page.getByRole("button", { name: "End Conversation" }).click();
      await expect(h.page.getByPlaceholder("Sarah")).toBeVisible({ timeout: 10_000 });

      // Recovery: place another call; a fresh room/agent dispatch serves it.
      await h.page.getByPlaceholder("Sarah").fill("Sarah");
      await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
      await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
      await h.page.getByRole("button", { name: "Call Now" }).click();
      await expect(h.page.getByText("Connected to EMMA voice agent")).toBeVisible({
        timeout: 15_000,
      });
      await expect(h.page.getByText("Listening")).toBeVisible({ timeout: 15_000 });
    }, { ...noScriptedTurns });
  });

  // --- F: invalid token ----------------------------------------------------

  test("F invalid token: connection failure is surfaced, not silently swallowed", async () => {
    await scenario(async (h) => {
      // Intercept the gateway token endpoint and return a bogus token.
      await h.page.route("**/api/voice/token", async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            token: "definitely-not-a-valid-jwt",
            room: "emma-voice",
            livekit_url: "ws://127.0.0.1:7880",
          }),
        });
      });

      await h.page.getByPlaceholder("Sarah").fill("Sarah");
      await h.page.getByPlaceholder("Jenkins").fill("Jenkins");
      await h.page.getByPlaceholder("7700 900077").fill("7700 900077");
      await h.page.getByRole("button", { name: "Call Now" }).click();

      // The call must not stay in "connecting" — the bad token must cause failure.
      await expect(h.page.getByText("Connecting…")).not.toBeVisible({ timeout: 10_000 });

      // The server rejects the invalid JWT with a 401 WebSocket error.
      expect(
        h.capturedErrors.some(
          (e) =>
            e.includes("401") || e.includes("Authentication") || e.includes("Unauthorized")
        )
      ).toBe(true);
    });
  });

  // --- G: software barge-in ------------------------------------------------

  test("G software barge-in: data-level interruption cancels assistant output", async () => {
    await scenario(
      async (h) => {
        // No "settles to Listening" assertion here: the barge-in deliberately
        // interrupts the agent mid-greeting, so the session is meant to leave
        // LISTENING almost immediately after connect.
        await fillAndConnect(h);

        // Software barge-in: the probe sends `{"event":"barge-in"}` while the
        // agent is SPEAKING, and the agent echoes a barge-in control event back
        // on the data channel (data-level interruption, never acoustic).
        const bargeIn = await h.probe.waitFor(
          (e) => e.event === "data" && e.data_event === "barge-in",
          { timeoutMs: 15_000 }
        );
        expect(bargeIn, `probe events: ${JSON.stringify(h.probe.events())}`).not.toBeNull();

        // After cancellation the session stops speaking (returns to LISTENING).
        await expect(h.page.getByText("Speaking", { exact: true })).not.toBeVisible({
          timeout: 10_000,
        });
        await expect(h.page.getByText("Listening", { exact: true })).toBeVisible({
          timeout: 10_000,
        });
      },
      {
        ...noScriptedTurns,
        autoBargeIn: true, // probe sends barge-in when the agent reports speaking
        micFile: "tone.wav", // amplitude-modulated synthetic mic (not acoustic)
      }
    );
  });
});

test.afterAll(async () => {
  await stopAgentWorker();
});
