# Eddings Networking Role

Configures `eddings`' host networking via netplan — the WAN and LAN bridges the server uses to
reach the internet and the home LAN.

## Overview

This role installs `files/netplan/00-installer-config.yaml`, which defines two bridges:

- **`br-wan`** (on `eno1`) — the public/WAN side, cabled directly to the Comcast Business modem.
  Holds the server's static public IPs and the default route.
- **`br-lan`** (on `eno2`) — the home LAN side, a DHCP client on `10.0.0.0/24`
  (reserved at `10.0.0.2`). It takes its address, default route (as a fallback, see below) and DNS
  servers from the AmpliFi's lease, but not host routes to those DNS servers:
  `files/systemd/network/10-netplan-br-lan.network.d/routes-to-dns.conf` sets networkd's
  `RoutesToDNS=no`, because the routes sent traffic for the lease's public resolvers (1.1.1.1,
  1.0.0.1), the monitoring role's WAN ping anchor among it, through the AmpliFi's NAT instead of
  out `br-wan`.

## Home network topology

```mermaid
flowchart TD
    net([Internet])
    modem["Comcast Business modem/gateway<br/>10.1.10.1 — routed, not bridged, UPnP off<br/>routes 96.86.32.136/29, gateway .142"]
    amplifi["AmpliFi HD router<br/>WAN 96.86.32.141 (static)<br/>LAN 10.0.0.1"]
    lan["Home LAN 10.0.0.0/24"]

    subgraph eddings
        wan["br-wan<br/>96.86.32.137 (main)<br/>96.86.32.139 (secondary)"]
        lanif["br-lan<br/>10.0.0.2"]
    end

    net --> modem
    modem --> wan
    modem --> amplifi
    amplifi --> lan
    lanif --- lan
```

### ISP / address block
- Provider: **Comcast Business** — routed static block **`96.86.32.136/29`**, gateway
  **`96.86.32.142`**. IPv6 block **`2603:3003:5207:ad00::/56`**.

### Public IP assignments (`96.86.32.136/29`)
| Address | Use |
| --- | --- |
| `.137` | `eddings` — primary public IP |
| `.139` | `eddings` — secondary IP, formerly the OpenVPN gateway; OpenVPN is gone, see #117 |
| `.141` | AmpliFi HD WAN / home network edge (`karlanderica`) |
| `.138`, `.140` | spare |
| `.142` | Comcast gateway |

### Cable modem
The Comcast Business gateway (`10.1.10.1`) runs in **routed mode — it is *not* bridged**, and
UPnP is disabled on it. It routes the `/29` to devices that statically configure a public IP from
the block, and hands out private `10.1.10.x` via DHCP to anything else. Two devices are cabled
directly to it: `eddings`' WAN (`br-wan`) and the **AmpliFi HD** router's WAN.

## Operational notes

**LAN-edge devices must use a static public IP from the block.** Because the modem is routed
(not bridged), a device left on modem DHCP lands on a private `10.1.10.x` address = **double-NAT**,
which breaks inbound/UPnP port mapping (this caused a Terraria hosting outage in 2026-08 until the
AmpliFi's WAN was set to the static `96.86.32.141`):

```mermaid
flowchart LR
    dev["LAN-edge device<br/>(e.g. AmpliFi WAN)"]
    dev -->|"static public IP (.141)"| ok([single NAT — UPnP works])
    dev -->|"modem DHCP (10.1.10.x)"| bad([double-NAT — UPnP breaks])
```

**DNS keeps working with the WAN unplugged.** `eno1` is sometimes unplugged during maintenance or a
security incident, as a simple way to take the public services offline. The `ignore_routes_with_linkdown`
sysctl then lets the lease's default route via the AmpliFi (metric 100) carry eddings' own traffic,
and because the lease's DNS servers stay configured on `br-lan` (only the host routes to them are
suppressed), name resolution keeps working through the LAN.

**Do not put the modem in bridge mode.** Bridge mode passes a single public IP to a single device
and stops routing the `/29` to multiple hosts — which would break `eddings`' directly-cabled public
IPs (and everything it serves). The routed-mode + per-device static IP arrangement is intentional.

## Files

- `files/netplan/00-installer-config.yaml` — the netplan applied to `eddings`.
- `files/systemd/network/10-netplan-br-lan.network.d/routes-to-dns.conf` — networkd drop-in on the
  unit netplan generates for `br-lan`; stops host routes to the lease's DNS servers.
