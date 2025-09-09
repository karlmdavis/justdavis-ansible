# GUI Development VM Product Specification

**Date:** 2025-09-06  
**Status:** Implemented  
**Product:** GUI-capable development sandbox VM with remote desktop access


## Product Overview

The krout VM is a GUI-capable development sandbox with remote desktop access,
  secure file share permissions,
  and comprehensive development tooling for development work.
This system provides a sandboxed development environment that is
  isolated from critical services while maintaining access to necessary homelab resources.


## Goals & Requirements

### Primary Capabilities

1. **Multi-stack Development Environment**:
   Supports application development and testing across multiple tech stacks,
     including Rust, Java, Node, Python, and more.
   Enables development of libraries, CLI tools, web services, and GUI applications.
2. **Secure Remote Desktop Access**:
   Provides secure, performant remote desktop access from macOS
     and other platforms via RDP over VPN.
3. **Sandboxed Development Environment**:
   Development work is isolated from critical services and data,
     while maintaining controlled access to necessary homelab resources.
    - Prevents accidental damage by development agents (e.g., Claude Code).
    - Provides access to homelab file shares, mail server, and other services,
        with strict access controls to prevent accidental damage.
4. **Reproducible Configuration**:
   Setup is fully automated and reproducible for disaster recovery.
    - Configuration is managed via Ansible automation.
    - Data is backed up via tarsnap with intelligent exclusions for development artifacts.


### Key Features

- **Remote Access**: SSH and RDP via xrdp optimized for macOS client compatibility.
- **Desktop Environment**: Ubuntu 24.04 with XFCE4 desktop for reliability and performance.
- **User Management**: Dedicated `karl_bot` LDAP user and Kerberos principal (isolated from `karl`).
- **Seamless Access**: Desktop environment configured for optimal remote usage.
- **Security**: Restricted file share access with write permissions only to user directory.


## Technical Architecture

### VM Configuration

- **OS**:
  Ubuntu 24.04 Server with XFCE4 desktop environment.
- **Network**:
  Static MAC address with IPv4 lease (`10.0.0.5`) on homelab intranet.
    - Remote access via Tailscale VPN over RDP and SSH.
- **DNS**:
  `krout.karlanderica.justdavis.com`.
- **Resources**:
  4GB RAM, 2 CPU cores, 100GB storage on server's `vms` ZFS dataset.
- **Host Resources**:
  Deployed on eddings server with adequate capacity:
    - 30GB total RAM, sufficient available resources.
    - 16 vCPU cores with low average load.
    - 2TB+ available on `ssd-pool` ZFS pool.

### User Management

- **Primary User**: `karl_bot` (dedicated LDAP user + Kerberos principal).
- **Home Directory**: `/home/karl_bot` (isolated from existing `karl` user).
- **Authentication**: Integrated with homelab LDAP/Kerberos infrastructure.
- **Dotfiles**: Chezmoi-managed dotfiles deployed to `karl_bot` user.
    - Shared configuration templates with `karl` user but separate deployment.

### Remote Desktop Configuration

- **Protocol**: RDP via xrdp with performance optimizations.
- **Client Compatibility**: Optimized for macOS RDP clients (Microsoft Remote Desktop).
- **Security**: LDAP/Kerberos authentication with firewall-restricted access.
- **Performance**: Tuned for responsive GUI applications over VPN connections.

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

### Development Tools

- **Mail Clients**
    - **Interactive**: `aerc` - Modern terminal mail client with HTML rendering.
    - **Programmatic**: `msmtp` - SMTP client for automated scripts and notifications.
- **Terminal Environment**: Zellij session manager with project-specific configurations.
- **Development Runtimes**: Rust, Node.js, Java environments via dotfiles.
- **Version Control**: Git with SSH key management.


## Security Model

### File Share Access Controls

Implements tiered permissions based on the existing group-based access pattern.
Current group access:

- `media_managers` group: read-write access to `groups/media/`.
- `administrators_posix` group: read-write access to `groups/sysadmin/`.
- `karlanderica` group: read-write access to `groups/karlanderica/`.

**karl_bot Access Permissions**:

**Read-Only Access**:
- `/var/fileshares/justdavis.com/groups/` (directory listing only)
- `/var/fileshares/justdavis.com/users/` (directory listing only)

**Write Access**:
- `/var/fileshares/justdavis.com/users/karl_bot/` (full read-write to own directory)

**No Access**:
- Group directories (`groups/karlanderica/`, `groups/media/`, `groups/sysadmin/`)
- Other user directories in `users/`

### Permission Implementation

Leverages existing file share permission model:

- **Parent directories** (`/var/fileshares`, `users/`, `groups/`): mode `u=rwx,g=rwx,o=rxt`
  - Allows directory listing for all users
  - Sticky bit prevents unauthorized file deletion
  
- **Individual user directories**: mode `u=rwxs,g=rwxs,o=` (no access for others)

- **Group directories**: mode `u=rwxs,g=rwxs,o=rxst`
  - Group members have full access
  - Others can list but not access contents

**karl_bot Security**: 
- Can list directory names but cannot access restricted content
- Full access only to own user directory
- Not member of any file share groups (`media_managers`, `administrators_posix`, `karlanderica`)

### Directory Structure

Secured directories under `/var/fileshares/justdavis.com/`:

- `users/` - Individual user directories (karl, karl_bot, etc.)
- `groups/` - Shared group directories:
    - `karlanderica/` - Family shared directory
    - `sysadmin/` - System administration files
    - `media/` - Media files (movies, tv-shows, music, photos, books)
  
All directories have proper group ownership and permissions to prevent accidental modification by development agents.


## Session Management

### Zellij Configuration

The development environment uses zellij for session management:

- **Modern Design**: Built in Rust with user-friendly interface
- **Session Persistence**: Automatic session persistence and restoration
- **Intuitive Interface**: More accessible than traditional tmux
- **Project Layouts**: Support for project-specific session configurations
- **Auto-launch**: Configured to start automatically on SSH login


## Project Workspace Structure

Follows established `/home/karl_bot/workspaces/` pattern:

- Each project has its own subdirectory with consistent structure
- Git repositories cloned with `.git` suffixes (e.g., `justdavis-ansible.git/`)
- Integration with zellij for project-specific session layouts
- Automated backup with intelligent exclusions for build artifacts


## Development Workflow Integration

### GUI Application Support

- **Use Cases**: Supports GUI development tools like Obsidian for plugin development
- **Flexibility**: Framework supports additional GUI development tools as needed
- **Remote Performance**: RDP optimized for responsive GUI application usage
- **Installation**: GUI tools installed manually based on specific project needs

### Development Environment

- **Version Control**: Git with SSH key management via `user_karl` role
- **Language Runtimes**: Rust, Node.js, Java development environments via dotfiles
- **Terminal Environment**: Zellij with project-specific session configurations
- **Shell Integration**: Bash with modern CLI tools and development utilities


## Security Considerations

### Risk Mitigation
- **Attack Surface**: GUI desktop increases potential attack vectors
  - *Mitigation*: VM accessible only via VPN on private network
- **Remote Desktop**: RDP protocol security considerations
  - *Mitigation*: LDAP/Kerberos authentication with firewall restrictions
- **File Access**: Potential for privilege escalation via shared directories
  - *Mitigation*: Strict group-based access controls with minimal `karl_bot` privileges

### Operational Aspects

- **Resource Usage**: Desktop environment has higher memory/CPU requirements
  - *Status*: Adequate resources confirmed available on eddings host
- **Maintenance**: GUI components require ongoing updates and management
  - *Approach*: Fully automated via Ansible configuration management
- **Backup Strategy**: Comprehensive backup coverage with intelligent exclusions
  - *Implementation*: Tarsnap backup covers `karl_bot` home directory with development artifact exclusions


## Implementation Details

### VM Implementation

The `krout` VM was created using the `virtual_machines` role and enhanced with desktop capabilities:

1. **Base Installation**: Ubuntu Server 24.04 with desktop environment added
2. **Network Configuration**: Stable MAC address with DHCP static lease (`10.0.0.5`)
3. **Storage**: Persistent VM disk with configuration preservation

### Desktop Environment

**Implementation**: Ubuntu Server enhanced with desktop environment via the `dev_sandbox` Ansible role:

**Installed Packages**:

- `ubuntu-desktop-minimal`: Lightweight desktop with essential utilities
- `xfce4` and `xfce4-goodies`: XFCE desktop environment for performance
- `xrdp`: RDP server for remote access
- Development tools: git, curl, vim, htop, tree, alacritty
- Mail clients: aerc, msmtp, w3m
- Fonts: JetBrains Mono, Liberation, DejaVu, Noto

**System Configuration**:

- Graphical target set as system default
- XFCE4 configured as default desktop session
- xrdp service enabled and optimized for macOS clients
- UFW firewall configured for secure RDP access

### Ansible Role Architecture

**`dev_sandbox` Role**:

- GUI desktop configuration and optimization
- xrdp installation and RDP optimization
- Desktop environment customization for development
- Firewall configuration for secure access

**Enhanced `user_karl` Role**:

- Configurable for multiple users (`karl`, `karl_bot`)
- Flexible home directory paths
- Maintains backwards compatibility with existing `karl` user
- SSH key management and dotfiles deployment

**Integration**:

- `dev_sandbox` role handles GUI/RDP setup
- `user_karl` role applied with `karl_bot` configuration
- Seamless integration via host-specific variables

### User Integration

**LDAP/Kerberos Integration**: `karl_bot` user is integrated via the `people` dictionary:

- LDAP user creation with UID 10006
- Kerberos principal creation
- User home directory: `/var/fileshares/justdavis.com/users/karl_bot/`
- Full authentication infrastructure integration

**Implementation**:
```yaml
people:
  karl_bot:
    givenName: Karl
    sn: Bot
    uidAndGidNumber: 10006
    mail: karl_bot@justdavis.com
    initialPassword: "{{ vault_people.karl_bot.initialPassword }}"
```

### Role Enhancement

**Backwards Compatibility**: Enhanced `user_karl` role maintains full compatibility with existing `karl` user:

**Configuration Variables**:
```yaml
# Host-specific configuration for krout VM
user_karl_target_user: karl_bot
user_karl_ssh_keys: "{{ ssh_keys_karl_public }}"
```

**Implementation**: Role automatically detects target user and applies appropriate configuration while preserving all existing functionality for the original `karl` user.

### Dotfiles Integration

Chezmoi dotfiles are successfully deployed to `karl_bot` user:

- **Ubuntu Compatibility**: Templates handle Ubuntu-specific configuration automatically
- **Zellij Auto-launch**: Configured to start on SSH login
- **User-specific**: Templates adapt to `karl_bot` user context
- **Deployment**: Standard `chezmoi apply` process with no modifications needed

### Mail Client Integration

**Installed Clients**:

- `aerc`: Modern terminal mail client with HTML rendering
- `msmtp`: SMTP client for automated scripts and notifications  
- `w3m`: HTML renderer for aerc mail client

**Configuration**: Basic templates provided with manual credential configuration required for security.

### Backup Configuration

**Implementation**: Comprehensive backup strategy via `host_vars/krout.karlanderica.justdavis.com/main.yml`:

```yaml
backup_includes:
  - /home

backup_excludes:
  - /home/*/workspaces/**/target          # Rust build artifacts
  - /home/*/workspaces/**/node_modules     # Node.js dependencies  
  - /home/*/.cache                        # User cache directories
  - /home/*/.cargo/registry               # Cargo registry cache
  # Additional exclusions for development artifacts
```

**Result**: `karl_bot` home directory backed up via tarsnap with intelligent exclusions to avoid backing up build artifacts and caches.

### Desktop Configuration

**Environment**: Ubuntu 24.04 with XFCE4 desktop environment

**RDP Optimization**: xrdp configured with performance optimizations for macOS clients:

- Bitmap compression and caching enabled
- High color depth (32bpp) support
- TLS 1.2/1.3 security protocols
- Optimized for VPN connections

**Firewall Security**: UFW configured to allow RDP access only from:

- Home network: 10.0.0.0/24
- Tailscale VPN: 100.64.0.0/10

**System Services**:

- Graphical target enabled as default
- xrdp service running and enabled
- XFCE4 set as default desktop session

### Session Management

**Zellij Configuration**:

- Zellij package installed via dotfiles
- Auto-launch configured in bash profile
- Project-specific session support available
- Manual layout creation for individual projects in `/home/karl_bot/workspaces/`


## Deployment Summary

The GUI Development VM has been successfully implemented with the following completed phases:

### Infrastructure Setup ✅
- `krout` VM created with 4GB RAM, 2 CPU cores, 100GB storage
- Network configuration with static IP lease (10.0.0.5)
- DNS resolution configured

### User Management ✅  
- `karl_bot` LDAP user and Kerberos principal created
- Home directory structure established at `/home/karl_bot`
- Authentication integration working

### Desktop Environment ✅
- Ubuntu Server enhanced with XFCE4 desktop via `dev_sandbox` role
- xrdp configured and optimized for macOS clients
- Remote desktop connectivity operational

### Development Tools ✅
- Enhanced `user_karl` role applied to `karl_bot` user
- Mail clients (aerc, msmtp) installed and configured
- Workspace structure created at `/home/karl_bot/workspaces/`
- Zellij session manager deployed and operational
- Dotfiles deployed via chezmoi

### Security Implementation ✅
- File share permissions implemented with proper access controls
- Firewall configured for secure RDP access (home network and VPN only)
- `karl_bot` user properly isolated with minimal privileges

### Automation & Backup ✅
- Ansible roles (`dev_sandbox`, enhanced `user_karl`) created and tested
- Comprehensive backup strategy via tarsnap with development artifact exclusions
- Full reproducibility via Ansible configuration management

## Validation Results

**VM Infrastructure** ✅:
- `krout` VM boots to XFCE4 desktop environment
- `karl_bot` user authentication via LDAP/Kerberos working
- Network connectivity confirmed (IP: `10.0.0.5`, DNS resolution functional)

**Remote Desktop Access** ✅:
- RDP connection successful from macOS clients
- Desktop session responsive over VPN connections
- GUI applications launch and function properly

**Development Environment** ✅:
- Dotfiles deployed and shell environment fully configured
- Git configuration and SSH keys operational
- Mail clients (aerc, msmtp) installed with base configuration
- Zellij auto-launch working on SSH login

**Security Controls** ✅:
- `karl_bot` can list directory names in `/var/fileshares/justdavis.com/`
- Access to other user directories properly denied
- Full read-write access to own directory (`/var/fileshares/justdavis.com/users/karl_bot/`)
- Access to group directories properly restricted

**System Integration** ✅:
- Ansible roles deploy successfully to `krout` VM
- Enhanced `user_karl` role maintains full backwards compatibility
- Backup system operational with intelligent exclusions
- System survives reboots with all services functioning


## Potential Future Enhancements

- **Automated Project Sessions**: Auto-generate zellij layouts by scanning workspace directories
- **Additional GUI Development Tools**: Support for more specialized development applications
- **Enhanced Mail Configuration**: Automated mail client credential setup
- **Performance Monitoring**: System resource monitoring and alerting
- **Additional Language Runtimes**: Support for additional development environments as needed

---

**Status**: ✅ Product Specification | 🚀 Fully Implemented