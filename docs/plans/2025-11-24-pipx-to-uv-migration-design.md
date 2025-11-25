# Migration from pipx to uv for Python Dependency Management

**Date:** 2025-11-24
**Status:** Approved Design

## Overview

This design document outlines the migration from pipx to uv for managing Python dependencies in the justdavis-ansible repository. The primary motivation is to achieve better dependency management with lockfiles for reproducible environments.

## Goals and Constraints

### Primary Goal
- Better dependency management with lockfiles for reproducibility across development, testing, and production environments

### Constraints
- Maintain exact same versions of all Python tools currently installed (no version changes during migration)
- Single unified approach that works on both macOS and Linux
- All existing tests must continue to work (provision systems and run plays successfully)

### Success Criteria
- Same versions installed with lockfile ensuring reproducibility
- Documentation updated (README and CLAUDE.md)
- Tests pass (able to provision and run plays, even if some plays fail for unrelated reasons)
- Clean removal of pipx possible without breaking anything

## Current State

The project currently uses pipx to install Python tooling with injected dependencies:

```bash
pipx install --include-deps ansible passlib awscli black yamllint
```

Current versions:
- ansible==11.8.0
- awscli==1.37.16
- black==25.1.0
- passlib==1.7.4
- yamllint==1.35.1
- Python 3.13.2

## Architectural Approach

### Selected Approach: Project-Based with pyproject.toml

Use uv's project-based dependency management with `pyproject.toml` and `uv.lock` for maximum dependency resolution capabilities and reproducibility.

**Why this approach:**
- Best lockfile support with cryptographic hashes
- Proper dependency management for transitive dependencies
- Single source of truth for all Python dependencies
- Leverages uv's strengths in dependency resolution

**Alternatives considered:**
1. **uv tool install (pipx-like)**: Closer to current behavior but less sophisticated dependency management
2. **Simple requirements.txt with uv pip**: Straightforward but misses lockfile benefits

## Design Details

### 1. Project Structure and Configuration

**New files:**
- `pyproject.toml` - Project configuration with pinned dependencies at root of repository
- `uv.lock` - Generated lockfile with exact versions and hashes (committed to git)
- `.venv/` - Virtual environment directory (gitignored)

**pyproject.toml structure:**
```toml
[project]
name = "justdavis-ansible-tools"
version = "1.0.0"
description = "Python tooling for justdavis-ansible infrastructure management"
requires-python = ">=3.13"
dependencies = [
    "ansible==11.8.0",
    "awscli==1.37.16",
    "black==25.1.0",
    "passlib==1.7.4",
    "yamllint==1.35.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Lockfile benefits:**
- Exact versions of all packages including transitive dependencies
- Cryptographic hashes for integrity verification
- Platform-specific markers for cross-platform compatibility
- Committed to git for reproducible environments across all systems

### 2. Installation Workflow

**Initial setup:**
```bash
# Install uv (one-time)
brew install uv  # macOS
# For Linux: curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies from lockfile
cd justdavis-ansible.git
uv sync
```

**Updating dependencies:**
```bash
# After changing pyproject.toml versions:
uv sync

# To upgrade all dependencies within version constraints:
uv lock --upgrade
uv sync
```

**Key advantage:** Single `uv sync` command replaces complex pipx invocation with multiple injected packages.

### 3. Command Execution

**Selected approach:** Use `uv run` prefix for all Python tool invocations.

**Wrapper script integration:**
The `ansible-playbook-wrapper` script will be updated to use `uv run` internally:

```bash
# Line 46 changes from:
ansible-playbook "$@"

# To:
uv run ansible-playbook "$@"
```

**Test script integration:**
The `test/test.sh` script will use the wrapper instead of direct ansible-playbook calls:

```bash
# Changes from:
ansible-playbook test/provision.yml ...

# To:
./ansible-playbook-wrapper test/provision.yml ...
```

This provides a single source of truth for how ansible-playbook is invoked, ensuring both production and test workflows automatically use the uv-managed environment.

**Benefits of uv run:**
- No need to activate/deactivate virtual environments
- Always uses project's locked dependencies
- Works consistently across shells and sessions
- Clear indication that tools are managed by uv

### 4. Documentation Updates

#### README.md Changes

**Section:** Development Environment Setup

**New content:**
```bash
# Install uv (one-time setup)
brew install uv  # macOS
# For Linux: curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync all Python dependencies from lockfile
uv sync

# Install external Ansible roles
uv run ansible-galaxy install -r install_roles.yml
```

#### CLAUDE.md Changes

**Change 1:** Remove the "Development Environment Setup" section entirely (lines 41-48)

**Change 2:** Update "Environment Requirements" section:

```markdown
### Environment Requirements
- Python dependencies managed via `uv` - if you encounter errors about `ansible`, `black`, or related
  commands not being found, tell the user to run the development environment setup steps from the README
- AWS credentials configured in `~/.aws/credentials` with `[justdavis]` profile
- Vault password in `vault.password` file (contact karl@madriverdevelopment.com)
- SSH keys loaded for EC2 access when testing
- Dependency lockfile (`uv.lock`) and configuration (`pyproject.toml`) committed to git; virtual
  environment (`.venv/`) is gitignored
```

#### .gitignore Updates

Add:
```
# Python virtual environment (uv-managed)
/.venv/
```

### 5. Migration Path

**Step 1: Capture current state**
- ✓ Export exact versions from pipx venv (already documented above)

**Step 2: Create uv configuration**
- Create `pyproject.toml` with pinned versions and Python 3.13+ requirement
- Run `uv sync` to generate initial `uv.lock` lockfile
- Verify `.venv/` is created with all expected tools

**Step 3: Update scripts**
- Modify `ansible-playbook-wrapper` to use `uv run ansible-playbook`
- Modify `test/test.sh` to use `./ansible-playbook-wrapper` instead of direct `ansible-playbook` calls

**Step 4: Update documentation**
- Update README.md with new uv-based setup instructions
- Update CLAUDE.md per changes specified above

**Step 5: Update .gitignore**
- Add `/.venv/` to .gitignore

**Step 6: Verification**
- Run `./test/test.sh` to verify:
  - Tests can provision AWS instances
  - Playbooks can run via wrapper using `uv run`
  - Success = tools execute correctly (even if plays fail for unrelated reasons)
- Verify commands available: `uv run ansible --version`, `uv run black --version`, etc.

**Step 7: Commit and document**
- Commit `pyproject.toml`, `uv.lock`, updated scripts, and documentation
- Commit message: "Migrate from pipx to uv for Python dependency management"

**Post-migration:**
- pipx installation can be removed after verification (not required for migration PR)

## Platform Compatibility

The solution uses a single unified approach for both macOS and Linux:
- Same `pyproject.toml` and `uv.lock` files
- Installation differs only in uv installation method (homebrew vs curl script)
- uv handles platform-specific package variations automatically via lockfile hashes
- Both platforms get compatible dependency versions guaranteed by the lockfile

## Testing Strategy

Verification is considered successful when:
1. AWS test instances can be provisioned
2. Ansible playbooks can execute via the wrapper (using `uv run`)
3. All uv-managed tools are accessible and functional

Note: Some plays may fail for unrelated reasons - the goal is to verify the uv migration works, not to fix unrelated test failures.

## Rollback Plan

If issues are discovered after migration:
1. Revert the branch/commit
2. Re-run `pipx install --include-deps ansible passlib awscli black yamllint`
3. Original workflow resumes immediately

The migration is atomic (single PR) and easily reversible via git.

## Future Considerations

After successful migration, consider:
- Relaxing version pins (e.g., `ansible>=11.8,<12`) to allow patch updates while maintaining lockfile
- Adding more development tools to pyproject.toml as project needs evolve
- Leveraging uv's script capabilities for project-specific automation
- Using `uv tool install` for truly global tools not tied to this project
