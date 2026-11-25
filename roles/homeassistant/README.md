# Home Assistant Role

Runs [Home Assistant](https://www.home-assistant.io/) on eddings as a bridge: it talks to the devices in the house that the monitoring role's own exporters cannot read (HomeKit accessories, devices that exist only behind a vendor's cloud or an asyncio library), and the monitoring role's Prometheus scrapes it.

## Overview

- One container (`ghcr.io/home-assistant/home-assistant`, pinned in `defaults/main.yml`) on the host network, run by the `homeassistant` systemd unit from `/opt/homeassistant`.
- Its web UI is published at `https://homeassistant.intranet.<domain>` by the `apache` role, on the LAN and the tailnet only.
- Its metrics are served at `/api/prometheus` and scraped by the `monitoring` role (job `homeassistant`).
- It is a bridge, not a controller: no automations are kept in it.

## Architecture Decisions

### What Ansible manages, and what it cannot

Ansible owns the container, the systemd unit, the network exposure and one file, `config/configuration.yaml`. Everything else under `/opt/homeassistant/config` belongs to Home Assistant: its users, integrations, areas and helpers, and with them the pairing keys and vendor tokens, live as JSON under `config/.storage`, which Home Assistant rewrites while it runs. This is how Home Assistant is built, not a choice made here:

- An integration is set up by an interactive flow that cannot be replayed from a file: pressing a bridge's button, typing a PIN shown on a screen, signing in to a vendor account.
- For most integrations there is no YAML alternative any more.
- What ends up in `.storage` is exactly what must not be in a public repository: tokens, pairing keys, and the name of every room and device.

So nothing a vendor issues is in this repository or its vault. The state is protected by backups instead of by git, and each manual step is written down in the runbook below so that the state can be rebuilt. The cost is real: a change clicked in the UI has no diff and no review.

### Networking

Home Assistant uses the host network, because HomeKit and mDNS discovery need the LAN's multicast. ufw decides what can reach it:

- Port 8123 is not open to the LAN. People reach the UI through Apache (which connects over loopback), and Prometheus reaches the metrics endpoint from the monitoring Docker network, which the monitoring role allows.
- Home Assistant trusts forwarded headers from `127.0.0.1` only. That is set by the `http:` block in `configuration.yaml`, which Home Assistant reads on its very first start and then keeps in its own storage; later changes are made under Settings > System > Network.

### Security

Anything that can reach the UI and sign in can control whatever Home Assistant is paired with. The vhost adds no authentication of its own; Home Assistant's login is the control, so the one account has a strong password and two-factor authentication. The container runs without privileges or capabilities. `config/` is readable by root only.

### What this role refuses

Custom integrations (HACS), the `trusted_networks` login bypass, an unauthenticated metrics endpoint in production, opening port 8123 to the LAN, and automations in Home Assistant.

## Requirements

Docker, and for the UI the `apache` and `dns_server` roles (they own the vhost and the DNS record, so a first deploy needs a full run). No database, directory or other service is needed.

## Role Variables

See `defaults/main.yml`: the image, the port, the memory limit, and how many days of history Home Assistant keeps for its own UI (Prometheus holds the history that matters). There are no vault variables.

## Runbook: the Manual Steps

Done once, in this order, after the first deploy. Keep this list current: it is the only record of how the instance was built.

1. Open `https://homeassistant.intranet.<domain>` and create the owner account. Home Assistant cannot recover a lost owner account, so store its credentials in the password manager. Set the location, time zone and unit system when asked, and decline analytics.
2. In the profile's Security tab, turn on multi-factor authentication (TOTP).
3. Give Prometheus its access token. In the profile's Security tab create a long-lived access token named `prometheus`, then install it on eddings without it touching the shell history or this repository, and deploy the monitoring role so that it adds the scrape job:

   ```bash
   sudo install -o monitoring -g monitoring -m u=r,g=,o= /dev/null /opt/monitoring/secrets/homeassistant_token
   sudoedit /opt/monitoring/secrets/homeassistant_token
   ./ansible-playbook-wrapper site.yml --limit=eddings.justdavis.com --tags=monitoring
   ```

   The file holds the token alone, on one line. If the instance is ever rebuilt, create a new token.
4. Add each device's integration under Settings > Devices & services, and assign every device to an area: the dashboards group by area, and an entity with no area is left out of them.

Integrations added so far, and what each needed:

- None yet.

## Upgrading

Bump `homeassistant_image` in a PR, to a point release, after reading that release's breaking changes; the AWS test run gates it. Home Assistant migrates its stored state when a new version first starts and cannot go back, so take a backup first: the way back from a bad upgrade is a restore.

## Known Limitations

- State created in the UI is not in git; see above.
- DHCP-based device discovery is off, because the container has no capability for raw sockets. Devices are added by address or by mDNS discovery.
- Until the access token is installed, Prometheus has no Home Assistant job at all, and so nothing alerts on Home Assistant being down.

## Troubleshooting

```bash
sudo systemctl status homeassistant
sudo docker compose -f /opt/homeassistant/docker-compose.yml logs --tail 100
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8123/manifest.json
```

A `400 Bad Request` through the vhost means Home Assistant does not trust the proxy: check Settings > System > Network.

## References

- [Home Assistant Container installation](https://www.home-assistant.io/installation/linux/#install-home-assistant-container).
- [Prometheus integration](https://www.home-assistant.io/integrations/prometheus/).
- [HTTP integration and reverse proxies](https://www.home-assistant.io/integrations/http/).
