# Kavita Ansible Role

This role runs [Kavita](https://www.kavitareader.com/), a self-hosted reader for comics (CBZ/CBR), EPUBs and
PDFs, as a single Docker container that scans the family's shared books folder read-only. It sits next to
Jellyfin, which indexes the same folder, and exists for one reason: reading on the kids' iPads. Jellyfin's
book support stops at a basic web reader with no iPad client; Kavita adds per-child accounts with age
ratings, page-level progress sync, and OPDS for native iPad apps.

## Architecture decisions

- **Same folder as Jellyfin, mounted read-only.** Books live in
  `/var/fileshares/justdavis.com/groups/media/books` (owned `root:media_managers`, created by the
  `file_server` role, in the offsite backups). Kavita keeps covers, metadata and its database under its own
  config directory, so it never needs to write into the library, and the `:ro` mount means a bug or a
  misclick in its UI cannot delete a book. Jellyfin keeps working unchanged; Kavita can be removed later with
  no data migration.
- **Official image, non-root via compose `user:`.** The `jvmilazz0/kavita` entrypoint runs Kavita as whatever
  user starts the container (its PUID/PGID handling is commented out upstream), so the compose file sets
  `user: kavita_uid:media_managers_gid` and the role pre-creates `/opt/kavita/config` owned by that user.
  Everything Kavita writes (database, covers, cache, logs, its own backups) lives under that directory. If
  this ever proves awkward, `lscr.io/linuxserver/kavita` supports real PUID/PGID.
- **Loopback only, behind Apache.** Docker-published ports bypass UFW, so the port binding is the whole
  exposure model: the container listens on `127.0.0.1:5000` and the `apache` role's
  `intranet-kavita.conf.j2` serves it as `https://kavita.intranet.justdavis.com` (wildcard certificate,
  websocket upgrade). TLS is not optional: the iPad apps below refuse plain HTTP to a server that is not on
  the local network, and a Tailscale address counts as remote.
- **Own hostname, not a subpath.** Kavita's Base URL cannot be changed when running as a non-root user, and
  subpath proxying has a history of bugs, so it gets `kavita.intranet.justdavis.com` like Immich and Grafana
  rather than `intranet.justdavis.com/kavita`.
- **Pinned image, deliberate bumps.** `kavita_version` pins the Docker Hub tag. Kavita fixed a CVSS 9.3
  pre-authentication issue in 0.9.0.2 (2026-05) and ten more security fixes in 0.9.1.0 (2026-08), so keep it
  current, but as reviewed changes. It is never exposed to the public internet.
- **State is backed up.** `/opt/kavita/config` holds the SQLite database (users, reading progress, age
  restrictions, API keys), covers, and Kavita's own daily backup zips (30 kept). It is in eddings'
  `backup_includes`.

## Requirements

- Docker and Docker Compose (the `docker` role).
- The `media_managers` group and the `books/`, `books/comics/` and `books/ebooks/` folders (the `file_server`
  role).
- The Apache vhost and the DNS record (the `apache` and `dns_server` roles). A first deploy therefore needs a
  full `site.yml` run; later upgrades can be scoped with `--tags=kavita`.

## Role variables

See `defaults/main.yml`: `kavita_version` (Docker Hub tag), `kavita_base_dir` (`/opt/kavita`), `kavita_user`.

## Library layout (the convention everything else follows)

Kavita's scanner has rules the Jellyfin library did not need, and this is where they are decided for the
whole repository: the Bindery import destination and template (`arr_suite` role) and the Calibre
save-to-disk template used for converted purchases follow this layout too. Jellyfin is unaffected: its one
Books library covers the whole `books/` tree.

- `books/comics/`: one folder per series, files `Series/Series v01.cbz`, `Series/Series v02.cbz`, and so on.
  The volume marker matters: a lone `Series/Series.cbz` with no volume or chapter number is classified as a
  "Special" rather than a volume. Kavita library type **Comic (Flexible)**; not "Comic", which expects Comic
  Vine's year-based naming and misparses these.
- `books/ebooks/`: prose, one folder per book, `Author/Title (Year)/Title - Author.epub` (Bindery's default
  template). Kavita library type **Book**, which reads the EPUB's own metadata, so folder names matter less
  here.
- Nothing directly under `books/`, `books/comics/` or `books/ebooks/` except those folders. Kavita wants
  every file inside a series folder; verify what it does with a loose file at a library root before relying
  on either behaviour.
- Every CBZ carries a `ComicInfo.xml` with at least `<Series>`, `<Volume>` (or `<Number>`), and
  `<AgeRating>Everyone</AgeRating>` (or the right rating). A series takes the highest rating of its files,
  and the kids' accounts are configured to hide unrated series (see below), so an unrated file disappears
  for them.

### One-time move of the existing files (2026-10-31 state)

The folder held 12 CBZs when this role was written, laid out for Jellyfin at the top of `books/`. The move
below is a manual, approved step on eddings; Jellyfin rescans the moved files without complaint.

| From | To |
|---|---|
| `Amulet by Kazu Kibuishi/<file>.cbz` | `comics/Amulet/Amulet v01.cbz` ... (one per volume, in order) |
| `Dog Man by Dav Pilkey/<file>.cbz` | `comics/Dog Man/Dog Man v01.cbz` ... |
| `Sorceline by Sylvia Douyé/<file>.cbz` | `comics/Sorceline/Sorceline v01.cbz` ... |
| `Beetle & the Hollowbones.cbz` | `comics/Beetle & the Hollowbones/Beetle & the Hollowbones v01.cbz` |

Adding a `ComicInfo.xml` to an existing CBZ without touching its pages, as the `karl` user over SSH (the
files are group-writable by `media_managers`):

```bash
cd /tmp
cat > ComicInfo.xml <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Series>Dog Man</Series>
  <Volume>1</Volume>
  <AgeRating>Everyone</AgeRating>
</ComicInfo>
XML
zip -u "/var/fileshares/justdavis.com/groups/media/books/comics/Dog Man/Dog Man v01.cbz" ComicInfo.xml
```

`unzip -l <file>.cbz | grep ComicInfo` confirms it; Kavita picks the metadata up on the next scan.

## First-run setup (manual, once)

1. Open `https://kavita.intranet.justdavis.com` and create the admin account (the first account created is
   the admin; do this promptly after the first deploy, since the endpoint is open until then).
2. Settings, General: set **Host Name** to `https://kavita.intranet.justdavis.com`. This is what puts the
   right address into OPDS feeds and emails; the proxy headers do not.
3. Libraries: add **Comics**, type **Comic (Flexible)**, folder `/books/comics`; and **Ebooks**, type
   **Book**, folder `/books/ebooks`. Scan both.
4. For each child, Users: create an account with **Download** on (the iPad apps need it), **Change
   Restriction** off, **Age Rating** set to the right level and **Include Unknowns** off, so that unrated
   series are hidden rather than shown. Give the account access to the Comics library (and Ebooks when there
   is something in it).
5. Check the restrictions from Safari on an iPad while logged in as the child. Both known bugs in this area
   showed up exactly there: a child able to change their own rating
  ([Kavita #3274](https://github.com/Kareadita/Kavita/issues/3274), fixed 2024) and restrictions dropped by an
  identity-provider sync ([Kavita #4955](https://github.com/Kareadita/Kavita/issues/4955), 2026).
6. In each child's account settings, create one named **API key** per app per device (the clients tab of the
   user settings), so a lost iPad can be revoked on its own.

## iPad apps

Claims about third-party apps below were taken from their App Store listings on 2026-10-31; re-check before
buying anything.

- **Klibri** (free, no in-app purchases, iPadOS 16+) is the first choice: a Kavita-native reader for CBZ,
  CBR, EPUB and PDF with progress written back to the server. It needs the account's Download role.
- **Panels** is the most polished comic reader and syncs progress with Kavita, but connecting to your own
  server is a premium feature, per iPad or via Family Sharing.
- Not Chunky (last updated 2020, does not write progress back) and not the Jellyfin iOS app (no book or
  comic reading at all).
- iPadOS **Guided Access** supplies the kid mode none of the apps have.

## Tests

`tasks/test.yml` runs in production and in the AWS harness:

- The systemd unit is active, `/api/health` answers, the container reports `healthy`, runs the pinned image,
  and has not restarted since this run recreated it.
- In test environments only (and never in check mode): generates a one-page CBZ under
  `books/comics/Kavita Test Series/`, registers an admin, creates the Comics library over the API, triggers a
  scan and waits for the series to appear. This proves the read-only mount, the folder convention above, and
  the API routes used, without ever creating accounts on the production server.

## Operations

- **Upgrade**: bump `kavita_version` after reading the release notes, run `site.yml --tags=kavita`, and the
  tests confirm the new image is the one serving.
- **Backups**: everything is under `/opt/kavita/config`, already in `backup_includes`. Kavita also writes its
  own backup zips to `config/backups/`.
- **Logs**: `docker logs kavita`, or `config/logs/` on the host.
