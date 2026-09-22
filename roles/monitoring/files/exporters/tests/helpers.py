"""Helpers shared by the test modules."""

from pathlib import Path

from justdavis_monitoring_exporters.common.settings import ClientKind, TrackedClient, normalise_mac

FIXTURES = Path(__file__).parent / "fixtures"


def tracked(mac: str, name: str, kind: ClientKind, airplay_name: str | None = None) -> TrackedClient:
    """A tracked client from a plain MAC string, as the settings parser would build it."""
    return TrackedClient(
        mac=normalise_mac(mac, "tests"), name=name, kind=kind, airplay_name=airplay_name or name
    )
