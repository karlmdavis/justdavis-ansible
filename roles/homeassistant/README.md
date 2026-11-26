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
- Most integrations have no YAML alternative.
- What ends up in `.storage` is exactly what must not be in a public repository: tokens, pairing keys, and the name of every room and device.

So nothing a vendor issues is in this repository or its vault. Each manual step is written down in the runbook below so that the state can be rebuilt, and that runbook is the only protection the state has: `/opt/homeassistant/config` is not backed up yet (see Known Limitations). The cost is real either way: a change clicked in the UI has no diff and no review.

### Networking

Home Assistant uses the host network, because HomeKit and mDNS discovery need the LAN's multicast. Its web server listens on two addresses only:

- `127.0.0.1`, where the Apache vhost reaches it. People use the vhost, on the LAN and the tailnet.
- The monitoring Docker network's gateway address, where Prometheus reaches the metrics endpoint. The monitoring role's ufw rule admits that network and nothing else.

It does not listen on the LAN address at all, and that, not the firewall, is what keeps port 8123 off the LAN and the tailnet: Tailscale accepts everything arriving on its own interface before ufw is consulted. The smoke tests check it.

The listen addresses and the trusted proxy are Home Assistant's own settings. It imports them from the `http:` block of `configuration.yaml` on its very first start and keeps them in its storage from then on, so the role renders that block only until then, and later changes are made under Settings > System > Network.

### Security

Anything that can reach the UI and sign in can control whatever Home Assistant is paired with. The vhost adds no authentication of its own; Home Assistant's login is the control, so the one account has a strong password and two-factor authentication. The container runs without privileges or capabilities. `config/` is readable by root only.

### What this role does not do

Enforced by the role: the metrics endpoint requires a token in production, and the web server does not listen on the LAN. By policy, with nothing in the role to stop it: no custom integrations (HACS), no `trusted_networks` login bypass, and no automations kept in Home Assistant.

## Requirements

Docker, and the `monitoring` role deployed first: Home Assistant listens on that role's Docker network gateway address and will not start its web server without it. For the UI, the `apache` and `dns_server` roles, which own the vhost and the DNS record. So the first deploy is a full run of `site.yml`; after that `--tags=homeassistant` is enough. No database, directory or other service is needed.

## Role Variables

See `defaults/main.yml`: the base directory and the pinned image. There are no vault variables.

## Runbook: the Manual Steps

Done once, in this order. Keep this list current: it is the only record of how the instance was built.

1. Deploy with a full run of `site.yml`. Deploy once more afterwards (`--tags=homeassistant`): the second run removes the first-start `http:` block from `configuration.yaml`, which would otherwise show as a repair notice.
2. Open `https://homeassistant.intranet.<domain>` and create the owner account, with its credentials in the password manager. Set the location, time zone and unit system when asked, and decline analytics.
3. In the profile's Security tab, turn on multi-factor authentication (TOTP).
4. Give Prometheus its access token. In the profile's Security tab create a long-lived access token named `prometheus`. On eddings, put it in a file that only the monitoring user can read, without it touching the shell history:

   ```bash
   sudo install -o monitoring -g monitoring -m u=r,g=,o= /dev/null /opt/monitoring/secrets/homeassistant/token
   sudoedit /opt/monitoring/secrets/homeassistant/token
   ```

   The file holds the token alone, on one line. Then, from the checkout, deploy the monitoring role, which adds the scrape job now that the file is there:

   ```bash
   ./ansible-playbook-wrapper site.yml --limit=eddings.justdavis.com --tags=monitoring
   ```

   Until this is done Prometheus does not scrape Home Assistant, and the `HomeAssistantNotScraped` alert fires after an hour. Replacing the token later needs only the file edited: Prometheus reads it on every scrape. If the instance is ever rebuilt, create a new token.
5. Add each device's integration under Settings > Devices & services, and assign every device to an area: the dashboards select by area, and an entity with no area is left out of them.

Integrations added so far, and what each needed:

- None yet.

## Upgrading

Bump `homeassistant_image` in a PR, to a point release, after reading that release's breaking changes; the AWS test run gates it. Home Assistant migrates its stored state when a new version first starts and cannot go back, so the way back from a bad upgrade is a restore, which needs a backup taken first (Settings > System > Backups).

## Known Limitations

- State created in the UI is not in git; see above.
- `/opt/homeassistant/config` is not backed up yet. Until it is, losing it means rebuilding from the runbook, and any pairing that cannot simply be redone is lost with it.
- DHCP-based device discovery is off, because the container has no capability for raw sockets. Devices are added by address or by mDNS discovery.
- The role's `http:` settings apply to a new instance only. On a running one they are Home Assistant's own, and changing the template does nothing.

## Troubleshooting

```bash
sudo systemctl status homeassistant
sudo docker compose -f /opt/homeassistant/docker-compose.yml logs --tail 100
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8123/manifest.json
```

- A `400 Bad Request` through the vhost means Home Assistant does not trust the proxy. The setting is under Settings > System > Network, which is then only reachable past the proxy: `ssh -L 8123:127.0.0.1:8123 eddings.justdavis.com`, and open `http://127.0.0.1:8123`.
- A lost owner password is reset from the host, then Home Assistant is restarted:

  ```bash
  sudo docker exec -it homeassistant-homeassistant-1 hass --script auth --config /config change_password <user> <new password>
  sudo systemctl restart homeassistant
  ```

- The smoke tests fail on "the pinned image" when a deploy's image pull did not complete: run the deploy again once the registry is reachable.

## References

- [Home Assistant Container installation](https://www.home-assistant.io/installation/linux/#install-home-assistant-container).
- [Prometheus integration](https://www.home-assistant.io/integrations/prometheus/).
- [HTTP integration and reverse proxies](https://www.home-assistant.io/integrations/http/).
