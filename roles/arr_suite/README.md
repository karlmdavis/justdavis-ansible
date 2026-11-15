# arr_suite Ansible Role

This role runs the media-management stack as one Docker Compose project under `/opt/arr_suite`: Sonarr (TV),
Bazarr (subtitles), Prowlarr (indexers), qBittorrent (downloads) and Bindery (books). All of it is reached
through the intranet Apache vhost (`https://intranet.justdavis.com/sonarr`, `/bazarr`, `/prowlarr`,
`/bindery`; qBittorrent has its own hostname) or on the private address directly. The compose file was
derived from [ezarr](https://github.com/Luctia/ezarr), whose long list of other services this stack does not
run.

Every container runs as the `arr_suite` system user with the `media_managers` group, and mounts the shared
`groups` tree (or a part of it) at `/data`, so that completed downloads under `/data/torrents` can be
hardlinked into `/data/media/...` rather than copied. The systemd unit restarts the stack on every run of
this role and pulls images on start.

## Bindery (books)

[Readarr](https://github.com/Readarr/Readarr) was retired on 2025-06-27, when its metadata service could not
be kept alive. No single successor has become the community default (late 2026). The candidates and why
Bindery:

- **[Bindery](https://github.com/vavallee/bindery)** (MIT; the awesome-selfhosted entry of the same name is an
  unrelated converter): a from-scratch Go rewrite, not a fork. Monitors authors and wanted books, searches
  Torznab/Newznab indexers (Prowlarr is one such source), sends to qBittorrent, imports into a plain folder
  tree, and takes metadata from public APIs (OpenLibrary, Google Books, Hardcover). It is young (April 2026),
  fast-moving (several releases a week) and one maintainer deep, which is why it is pinned and bumped
  deliberately rather than floating on `:latest` like the linuxserver images.
- **Bookshelf** (`pennydreadful/bookshelf`): the straight Readarr revival, with a choice of Goodreads
  metadata (`softcover` tags, "generally poor" per its own README, compatible with Readarr databases) or
  Hardcover metadata (`hardcover` tags, a fresh database), and an import bug that deletes unrelated files,
  open since 2026-07 (#173). Only worth it for the familiar Readarr UI, which nobody here has used.
- **Shelfmark** (`calibrain/shelfmark`, formerly calibre-web-automated-book-downloader): a manual
  search-and-request UI, like Jellyseerr for books, with multi-user approval. It does not monitor authors.
  The natural addition if one-off requests turn out to be the real need; it would follow the same pattern as
  Bindery in this compose file.
- **Chaptarr**: open source since 2026-08 but with no commits on its default branch since 2026-09, dependent
  on a hosted metadata API, and with CBZ support still an open feature request (#324). **LazyLibrarian** and
  **Mylar3** are the honest options if *comics* acquisition ever matters: no Readarr successor understands
  comic metadata, Bindery included (it files a graphic novel as a book).

### Deployment details that differ from the linuxserver images

- Distroless image, no shell, no PUID/PGID entrypoint: compose `user:` runs it as `arr_suite`, and
  `BINDERY_PUID`/`BINDERY_PGID` are sanity checks that make it refuse to start as anything else.
- Its config directory must exist and be writable before the first start (the role creates it); the
  linuxserver images chown their own.
- `BINDERY_DIR_MODE` caps at `0755`: folders Bindery creates are never group-writable, unlike the rest of the
  media tree. Expect to `chmod g+w` by hand if someone else needs to edit them.
- Single `/data` mount shared with qBittorrent's path layout, so imports hardlink.
- Pinned by `arr_suite_bindery_version` (`defaults/main.yml`).
- Its library root, `groups/media/books/ebooks/`, is created by the `file_server` role (the same change
  that introduced the `kavita` role, which defines the comics/ebooks split); Bindery only creates the
  author and book folders beneath it.

### First-run configuration (manual, once)

1. Open `https://intranet.justdavis.com/bindery` and create the admin account. Do not pick the "local only"
   authentication mode: behind Apache every request arrives from a private address, so that mode would
   serve everything without a login.
2. Settings, API Keys: add a free [Hardcover](https://hardcover.app/) token (format `hc_pat_...`). Without one
   Hardcover metadata is silently skipped and results get noticeably worse.
3. Settings, Indexers: add Prowlarr at `http://prowlarr:9696/prowlarr` (the service name on the compose
   network, since Bindery blocks download links that resolve to loopback, plus Prowlarr's URL base, which it
   serves its API under too). Bindery's docs describe "Prowlarr-synced indexers"; the exact UI wording is
   worth confirming on first run. Individual Torznab endpoints from Prowlarr work as well.
4. Settings, Download Clients: qBittorrent at `http://qbittorrent:8080`, with a `books` category whose save
   path stays under `/data/torrents`.
5. Settings, General, File Naming: leave the default ebook template
   (`{Author}/{Title} ({Year})/{Title} - {Author}.{ext}`). It gives each book its own folder, which Bindery
   needs for reliable re-scans and which Kavita's "Book" library type reads fine. The destination is
   `groups/media/books/ebooks/`, the prose half of the layout the `kavita` role's README defines (comics live
   in `groups/media/books/comics/` with their own naming rules); graphic novels Bindery grabs need filing
   into the comics layout by hand.
6. Add the books the family already owns elsewhere as wanted titles with monitoring off, and look at what
   the indexers return before turning monitoring on for anything.

## Tests

`tasks/test.yml` checks that the unit is active, that Bindery answers `/bindery/api/v1/health`, that its
container is healthy, runs the pinned image and has not restarted, and that all five containers are running.
The linuxserver containers have no further tests; Bazarr, Sonarr and Prowlarr have been running for years
and their failures show up in daily use.
