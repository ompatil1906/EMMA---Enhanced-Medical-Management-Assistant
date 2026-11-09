"use strict";

import { chromium, type Browser, type LaunchOptions } from "playwright";
import * as path from "path";

/* Day 17 test helper: launches a real Chromium browser configured for the
   voice acceptance suite.

   Chromium flags:
   * `--use-fake-ui-for-media-stream` — auto-grant mic permission (no dialog).
   * `--use-fake-device-for-media-stream` — synthetic capture device.
   * `--use-file-for-fake-audio-capture=<wav>` — the WAV file played as mic
     input (silence.wav for the default mic; tone.wav for the barge-in test).
   * `--autoplay-policy=no-user-gesture-required` — allow Web Audio playback
     without a gesture so assistant audio is audible.
   * `--use-fake-device-for-media-stream` + the channel build ("chromium")
     gives a working mediaDevices / WebRTC stack (the headless shell does not).
*/

const FIXTURES = path.resolve(__dirname, "../fixtures");

const BASE_ARGS = [
  "--use-fake-ui-for-media-stream",
  "--use-fake-device-for-media-stream",
  "--autoplay-policy=no-user-gesture-required",
];

export interface LaunchVoiceBrowserOptions {
  /** WAV that plays as the browser's microphone input: a fixture name
     (`silence.wav`, `tone.wav`) or an absolute path (Day 18 uses a locally
     synthesised speech WAV for the live-provider run). */
  fakeAudioCaptureFile?: string;
  headless?: boolean;
}

function resolveCaptureFile(value: string): string {
  if (path.isAbsolute(value) || value.includes(path.sep)) return value;
  return path.join(FIXTURES, value);
}

export async function launchVoiceBrowser({
  fakeAudioCaptureFile = "silence.wav",
  headless = true,
}: LaunchVoiceBrowserOptions = {}): Promise<Browser> {
  const args = [...BASE_ARGS];
  if (fakeAudioCaptureFile) {
    args.push(`--use-file-for-fake-audio-capture=${resolveCaptureFile(fakeAudioCaptureFile)}`);
  }

  const launchOptions: LaunchOptions = {
    headless,
    channel: "chromium",
    args,
  };

  return chromium.launch(launchOptions);
}
