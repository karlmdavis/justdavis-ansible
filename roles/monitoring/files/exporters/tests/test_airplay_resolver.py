"""Tests for the unicast query itself: real packets, and a real socket on the loopback interface."""

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

# zeroconf does not re-export its wire-level classes or record-type constants.
from zeroconf._dns import DNSAddress, DNSRecord, DNSService, DNSText
from zeroconf._protocol.incoming import DNSIncoming
from zeroconf._protocol.outgoing import DNSOutgoing
from zeroconf.const import _CLASS_IN, _FLAGS_AA, _FLAGS_QR_RESPONSE, _TYPE_A, _TYPE_SRV, _TYPE_TXT

from justdavis_monitoring_exporters.airplay import resolver as resolver_module
from justdavis_monitoring_exporters.airplay.models import UnicastAnswer
from justdavis_monitoring_exporters.airplay.resolver import AIRPLAY_TYPE, ZeroconfResolver, answer_from_packet

FQDN = f"Speaker A.{AIRPLAY_TYPE}"
HOST = "Speaker-A.local."
PLAYING = {"flags": "0x5b8c04", "gid": "00000000-0000-4000-8000-0000000000F0", "igl": "0"}


def txt_record(name: str, fields: dict[str, str]) -> DNSText:
    text = b"".join(bytes([len(f"{k}={v}")]) + f"{k}={v}".encode() for k, v in fields.items())
    return DNSText(name, _TYPE_TXT, _CLASS_IN, 4500, text)


def srv_record(name: str) -> DNSService:
    return DNSService(name, _TYPE_SRV, _CLASS_IN, 120, 0, 0, 7000, HOST)


def reply(answers: list[DNSRecord], additionals: list[DNSRecord] | None = None) -> bytes:
    outgoing = DNSOutgoing(_FLAGS_QR_RESPONSE | _FLAGS_AA)
    for record in answers:
        outgoing.add_answer_at_time(record, 0)
    for record in additionals or []:
        outgoing.add_additional_answer(record)
    return outgoing.packets()[0]


def test_answer_carries_the_txt_fields_of_the_queried_instance() -> None:
    address = DNSAddress(HOST, _TYPE_A, _CLASS_IN, 120, socket.inet_aton("192.0.2.110"))
    packet = reply([srv_record(FQDN), txt_record(FQDN, PLAYING)], [address])
    assert answer_from_packet(packet, FQDN) == UnicastAnswer(answered=True, txt=PLAYING)


def test_instance_name_is_matched_without_regard_to_case() -> None:
    packet = reply([txt_record(FQDN.upper(), PLAYING)])
    assert answer_from_packet(packet, FQDN) == UnicastAnswer(answered=True, txt=PLAYING)


def test_answer_without_a_txt_record_is_still_an_answer() -> None:
    assert answer_from_packet(reply([srv_record(FQDN)]), FQDN) == UnicastAnswer(answered=True, txt=None)


def test_txt_record_of_another_name_is_never_read_as_this_instances() -> None:
    other = txt_record("Speaker A._device-info._tcp.local.", {"model": "Example1,1"})
    packet = reply([other, srv_record(FQDN), txt_record(FQDN, PLAYING)])
    assert answer_from_packet(packet, FQDN) == UnicastAnswer(answered=True, txt=PLAYING)
    # With no record of its own, a reply about other names is not an answer for this instance.
    only_others = reply([other, txt_record(f"Speaker B.{AIRPLAY_TYPE}", PLAYING)])
    assert answer_from_packet(only_others, FQDN) == UnicastAnswer(answered=False, txt=None)


@pytest.mark.parametrize("packet", [b"", b"\xff" * 40, reply([srv_record(FQDN)])[:20]], ids=str)
def test_packet_that_cannot_be_decoded_counts_as_no_answer(packet: bytes) -> None:
    assert answer_from_packet(packet, FQDN) == UnicastAnswer(answered=False, txt=None)


@contextmanager
def device(respond: Callable[[bytes], bytes | None]) -> Iterator[tuple[int, list[bytes]]]:
    """A UDP socket on the loopback interface that answers each query with `respond(query)`, or stays
    silent when that is None. Yields its port and the queries it received."""
    received: list[bytes] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(2.0)

        def serve() -> None:
            try:
                data, sender = server.recvfrom(9000)
            except OSError:
                return
            received.append(data)
            answer = respond(data)
            if answer is not None:
                server.sendto(answer, sender)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield server.getsockname()[1], received
        finally:
            thread.join(timeout=3.0)


def loopback_resolver(monkeypatch: pytest.MonkeyPatch, port: int) -> ZeroconfResolver:
    # Built without __init__, which would start zeroconf's multicast listener; the unicast query
    # uses only the LAN address.
    resolver = ZeroconfResolver.__new__(ZeroconfResolver)
    resolver._lan_ip = "127.0.0.1"
    monkeypatch.setattr(resolver_module, "_MDNS_PORT", port)
    return resolver


def test_unicast_query_asks_for_srv_and_txt_and_reads_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    with device(lambda _query: reply([srv_record(FQDN), txt_record(FQDN, PLAYING)])) as (port, received):
        resolver = loopback_resolver(monkeypatch, port)
        answer = resolver.resolve_unicast("127.0.0.1", AIRPLAY_TYPE, "Speaker A", timeout_ms=1000)
    assert answer == UnicastAnswer(answered=True, txt=PLAYING)
    questions = DNSIncoming(received[0]).questions
    assert sorted((question.name, question.type) for question in questions) == [
        (FQDN, _TYPE_TXT),
        (FQDN, _TYPE_SRV),
    ]


def test_unicast_query_that_gets_no_reply_is_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    with device(lambda _query: None) as (port, _received):
        resolver = loopback_resolver(monkeypatch, port)
        answer = resolver.resolve_unicast("127.0.0.1", AIRPLAY_TYPE, "Speaker A", timeout_ms=200)
    assert answer == UnicastAnswer(answered=False, txt=None)


def test_unicast_query_to_a_closed_port_is_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    resolver = loopback_resolver(monkeypatch, port)
    answer = resolver.resolve_unicast("127.0.0.1", AIRPLAY_TYPE, "Speaker A", timeout_ms=200)
    assert answer == UnicastAnswer(answered=False, txt=None)


def test_reply_from_another_host_is_not_read_as_the_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    # The query goes to a silent device; a second socket, on another port, sends a well-formed reply
    # to the asking socket. The connected socket must not accept it.
    def answer_from_elsewhere(query_sender: tuple[str, int]) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
            stranger.bind(("127.0.0.1", 0))
            stranger.sendto(reply([txt_record(FQDN, PLAYING)]), query_sender)

    senders: list[tuple[str, int]] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        silent.settimeout(2.0)

        def watch() -> None:
            _data, sender = silent.recvfrom(9000)
            senders.append(sender)
            answer_from_elsewhere(sender)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        resolver = loopback_resolver(monkeypatch, silent.getsockname()[1])
        answer = resolver.resolve_unicast("127.0.0.1", AIRPLAY_TYPE, "Speaker A", timeout_ms=500)
        thread.join(timeout=3.0)
    assert senders
    assert answer == UnicastAnswer(answered=False, txt=None)
