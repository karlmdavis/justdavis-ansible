# Pipx to UV Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate Python dependency management from pipx to uv with lockfile-based reproducibility.

**Architecture:** Project-based approach using pyproject.toml for dependency declaration and uv.lock for version locking. All tools accessed via `uv run` or activated venv. Wrapper scripts updated to use uv internally.

**Tech Stack:** uv (Python package manager), pyproject.toml (dependency configuration), uv.lock (lockfile)

---

## Task 1: Create pyproject.toml Configuration

**Files:**
- Create: `pyproject.toml`

**Step 1: Create pyproject.toml with exact current versions**

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

**Step 2: Verify file syntax**

Run: `cat pyproject.toml`
Expected: File displays without errors

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "Add pyproject.toml with current dependency versions

Defines Python tooling dependencies for uv-based management.
Pins exact versions from current pipx installation to ensure
no version changes during migration.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: Generate Lockfile and Virtual Environment

**Files:**
- Create: `uv.lock` (generated)
- Create: `.venv/` (generated, gitignored)

**Step 1: Run uv sync to generate lockfile**

Run: `uv sync`
Expected: Output shows "Resolved N packages" and "Installed M packages"

**Step 2: Verify lockfile created**

Run: `ls -lh uv.lock`
Expected: File exists with non-zero size

**Step 3: Verify venv created**

Run: `ls -d .venv`
Expected: Directory exists

**Step 4: Test tool availability**

Run: `uv run ansible --version`
Expected: Output shows "ansible [core 2.18.2]" (or similar version for ansible 11.8.0)

Run: `uv run black --version`
Expected: Output shows "black, 25.1.0"

**Step 5: Commit lockfile**

```bash
git add uv.lock
git commit -m "Generate uv.lock with exact dependency tree

Lockfile ensures reproducible environments across all systems.
Includes cryptographic hashes for integrity verification.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Update ansible-playbook-wrapper Script

**Files:**
- Modify: `ansible-playbook-wrapper:46`

**Step 1: Read current wrapper implementation**

Run: `sed -n '44,48p' ansible-playbook-wrapper`
Expected: Shows lines around the ansible-playbook invocation

**Step 2: Update line 46 to use uv run**

Change line 46 from:
```bash
  ansible-playbook "$@"
```

To:
```bash
  uv run ansible-playbook "$@"
```

**Step 3: Verify change**

Run: `sed -n '44,48p' ansible-playbook-wrapper`
Expected: Line 46 shows `uv run ansible-playbook "$@"`

**Step 4: Test wrapper functionality**

Run: `./ansible-playbook-wrapper --version`
Expected: Output shows ansible version (via uv run)

**Step 5: Commit**

```bash
git add ansible-playbook-wrapper
git commit -m "Update ansible-playbook-wrapper to use uv run

Wrapper now invokes ansible-playbook via uv run, ensuring it uses
the project's locked dependencies from .venv.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Update test.sh to Use Wrapper Script

**Files:**
- Modify: `test/test.sh:100,108,116`

**Step 1: Update provision playbook call (line 100)**

Change line 100 from:
```bash
  ansible-playbook test/provision.yml --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars "{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
```

To:
```bash
  ./ansible-playbook-wrapper test/provision.yml --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars "{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
```

**Step 2: Update site.yml playbook call (line 108)**

Change line 108 from:
```bash
  ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars "{is_test: true, domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
```

To:
```bash
  ./ansible-playbook-wrapper site.yml --inventory-file=test/hosts-test --extra-vars "{is_test: true, domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
```

**Step 3: Update teardown playbook call (line 116)**

Change line 116 from:
```bash
  ansible-playbook test/teardown.yml --inventory-file=test/hosts-test --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars "{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
```

To:
```bash
  ./ansible-playbook-wrapper test/teardown.yml --inventory-file=test/hosts-test --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars "{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
```

**Step 4: Also update the echo statements that precede each ansible-playbook call**

Update line 99 from:
```bash
  echo "$ ansible-playbook test/provision.yml --extra-vars \"@${AWS_PROVISIONING_VARS_FILE}\" --extra-vars \"{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}\" ${verboseArg}" | tee -a "${originalLog}"
```

To:
```bash
  echo "$ ./ansible-playbook-wrapper test/provision.yml --extra-vars \"@${AWS_PROVISIONING_VARS_FILE}\" --extra-vars \"{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}\" ${verboseArg}" | tee -a "${originalLog}"
```

Update line 107 from:
```bash
  echo "$ ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars \"{is_test: true, domain_test_prefix: ${DOMAIN_TEST_PREFIX}}\" ${verboseArg}" | tee -a "${originalLog}"
```

To:
```bash
  echo "$ ./ansible-playbook-wrapper site.yml --inventory-file=test/hosts-test --extra-vars \"{is_test: true, domain_test_prefix: ${DOMAIN_TEST_PREFIX}}\" ${verboseArg}" | tee -a "${originalLog}"
```

Update line 115 from:
```bash
  echo "$ ansible-playbook test/teardown.yml --inventory-file=test/hosts-test --extra-vars \"@${AWS_PROVISIONING_VARS_FILE}\" --extra-vars \"{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}\" ${verboseArg}" | tee -a "${originalLog}"
```

To:
```bash
  echo "$ ./ansible-playbook-wrapper test/teardown.yml --inventory-file=test/hosts-test --extra-vars \"@${AWS_PROVISIONING_VARS_FILE}\" --extra-vars \"{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}\" ${verboseArg}" | tee -a "${originalLog}"
```

**Step 5: Verify changes**

Run: `grep -n "ansible-playbook-wrapper" test/test.sh`
Expected: Shows lines 99, 100, 107, 108, 115, 116 with wrapper references

**Step 6: Commit**

```bash
git add test/test.sh
git commit -m "Update test.sh to use ansible-playbook-wrapper

All ansible-playbook invocations now use the wrapper script,
ensuring consistent uv-based execution for both production
and test workflows.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Update README.md Documentation

**Files:**
- Modify: `README.md:34-40` (Development Environment Setup section)

**Step 1: Read current README section**

Run: `sed -n '30,50p' README.md`
Expected: Shows current development environment setup instructions

**Step 2: Replace pipx instructions with uv instructions**

Find the section that currently reads:
```markdown
## Development Environment Setup

To work with this repository, you'll need to install Ansible and related tools:

```bash
pipx install --include-deps ansible passlib awscli black yamllint
ansible-galaxy install -r install_roles.yml
```
```

Replace with:
```markdown
## Development Environment Setup

To work with this repository, you'll need to install uv and sync Python dependencies:

```bash
# Install uv (one-time setup)
brew install uv  # macOS
# For Linux: curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync all Python dependencies from lockfile
uv sync

# Install external Ansible roles
uv run ansible-galaxy install -r install_roles.yml
```
```

**Step 3: Verify changes**

Run: `sed -n '30,50p' README.md`
Expected: Shows new uv-based instructions

**Step 4: Commit**

```bash
git add README.md
git commit -m "Update README with uv-based setup instructions

Replace pipx installation instructions with uv workflow.
Includes platform-specific uv installation (homebrew for macOS,
curl script for Linux) and uv sync for dependency management.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md:41-82` (Remove dev setup, update environment requirements)

**Step 1: Remove Development Environment Setup section**

Delete lines 41-48 (the entire "### Development Environment Setup" section including the code block).

**Step 2: Update Environment Requirements section**

Find the section at lines ~79-82 that reads:
```markdown
### Environment Requirements
- AWS credentials configured in `~/.aws/credentials` with `[justdavis]` profile
- Vault password in `vault.password` file (contact karl@madriverdevelopment.com)
- SSH keys loaded for EC2 access when testing
```

Replace with:
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

**Step 3: Verify changes**

Run: `grep -A 8 "### Environment Requirements" CLAUDE.md`
Expected: Shows updated environment requirements with uv reference

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md for uv-based dependency management

Remove detailed dev setup (belongs in README).
Add environment requirements note about uv and dependency files.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Update .gitignore

**Files:**
- Modify: `.gitignore`

**Step 1: Check if .venv is already ignored**

Run: `grep -n "\.venv" .gitignore || echo "Not found"`
Expected: Either shows existing line or "Not found"

**Step 2: Add .venv to .gitignore**

Add to .gitignore (if not already present):
```
# Python virtual environment (uv-managed)
/.venv/
```

**Step 3: Verify addition**

Run: `grep -A 1 "Python virtual environment" .gitignore`
Expected: Shows the new comment and /.venv/ line

**Step 4: Commit**

```bash
git add .gitignore
git commit -m "Add .venv to .gitignore for uv virtual environment

Prevents uv-managed virtual environment from being tracked by git.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: Verification Testing

**Files:**
- No file changes (testing only)

**Step 1: Verify all tools are accessible**

Run: `uv run ansible --version`
Expected: Shows ansible version

Run: `uv run black --version`
Expected: Shows "black, 25.1.0"

Run: `uv run aws --version`
Expected: Shows awscli version

Run: `uv run yamllint --version`
Expected: Shows yamllint version

**Step 2: Run test suite**

Run: `./test/test.sh -c true -t true`
Expected: Tests should be able to:
- Provision AWS instances
- Run ansible playbooks via wrapper
- May fail for unrelated reasons, but uv tooling should work

**Step 3: Document test results**

Note: If tests fail, document the failure type:
- Tooling failures (uv/ansible not found) = MUST FIX
- Unrelated play failures = ACCEPTABLE for migration verification

**Step 4: If tests fail due to tooling issues**

Investigate and fix before proceeding. If tests fail for unrelated reasons, verification is complete.

---

## Task 9: Final Commit and Summary

**Files:**
- None (summary only)

**Step 1: Verify all changes committed**

Run: `git status`
Expected: "nothing to commit, working tree clean"

**Step 2: Review commit history**

Run: `git log --oneline origin/main..HEAD`
Expected: Shows 8 commits (pyproject.toml, lockfile, wrapper, test.sh, README, CLAUDE.md, gitignore, verification results if any fixes were needed)

**Step 3: Verify branch is ready for PR**

Run: `git diff origin/main...HEAD --stat`
Expected: Shows all modified files with reasonable line changes

**Step 4: Document completion**

Note: Migration complete. Ready to create pull request. Tests verified that uv-managed tools execute correctly.

---

## Post-Implementation Notes

After this plan is executed:
1. Create pull request from `migrate-pipx-to-uv` branch
2. PR should include test results showing tools work correctly
3. After PR merge, pipx installation can be removed via: `pipx uninstall ansible`
4. Team members should run `uv sync` after pulling changes

## Rollback Plan

If issues discovered:
```bash
git revert HEAD~8..HEAD  # Revert all migration commits
pipx install --include-deps ansible passlib awscli black yamllint
```
