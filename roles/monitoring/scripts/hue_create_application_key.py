# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Create the Hue bridge application key the monitoring role's Hue exporter uses, without showing it.

WHEN TO RUN IT

* Once, before the first deploy of the Hue exporter: the key goes into the vault as
  `vault_hue_application_key`.
* Again if the key is ever lost or revoked (the bridge forgets keys when it is factory reset).
* With `--check`, any time, to confirm the bridge's TLS certificate validates against Signify's root
  and names the bridge id: that is what the exporter requires before it sends the key.

WHAT IT DOES

Fetches the bridge's public configuration (`/api/0/config`, no key needed) over TLS verified against
Signify's published root certificate (shipped with the exporters, next to the Hue module), checks that
the certificate's common name is the bridge id, waits for the bridge's link button to be pressed, asks
the bridge for a new application key, and writes the key to a file only you can read. The key is never
printed, and the bridge's answer is never shown.

HOW TO RUN IT

From the repository on the controller, on the LAN (the bridge is not reachable from outside it):

    uv run roles/monitoring/scripts/hue_create_application_key.py --bridge 192.0.2.21

Then put the file's contents into the vault yourself and remove the file:

    ansible-vault edit group_vars/all/vault.yml   # vault_hue_application_key: <contents of the file>
    rm ~/hue-application-key.txt

Options:

* `--check` only verifies TLS and prints the bridge id and software version; no key is created.
* `--out PATH` writes the key somewhere other than `~/hue-application-key.txt` (the file must not exist).
* `--devicetype NAME` names the key on the bridge (default `justdavis-monitoring#eddings`, which is how
  the Hue app lists it).

SAMPLE OUTPUT

    bridge 192.0.2.21: id 001788FFFE000000, model BSB002, software 1978545030, API 1.78.0
    TLS: chain verified against Signify's root; certificate names the bridge id
    Press the round link button on the bridge, then press Enter here (within 30 seconds)...
    key written to /Users/example/hue-application-key.txt (40 characters, mode 0600)

HOW TO READ IT

* "link button not pressed" repeated six times: the button was not pressed within the window; run it
  again and press the button right after Enter.
* A TLS error names what failed: a certificate that does not validate against Signify's root, or one
  whose common name is not the bridge id. Do not pass a key to such a bridge; the exporter will not.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT_CERT = (
    Path(__file__).resolve().parents[1]
    / "files/exporters/src/justdavis_monitoring_exporters/hue/huebridge_cacert.pem"
)
_RETRIES = 6
_RETRY_SECONDS = 5
_TIMEOUT_SECONDS = 10


def tls_context() -> ssl.SSLContext:
    """Chain verification against Signify's root; the name check is done by hand, because the bridge's
    certificate has no subjectAltName and names the bridge id in its common name only."""
    context = ssl.create_default_context(cafile=str(ROOT_CERT))
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def fetch(url: str, context: ssl.SSLContext, data: bytes | None = None) -> tuple[object, str]:
    """The decoded JSON body and the certificate's common name."""
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS, context=context) as response:
        certificate = response.fp.raw._sock.getpeercert()  # type: ignore[attr-defined]
        common_name = next(
            (value for rdn in certificate["subject"] for key, value in rdn if key == "commonName"), ""
        )
        return json.loads(response.read()), str(common_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--bridge", required=True, help="the bridge's LAN address")
    parser.add_argument("--out", default=str(Path.home() / "hue-application-key.txt"))
    parser.add_argument("--devicetype", default="justdavis-monitoring#eddings")
    parser.add_argument("--check", action="store_true", help="verify TLS and print the bridge id only")
    args = parser.parse_args()
    base = f"https://{args.bridge}"
    context = tls_context()
    try:
        config, common_name = fetch(f"{base}/api/0/config", context)
    except ssl.SSLError as exc:
        print(f"TLS: the bridge's certificate does not validate against Signify's root: {exc}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"could not reach the bridge at {args.bridge}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(config, dict):
        print("the bridge's configuration is not the expected JSON object", file=sys.stderr)
        return 1
    bridge_id = str(config.get("bridgeid", ""))
    print(
        f"bridge {args.bridge}: id {bridge_id}, model {config.get('modelid')}, "
        f"software {config.get('swversion')}, API {config.get('apiversion')}"
    )
    if common_name.lower() != bridge_id.lower():
        print(f"TLS: the certificate names {common_name!r}, not the bridge id {bridge_id!r}", file=sys.stderr)
        return 1
    print("TLS: chain verified against Signify's root; certificate names the bridge id")
    if args.check:
        return 0
    out = Path(args.out).expanduser()
    if out.exists():
        print(f"{out} already exists; remove it or pass --out", file=sys.stderr)
        return 1
    input("Press the round link button on the bridge, then press Enter here (within 30 seconds)...")
    body = json.dumps({"devicetype": args.devicetype, "generateclientkey": True}).encode()
    for attempt in range(1, _RETRIES + 1):
        answer, _ = fetch(f"{base}/api", context, data=body)
        entry = answer[0] if isinstance(answer, list) and answer else {}
        if isinstance(entry, dict) and isinstance(entry.get("success"), dict):
            key = str(entry["success"].get("username", ""))
            if not key:
                print("the bridge answered without a key", file=sys.stderr)
                return 1
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(key + "\n")
            print(f"key written to {out} ({len(key)} characters, mode 0600)")
            return 0
        error = entry.get("error", {}) if isinstance(entry, dict) else {}
        if isinstance(error, dict) and error.get("type") == 101:
            print(f"link button not pressed (attempt {attempt} of {_RETRIES}); waiting {_RETRY_SECONDS} s")
            time.sleep(_RETRY_SECONDS)
            continue
        print("the bridge refused to create a key (its answer is not shown here)", file=sys.stderr)
        return 1
    print("gave up: the link button was not pressed in time", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
