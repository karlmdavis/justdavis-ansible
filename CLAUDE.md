# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an Ansible-based infrastructure management repository for the Davis family systems (`justdavis.com`). It automates the setup and configuration of home network servers and workstations, with support for testing changes in temporary cloud VMs.

### Project Goals
- Balance self-hosting fun projects and cost savings vs SaaS subscriptions
- Minimize home lab maintenance burden and time investment
- Experiment with LLM/Claude Code strategies for infrastructure management
- Maintain reliability through comprehensive testing

## Key Architecture Components

### Main Server: Eddings
The primary server (`eddings.justdavis.com`) hosts multiple services including:
- DNS server (BIND)
- Apache web server with SSL/TLS (Let's Encrypt)
- Kerberos authentication server 
- LDAP directory server
- File server (Samba)
- Mail server (Postfix/Dovecot)
- PostgreSQL database
- Media services (Jellyfin, *arr suite)
- Docker containerized applications
- ZFS storage pool management

### Role-Based Architecture
The codebase uses Ansible roles in `/roles/` for modular configuration:
- `base`: Common system configuration for all hosts
- `auth_client`: Authentication client setup (SSSD, LDAP)
- `eddings_networking`: Network configuration specific to main server
- `workstation_apps`: Desktop application setup
- `user_karl`: User account configuration
- Service-specific roles: `apache`, `dns_server`, `mail_server`, etc.

## Essential Commands

### Development Environment Setup
```bash
# Install Ansible with all dependencies via pipx
pipx install --include-deps ansible awscli black yamllint

# Install external Ansible roles
ansible-galaxy install -r install_roles.yml
```

### Running Playbooks

#### Production Deployment
```bash
# Deploy to all production systems
./ansible-playbook-wrapper site.yml

# Deploy to specific host only
./ansible-playbook-wrapper site.yml --limit=eddings.justdavis.com
```

#### Testing in AWS
```bash
# Full test cycle: provision, configure, test, teardown
./test/test.sh -c true -t true

# Configure only (skip teardown for debugging)
./test/test.sh -c true -t false

# Teardown only
./test/test.sh -c false -t true
```

### Environment Requirements
- AWS credentials configured in `~/.aws/credentials` with `[justdavis]` profile
- Vault password in `vault.password` file (contact karl@madriverdevelopment.com)
- SSH keys loaded for EC2 access when testing

## Configuration Management

### Inventory Structure
- `hosts`: Production inventory with eddings server and workstation groups
- `test/hosts-test`: Generated dynamically for AWS test instances
- `group_vars/all/`: Variables shared across all hosts
- `host_vars/`: Host-specific variable overrides

### Logging
All Ansible runs are automatically logged to `logs/` with timestamps. The `ansible-playbook-wrapper` script manages log rotation and naming.

### Testing Strategy
The repository supports a dual-environment approach:
1. **Production**: Direct deployment to physical home network systems
2. **Testing**: Temporary AWS EC2 instances with isolated domain testing

The AWS-based testing infrastructure is critical - it provisions ephemeral systems, runs playbooks, verifies results, and tears down resources. While the implementation may seem "janky," it has proven to be an incredible investment in reliability and confidence.

Use the `is_test` variable to conditionally skip network configurations incompatible with cloud environments.

## Development Workflow

### Branch Strategy
- Work directly on `master` branch (no pull requests or feature branches)
- Tests must pass before committing any changes
- Commit completed work immediately; avoid leaving untracked or staged changes
- Each commit should have a clear overall purpose/goal

### Cross-Platform Compatibility
- All scripts and playbooks must work on both macOS and Linux
- Avoid newer Bash features that may not be available on macOS
- Prevent "works on my machine" issues by avoiding local path dependencies
- Test on the actual deployment platform when possible

### AWS Usage
- AWS is used primarily for testing infrastructure, not production services
- Keep AWS costs minimal by ensuring test teardown works properly
- Test instances should be ephemeral and automatically cleaned up

## Important Notes

- Eddings server must be included in most playbook runs due to VPN client dependencies
- Network configuration is skipped during AWS testing via `when: not is_test` conditions
- External roles are installed to `roles_external/` and managed via `install_roles.yml`
- Vault-encrypted files contain sensitive data (passwords, keys, certificates)