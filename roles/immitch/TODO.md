# Immich Implementation Plan

## Overview
Implementing Immich (self-hosted photo and video management) using Docker Compose with external PostgreSQL database integration.

## Current Status
- [x] PostgreSQL vector extension (VectorChord) installation
- [x] Database user and database creation
- [ ] Complete Docker Compose setup
- [ ] Environment variable configuration
- [ ] Directory structure and permissions
- [ ] Service management and startup
- [ ] Testing and validation

## Current Issues to Fix
1. **Database variables**: Still using `database_user_name` instead of `vault_postgres_immich_username`
2. **Vector extension**: Not being created IN the immich database after DB creation
3. **Missing structure**: No defaults/main.yml, no templates directory
4. **TODO comment**: Line 30 in install_and_configure.yml needs to be addressed

## Implementation Tasks

### 1. Configuration Questions (ANSWERED)
- **Upload location**: `/var/fileshares/justdavis.com/groups/media/photos`
- **Database variables**: Using vault variables: `vault_postgres_immich_username` and `vault_postgres_immich_password`
- **Service user**: Follow arr_suite pattern (system user with UID/GID management)
- **Backup integration**: 
  - PostgreSQL backups already handled by existing pg_dumpall service ✓
  - Photos directory already covered by tarsnap via `/var/fileshares` ✓
- **Version pinning**: Pin to v1.136.0 (latest stable as of 2025-07-27)
  - Store version in `roles/immitch/defaults/main.yml` for easy access
- **Network access**: Internal only (intranet access)

### 2. Fix VectorChord Setup (HIGH PRIORITY)
- [ ] Update database variables to use vault variables
- [ ] Create vector extension IN the immich database after DB creation
- [ ] Verify PostgreSQL 16 compatibility
- [ ] Test vector extension functionality

### 3. Docker Compose Implementation
- [ ] Create defaults/main.yml with version and configuration
- [ ] Create docker-compose.yml template
- [ ] Configure environment file (.env) template
- [ ] Set up proper volume mounts for upload location
- [ ] Configure database connection to external PostgreSQL
- [ ] Set up proper networking (internal vs external access)

### 4. Directory and Permissions Setup
- [ ] Create upload directory with proper ownership
- [ ] Create config directory for docker-compose files
- [ ] Set up log directory if needed
- [ ] Configure proper file permissions

### 5. Service Management
- [ ] Create systemd service for Docker Compose
- [ ] Configure service to start on boot
- [ ] Set up proper dependency ordering (after PostgreSQL)
- [ ] Create service management handlers

### 6. Database Integration
- [x] Install VectorChord extension (already implemented)
- [x] Create database and user (already implemented)
- [ ] Enable vector extension in Immich database
- [ ] Verify PostgreSQL configuration is compatible

### 7. Testing and Validation
- [ ] Test Docker Compose startup
- [ ] Verify database connectivity
- [ ] Test web interface accessibility
- [ ] Validate upload functionality
- [ ] Check backup integration

### 8. Security and Hardening
- [ ] Configure proper firewall rules if external access needed
- [ ] Set secure database passwords
- [ ] Configure SSL/TLS if exposing externally
- [ ] Review and harden Docker security

## Technical Notes

### Database Configuration
- Using VectorChord extension for vector operations (already configured)
- Database: `immich` (hardcoded name required by Immich)
- User: `{{ vault_postgres_immich_username }}`
- Password: `{{ vault_postgres_immich_password }}`

### Docker Compose Approach
- Using external PostgreSQL instead of containerized DB
- Simplified backup strategy (only need to backup upload files + DB)
- Better resource utilization on single server
- Consistent with existing infrastructure patterns

### Key Environment Variables
```
DB_HOSTNAME=localhost  # or specific hostname
DB_PORT=5432
DB_USERNAME={{ vault_postgres_immich_username }}
DB_PASSWORD={{ vault_postgres_immich_password }}
DB_DATABASE_NAME=immich
UPLOAD_LOCATION=/var/fileshares/justdavis.com/groups/media/photos
TZ=America/New_York
IMMICH_VERSION=v1.136.0
```

## Upgrade Strategy
The role will pin Immich to a specific version (stored in `defaults/main.yml`) for stability. To upgrade:
1. Check release notes at https://github.com/immich-app/immich/releases
2. Update `immich_version` in `roles/immitch/defaults/main.yml`
3. Run the playbook which will:
   - Pull new Docker images
   - Restart services with new version
   - Database migrations run automatically on startup
4. Test functionality before considering the upgrade complete

## Implementation Notes
- Following arr_suite pattern for user/permission management
- Using existing PostgreSQL server instead of containerized DB
- Photos stored in existing fileshare structure (already backed up)
- Internal access only via intranet
- Version stored in role defaults for easy updates