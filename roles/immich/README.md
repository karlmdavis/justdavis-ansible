# Immich Ansible Role

This role installs and configures [Immich](https://immich.app/), a self-hosted photo and video management solution, using Docker Compose with an external PostgreSQL database.

## Overview

Immich provides a self-hosted alternative to Google Photos with features including:
- Automatic photo/video backup from mobile devices
- Machine learning-based search and organization
- Face recognition and object detection
- Map view for geotagged photos
- Sharing albums with family members

## Architecture Decisions

### External PostgreSQL Database
Rather than using Immich's containerized PostgreSQL, this role integrates with the existing PostgreSQL server on the system. This approach:
- Simplifies backup management (existing pg_dumpall covers Immich)
- Reduces resource usage (one less database server)
- Maintains consistency with other applications
- Leverages existing PostgreSQL maintenance and monitoring

### Storage Location
Photos are stored in `/var/fileshares/justdavis.com/groups/media/photos`, which:
- Integrates with existing file share structure
- Is already included in the offsite backups (`offsite_backups` role)
- Allows for easy access and management
- Provides proper permissions through media_managers group

### Hardware Acceleration
The role includes optional hardware acceleration configurations:
- Video transcoding: VAAPI support for AMD integrated graphics
- Machine learning: ROCm support for AMD GPUs (experimental)
- Disabled by default but easily enabled via docker-compose.yml comments
- Automatically disabled in test environments

## Requirements

- Docker and Docker Compose (provided by the `docker` role)
- PostgreSQL 16 with VectorChord extension (configured by this role)
- Sufficient storage space for photos/videos
- 4GB RAM minimum (6GB recommended)

### PostgreSQL Extension Version Requirements

Immich checks the PostgreSQL and extension versions at startup and refuses to start outside these ranges
(as of Immich v3.2.4):

- **PostgreSQL**: >= 14, < 20.
- **pgvector**: >= 0.7, < 0.9 (installed from PostgreSQL APT repository).
- **VectorChord**: >= 0.3, < 2.0 (manually installed from GitHub; this role installs 0.4.3, which is also the
  version Immich's own database image ships).

The ranges move between Immich releases. The authoritative values for a release are in
`server/src/constants.ts` at that release's tag, and in the
[standalone PostgreSQL guide](https://docs.immich.app/administration/postgres-standalone).

The role configures the PostgreSQL official APT repository to ensure newer pgvector versions are available, as Ubuntu's default repositories may have older versions that lack required features like the `halfvec[]` type.

## Role Variables

This role requires no configuration, but the following variables must be set in the Ansible vault:

```yaml
# PostgreSQL credentials (in vault)
vault_postgres_immich_username: immich
vault_postgres_immich_password: <encrypted>
```

## Dependencies

This role depends on:
- `docker` role (for Docker installation)
- `postgresql_server` role (for database server)
- PostgreSQL 16 must be installed and running

## Installation Process

The role performs the following steps:

1. Creates system user and directories
2. Installs VectorChord PostgreSQL extension
3. Creates PostgreSQL database and user
4. Configures Docker Compose with:
   - Immich server
   - Machine learning container
   - Redis cache
5. Sets up systemd service for automatic startup

## Upgrading Immich

Things that hold for every upgrade:

- **Downgrading is not supported**, even between patch releases. Going back means restoring the database,
  so take a dump first.
- **The server and the mobile app must be within one major version of each other.** The app supports the
  current and the previous major server version only. Upgrade the server before opening an app that has
  been updating itself unattended.
- **The vector extension range can move.** A new Immich release may need a newer VectorChord, and an older
  Immich will not start on a VectorChord newer than it knows (v1.140 needed < 0.5).

To upgrade to a new version:

1. Read the [Immich release notes](https://github.com/immich-app/immich/releases) for every release between
   the current and the new version, looking for breaking changes, removed environment variables, and changes
   to the `docker-compose.yml` attached to the release.
2. Check the extension ranges for the new version (see [above](#postgresql-extension-version-requirements)).
   If VectorChord needs updating:
   - Check [VectorChord releases](https://github.com/tensorchord/VectorChord/releases) for a PostgreSQL 16
     package.
   - Update the download URL in `roles/immich/tasks/install_and_configure.yml`.
   - The playbook restarts PostgreSQL to load the new library, and Immich updates the extension when it
     next starts. The standalone PostgreSQL guide lists the `ALTER EXTENSION` and `REINDEX` commands to run
     by hand if it does not.
3. Update the version in `roles/immich/defaults/main.yml`:
   ```yaml
   immich_version: v3.2.4
   ```
4. Stop Immich and take a rollback point:
   ```bash
   sudo systemctl stop immich
   sudo -u postgres pg_dump -Fc immich -f /var/lib/postgresql/backups/immich-pre-upgrade.dump
   sudo zfs snapshot ssd_pool/pgdata@pre-immich-upgrade
   sudo zfs snapshot ssd_pool/fileshares@pre-immich-upgrade
   ```
5. Run the playbook:
   ```bash
   ./ansible-playbook-wrapper site.yml --limit=eddings.justdavis.com --tags=immich
   ```
   The service pulls the new images and starts, and the database migrations run on startup. The role's
   tests then wait for the API and check that the running version is the pinned one.
6. Check the web interface at `https://immich.intranet.justdavis.com`: the timeline loads, a photo and a
   video open, and a search returns results.
7. Once the new version has proven itself, delete the dump and the two snapshots.

To roll back: stop Immich, drop and recreate the `immich` database, restore the dump into it with
`pg_restore`, set `immich_version` back, and run the playbook again.

## Enabling Hardware Acceleration

### For Video Transcoding (AMD Ryzen 9 4900H)
1. Edit `{{ immich_base_dir }}/docker-compose.yml`
2. Uncomment the `extends` section under `immich-server`:
   ```yaml
   extends:
     file: hwaccel.transcoding.yml
     service: vaapi
   ```
3. Uncomment the GPU device mount:
   ```yaml
   - /dev/dri:/dev/dri
   ```
4. Restart the service: `sudo systemctl restart immich`

### For Machine Learning (Experimental)
1. For AMD GPUs with ROCm support, uncomment the ML acceleration section
2. Change the image tag to include `-rocm`
3. Note: This significantly increases resource usage

## Backup Strategy

- **Database**: Automatically backed up via existing PostgreSQL pg_dumpall
- **Photos**: Stored in `/var/fileshares` which is included in the offsite backups
- **Configuration**: Minimal configuration in `/opt/immich` (can be recreated)

## Security Considerations

- Service only accessible on internal network (intranet)
- Database credentials stored in Ansible vault
- No external access configured by default
- Redis is not exposed outside of Docker network

## Troubleshooting

### Check Service Status
```bash
sudo systemctl status immich
sudo docker compose -f /opt/immich/docker-compose.yml logs
```

### Database Connection Issues
- Verify PostgreSQL is running: `sudo systemctl status postgresql`
- Check VectorChord extension: `sudo -u postgres psql immich -c '\dx'`
- Verify credentials in `/opt/immich/.env`

### Storage Issues
- Check disk space: `df -h /var/fileshares`
- Verify permissions: `ls -la /var/fileshares/justdavis.com/groups/media/photos`

## Known Limitations

- Hardware acceleration may not work in virtualized environments
- Large imports can consume significant CPU/RAM during ML processing
- Initial face recognition can take hours for large libraries

## References

- [Immich Documentation](https://immich.app/docs)
- [Hardware Acceleration Guide](https://immich.app/docs/features/hardware-transcoding)
- [ML Hardware Acceleration](https://immich.app/docs/features/ml-hardware-acceleration)
