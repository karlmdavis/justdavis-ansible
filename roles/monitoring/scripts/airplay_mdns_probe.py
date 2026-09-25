r"""Ask each tracked HomePod for its AirPlay mDNS records two ways, from the wired LAN: a fresh MULTICAST
resolve (what the airplay exporter and a phone's AirPlay picker do) and a UNICAST query sent straight to
the HomePod's own address on port 5353. A HomePod that answers unicast but not multicast is up and
advertising; the multicast path between the wired LAN and its radio has lost it (seen 2026-09-24 on
5 GHz clients of every access point, never on 2.4 GHz; they recover on their own next announcement).
One that answers neither has its AirPlay service down.

Runs INSIDE the airplay_exporter container: it needs the host network the exporter probes from and the
`zeroconf` package in the image's virtualenv. The container's filesystem is read-only, so the script is
fed to `python -` over stdin rather than copied or run with `uv run`. It reads MONITORING_LAN_IP and
MONITORING_TRACKED_CLIENTS from the container's environment, and the HomePods' current addresses from
HOMEPOD_IPS, which is the raw JSON answer to the Prometheus query `monitoring_target_info{kind="homepod"}`
(the redirect sits outside the ssh quotes, so the copy in the repo is what runs):

    ssh eddings.justdavis.com 'IPS=$(curl -s http://127.0.0.1:9090/api/v1/query \
        --data-urlencode "query=monitoring_target_info{kind=\"homepod\"}") &&
        cd /opt/monitoring &&
        sudo docker compose exec -T -e HOMEPOD_IPS="$IPS" airplay_exporter python -' \
        < roles/monitoring/scripts/airplay_mdns_probe.py

Options go after `python -`: `--wake` sends the unicast query first and the multicast one right after
it (a HomePod that still fails multicast just after answering unicast is awake, so power save is not the
cause); `--targets name=ip,...` probes other devices instead of the tracked HomePods, first learning
their AirPlay-related service instances with unicast PTR queries. Exits 1 when any multicast resolve
failed, so a shell loop can watch for a change.
"""

import argparse
import json
import os
import socket
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from zeroconf import IPVersion, Zeroconf

# zeroconf does not re-export its wire-level classes or record-type constants, so they come from the
# modules that define them.
from zeroconf._dns import DNSPointer, DNSQuestion, DNSRecord
from zeroconf._protocol.incoming import DNSIncoming
from zeroconf._protocol.outgoing import DNSOutgoing
from zeroconf.const import _CLASS_IN, _FLAGS_QR_QUERY, _TYPE_PTR, _TYPE_SRV, _TYPE_TXT

AIRPLAY_TYPE = "_airplay._tcp.local."
# The service types an Apple device advertises that matter to AirPlay and Home; `--targets` looks for
# instances of these.
DISCOVERY_TYPES = (AIRPLAY_TYPE, "_raop._tcp.local.", "_companion-link._tcp.local.", "_rdlink._tcp.local.")
MDNS_PORT = 5353
MULTICAST_TIMEOUT_MS = 4000
UNICAST_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class Target:
    label: str
    # None when Prometheus knows no address for a tracked HomePod; only multicast can be tried then.
    ip: str | None


@dataclass(frozen=True, slots=True)
class Instance:
    service_type: str
    # The full instance name, e.g. "Kitchen._airplay._tcp.local.".
    name: str


def env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        sys.exit(
            f"{name} is not set; this script runs inside the airplay_exporter container (see its docstring)"
        )
    return value


def unicast_query(lan_ip: str, ip: str, questions: Iterable[tuple[str, int]]) -> list[DNSRecord]:
    """Send one mDNS query straight to `ip` from `lan_ip`; the answer records, or [] when none came back."""
    outgoing = DNSOutgoing(_FLAGS_QR_QUERY)
    for name, record_type in questions:
        outgoing.add_question(DNSQuestion(name, record_type, _CLASS_IN))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((lan_ip, 0))
        sock.settimeout(UNICAST_TIMEOUT_SECONDS)
        for packet in outgoing.packets():
            sock.sendto(packet, (ip, MDNS_PORT))
        try:
            data, _ = sock.recvfrom(9000)
        except TimeoutError:
            return []
    return DNSIncoming(data).answers()


def multicast_resolve(lan_ip: str, instance: Instance) -> float | None:
    """Resolve the instance with a fresh multicast browser, as the exporter does; seconds taken, or None."""
    zeroconf = Zeroconf(interfaces=[lan_ip], ip_version=IPVersion.V4Only)
    try:
        started = time.monotonic()
        info = zeroconf.get_service_info(instance.service_type, instance.name, timeout=MULTICAST_TIMEOUT_MS)
        return time.monotonic() - started if info else None
    finally:
        zeroconf.close()


def unicast_resolve(lan_ip: str, ip: str, instance: Instance) -> bool:
    return bool(unicast_query(lan_ip, ip, [(instance.name, _TYPE_SRV), (instance.name, _TYPE_TXT)]))


def discover_instances(lan_ip: str, ip: str) -> list[Instance]:
    """The device's instances of the discovery types, learned by unicast PTR queries."""
    answers = unicast_query(lan_ip, ip, [(service_type, _TYPE_PTR) for service_type in DISCOVERY_TYPES])
    pointers = {(record.name, record.alias) for record in answers if isinstance(record, DNSPointer)}
    return [Instance(service_type, name) for service_type, name in sorted(pointers)]


def probe(lan_ip: str, target: Target, instance: Instance, *, wake: bool) -> bool:
    """Probe one instance both ways and print a line; True when the multicast resolve succeeded."""
    unicast: bool | None = None
    if wake and target.ip is not None:
        unicast = unicast_resolve(lan_ip, target.ip, instance)
    seconds = multicast_resolve(lan_ip, instance)
    if not wake and target.ip is not None:
        unicast = unicast_resolve(lan_ip, target.ip, instance)

    multicast_text = (
        f"resolved in {seconds:.1f} s"
        if seconds is not None
        else f"NOT resolved ({MULTICAST_TIMEOUT_MS // 1000} s)"
    )
    if unicast is None:
        unicast_text = "not tried (address unknown)"
    else:
        unicast_text = "answered" if unicast else "NO ANSWER"
    short_name = instance.name.removesuffix("." + instance.service_type)[:32]
    print(
        f"{target.label:18} {target.ip or '?':15} {instance.service_type:24} {short_name:32} "
        f"multicast: {multicast_text:22} unicast: {unicast_text}"
    )
    return seconds is not None


def homepod_addresses(prometheus_answer: str) -> dict[str, str]:
    """Tracked name -> address from the raw `monitoring_target_info{kind="homepod"}` query answer."""
    payload = json.loads(prometheus_answer)
    if payload.get("status") != "success":
        sys.exit(f"HOMEPOD_IPS is not a successful Prometheus answer: {prometheus_answer[:200]}")
    return {
        str(series["metric"]["name"]): str(series["metric"]["ip"]) for series in payload["data"]["result"]
    }


def tracked_homepods(environment: Mapping[str, str]) -> list[tuple[Target, Instance]]:
    """Every tracked HomePod with its current address and `_airplay._tcp` instance name."""
    addresses = homepod_addresses(environment["HOMEPOD_IPS"])
    probes: list[tuple[Target, Instance]] = []
    for client in json.loads(environment["MONITORING_TRACKED_CLIENTS"]):
        if client.get("kind") != "homepod":
            continue
        name = str(client["name"])
        airplay_name = str(client.get("airplay_name") or name)
        probes.append(
            (Target(name, addresses.get(name)), Instance(AIRPLAY_TYPE, f"{airplay_name}.{AIRPLAY_TYPE}"))
        )
    return sorted(probes, key=lambda probe: probe[0].label)


def parse_targets(spec: str) -> list[Target]:
    targets: list[Target] = []
    for item in spec.split(","):
        label, separator, ip = item.partition("=")
        if not separator or not label or not ip:
            sys.exit(f"--targets expects name=ip,...; got {item!r}")
        targets.append(Target(label, ip))
    return targets


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--wake", action="store_true", help="Send the unicast query first, then the multicast one."
    )
    parser.add_argument(
        "--targets", metavar="NAME=IP,...", help="Probe these devices instead of the tracked HomePods."
    )
    args = parser.parse_args(argv)

    lan_ip = env("MONITORING_LAN_IP")
    order = "unicast, then multicast" if args.wake else "multicast, then unicast"
    print(f"probing from {lan_ip} ({order}; multicast timeout {MULTICAST_TIMEOUT_MS // 1000} s)")

    all_resolved = True
    if args.targets:
        for target in parse_targets(args.targets):
            assert target.ip is not None
            instances = discover_instances(lan_ip, target.ip)
            if not instances:
                print(f"{target.label:18} {target.ip:15} no instances answered by unicast (asleep, or none)")
                continue
            for instance in instances:
                all_resolved &= probe(lan_ip, target, instance, wake=args.wake)
    else:
        env("HOMEPOD_IPS")
        env("MONITORING_TRACKED_CLIENTS")
        for target, instance in tracked_homepods(os.environ):
            all_resolved &= probe(lan_ip, target, instance, wake=args.wake)
    return 0 if all_resolved else 1


if __name__ == "__main__":
    sys.exit(main())
