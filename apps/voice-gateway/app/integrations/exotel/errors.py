"""Narrow error types for the Exotel integration (importable without the adapter)."""

from __future__ import annotations


class ExotelIntegrationError(Exception):
    """Base class for Exotel integration failures."""


class ExotelProtocolError(ExotelIntegrationError):
    """A provider frame did not match the verified AgentStream envelope."""


class ExotelNegotiationError(ExotelProtocolError):
    """The stream metadata cannot be negotiated (e.g. unsupported sample rate)."""
