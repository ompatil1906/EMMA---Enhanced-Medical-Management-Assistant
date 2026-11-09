"use strict";

/* Day 17 test helper: load LiveKit credentials from the repo-root `.env`.

Never prints or commits credentials. The `.env` itself is never overwritten.
Only the non-secret `LIVEKIT_URL` plus API key/secret needed to mint probe/agent
tokens are read (these are test-instrumentation credentials, not user data).
*/

import * as fs from "fs";
import * as path from "path";

/* Resolve the repo root by walking up from this file until we find `.git`. */
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

const ROOT_ENV = path.join(findRepoRoot(__dirname), ".env");

export interface LiveKitEnv {
  livekitUrl: string;
  apiKey: string;
  apiSecret: string;
  practiceId: string;
}

function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

export function loadLiveKitEnv(): LiveKitEnv {
  const vars = fs.existsSync(ROOT_ENV) ? parseEnv(fs.readFileSync(ROOT_ENV, "utf8")) : {};
  // Process environment wins over .env — the same precedence the gateway
  // (pydantic-settings) uses. This lets an isolated test stack point at a
  // temporary LiveKit server with a throw-away keypair without editing `.env`
  // and without ever reusing a credential that lives in it.
  const livekitUrl = process.env.LIVEKIT_URL ?? vars["LIVEKIT_URL"];
  const apiKey = process.env.LIVEKIT_API_KEY ?? vars["LIVEKIT_API_KEY"];
  const apiSecret = process.env.LIVEKIT_API_SECRET ?? vars["LIVEKIT_API_SECRET"];
  const practiceId = vars["PRACTICE_ID"] ?? "demo-practice-001";
  if (!livekitUrl || !apiKey || !apiSecret) {
    throw new Error(
      "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing from the " +
        "process environment and the root .env"
    );
  }
  return { livekitUrl, apiKey, apiSecret, practiceId };
}
