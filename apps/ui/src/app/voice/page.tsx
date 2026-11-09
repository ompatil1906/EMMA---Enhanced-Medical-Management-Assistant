import { VoiceScreen } from "@/components/voice/voice-screen";

/* The voice interface route (`/voice`, documented in
 * docs/runbooks/browser-voice-demo.md). Renders the same VoiceScreen as `/'
 * — a single voice experience, two stable URLs. */
export default function Voice() {
  return <VoiceScreen />;
}
