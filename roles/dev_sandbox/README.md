# dev_sandbox Role

This Ansible role configures a development sandbox VM with GUI desktop environment and RDP access. It
transforms a headless Ubuntu server into a desktop environment suitable for development work with remote
access capabilities.

## Features

- Installs ubuntu-desktop-minimal with XFCE4 desktop environment
- Configures xrdp for remote desktop access
- Sets XFCE4 as the default desktop session
- Optimizes RDP settings for macOS clients  
- Installs development and mail client tools
- Configures firewall for secure RDP access

## Requirements

- Ubuntu Server 24.04 (or compatible)
- Target user must exist in the system (created via LDAP/Kerberos integration)
- Network access to install packages

## Dependencies

This role depends on:
- `ufw` firewall being enabled
- User management via LDAP/Kerberos (handled by other roles)

## Variables

This role uses variables from `group_vars` for:
- `network_home_private_cidr`: Home network CIDR for firewall rules

## Usage

```yaml
- name: Configure Development Sandbox
  hosts: development_vms
  become: true
  tasks:
    - name: Apply dev_sandbox role
      ansible.builtin.import_role:
        name: dev_sandbox
```

## Services Configured

- **graphical.target**: Sets system to boot into graphical mode
- **xrdp**: RDP server for remote access

## Packages Installed

### Desktop Environment
- ubuntu-desktop-minimal (provides base Ubuntu desktop with Firefox)
- xfce4 (XFCE4 desktop environment)
- xfce4-goodies (additional XFCE utilities)
- xrdp (remote desktop server)
- chromium-browser (additional web browser)
- fonts-liberation, fonts-dejavu-core, fonts-noto-core (font support)
- fonts-jetbrains-mono (monospace font for development)

### Development Tools
- git (included in ubuntu-desktop-minimal)
- curl
- wget
- vim
- htop
- tree
- alacritty (terminal emulator)

### Mail Clients
- aerc (terminal mail client)
- msmtp (SMTP client)
- w3m (HTML renderer)

## Security Considerations

- RDP access is restricted to home network and VPN ranges (10.0.0.0/24 and 100.64.0.0/10)
- XFCE4 desktop session is set as system default
- All services use standard authentication mechanisms
- xrdp server includes performance and security optimizations

## Files Modified

- `/etc/xrdp/xrdp.ini`: RDP server performance and security optimization
- System alternatives: XFCE4 set as default session manager
- UFW rules: RDP port 3389 access controls for home network and Tailscale VPN
- Systemd: graphical.target enabled as system default

## Integration Notes

- Designed to work with `user_karl` role for complete user environment setup
- Requires LDAP/Kerberos authentication infrastructure for user management
- Compatible with existing backup strategies via tarsnap role