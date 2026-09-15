# Offsite Backups Ansible Role

This role keeps an encrypted, deduplicated offsite copy of each host's important data using
[restic](https://restic.net/) and Backblaze B2. It replaces the former `tarsnap` role: same privacy model (the
provider only ever sees ciphertext, the key never leaves the house), but with predictable cost, automatic
card payment, and snapshot retention that gives a rollback window for accidental deletions.

## Overview

- **What is backed up** comes from the existing per-host `backup_includes` and `backup_excludes`
  variables in `host_vars/`. On eddings that is photos, the file shares, mail, home directories,
  websites, the PostgreSQL dumps, and the arr suite configuration; movies and TV shows are excluded.
- **Where it goes**: one B2 bucket, one repository per host (`b2:<bucket>:<hostname>`). In the AWS tests
  the repository is a local directory, so tests need no credentials.
- **How**: three systemd timers, all with `[Install]` sections so they survive reboots.

| Unit | Schedule | What it does |
|---|---|---|
| `offsite-backups.timer` | daily, 03:00 UTC (+ up to 30 min jitter) | `restic backup` of the include list; writes the metrics file. |
| `offsite-backups-prune.timer` | weekly, Sunday 05:00 UTC | `restic forget` per the keep policy, then `--prune`. |
| `offsite-backups-check.timer` | monthly, 1st at 06:00 UTC | `restic check --read-data-subset=5%`. |

The three services share a `flock`, so they never run concurrently. Each one mails root on failure
(`status-email-root@.service`, carried over from the tarsnap role).

## Rollback window

Retention is 14 daily, 8 weekly, and 12 monthly snapshots (`offsite_backups_keep_*`). A file deleted or
overwritten on the host disappears from the *next* snapshot only; every earlier snapshot still has it,
and its data stays in the repository until the last snapshot referencing it is forgotten. In practice a
mistake can be undone for two weeks at daily granularity and for a year at monthly granularity. Local
ZFS snapshots for instant rollback are a separate concern (see issue #104).

## Architecture Decisions

- **restic over Borg**: restic talks to object storage directly (B2, S3, SFTP), is a single static
  binary in Ubuntu's apt, encrypts client-side with AES-256, and has first-class `forget`/`prune`/`check`.
  Borg needs an SSH host that runs Borg. Kopia is comparable but less proven for unattended servers.
- **Backblaze B2 over a flat-rate box**: $6.95/TB-month (2026-09 list price) with card auto-pay and free egress up to three
  times the stored volume; roughly $3.50/month for eddings' ~440 GB. A Hetzner Storage Box (flat ~€3.81 for
  1 TB) is the alternative if a fixed invoice ever matters more; moving is one `rclone copy` of the
  encrypted repository.
- **Credentials in an EnvironmentFile**: `/etc/offsite-backups/restic.env` (root, `0600`) holds the
  repository, the restic password, and the B2 application key. The systemd units load it, and the
  `offsite-restic` wrapper loads it for interactive use, so nothing sensitive is on a command line.
- **Prune is a separate unit**: backups and pruning have different failure modes and runtimes, and
  keeping them apart is what makes an append-only credential possible later (see below).
- **Metrics via textfile**: the backup script writes `offsite_backups_last_success_timestamp_seconds`
  and friends to `/var/lib/node_exporter/textfile_collector/offsite_backups.prom`, so the monitoring
  stack can alert when a backup is more than about 36 hours old. Wiring that directory into the
  monitoring role's node_exporter is a follow-up.

## One-Time Setup (owner)

1. Sign up for **Backblaze B2 Cloud Storage** (the pay-as-you-go object storage product at
   backblaze.com/cloud-storage, not the $99/year "Computer Backup" client, which is a Mac/Windows
   desktop agent and does not apply to a Linux server). Add a card; billing is monthly by usage, and
   the first 10 GB is free. Then create a **private** bucket named
   `justdavis-offsite-backups` (or override `offsite_backups_b2_bucket`). Leave versioning at its
   default ("keep only the last version"); restic manages its own history. Leave **Default Encryption
   (SSE-B2) off**: restic already encrypts everything client-side with a key only we hold, so
   server-side encryption with a Backblaze-held key adds nothing. Do not enable object lock yet.
2. Create an **application key restricted to that bucket** with capabilities `listBuckets`, `listFiles`,
   `readFiles`, `writeFiles`, `deleteFiles` (prune needs delete).
3. Generate a long random restic password and store it in 1Password together with the B2 key. Losing
   the password means losing every backup; there is no recovery.
4. Add to the vault (`uv run ansible-vault edit group_vars/all/vault.yml`):

   ```yaml
   vault_offsite_backups_restic_password: "..."
   vault_offsite_backups_b2_key_id: "..."
   vault_offsite_backups_b2_key: "..."
   ```

5. Run the playbook. The role initializes the repository on first run and starts the timers. The first
   backup of eddings uploads roughly 440 GB, which takes about a day on the current uplink; later runs
   upload only new or changed chunks.

## Operations

All commands run as root on the host, through the wrapper that loads the credentials:

```bash
sudo offsite-restic snapshots                     # list snapshots
sudo offsite-restic stats latest                  # size of the latest snapshot
sudo offsite-restic stats --mode raw-data         # actual bytes stored in B2 (what you pay for)
sudo systemctl start offsite-backups.service      # run a backup now
journalctl -u offsite-backups.service -n 50       # what happened last time
systemctl list-timers 'offsite-backups*'          # next scheduled runs
```

### Restoring

```bash
# One file or directory, from the latest snapshot, into a scratch location:
sudo offsite-restic restore latest --target /var/tmp/restore --include /var/fileshares/justdavis.com/users/karl/docs/thing.pdf

# The same from a specific point in time (see `snapshots` for IDs):
sudo offsite-restic restore 3a1b2c3d --target /var/tmp/restore --include /home/karl

# Browse without restoring:
sudo offsite-restic ls latest /var/vmail | head
sudo offsite-restic mount /mnt/restic                # FUSE mount of every snapshot, read-only
```

Restored files keep their ownership and modes. Move them into place by hand; do not restore straight
onto the live path without looking first.

### Restore drill

Once a quarter, restore one recently changed file and one older photo and confirm they open. The AWS
test does the same automatically with a canary file on every run.

### Disaster recovery on a fresh machine

Install restic, recreate `/etc/offsite-backups/restic.env` from the values in 1Password (repository,
password, B2 key), then `restic restore latest --target /` for the paths you need. The cache under
`/var/cache/restic` is rebuilt automatically.

## Optional Hardening: Append-Only Credentials

To protect the history against a compromised host, give the host a B2 key **without** `deleteFiles`
and run the prune unit only from a workstation with the full key. Object lock on the bucket adds a
retention floor that even the full key cannot bypass. Not enabled by default; it doubles the number of
credentials to manage.

## Tarsnap

Tarsnap preceded this role. Its account was deleted in mid-2026 after the prepaid balance ran out (the
archives went with it), so `tasks/remove_tarsnap.yml` uninstalls the client, its apt repository and
expired signing key, the key file, the ~1 GB cache, and the systemd units from every host. The tasks are
no-ops where Tarsnap was never installed.

## Testing

`roles/offsite_backups/tasks/test.yml` (tag `test`) stops the timers, writes a canary file into the
first backed-up directory, runs the backup unit, verifies a snapshot exists and the metrics file was
written, restores the canary and compares it byte for byte, runs the prune and check units once, cleans
up, and starts the timers again. Everything but the timer handling is skipped on production hosts
(`is_test`), because a first full backup takes far too long for a playbook run.
