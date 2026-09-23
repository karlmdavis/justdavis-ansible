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
- **gateway_exporter** (custom) reads the Comcast Business gateway's status page: uptime, whether the
  Internet connection is active, and the downstream DOCSIS channels' SNR, power, and codeword error
  counters.
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
  gateway (both sides), Comcast's resolver inside the ISP network, and public anchors. Whichever hop
  first shows loss or latency is where the problem is.
- DOCSIS SNR, power, and uncorrectable codewords distinguish a line problem (which a reboot only masks)
  from a router problem.

### What each signal tells you

The signals are layered so that each failure is told apart from its neighbours by one metric. Reading
down a column is the diagnosis; the last column is the rule that says so.

| Failure | What isolates it | Alert |
|---|---|---|
| HomePod has left the WiFi | `amplifi_tracked_client_associated` is 0 (the ping and AirPlay probes drop it at the same moment, so only this gauge can see it). | `HomePodNotOnWifi` |
| HomePod is on the WiFi but not reachable | Associated, but `ping_loss_ratio` for its address is 1. `amplifi_client_signal_quality`, band, and mesh point say whether the radio link is the reason. | `HomePodUnreachable` |
| HomePod is reachable but AirPlay is wedged | Ping is fine, but the TCP probe of port 7000 (`probe_success`) fails. Restarting the HomePod fixes this one. | `HomePodAirPlayPortDown` |
| HomePod answers on port 7000 but cannot be found | Port open, but the LAN-side mDNS query (`airplay_service_resolved`) fails. `amplifi_client_airplay_advertised` gives the router's view for comparison. | `HomePodAirPlayNotResolving` |
| HomePod is on a distant mesh point or 2.4 GHz | `amplifi_client_info{ap_name,band}` history on the WiFi dashboard. | None yet: recorded first, rule later. |
| A mesh point has dropped out | `amplifi_mesh_point_online` is 0 (the router keeps listing a lost mesh point as offline), or fewer `amplifi_mesh_point_info` series than mesh points. `_rssi_min_dbm` and the backhaul band show degradation beforehand. | `MeshPointOffline`, `MeshPointMissing` |
| A mesh point keeps re-joining the mesh | `rate(amplifi_mesh_point_connections_total[1h])` and `amplifi_mesh_point_last_disconnected_age_seconds`; the first night showed one mesh point re-joining ~15 times a day on a 2.4 GHz backhaul. | None yet: recorded first, rule later. |
| The internet is bad | Loss, round trip, or jitter to the public anchor (`ping_*{target="1.1.1.1"}`). | `WanPacketLoss`, `WanLatencyHigh`, `WanJitterHigh` |
| Where the internet is bad | The first hop to show it, in order: router LAN, router WAN, gateway LAN, gateway static IP, Comcast's resolver, public anchors (the layered ping panel). | Covered by the three above. |
| The cable line, not the router | DOCSIS SNR, receive power, uncorrectable codewords, and `gateway_internet_active`; these persist through a reboot, a router fault does not. | `DocsisSnrLow`, `DocsisPowerOutOfRange`, `DocsisUncorrectableCodewords*`, `GatewayInternetInactive` |
| Something rebooted (or was power cycled) | Uptime under ten minutes for the router, a mesh point, or the gateway; correlate with the rows above. | `AmpliFiRebooted`, `MeshPointRebooted`, `GatewayRebooted` |
| The WAN link is saturated | `amplifi_wan_*_bits_per_second` and `node_network_*_bytes_total{device="br-wan"}` against the latency panels. | None: dashboard only. |
| Prometheus cannot reach an exporter | `up` is 0 for the job. | `CollectorDown` |
| An exporter runs but cannot read its device | `<name>_up` is 0 and `<name>_scrape_errors_total{stage}` names the step (login, fetch, tls, parse, write, resolve). | `AmpliFiScrapeFailing`, `GatewayScrapeFailing`, `AirPlayProbeFailing` |
| The router is read but the probe targets cannot be updated | `amplifi_target_files_ok` is 0; probes keep following stale addresses. | `AmpliFiTargetFilesNotWritable` |
| The WAN probe itself has gone missing | `absent()` of the anchor's ping series, without which the three WAN rules would be silent. | `WanProbeMissing` |
| Alerts are not being delivered | Alertmanager's failed-notification counter, or Prometheus seeing no Alertmanager. | `AlertmanagerNotificationsFailing`, `PrometheusAlertmanagerUnreachable` |
| The offsite backup is not happening | `offsite_backups_*` gauges from the `offsite_backups` role. | `OffsiteBackupStale`, `OffsiteBackupFailed`, `OffsiteBackupMetricsMissing` |

### Why three custom exporters

No existing Prometheus exporter fits any of the three devices; the exporters' README
(`files/exporters/README.md`) records what was found and why it was not usable.

### Networking

Services that need the host's network stack (`node_exporter`, `ping_exporter`, `airplay_exporter`)
run with host networking but bind their metrics endpoints to the gateway address of a dedicated,
fixed-subnet Docker bridge network; a ufw rule admits only that subnet. Everything else lives on the
bridge and publishes on `127.0.0.1` only. This mirrors how the Immich network reaches PostgreSQL, and
keeps the parsers of untrusted router/gateway output off the host's loopback (which Postfix trusts).

### Storage

Prometheus data lives under `/opt/monitoring/data` on the root volume with a 6 GB size cap (set in
the Compose template) and no time cap, so it can never fill the disk and history is simply "as much as
fits". It is deliberately not backed up.

### Security

- The repository is public, so the list of tracked devices (names and MAC addresses) is kept in the
  vault, and no dashboard, rule, fixture, or document in git names a real household device.
- Passwords, the Slack bot token, and the Discord webhook URL are written to root-only or
  service-user-only files under `/opt/monitoring`; no Compose or config file contains a secret. The
  AWS test environment receives dummy values.
- Containers run as the unprivileged `monitoring` user with read-only root filesystems and dropped
  capabilities (`ping_exporter` keeps `NET_RAW`; `node_exporter` uses its image's `nobody` user).
- The AmpliFi password is the router's only admin credential; compromise of
  `/opt/monitoring/amplifi-credentials.env` equals control of the LAN. Each device scraper receives
  only its own device's credentials file, and the host-network AirPlay probe receives none. The
  AmpliFi UI is plain HTTP, so the password crosses the LAN in cleartext on each login (roughly once
  per session expiry).
- The gateway is scraped over HTTPS with its certificate (Comcast's, for `myrouter.io`, so it does not
  name the gateway's address) pinned by SHA-256 fingerprint, computed at deploy time and recorded in
  `/opt/monitoring/gateway_tls_fingerprint`; if the gateway is unreachable during a deploy the previous
  fingerprint is kept, and the deploy output says so, as it does when the fingerprint changed and was
  re-pinned. After a certificate change (Comcast's expires yearly), re-run the role if `gateway_up`
  stays 0.

## Requirements

- Docker and Docker Compose (provided by the `docker` role).
- The Apache and DNS roles, for the `grafana.intranet.justdavis.com` name and TLS proxy.
- Vault variables (see below).

## Role Variables

`defaults/main.yml` holds only the values shared between files (paths, the service user, the Docker
network, ports, the gateway address, and the ping targets and settings) plus the image tags, so that
upgrades happen in one place; everything used once, such as alert thresholds and polling intervals, is
set in the template that uses it, with its reasoning alongside. The defaults are the production values;
in the AWS test environment the templates blank the device hosts, substitute dummy credentials, and
ping only the public anchors. The following must be set in the Ansible vault:

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
requesting `chat:write` and `chat:write.public`; the manifest is kept in
`files/slack-app/manifest.json` so the app can be recreated); Alertmanager posts through the
Web API with the app's bot token into `#home-alerts` (a private channel the bot was invited to,
named by channel ID in the Alertmanager template). The bot token is on the app's settings page under
OAuth & Permissions; regenerate it there and update the vault to rotate it. The Discord side is a
channel webhook URL from Discord's channel integration settings. Thresholds are set at the top of
`templates/alerts.yml.j2` and follow common guidance: packet loss above 5%, round trip above 100 ms,
or jitter above 30 ms to the internet for 5 minutes; DOCSIS SNR below 33 dB or downstream power outside
-8 to +15 dBmV (one alert with a channel count, kept firing across the gateway's hourly web UI stall);
any uncorrectable codewords (warning) or more than 1000 in 15 minutes (critical); the
gateway reporting its Internet connection inactive for 2 minutes; HomePods off the WiFi, not answering
ping, not accepting AirPlay connections, or not resolving over mDNS; a mesh point offline or missing
from the topology; router, mesh point, or gateway reboots; collectors that stop working; the WAN probe series
going missing; the offsite backup
(from the `offsite_backups` role's metrics file, read by node_exporter's textfile collector): no
success for 36 hours, a failed run, or the metrics missing for an hour; and the alerting path itself:
Prometheus losing Alertmanager, or Alertmanager failing to deliver to Slack or Discord (each of which
still reaches the other). What no rule can cover is the whole stack being down at once; a dead-man's
switch to an outside heartbeat service would, and is a possible follow-up.

The downstream power bounds are the DOCSIS receive range rather than the commonly cited -7..+7 dBmV
ideal; the template explains why next to the value.

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
talks to an unauthenticated HTTPS peer). The AirPlay probe exits instead when its LAN address is not
local to the host or mDNS cannot start, since both can be transient at boot and the container's
restart policy retries.

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
- Delete series that tell a false story (a mislabelled metric, a dead probe target, an alert that flapped
  on a bug): do not leave them to age out, or the dashboards will mislead later. Prometheus's admin API
  is off in the deployed stack; `scripts/prometheus-admin-api.yml` turns it on for the duration of a
  cleanup, and `scripts/tsdb_cleanup_2026_09_23.py` is a worked example (one row per false story, each
  with a matcher and an end bound read from Prometheus itself).

## Known Limitations

- The gateway allows a single admin session and logs every login. The exporter keeps one session alive
  and re-logs in at most every five minutes (`MONITORING_GATEWAY_RELOGIN_MIN_SECONDS` in the `.env`
  template), but using the gateway's web UI yourself will log the exporter out (and vice versa) until
  then.
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
