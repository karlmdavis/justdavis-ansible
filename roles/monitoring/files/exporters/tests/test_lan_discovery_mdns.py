"""Tests for the mDNS source: reading instances from a zeroconf cache and turning them into sightings."""

import socket

from zeroconf._cache import DNSCache
from zeroconf._dns import DNSAddress, DNSPointer, DNSService, DNSText
from zeroconf.const import _CLASS_IN, _TYPE_A, _TYPE_PTR, _TYPE_SRV, _TYPE_TXT

from justdavis_monitoring_exporters.common.settings import Mac
from justdavis_monitoring_exporters.lan_discovery.mdns import (
    CachedInstance,
    cached_instances,
    instances_by_hostname,
    mac_from_instance,
    mdns_sightings,
)

AIRPLAY = "_airplay._tcp.local."
RAOP = "_raop._tcp.local."
HUE = "_hue._tcp.local."
TYPES = [AIRPLAY, RAOP, HUE]
NOW = 1_000_000.0
MONO_MS = 5_000_000.0


def txt(fields: dict[str, str]) -> bytes:
    return b"".join(bytes([len(f"{k}={v}")]) + f"{k}={v}".encode() for k, v in fields.items())


def cache_with_speaker_and_bridge(*, speaker_a_created_ms: float = MONO_MS - 30_000) -> DNSCache:
    cache = DNSCache()
    cache.async_add_records(
        [
            DNSPointer(AIRPLAY, _TYPE_PTR, _CLASS_IN, 4500, f"Speaker A.{AIRPLAY}", created=MONO_MS),
            DNSService(
                f"Speaker A.{AIRPLAY}",
                _TYPE_SRV,
                _CLASS_IN,
                120,
                0,
                0,
                7000,
                "Speaker-A.local.",
                created=MONO_MS,
            ),
            DNSText(
                f"Speaker A.{AIRPLAY}",
                _TYPE_TXT,
                _CLASS_IN,
                4500,
                txt({"deviceid": "AA:BB:CC:DD:EE:01"}),
                created=MONO_MS,
            ),
            DNSPointer(RAOP, _TYPE_PTR, _CLASS_IN, 4500, f"AABBCCDDEE01@Speaker A.{RAOP}", created=MONO_MS),
            DNSService(
                f"AABBCCDDEE01@Speaker A.{RAOP}",
                _TYPE_SRV,
                _CLASS_IN,
                120,
                0,
                0,
                7000,
                "Speaker-A.local.",
                created=MONO_MS,
            ),
            DNSAddress(
                "Speaker-A.local.",
                _TYPE_A,
                _CLASS_IN,
                120,
                socket.inet_aton("192.0.2.110"),
                created=speaker_a_created_ms,
            ),
            DNSPointer(HUE, _TYPE_PTR, _CLASS_IN, 4500, f"Hue Bridge - 000030.{HUE}", created=MONO_MS),
            DNSService(
                f"Hue Bridge - 000030.{HUE}",
                _TYPE_SRV,
                _CLASS_IN,
                120,
                0,
                0,
                443,
                "020000000030.local.",
                created=MONO_MS,
            ),
            DNSText(
                f"Hue Bridge - 000030.{HUE}",
                _TYPE_TXT,
                _CLASS_IN,
                4500,
                txt({"bridgeid": "020000fffe000030", "modelid": "BSB002"}),
                created=MONO_MS,
            ),
            DNSAddress(
                "020000000030.local.",
                _TYPE_A,
                _CLASS_IN,
                120,
                socket.inet_aton("192.0.2.21"),
                created=MONO_MS - 9_000,
            ),
            # An instance whose host has no A record yet (announced over a path that lost the rest).
            DNSPointer(AIRPLAY, _TYPE_PTR, _CLASS_IN, 4500, f"Silent.{AIRPLAY}", created=MONO_MS),
        ]
    )
    return cache


def test_cache_read_pairs_each_instance_with_its_hosts_addresses_and_their_ages() -> None:
    instances = {
        i.fqdn: i
        for i in cached_instances(cache_with_speaker_and_bridge(), TYPES, now=NOW, monotonic_ms=MONO_MS)
    }
    speaker = instances[f"Speaker A.{AIRPLAY}"]
    assert (speaker.service, speaker.instance, speaker.server) == (
        "_airplay._tcp",
        "Speaker A",
        "Speaker-A.local.",
    )
    assert speaker.txt == {"deviceid": "AA:BB:CC:DD:EE:01"}
    assert speaker.addresses == (("192.0.2.110", NOW - 30.0),)
    assert instances[f"Hue Bridge - 000030.{HUE}"].addresses == (("192.0.2.21", NOW - 9.0),)
    silent = instances[f"Silent.{AIRPLAY}"]
    assert (silent.server, silent.addresses) == (None, ())


def test_expired_address_records_are_left_out() -> None:
    # The A record's 120 s TTL has passed at this monotonic time.
    instances = {
        i.fqdn: i
        for i in cached_instances(
            cache_with_speaker_and_bridge(speaker_a_created_ms=MONO_MS - 200_000),
            TYPES,
            now=NOW,
            monotonic_ms=MONO_MS,
        )
    }
    assert instances[f"Speaker A.{AIRPLAY}"].addresses == ()


def test_sightings_identify_the_bridge_by_mac_and_apple_devices_by_hostname() -> None:
    sightings = mdns_sightings(
        cached_instances(cache_with_speaker_and_bridge(), TYPES, now=NOW, monotonic_ms=MONO_MS)
    )
    by_address = {s.address: s for s in sightings}
    bridge = by_address["192.0.2.21"]
    assert (bridge.mac, bridge.hostname, bridge.observed_at) == (
        Mac("02:00:00:00:00:30"),
        "020000000030",
        NOW - 9.0,
    )
    assert bridge.extra == {"service": "_hue._tcp", "identifier": "020000fffe000030"}
    speaker = by_address["192.0.2.110"]
    # The AirPlay deviceid is NOT the WiFi MAC, so the device is known by hostname only,
    # and the `_airplay` instance (not the `_raop` one) supplies the name.
    assert (speaker.mac, speaker.hostname, speaker.name) == (None, "speaker-a", "Speaker A")
    assert speaker.extra == {"service": "_airplay._tcp", "identifier": "AA:BB:CC:DD:EE:01"}
    assert len(sightings) == 2


def test_bridge_id_must_have_the_fffe_infix_to_be_a_mac() -> None:
    assert mac_from_instance("_hue._tcp", {"bridgeid": "020000fffe000030"}) == Mac("02:00:00:00:00:30")
    assert mac_from_instance("_hue._tcp", {"bridgeid": "0200000000000030"}) is None
    assert mac_from_instance("_hue._tcp", {"bridgeid": "zz0000fffe000030"}) is None
    assert mac_from_instance("_hue._tcp", {}) is None
    assert mac_from_instance("_airplay._tcp", {"deviceid": "02:00:00:00:00:10"}) is None


def test_instances_by_hostname_prefers_the_airplay_instance() -> None:
    raop = CachedInstance(
        "_raop._tcp", "AABB@Speaker A", f"AABB@Speaker A.{RAOP}", "Speaker-A.local.", {}, ()
    )
    airplay = CachedInstance("_airplay._tcp", "Speaker A", f"Speaker A.{AIRPLAY}", "Speaker-A.local.", {}, ())
    assert instances_by_hostname([raop, airplay])["speaker-a"] is airplay
    assert instances_by_hostname([airplay, raop])["speaker-a"] is airplay
