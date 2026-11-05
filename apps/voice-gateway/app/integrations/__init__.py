"""Exotel AgentStream integration boundary (`/ws/exotel`).

Owns everything Exotel-specific: JSON event schemas, audio codec details and the
adapter that maps verified provider envelopes to internal domain events.

Protocol source of truth: docs/integrations/exotel-protocol.md (verified 2026-11-06
against official Exotel documentation; `clear` and `mark` responses follow the
documented agent-facing convention, not implementation guesses).
"""
