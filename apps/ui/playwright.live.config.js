// eslint-disable-next-line @typescript-eslint/no-require-imports
const base = require("./playwright.config");

/* Day 18: config for the OPT-IN live-provider acceptance run.

   The default config scans `tests/e2e` only, so the eight-scenario offline
   suite can never start a paid provider call. This config points at
   `tests/e2e-live`, which is never scanned by `npx playwright test`.

   Usage (see docs/LIVE_PROVIDER_ACCEPTANCE.md):

     EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
       npx playwright test -c playwright.live.config.js --reporter=list
*/

module.exports = {
  ...base,
  testDir: "./tests/e2e-live",
  testIgnore: [],
  // One controlled conversation; no retries (a retry would spend again).
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 30_000 },
};
