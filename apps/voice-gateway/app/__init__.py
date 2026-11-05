"""Voice gateway application package (Day 2 foundation).

Layout:
  app.api       — HTTP/WebSocket transport wiring (routes coordinate only)
  app.core      — config, logging, process state / session registry
  app.domain    — provider-independent events, audio model, session bookkeeping
  app.integrations — provider adapters (Exotel only today)
  app.services  — the audio-handling service (echo today; VAD/STT/LLM/TTS later)
"""
