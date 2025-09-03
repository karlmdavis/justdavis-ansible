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
- Is already included in tarsnap backups
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

Immich requires specific versions of PostgreSQL extensions:
- **pgvector**: >= 0.7.0, < 1.0.0 (installed from PostgreSQL APT repository)
- **VectorChord**: >= 0.3.0, < 0.5.0 (manually installed from GitHub)

The role configures the PostgreSQL official APT repository to ensure newer pgvector versions are available, as Ubuntu's default repositories may have older versions that lack required features like the `halfvec[]` type.

## Role Variables

```yaml
# Version of Immich to install (in defaults/main.yml)
immich_version: v1.136.0

# Base directory for Immich installation
immich_base_dir: /opt/immich

# System user for running Immich
immich_user: immich

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

To upgrade to a new version:

1. Check the [Immich releases](https://github.com/immich-app/immich/releases) for breaking changes
2. Update the version in `roles/immitch/defaults/main.yml`:
   ```yaml
   immich_version: v1.137.0  # or newer
   ```
3. If VectorChord needs updating:
   - Check [VectorChord releases](https://github.com/tensorchord/VectorChord/releases) for PostgreSQL 16 compatible versions
   - Update the download URL in `roles/immitch/tasks/install_and_configure.yml`
   - Ensure the version remains within Immich's required range (>= 0.3.0, < 0.5.0)
4. Run the playbook:
   ```bash
   ./ansible-playbook-wrapper site.yml --tags immitch
   ```
5. The service will automatically pull new images and restart
6. Database migrations run automatically on startup
7. Test the web interface at `http://eddings.justdavis.com:2283`

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
- **Photos**: Stored in `/var/fileshares` which is included in tarsnap backups
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
