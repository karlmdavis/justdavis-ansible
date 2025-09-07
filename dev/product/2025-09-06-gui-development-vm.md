# GUI Development VM Epic

**Date:** 2025-09-06  
**Status:** In Progress  
**Epic:** Transform krout VM into GUI-capable development sandbox with remote desktop access


## Project Overview

Transform the current headless krout VM into a GUI-capable development sandbox with remote desktop access,
  secure file share permissions,
  and comprehensive development tooling for development work.


## Goals & Requirements

### Primary Goals

1. Enable application development and testing across multiple tech stacks,
     including Rust, Java, Node, Python, and more.
   Support development of libraries, CLI tools, web services, and GUI applications.
2. Provide secure, performant remote desktop access from macOS
     and other platforms (including iOS).
3. Keep development sandboxed from critical services and data,
     while still having access to the tools, services, and data needed to get the work done.
    - This is critical to prevent accidental damage by development agents (e.g., Claude Code).
    - Will have access to homelab file shares, mail server, and other services as needed,
        but with strict access controls to prevent accidental damage.
4. Ensure that setup is reproducible for disaster recovery.
    - Particularly important, given the increased risks of damage from development agents.
    - Will leverage Ansible for configuration management and tarsnap for backups.


### Key Requirements

- **Remote Access**: SSH and RDP via xrdp for best client support and performance.
- **Desktop Environment**: Full Ubuntu 24.04 Desktop for maximum compatibility and reliability.
- **User Management**: Separate `karl_bot` LDAP user and Kerberos principal (not shared with `karl`).
- **Auto-login**: Desktop session auto-login on VM boot for seamless remote access.
- **Security**: Restricted file share access with write permissions only to user directory.


## Technical Architecture

### VM Configuration

- **OS**: Ubuntu 24.04 Desktop.
- **Network**: Stable MAC address to support static IPv4 lease on homelab intranet.
    - Remote access via Tailscale VPN, over RDP and/or SSH.
- **DNS**: `krout.karlanderica.justdavis.com`.
- **Resources**: 4GB RAM, 2 CPU cores, 100GB storage on server's `vms` ZFS dataset.
    - Confirmed adequate host resources are available as of 2025-09-06:
        - 30GB total RAM, 8.4GB available.
        - 16 vCPU cores, average load of 1.0.
        - 2TB free on `ssd-pool` ZFS pool.

### User Management

- **Primary User**: `karl_bot` (new separate LDAP user + Kerberos principal).
- **Home Directory**: `/home/karl_bot` (completely separate from existing `karl` user).
- **Auto-login**: Configure Ubuntu Desktop to auto-login `karl_bot` user on boot.
- **Dotfiles**: Apply existing chezmoi-managed dotfiles to `karl_bot` user.
    - These are also used by the `karl` user, but are well-structured for multi-user deployment.

### Remote Desktop Setup

- **Protocol**: RDP via xrdp package.
- **Client Support**: Optimized for macOS RDP clients (Microsoft Remote Desktop, others).
- **Security**: Authentication via existing LDAP/Kerberos integration.
- **Performance**: Configured for responsive GUI application usage over LAN/VPN.

### Development Environment

```
/home/karl_bot/
├── workspaces/           # Project directories (mirror current /home/karl/workspaces/)
│   ├── obsidian-operator/
│   ├── other-projects/
│   └── ...
├── .config/
│   └── zellij/          # Zellij configuration with project-specific sessions
└── ...                  # Standard dotfiles via chezmoi (bash, vim, git, etc.)
```

### Other Tools and Utilities

- Mail Clients
    - **Interactive**: `aerc` - Modern, Go-based terminal mail client.
        - Easier setup than mutt/neomutt.
        - Excellent HTML rendering and MIME handling.
        - Active development and growing adoption in 2025.
    - **Programmatic**: `msmtp` - Modern SMTP client for automated scripts.
        - Secure authentication with external SMTP servers.
        - Modern replacement for deprecated ssmtp.
        - Ideal for automated notifications and development workflows.


## Security Model

### File Share Access Controls

Implement tiered permissions based on the existing only-members-of-groups
  have access to specific group shares pattern.
Currently:

- `media_managers` group has read-write access to `groups/media/`.
- `administrators_posix` group has read-write access to `groups/sysadmin/`.
- `karlanderica` group has read-write access to `groups/karlanderica/`.

**Read-Only Access** (for `karl_bot`):

- `/var/fileshares/justdavis.com/groups/`...
    - (read access to `groups/` only; no read access to its children)
- `/var/fileshares/justdavis.com/users/`...
    - (read access to `users/` only; no read access to other users' directories here)

**Write Access** (for `karl_bot`):

- `/var/fileshares/justdavis.com/users/karl_bot/`
    - Full read-write access to own user directory.

**No Access** (for `karl_bot`):

- Any other directories not explicitly listed above,
    including `groups/karlanderica/`, `groups/media/` and other directories in `users/`.

**Permission Implementation Analysis**:

Based on `roles/file_server/tasks/config.yml`, the current file share permission model:

- **Parent directories** (`/var/fileshares`, `users/`, `groups/`): mode `u=rwx,g=rwx,o=rxt`.
  - Gives all users read/execute access to list directory contents.
  - Sticky bit prevents deletion of files they don't own.
  
- **Individual user directories**: mode `u=rwxs,g=rwxs,o=` (no access for others).

- **Group directories** (e.g., `groups/media`): mode `u=rwxs,g=rwxs,o=rxst`.
  - Group members have full access.
  - Others can read/list but not write.

**For karl_bot**: This existing model actually provides the desired security:

- karl_bot can *list* user and group directory names (due to `o=rxt` on parents).
- karl_bot cannot *access* most directories (due to `o=` or group restrictions on children).
- karl_bot gets full access only to `/var/fileshares/justdavis.com/users/karl_bot/`.

**Implementation**: `karl_bot` should NOT be added to any existing file share groups
  (`media_managers`, `administrators_posix`, `karlanderica`).

### Directory Structure Security Audit

Review and secure all directories under `/var/fileshares/justdavis.com/`:

- `users/` - Individual user directories (karl, karl_bot, etc.).
- `groups/` - Shared group directories:
    - `karlanderica/` - Family shared directory.
    - `sysadmin/` - System administration files.
    - `media/` - Media files (movies, tv-shows, music, photos, books).
  
Each directory needs proper group ownership and permissions to prevent accidental deletion by development agents.


## Session Management

### Zellij Configuration (Recommended Choice)

After researching 2025 alternatives, zellij remains an excellent choice:

- **Modern Design**: Built in Rust, user-friendly interface.
- **Built-in Session Management**: Automatic session persistence and restoration.
- **Intuitive Keybindings**: Much more accessible than tmux.
- **Project Sessions**: Configure layouts and sessions for different projects.

**Alternative Options Considered**:

- **WezTerm**: Rust-based terminal with built-in multiplexer, GPU-accelerated.
- **Ghostty**: Very new (Dec 2024), fast, platform-native.
- **byobu**: Enhanced tmux with better defaults.

**Decision**: Continue with zellij due to proven user experience and excellent session management capabilities.


## Project Workspace Structure

Mirror current `/home/karl/workspaces/` pattern from `mantis` workstation:

- Each project gets its own subdirectory with consistent structure.
- Git clone(s) of the specific repos cloned into each project directory,
    as children of it, with `.git` suffixes for each clone,
    e.g. justdavis-ansible.git/`.
- Integration with zellij for project-specific session layouts.


## Development Workflow Integration

### GUI Application Support

- **Example Use Case**: Obsidian plugin development requiring Obsidian desktop app.
- **Future Flexibility**: Framework supports additional GUI development tools.
- **Remote Performance**: RDP provides responsive GUI application usage.
- **Manual Installation**: Obsidian and other GUI tools installed manually (not automated).

### Development Tools Integration

- **Version Control**: Git configuration and SSH key management via `user_karl` role.
- **Language Runtimes**: Rust, Node.js, Java development environments.
    - Most of these are installed via `chezmoi apply` of the dotfiles.
- **Terminal Environment**: Zellij with project-specific session configurations.
- **Shell Integration**: Bash, modern CLI tools, development utilities.


## Risks & Mitigation Strategies

### Security Risks & Mitigation
- **Increased Attack Surface**: GUI desktop vs headless server.
  - *Mitigation*: VM remains on private LAN, accessible via VPN only.
- **Remote Desktop Protocol**: RDP security considerations.
  - *Mitigation*: LDAP/Kerberos authentication, private network only.
- **File Access Privilege Escalation**: Risk via shared directories.
  - *Mitigation*: Strict group-based access controls, minimal `karl_bot` privileges.

### Operational Considerations

- **Resource Usage**: Desktop environment increases memory/CPU usage.
  - *Assessment*: Confirmed adequate resources on eddings.
- **Maintenance Overhead**: Additional GUI components to manage and update.
  - *Approach*: Ansible automation for configuration management.
- **Backup Strategy**: Include GUI application data and configurations.
  - *Integration*: Extend existing tarsnap backup to cover `karl_bot` home directory.


## Implementation Approach

### VM Enhancement Strategy

Previously, I'd created a headless development sandbox VM named `krout` using the `virtual_machines` role.
Since we'll be starting with Ubuntu Server and building up to desktop environment, we can upgrade the existing VM in place.

1. **In-Place Upgrade**: Transform existing Ubuntu Server VM into desktop environment.
2. **Network Continuity**: Keep existing MAC address and DHCP static lease (`10.0.0.5`).
3. **Configuration Preservation**: Maintain existing VM disk and basic configuration.

#### **Desktop Installation Approach**

**In-Place Enhancement**: Transform existing Ubuntu Server VM into desktop environment via Ansible:

**Desktop Package Options**:
- **ubuntu-desktop-minimal**: Lightweight desktop with "just the essentials, web browser and basic utilities"
  - Suitable for development work, faster installation, smaller footprint
  - Includes core GNOME desktop, Firefox, basic utilities
  - Recommended for this use case

- **ubuntu-desktop**: Full desktop with "office tools, utilities, web browser and games" 
  - Includes LibreOffice, games, additional applications
  - Larger installation, more comprehensive but not necessary for development

**Recommended Approach**: Use existing Ubuntu Server + automated desktop installation via new `dev_sandbox` Ansible role:

1. **Base**: Keep existing Ubuntu Server 24.04 VM
2. **Desktop Installation**: Install via Ansible tasks:
   ```yaml
   packages:
     - ubuntu-desktop-minimal    # Lightweight desktop option
     - xrdp                      # RDP server
     - gdm3                      # Display manager (included in desktop)
   ```
3. **Configuration**: Ansible handles desktop setup:
   ```yaml
   services:
     - systemctl set-default graphical.target
     - systemctl enable gdm3
     - systemctl enable xrdp
   ```

This approach maintains existing VM while adding GUI capabilities through automation.

### Ansible Role Architecture

**New Role: `dev_sandbox`**

- GUI desktop configuration and optimization.
- xrdp installation and configuration.
- Auto-login setup for karl_bot user.
- Desktop environment customization.

**Enhanced Role: `user_karl`**

- Make configurable for multiple users (`karl`, `karl_bot`).
- Support different home directory paths.
- Maintain existing functionality while adding flexibility.

**Integration Pattern**:

- `dev_sandbox` role handles GUI/desktop setup.
- Enhanced `user_karl` role applied to `karl_bot` user.
- Both roles work together via standard Ansible imports.

### karl_bot User Integration

**LDAP/Kerberos Integration**: `karl_bot` must be added to the existing `people` Ansible dictionary to enable:

- Automatic LDAP user creation.
- Kerberos principal creation.
- User home directory creation (`/var/fileshares/justdavis.com/users/karl_bot/`).
- Integration with existing authentication infrastructure.

**People Dictionary Entry**:

```yaml
people:
  karl_bot:
    initialPassword: "[generated_password]"
    # Additional user properties as needed
```

**Samba Access**: `karl_bot` will automatically get Samba user creation (via `file_server` role)
  but this is likely unnecessary for development use.

### `user_karl` Role Enhancement Strategy

**Backwards Compatibility Approach**: Make the role configurable without breaking existing functionality:

```yaml
# New configurable variables (with defaults for existing karl user)
target_user: "{{ user_karl_target_user | default('karl') }}"
target_home: "{{ user_karl_target_home | default('/home/karl') }}"
target_groups: "{{ user_karl_target_groups | default([]) }}"

# Usage for karl_bot:
user_karl_target_user: "karl_bot" 
user_karl_target_home: "/home/karl_bot"
user_karl_target_groups: []  # No additional groups beyond base user group
```

**Testing Strategy**: Test enhanced role with existing `karl` user
  before applying to `karl_bot` to ensure backwards compatibility.

### Dotfiles Compatibility Assessment

Current chezmoi setup is well-structured for multi-user deployment:

- **Ubuntu Detection**: Template system already handles Ubuntu-specific configuration.
- **Zellij Auto-launch**: Works seamlessly for `karl_bot` user.
- **No Modifications Needed**: Existing templates support user-specific configuration.
- **Deployment**: Standard chezmoi apply process for `karl_bot`.

### Mail Client Configuration

**Integration Point**: Add to enhanced `user_karl` role:

**Package Installation**:
```yaml
packages_mail:
  - aerc      # Modern terminal mail client
  - msmtp     # SMTP client for scripts
  - w3m       # For HTML mail rendering in aerc
```

**Configuration Templates**:

- `aerc` config template with IMAP/SMTP server settings (credentials left blank).
- `msmtp` config template for programmatic mail sending.
- Basic mail aliases and settings.

**Manual Step**: User must configure mail credentials after deployment.

### Backup Configuration

**Integration Pattern**: Create `host_vars/krout.karlanderica.justdavis.com/main.yml` following existing workstation pattern:

```yaml
---
backup_includes:
  - /home

backup_excludes:
  - /home/*/workspaces/**/target          # Rust build artifacts
  - /home/*/.cache                        # User cache directories  
  - /home/*/.local/share/Trash            # Trash directories
  - /home/*/.npm                          # npm cache
  - /home/*/.cargo/registry               # Cargo registry cache
```

**Pattern Analysis**: Based on existing `host_vars/brust.karlanderica.justdavis.com/main.yml` (workstation)
  vs `host_vars/eddings.justdavis.com/main.yml` (server):

- Workstations: Simple `/home` inclusion with build artifact exclusions.
- Servers: Multiple service directories with media file exclusions.

**Result**: `karl_bot` home directory automatically backed up via tarsnap with intelligent exclusions for development artifacts.

### Desktop Environment Configuration Details

**Target Environment**: Ubuntu 24.04 Desktop with GNOME (default).

**Auto-login Configuration**: 

```bash
# Configure GDM3 auto-login for karl_bot user
# File: /etc/gdm3/custom.conf
[daemon]
AutomaticLoginEnable=true  
AutomaticLogin=karl_bot
```

**xrdp Optimization for macOS**:

```ini
# File: /etc/xrdp/xrdp.ini optimizations
[Globals]
bitmap_cache=yes
bitmap_compression=yes
bulk_compression=yes
max_bpp=32
xrdp.ini security_layer=negotiate
crypt_level=high
certificate=
key_file=
ssl_protocols=TLSv1.2, TLSv1.3
```

**Required Packages** (via cloud-init or Ansible):

```yaml
desktop_packages:
  - ubuntu-desktop-minimal    # Core desktop environment
  - xrdp                      # RDP server
  - gdm3                      # Display manager
  - firefox                   # Web browser
  - code                      # VS Code (optional, for development)
  - git                       # Version control
```

**Firewall Configuration**:

```bash
# Allow RDP access on private network
ufw allow from 10.0.0.0/24 to any port 3389
ufw allow from 100.64.0.0/10 to any port 3389  # Tailscale VPN range
```

### Zellij Session Management

**Configuration Approach**: Start with basic zellij setup, project sessions configured manually

**Basic Setup** (via user_karl role):
- Install zellij package
- Deploy basic zellij config template
- Configure auto-launch in bash_profile (already exists in dotfiles)

**Manual Project Sessions**: User creates custom layouts for each project in `/home/karl_bot/workspaces/`

**Future Enhancement**: Could automate session creation by scanning workspace directories and generating zellij layout files.


## Implementation Plan

### Phase 1: VM Enhancement & Base Setup

1. Verify current `krout` VM status and network configuration.
2. Update VM configuration if needed (memory, storage, etc.).
3. Confirm network connectivity and DNS resolution working.
4. Prepare for desktop environment installation.

### Phase 2: User Management & Authentication

1. Create `karl_bot` LDAP user and Kerberos principal.
2. Configure separate `/home/karl_bot` directory structure.
3. Set up desktop session auto-login for `karl_bot` user.
4. Test authentication integration.

### Phase 3: GUI & Remote Desktop Setup

1. Install xrdp package and configure for RDP access.
2. Optimize RDP settings for macOS client compatibility.
3. Configure desktop environment for remote usage.
4. Test remote desktop connectivity from macOS.

### Phase 4: Development Environment

1. Apply enhanced `user_karl` role to `karl_bot` user.
2. Install aerc (interactive) + msmtp (programmatic) mail clients.
3. Set up `/home/karl_bot/workspaces/` project structure.
4. Configure zellij with project-specific sessions.
5. Deploy dotfiles via chezmoi.

### Phase 5: Security & File Share Lockdown

1. Audit all `/var/fileshares/justdavis.com/` directory permissions.
2. Implement any access restrictions needed, per above specification.
3. Test file access controls and permission boundaries.

### Phase 6: Ansible Role Development & Testing

1. Create `dev_sandbox` role for GUI/RDP/auto-login configuration.
2. Enhance `user_karl` role to support configurable users.
3. Test role integration with existing LDAP/Kerberos infrastructure.
4. Document role usage and configuration options.

### Success Criteria & Testing

**Phase 1 - VM Creation**:

- [ ] `krout` VM boots to Ubuntu Desktop login screen.
- [ ] `karl_bot` user can log in locally.
- [ ] Network connectivity confirmed (IP: `10.0.0.5`, DNS resolution).

**Phase 2 - Authentication**:

- [ ] `karl_bot` LDAP authentication working.
- [ ] Kerberos tickets can be obtained.
- [ ] SSH login with `karl_bot` user successful.

**Phase 3 - Remote Desktop**:

- [ ] RDP connection successful from macOS Microsoft Remote Desktop.
- [ ] Desktop session responsive over LAN/VPN.
- [ ] GUI applications launch and function properly.

**Phase 4 - Development Environment**:

- [ ] dotfiles deployed and shell environment configured.
- [ ] Git configuration and SSH keys working.
- [ ] Mail clients installed and basic configuration present.
- [ ] Zellij auto-launch working on SSH login.

**Phase 5 - Security**:

- [ ] `karl_bot` can list `/var/fileshares/justdavis.com/users/` and `groups/`.
- [ ] `karl_bot` cannot access other user directories (permission denied).
- [ ] `karl_bot` has full read-write access to own directory.
- [ ] `karl_bot` cannot access group directories (karlanderica, media, sysadmin).

**Phase 6 - Integration**:

- [ ] Ansible roles deploy successfully to `krout`.
- [ ] Enhanced `user_karl` role maintains backwards compatibility with `karl` user.
- [ ] Backup configured and first backup successful.
- [ ] VM survives reboot with auto-login working.


---

**Status**: ✅ Specification Complete | 🔄 Implementation In Progress