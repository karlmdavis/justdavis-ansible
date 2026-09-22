# Monitoring Exporters

The three custom Prometheus exporters used by the `monitoring` role, packaged as one small Python
project that builds into one Docker image.

## What is here

- `amplifi-exporter` reads the AmpliFi router's web UI: which mesh point and band every client is on,
  its signal quality and link rates, mesh point backhaul health, and WAN throughput. It also rewrites
  the ping and AirPlay probe target files whenever a tracked device's address changes.
- `gateway-exporter` reads the Comcast Business gateway's status page: uptime, whether the Internet
  connection is active, and the downstream DOCSIS channels' SNR, power, and codeword error counters.
- `airplay-exporter` actively resolves each HomePod's AirPlay mDNS services from the LAN side, and
  passively records their announcements.

Each exporter is a `[project.scripts]` entry point in `pyproject.toml`; the shared runtime (settings,
HTTP client, scrape loop, metrics server, snapshot handling) lives in `src/.../common/`. Parsers are
pure functions with fixture-based tests under `tests/`; the fixtures are sanitised copies of real device
responses (fake MAC addresses, and documentation IP ranges apart from Comcast's public resolvers).

## Why this exists

No open-source Prometheus exporter covers these devices. AmpliFi has Home Assistant integrations but
no exporter; the one Comcast gateway exporter that exists targets a different firmware's page, logs in
again on every failure (this gateway allows a single admin session and logs each login), and cannot pin
the gateway's self-signed certificate; and the existing mDNS tools discover scrape targets rather than
check whether a named AirPlay service still resolves. The AmpliFi and gateway modules are therefore
built around the specific pages and login flows those devices expose, documented in each module's
header.

## How it is used

The role copies `Dockerfile`, `pyproject.toml`, `uv.lock`, `.dockerignore`, and `src/` to
`/opt/monitoring/exporters` on the server (never `tests/` or local tool caches). The `Build Monitoring
Exporters` handler builds the image `justdavis-monitoring-exporters:local` from that directory, and the
stack's `docker-compose.yml` runs one container per exporter from that image, selecting the entry point
with the service's `command`. Configuration arrives through environment variables: the listen address
and port from the Compose service's `environment`, the device credentials from
`device-credentials.env` (given only to the AmpliFi and gateway scrapers), and everything else from the
stack's `.env` file. Each exporter's `main.py` (`*Settings.from_env`) declares the variables it reads.

## Development

The package manages its own virtual environment and lock file with `uv`, separately from the
repository's root environment:

```bash
cd roles/monitoring/files/exporters
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Runtime dependencies are pinned in `pyproject.toml`: edit the pin and run `uv lock`. The dev tools are
unpinned: `uv lock --upgrade-package <name>`. Commit `uv.lock` alongside either. The Dockerfile pins the
Python base image and the `uv` binary; bump those there.
