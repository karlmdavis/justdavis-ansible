# `user_karl` Role

This Ansible role configures user accounts with development tools, SSH access, and dotfiles management.
The actual username it gets applied for is configurable to allow it to be used for aliases, e.g. agents.

## Features

- Creates user home directories via LDAP integration
- Configures SSH access with authorized keys
- Installs Homebrew package manager for Linux development tools
- Sets up chezmoi for dotfiles management and deployment
- Configures passwordless sudo access for development convenience
- Multi-user support with variable-driven configuration

## Dotfiles Integration

The role integrates with the `karlmdavis` dotfiles repository:

- Automatically initializes chezmoi with "personal" system type.
- Applies full dotfile configuration including zellij, bash, vim, etc.
- Works seamlessly across multiple users and systems.

## Requirements

- LDAP/Kerberos integration (user must exist in directory).
- Internet access for Homebrew and chezmoi installation.
- Target user's dotfiles repository (currently: `karlmdavis`).

## Variables

### Configuration Variables (Override in host_vars)

- `user_karl_target_user`: Target username (default: `karl`).
- `user_karl_ssh_keys`: SSH public keys (default: `ssh_keys_karl_public`).

Note: Home directory is automatically built as `/home/<username>`.

## Usage

### Default Usage (`karl` user)

```yaml
- name: Configure karl user
  hosts: all
  tasks:
    - name: Apply user_karl role
      ansible.builtin.import_role:
        name: user_karl
```

### Alter-Ego Usage (`karl_bot` user)

In `host_vars/hostname/main.yml`:

```yaml
# Configure role for `karl_bot` user.
user_karl_target_user: karl_bot
user_karl_ssh_keys: "{{ ssh_keys_karl_public }}"
```

## Role Tasks

The role performs these configuration tasks:

1. **Home Directory**: Creates `/home/<username>` if it doesn't exist
2. **SSH Setup**: Creates `.ssh` directory with proper permissions and authorized keys
3. **Sudo Access**: Configures passwordless sudo via `/etc/sudoers.d/` and adds user to sudo group
4. **Homebrew Installation**: Installs Homebrew package manager for Linux
5. **Shell Configuration**: Configures Homebrew path in system-wide `/etc/profile.d/homebrew.sh`
6. **Chezmoi Setup**: Installs chezmoi via Homebrew and initializes with karlmdavis dotfiles
7. **Dotfiles Deployment**: Applies full dotfiles configuration including zellij, bash, vim, etc.

## Security Considerations

- Passwordless sudo is configured for development convenience
- SSH keys are properly secured with restrictive permissions
- Home directory and SSH directory use appropriate ownership and modes
- All configurations are validated where possible (e.g., sudoers syntax checking)

## Dependencies

- Target user must exist in LDAP directory
- Internet access required for Homebrew installation and dotfiles repository access
- Sufficient disk space for development tools and cached packages
