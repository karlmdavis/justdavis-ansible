# Monitoring Ansible Role

This role deploys a small home-network monitoring stack on eddings so that the recurring "AirPlay to the
HomePods is broken" and "the internet feels wrong" problems can be diagnosed from recorded data instead
of being papered over by power cycling. It records WiFi association and signal history for every
device, layered latency/loss from the LAN out to the internet, the cable modem's DOCSIS line health, and
AirPlay reachability, and it alerts to Slack and Discord on objective failures.

## Overview

Everything runs under `/opt/monitoring` as one Docker Compose stack managed by the `monitoring` systemd
unit:

- **Prometheus** stores the metrics (as much history as fits in the configured disk budget).
- **Grafana** serves the dashboards at `https://grafana.intranet.justdavis.com/` (through the Apache
  proxy; Grafana itself only listens on localhost).
- **Alertmanager** delivers alerts to Slack (as the "Home Monitoring" bot, via the Web API) and to a
  Discord channel webhook.
- **ping_exporter** pings every target continuously (one packet per second) and reports latency,
  jitter, and loss over a rolling window.
- **blackbox_exporter** connects to each HomePod's AirPlay port to tell "wedged AirPlay service" from
  "bad WiFi".
- **node_exporter** records eddings' own CPU, memory, and WAN/LAN interface counters.
- **amplifi_exporter** (custom) reads the AmpliFi router's web UI: which mesh point and band every
  client is on, its signal quality and link rates, mesh point backhaul health, and WAN throughput. It
  also keeps the ping and AirPlay probe target lists in sync with DHCP.
- **gateway_exporter** (custom) reads the Comcast Business gateway's status page: uptime and the
  downstream DOCSIS channels' SNR, power, and codeword error counters.
- **airplay_exporter** (custom) actively resolves each HomePod's AirPlay mDNS services from the LAN
  side every poll, and passively browses announcements.

The custom exporters are a small typed Python package in `files/exporters/` (see below).

## Architecture Decisions

### Why this data

- A HomePod stuck on a distant mesh point, or on 2.4 GHz, is the leading hypothesis for stuttering
  multi-room AirPlay. The AmpliFi feed is the only source for that, so it is polled every 30 seconds.
- AirPlay "cannot find / cannot connect" failures are often mDNS discovery problems rather than radio
  problems. The active mDNS resolve, the TCP port probe, and the ping data separate those cases.
- Video-call trouble is localised by pinging each hop separately: the AmpliFi router, the Comcast
  gateway (both sides), the ISP's first hop, and public anchors. Whichever hop first shows loss or
  latency is where the problem is.
- DOCSIS SNR, power, and uncorrectable codewords distinguish a line problem (which a reboot only masks)
  from a router problem.

### Networking

Services that need the host's network stack (`node_exporter`, `ping_exporter`, `airplay_exporter`)
run with host networking but bind their metrics endpoints to the gateway address of a dedicated,
fixed-subnet Docker bridge network; a ufw rule admits only that subnet. Everything else lives on the
bridge and publishes on `127.0.0.1` only. This mirrors how the Immich network reaches PostgreSQL, and
keeps the parsers of untrusted router/gateway output off the host's loopback (which Postfix trusts).

### Storage

Prometheus data lives under `/opt/monitoring/data` on the root volume with a size cap
(`monitoring_prometheus_retention_size`) and no time cap, so it can never fill the disk and history is
simply "as much as fits". It is deliberately not backed up.

### Security

- The repository is public, so the list of tracked devices (names and MAC addresses) is kept in the
  vault, and no dashboard, rule, fixture, or document in git names a real household device.
- Passwords, the Slack bot token, and the Discord webhook URL are written to root-only or
  service-user-only files under `/opt/monitoring`; no Compose or config file contains a secret. The
  AWS test environment receives dummy values.
- Containers run as the unprivileged `monitoring` user with read-only root filesystems and dropped
  capabilities (`ping_exporter` keeps `NET_RAW`; `node_exporter` uses its image's `nobody` user).
- The AmpliFi password is the router's only admin credential; compromise of `/opt/monitoring/.env`
  equals control of the LAN. The AmpliFi UI is plain HTTP, so that password crosses the LAN in
  cleartext on each login (roughly once per session expiry).
- The gateway is scraped over HTTPS with its self-signed certificate pinned by SHA-256 fingerprint,
  computed at deploy time and recorded in `/opt/monitoring/gateway_tls_fingerprint`; if the gateway is
  unreachable during a deploy the previous fingerprint is kept. After a gateway firmware change,
  re-run the role if `gateway_up` stays 0.

## Requirements

- Docker and Docker Compose (provided by the `docker` role).
- The Apache and DNS roles, for the `grafana.intranet.justdavis.com` name and TLS proxy.
- Vault variables (see below).

## Role Variables

Defaults are in `defaults/main.yml`. Production values for eddings are in
`host_vars/eddings.justdavis.com/main.yml`. The following must be set in the Ansible vault:

```yaml
vault_amplifi_password: <AmpliFi router web UI password>
vault_gateway_username: <Comcast gateway admin username>
vault_gateway_password: <Comcast gateway admin password>
vault_grafana_admin_password: <Grafana admin password; applied on first start only>
vault_alertmanager_slack_bot_token: <Bot User OAuth Token of the "Home Monitoring" Slack app, xoxb-...>
vault_alertmanager_discord_webhook_url: <Discord channel webhook URL>
vault_monitoring_tracked_clients:
  - mac: "aa:bb:cc:dd:ee:ff"
    name: Kitchen
    kind: homepod
    # Optional: the device's Bonjour name, when it differs from `name`.
    airplay_name: Kitchen
```

`kind` is one of `homepod`, `appletv`, `ipad`, `phone`, `laptop`, or `other`. Only `homepod` entries
get AirPlay probes and HomePod alerts; phones and iPads sleep, so they are never in loss alerts.

## Adding or Changing a Tracked Device

1. Find the device's MAC address (the AmpliFi app shows it; iOS devices may use a rotating private
   address per network, so prefer the Apple TV and HomePods for stable tracking).
2. Add or edit its entry in `vault_monitoring_tracked_clients`.
3. Deploy: `./ansible-playbook-wrapper site.yml --limit=eddings.justdavis.com --tags=monitoring`.

## Alerts

Alerts go to Slack and Discord. The Slack side is the "Home Monitoring" Slack app (app ID
`A0C1LFW5N9X` in the Davis Family workspace, created and installed with the Slack CLI from a manifest
requesting `chat:write`, `chat:write.public`, and `incoming-webhook`; the manifest is kept in
`files/slack-app/manifest.json` so the app can be recreated); Alertmanager posts through the
Web API with the app's bot token into `monitoring_slack_channel` (default `#home-alerts`, a private
channel the bot was invited to). The bot token is on the app's settings page under OAuth &
Permissions; regenerate it there and update the vault to rotate it. The Discord side is a channel
webhook URL from Discord's channel integration settings. Thresholds are role variables; the defaults
follow common guidance: packet loss above 5%, round trip above 100 ms, or jitter above 30 ms to the
internet for 5 minutes; DOCSIS SNR below 33 dB; downstream power outside -8 to +12 dBmV; any
uncorrectable codewords (warning) or more than 1000 in 15 minutes (critical); the gateway reporting
its Internet connection inactive for 2 minutes; HomePods not answering ping, not accepting AirPlay
connections, or not resolving over mDNS; a mesh point missing from the topology; router, mesh point,
or gateway reboots; collectors that stop working; exporters whose scrape loop has stalled; and the
offsite backup (from the `offsite_backups` role's metrics file, read by node_exporter's textfile
collector): no success for 36 hours, a failed run, or the metrics missing for an hour.

The downstream power threshold is deliberately above the commonly cited +7 dBmV ceiling because, as of
September 2026, the gateway reads around +10 to +11.5 dBmV with excellent SNR and zero uncorrectables.
That is a data point for a Comcast conversation, not an alert.

There is intentionally no "HomePod on the wrong mesh point" alert yet: the data is recorded first, and a
rule can be added once the dashboards show what normal looks like.

## Custom Exporters

The custom exporters live in `files/exporters/` as a uv-managed package with its own lock file and
README (what they are, why they are custom, and how to develop them). Each exporter serves
`<name>_up`, `<name>_last_success_timestamp_seconds`, `<name>_scrape_duration_seconds`, and
`<name>_consecutive_failures` at all times, plus `<name>_scrape_errors_total{stage}` to say which step
failed, and only serves device metrics from its last successful poll, so nothing goes stale silently.
An exporter whose device host variable is blank (as in the AWS test environment) serves `<name>_up 0`
and idles; the gateway exporter does the same when no certificate fingerprint was recorded (it never
falls back to plain HTTP), and the AirPlay probe when its LAN address is not local to the host.

Image builds and pulls happen during Ansible deploys (handlers), never when the systemd unit starts, so
the stack restarts cleanly during a WAN outage.

## Deploying and Upgrading

- Deploy just this stack: `./ansible-playbook-wrapper site.yml --limit=eddings.justdavis.com --tags=monitoring`.
  A scoped run does not touch the Apache vhost or DNS record (those belong to the `apache` and
  `dns_server` roles); until they have been applied, reach Grafana with an SSH port forward:
  `ssh -L 3000:127.0.0.1:3000 eddings.karlanderica.justdavis.com` then `http://localhost:3000/` (use
  Chrome or Firefox; Safari rejects Grafana's secure-only cookie over plain `http://localhost`).
- Upgrade an image: bump its tag in `defaults/main.yml` and deploy; the AWS test run is the gate.
- Rotate the Grafana admin password: the password file only applies on first start, so run
  `sudo docker compose exec grafana grafana cli admin reset-admin-password <new>` from
  `/opt/monitoring`.

## Known Limitations

- The gateway allows a single admin session and logs every login. The exporter keeps one session alive
  and re-logs in at most every `monitoring_gateway_relogin_min_seconds`, but using the gateway's web
  UI yourself will log the exporter out (and vice versa) until then.
- The gateway's firmware does not populate the upstream channel table and its event log has no DOCSIS
  entries, so upstream signal levels and T3/T4 timeouts are not available.
- AmpliFi reports client signal as a 0-100 quality figure, not dBm.
- The passive mDNS browser can lag a device's disappearance by up to the record TTL; the active
  resolve is the signal alerts use. The `_raop._tcp` instance name carries a device-id prefix that is
  learned from passive discovery, so its resolved gauge stays 0 until the HomePod has announced once.
- The ping and AirPlay target files keep their last contents across exporter restarts, so a HomePod
  that moved while the stack was down is probed at its old address until the first successful poll.

## Troubleshooting

```bash
sudo systemctl status monitoring
sudo docker compose -f /opt/monitoring/docker-compose.yml logs --tail=100 amplifi_exporter
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool | grep -E '"job"|"health"'
```

## References

- [Prometheus](https://prometheus.io/docs/), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/),
  [Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/).
- [ping_exporter](https://github.com/czerwonk/ping_exporter), [blackbox_exporter](https://github.com/prometheus/blackbox_exporter),
  [node_exporter](https://github.com/prometheus/node_exporter).
