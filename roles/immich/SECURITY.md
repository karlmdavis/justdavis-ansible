# Immich Security Analysis

This document is the threat model for the Immich deployment managed by this role, the controls that follow
from it, and the operating rules that keep it safe over time. It was written in October 2026 (Immich v3.3.1)
to prepare for exposing Immich to the public internet so that shared links work for people outside the
house. It is kept current as the controls land: each row of the controls table names the state of the
deployment, not an aspiration.

This repository is public. The document therefore describes architecture, controls and reasoning, which are
not secrets, and leaves out anything that would be a map for an attacker beyond what the repository already
shows: no account names or roles, no device inventory, no list of monitoring gaps.

## System summary

- Immich runs on eddings as three containers (`immich_server`, `immich_machine_learning`, `immich_redis`)
  behind Apache, which terminates TLS and proxies to the server container on `127.0.0.1:2283`.
- The database is the host PostgreSQL cluster shared with every other application on eddings, not Immich's
  own database container.
- The photo library lives on the ZFS fileshares dataset and is included in the nightly offsite backups.
- Today Immich is reachable only from the LAN and the Tailscale network, through a virtual host bound to the
  private address. The plan is to add a public hostname for the same instance.

## Assets

Ordered by how much losing them would hurt:

1. The photo library: tens of thousands of family photos and videos, including children. Confidentiality
   matters most; the library is backed up, so loss is recoverable but exposure is not.
2. The PostgreSQL cluster. While the Immich database role is a superuser, compromise of Immich is compromise
   of every database on eddings and of the `postgres` operating-system user (`COPY ... PROGRAM`).
3. The accounts: sessions and API keys on the household phones and browsers.
4. eddings itself and the other services it hosts (mail, directory, DNS, file shares).
5. The backups, which are pushed from eddings with credentials that can also delete them.

## Adversaries

- Internet scanners and credential-stuffing bots: automated, indiscriminate, constant once a hostname
  resolves to a public address.
- Someone holding a shared link they should not: a forwarded email, a relative's compromised device, a link
  pasted somewhere public.
- An attacker exploiting a newly published Immich vulnerability before this deployment is patched.
- An attacker holding a stolen session or API key from a household device.

Not in scope: a targeted attacker with physical access, a compromised upstream Immich release, or a
compromised eddings through some other service.

## Attack surface

### Added by public exposure

- The login endpoint (`POST /api/auth/login`). In Immich v3.3.1 this is password-only: no second factor, no
  passkeys, no lockout and no built-in rate limiting. The server logs each failure as
  `Failed login attempt for user <email> from ip address <ip>` at the `warn` level, and the IP is only the
  real client's when the reverse proxy is listed in `IMMICH_TRUSTED_PROXIES`.
- The whole authenticated API, including uploads and downloads.
- The administrative API, which should never need to be reached from the internet: `/api/admin/*`,
  `/api/system-config*`, `/api/system-metadata*`, `/api/jobs*`, `/api/libraries*`, `/api/auth/admin*`.
- The shared-link endpoints, which are the reason for the exposure.
- Endpoints that answer without authentication (from the v3.3.1 OpenAPI specification):
  `/api/server/{ping,version,version-history,features,config,media-types}`, `/api/public/config*`,
  `/api/auth/admin-sign-up` (only acts while no administrator exists), the maintenance-mode login
  (`/api/admin/maintenance/{status,login}`, `/api/admin/database-backups/start-restore`) and the OAuth
  callbacks.

### Vulnerability history

Published GitHub security advisories for Immich between July 2025 and September 2026:

| Published | Severity | Summary | Fixed in |
| --- | --- | --- | --- |
| 2026-09-28 | High | Authenticated SVG upload reaches ImageMagick coders and enables RCE | v3.2.4 |
| 2026-08-23 | Low | Incorrect sanitization of continue URL on the `/maintenance` endpoint | advisory lists <= v3.1.0 as affected, no fixed version recorded |
| 2026-07-06 | Medium | Incomplete fix for login continue redirect (open redirect) | v3.0.0 |
| 2026-07-06 | Medium | OIDC fetches run with TLS verification disabled | v3.0.0 |
| 2026-07-06 | Low | Unsafe ZIP entry names in archive download | v3.0.0 |
| 2026-07-06 | Medium | SSRF via OAuth profile picture URL | v3.0.0 |
| 2026-07-06 | Medium | Shared-link authentication allows adding owner's assets to shared links | v3.0.0 |
| 2026-06-01 | Critical | One-click account takeover via XSS in the login page continue redirect | unreleased `main` only; no stable release was affected |
| 2026-04-13 | Medium | Open redirect via shared album name | v2.7.3 |
| 2026-04-08 | High | Stored XSS via OCR text in the panorama viewer | v2.7.0 |
| 2026-04-02 | Medium | Insecure transmission of shared link password | v2.6.0 |
| 2026-01-29 | High | API key privilege escalation | v2.5.0 |
| 2025-07-11 | High | Account hijacking through OAuth | v1.132.0 |

What this history says about the risk:

- Most of the serious issues needed either an authenticated user or a victim who clicks a crafted link. The
  pattern argues for making a stolen or phished session less valuable (fence the administrative API, use
  HSTS so the login page is never served over plain HTTP) more than for any single network control.
- Fixes shipped within days of disclosure, often in the same release that disclosed them. The deployment's
  exposure window is therefore mostly a function of how fast this repository is updated, which is why the
  update policy below is a security control, not housekeeping.
- Two of the thirteen involved shared links. Shared links are a first-class feature with a track record of
  attention, not an afterthought.

### Shared links

- A link key is 50 random bytes, so links cannot be guessed or enumerated. The link is the secret.
- The optional password is compared as a plain string with no rate limit. It protects against a link seen
  over someone's shoulder, not against anyone who can hold the link and try passwords. Treat it as a
  courtesy layer.
- "Show metadata" off hides EXIF and location data from viewers. "Allow public user to upload" off keeps a
  leaked link from becoming an upload vector. Both are per link; the role documents the intended settings.
- Links are live views of an album. Deleting the link ends access immediately; nothing else needs to change.

## Controls

The table lists each control, the reasoning that led to it, and its current state. Pull requests that change
a row update it.

| Control | Why | State |
| --- | --- | --- |
| Modern TLS profile, `ServerTokens Prod`, `X-Content-Type-Options`, `Referrer-Policy` on all virtual hosts | Baseline for anything on the public address; drops CBC suites and version disclosure | Planned (shared Apache configuration) |
| HTTP/2 | One connection per client, which keeps the firewall's per-source connection limit from affecting normal browsing | Planned (shared Apache configuration) |
| HSTS on the Immich virtual hosts | The critical 2026 advisory needed the victim to load the login page; HSTS removes the plain-HTTP path | In place (intranet virtual host; the public virtual host will carry it too) |
| fail2ban jail on Immich failed logins | Immich has no lockout or rate limiting; five failures in ten minutes ban the source for an hour | Planned |
| `IMMICH_TRUSTED_PROXIES` set to the proxy's address | Without it every login is logged with the proxy's address and the fail2ban jail would ban the proxy itself | In place (`env.j2`) |
| Container logs to journald | Lets fail2ban read the login log through the journal and gives the logs rotation | Planned |
| Immich database role without `SUPERUSER` | Turns an Immich compromise from a cluster compromise into a one-database compromise; needs the extensions pre-created as `postgres` and the database owned by the Immich role | Planned |
| Administrative API reachable only from the LAN, Tailscale and the home network's public block | A stolen session or a future authorization bug cannot reach configuration, jobs or user management from the internet | Planned (public virtual host) |
| HTTP probe of the service with alerts for downtime and certificate expiry | Notice an outage or an expiring certificate before family members do | Planned |
| Automated release tracking with a written update deadline | The exposure window is the patch delay; see the update policy | Planned |
| Public upload disabled on shared links; metadata hidden | A leaked link cannot be used to upload or to read locations | Operating practice |
| Strong unique passwords on every account | Passwords are the only authentication factor | Operating practice |
| ufw connection-rate limit on 80/443 (pre-existing) | Slows connection floods from a single source; not a brute-force control in itself | In place |
| fail2ban on sshd, Apache and mail (pre-existing) | Baseline | In place |

Considered and not adopted, with reasons:

- A content security policy (Immich's `IMMICH_HELMET_FILE` or Apache headers): needs application-specific
  tuning and offers little behind a proxy that already controls what is served. Revisit if Immich ships a
  recommended policy.
- Proxying only the shared-link paths and nothing else: a smaller surface, but the path list is
  undocumented and moves between releases, and it would keep the phones from backing up away from home.
- GeoIP restrictions, Cloudflare or another tunnel: eddings already has a static public address and its own
  authoritative DNS; another party in the path adds a dependency without closing a gap that matters here.
- A VPN for family members: too much friction for the people the links are for.

## Update policy

Immich fixes ship fast, and the risk of a public instance is mostly the time between a fix and its
deployment. The rules:

- A release that fixes a published security advisory is deployed within 48 hours of publication.
- Any other patch release is deployed within a week. Minor and major releases are deployed after their first
  patch release unless an advisory forces the issue.
- Releases are tracked automatically: a dependency bot opens a pull request for each new Immich release with
  the upstream release notes, and the repository owner also watches the upstream repository for releases and
  security advisories. Immich's own "new version available" notice to administrators stays enabled as a
  backstop.
- Every upgrade follows the role README's upgrade procedure, including the release-note review; an advisory
  fix is not a reason to skip the drift check.

## Accepted residual risks

- Authentication is a password per account, with no second factor. Mitigated by strong unique passwords,
  the fail2ban jail and HSTS. The documented upgrade is OAuth with password login disabled, which moves the
  second factor to the identity provider; `immich-admin enable-password-login` is the recovery path if the
  provider is ever unavailable.
- A leaked shared link exposes that album until the link is deleted. Mitigated by per-link passwords, hidden
  metadata, and the ease of deleting and re-issuing a link.
- The offsite backups are pushed from eddings with credentials that can delete them, so a full compromise of
  eddings could also destroy the backup history. This predates the public exposure and is tracked as a
  follow-up (append-only credentials, pruning from another machine).
- The Immich web application is served from the same origin as the API, so a future XSS in Immich is a
  session compromise. Mitigated only by the update policy and the administrative fence.

## Runbook

Commands run on eddings as root unless noted.

- **Take Immich off the internet now.** `a2dissite photos.conf && systemctl reload apache2`. The intranet
  access path is unaffected. Re-running the playbook re-enables the site, so also revert or hold the change
  in this repository until the reason is resolved.
- **A shared link has leaked.** In the web interface, Sharing → Shared links → delete the link, then create a
  new one. Nothing else needs to change.
- **A device may be compromised.** Account Settings → Authorized Devices → log out the device, then change the
  account's password. Administrators can also remove any user's sessions under Administration → Users.
- **Someone legitimate was banned.** `fail2ban-client status immich` lists banned addresses;
  `fail2ban-client set immich unbanip <ip>` releases one. Addresses on the LAN, Tailscale and the home
  network's public block are never banned.
- **Check which version is running and whether a fix is out.** `curl -s https://<host>/api/server/version`
  against the running instance, and the [Immich releases](https://github.com/immich-app/immich/releases)
  and [security advisories](https://github.com/immich-app/immich/security/advisories) pages.
- **Confirm the proxy is trusted.** After a deliberate failed login from a known address, `journalctl
  CONTAINER_NAME=immich_server | grep 'Failed login attempt'` should show that address, not the Docker
  gateway.

## Sources

- Immich documentation: [environment variables](https://docs.immich.app/install/environment-variables),
  [reverse proxy](https://docs.immich.app/administration/reverse-proxy),
  [system settings](https://docs.immich.app/administration/system-settings),
  [standalone PostgreSQL, "without superuser"](https://docs.immich.app/administration/postgres-standalone),
  [server commands](https://docs.immich.app/administration/server-commands),
  [OAuth](https://docs.immich.app/administration/oauth), [sharing](https://docs.immich.app/features/sharing),
  [partner sharing](https://docs.immich.app/features/partner-sharing).
- Immich source at v3.3.1: `server/src/services/auth.service.ts` (login logging, no rate limiting),
  `server/src/services/shared-link.service.ts` (50-byte keys, plain password comparison),
  `server/src/app.common.ts` (`trust proxy` defaults to loopback), `server/src/controllers/server.controller.ts`
  and `open-api/immich-openapi-specs.json` (public and administrative endpoints),
  `packages/plugin-core/manifest.json` (workflow triggers, filters and actions).
- [Immich security advisories](https://github.com/immich-app/immich/security/advisories), listed via the
  GitHub API on 2026-10-10.
- [Mozilla SSL configuration guidelines](https://ssl-config.mozilla.org/), intermediate profile.
- [Apache mod_proxy](https://httpd.apache.org/docs/2.4/mod/mod_proxy.html) and
  [core](https://httpd.apache.org/docs/2.4/mod/core.html) documentation.
