# Jellyfin Ansible Role

This role installs [Jellyfin](https://jellyfin.org/) from the project's APT repository as a host service
(not a container), and opens its port to the LAN and the Tailscale network. Libraries, users and plugins are
configured by hand in the web UI; nothing under `/etc/jellyfin` or `/var/lib/jellyfin` is managed here.

## What the role does and does not do

- Adds the Jellyfin APT repository and installs the `jellyfin` metapackage with `state: present`.
- Allows port 8096 from `network_home_private_cidr` and `network_tailscale_cidr` in UFW. Jellyfin is not
  proxied through Apache (GitHub issue #35).
- Tests that the service is active, that `/health` answers, and (optionally) that the running version is at
  least `jellyfin_min_version`.
- Does **not** upgrade Jellyfin. Majors run a one-way database migration on first start, with no downgrade
  path short of a restore, so upgrades are the manual procedure below. Unattended-upgrades does not touch
  third-party repositories either (see the base role's `50unattended-upgrades.j2`).

## Role variables

- `jellyfin_min_version` (default empty): when set, e.g. `'12.4'`, the tests assert the server reports at
  least that version. Set it in `host_vars` in the same change as a manual upgrade, never ahead of it;
  the tests run in production too, and a value ahead of the installed version fails every run.

## Manual upgrade procedure (major versions)

Written for 10.11.6 to 12.4 on eddings (2026-10-31), but the shape applies to any major. Before starting,
read the release post for the target version; 12.0's is at
<https://jellyfin.org/posts/jellyfin-release-12.0/>.

Facts that matter on eddings (checked 2026-10-31, re-checked 2026-11-01):

- `/var/lib/jellyfin` (about 4 GB), `/etc/jellyfin` and `/var/cache/jellyfin` all live on the root ext4
  volume, not on ZFS, so the backup is a tarball rather than a snapshot.
- No plugins are installed (`/var/lib/jellyfin/plugins` holds only `configurations`). If that ever changes,
  remove every non-built-in plugin in the UI before upgrading and re-add afterwards.
- The Debian `postinst` starts `jellyfin.service` at the end of `apt install` unless the unit is masked or
  `DISABLE_JELLYFIN_AUTOSTART` is set in the environment. Masking is used below because it does not depend
  on `apt` passing environment variables through to `dpkg`.
- 12.x's `jellyfin` metapackage depends on `jellyfin-ffmpeg8`, which conflicts with and replaces
  `jellyfin-ffmpeg7`. Installing the metapackage handles the swap; installing `jellyfin-server` by name
  would not.
- `jellyfin-ffmpeg7` 7.1.3-3-noble was installed before the upgrade. The repository still publishes every
  10.x build and every ffmpeg7 build, so the rollback below can pin them.

### Before

1. Confirm the starting point and the candidate:

   ```bash
   dpkg-query -W -f='${Package} ${Version}\n' 'jellyfin*'
   apt-cache policy jellyfin | sed -n 2,3p
   ```

   Direct upgrades to 12.x are supported from 10.10.7 and 10.11.x only. Going straight to 12.4 is better than
   stopping at 12.0: 12.1 fixed the pre-migration backup and 12.2 fixed a slow migration step.
2. Check that no two usernames differ only by capitalisation; the migration refuses them. Either look at
   Dashboard, Users in the web UI, or read them from the database (eddings has no `sqlite3` binary, but
   Python's module works and the `mode=ro` URI keeps it read-only):

   ```bash
   sudo python3 - <<'PY'
   import sqlite3
   con = sqlite3.connect("file:/var/lib/jellyfin/data/jellyfin.db?mode=ro", uri=True)
   print(sorted(r[0] for r in con.execute("select Username from Users")))
   PY
   ```

3. Pick a quiet time. The first start after the upgrade runs the migration with no progress output; a small
   plugin-free database takes seconds to a few minutes.

### Upgrade

```bash
# Stop and mask, so that apt's postinst cannot start the new version (and its migration) behind your back.
sudo systemctl stop jellyfin
sudo systemctl mask jellyfin

# Back up state. transcodes/ is scratch and can be large; everything else is small.
sudo tar -C / --exclude=var/lib/jellyfin/transcodes -czf /root/jellyfin-pre-12-$(date +%F).tgz \
    var/lib/jellyfin etc/jellyfin
ls -lh /root/jellyfin-pre-12-*.tgz

# Install the metapackage (never jellyfin-server alone), then check that ffmpeg was swapped, not doubled.
sudo apt update
sudo apt install jellyfin
dpkg -l 'jellyfin-ffmpeg*' | grep '^ii'
/usr/lib/jellyfin-ffmpeg/ffmpeg -version | head -1

# Run the database migration deliberately, in the foreground, as the service user, with the service's own
# arguments. It exits when done. Do not interrupt it; if it fails, see Rollback.
sudo -u jellyfin bash -c \
    'set -a; . /etc/default/jellyfin; exec /usr/bin/jellyfin $JELLYFIN_ARGS --mode MigrateSystem'

# Start the service for real.
sudo systemctl unmask jellyfin
sudo systemctl start jellyfin
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8096/health
curl -s http://127.0.0.1:8096/System/Info/Public
```

Never restart or reboot while a migration is running. Every reported 12.x upgrade disaster (jellyfin issues
#17840, #18203) was either a restart mid-migration or pre-existing SQLite corruption surfacing; recovery was
restoring the automatic copy under `data/SQLiteBackups/<timestamp>` and starting again.

### After

1. Dashboard, Libraries: run a full scan of every library (the release notes require it; the first scan is
   slower than usual). For the Books library, confirm covers and page counts appear on the CBZs.
2. Dashboard, Scheduled Tasks: run "Optimize database", then restart the service once more.
3. Hard-refresh the web UI on the iPads (Safari holds the old client).
4. Check `/var/log/jellyfin/` for 401s. 12.0 removed the legacy `/emby` and `/mediabrowser` prefixes and the
   `X-Emby-Token` and `api_key` forms of authentication; current Swiftfin and web clients are fine, old
   scripts and integrations are not.
5. Play something that forces a transcode and watch `sudo intel_gpu_top`; the Core Ultra 5 235 is Jellyfin's
   "Gen 12.7 (MTL/ARL)" Intel class and `jellyfin-ffmpeg8` bundles the drivers.
6. Set `jellyfin_min_version: '12.4'` in `host_vars/eddings.justdavis.com/main.yml` and run the role's tests.
   For the 12.4 upgrade this is already set in the PR that introduced the variable; merge and deploy that PR
   after the upgrade, not before, or the production run fails on this assert.

### Rollback

Only if the migration failed or the new version is unusable. There is no downgrade path; this is a restore.

```bash
sudo systemctl stop jellyfin
sudo systemctl mask jellyfin
sudo mv /var/lib/jellyfin /var/lib/jellyfin.failed-12
sudo mv /etc/jellyfin /etc/jellyfin.failed-12
sudo tar -C / -xzf /root/jellyfin-pre-12-<date>.tgz
# The previous packages stay published in the repository; pin them explicitly. 7.1.4-3 is the newest ffmpeg7
# build; the 7.1.3-3 that eddings ran before the upgrade is also still available.
sudo apt install jellyfin=10.11.6+ubu2404 jellyfin-server=10.11.6+ubu2404 \
    jellyfin-web=10.11.6+ubu2404 jellyfin-ffmpeg7=7.1.4-3-noble
sudo systemctl unmask jellyfin
sudo systemctl start jellyfin
```

Then `apt-mark hold jellyfin jellyfin-server jellyfin-web` until the cause is understood, and remove the hold
before the next attempt.

## Tests

`tasks/test.yml` runs in production and in the AWS harness:

- The systemd unit is active.
- `GET /health` returns 200, waiting up to five minutes for a starting (or migrating) server.
- `GET /System/Info/Public` returns a version, and that version is at least `jellyfin_min_version` when set.

The AWS test instance installs whatever version the repository currently serves, so the harness exercises the
newest release before eddings does.
