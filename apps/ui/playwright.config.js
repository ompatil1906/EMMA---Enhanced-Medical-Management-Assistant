// eslint-disable-next-line @typescript-eslint/no-require-imports
const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // The acceptance suite requires a clean room + agent per test; run serially.
  workers: 1,
  // Each scenario starts a fresh LiveKit agent worker, purges the room, spawns
  // the server-side probe and launches Chromium — 30s (the Playwright default)
  // is not enough headroom for a real end-to-end WebRTC call.
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI ? [["html"], ["list"]] : [["list"]],
  // Day 13/17 scratch drafts kept on disk (uncommitted) — not part of the
  // acceptance suite. The acceptance contract is webrtc-acceptance.spec.ts.
  testIgnore: [
    "**/debug.spec.ts",
    "**/debug2.spec.ts",
    "**/test_f_debug.spec.ts",
    "**/livekit-browser.spec.ts",
  ],
  // Day 18: `tests/e2e-live/` (paid live-provider acceptance) is deliberately
  // outside this `testDir`, so the default suite cannot start real provider
  // calls. It runs only via `playwright.live.config.js`. 
  use: {
    // Overridable so an isolated stack (temp LiveKit + temp gateway + temp UI)
    // can be tested without touching the developer's running services.
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    permissions: ["microphone"],
    launchOptions: {
      args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    },
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Full Chromium build (new headless): the older headless shell has no
        // mediaDevices / WebRTC media stack, which the voice tests require.
        channel: "chromium",
      },
    },
  ],
});
