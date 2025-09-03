# To-Dos

This document is used to keep track of open ideas, tasks, etc. related to this project.

## Questions

(none at this time)

## Quick Tasks

- [ ] Add an immich user for Erica.
- [ ] Verify that the user-related roles aren't pushing out any secrets by default.
      This is needed to make it safe to run Claude Code and friends in a sandboxed VM/system.

## Larger Tasks

- [ ] Make the project more resilient to tech debt.
    - [ ] Add a linter, with a pre-commit hook and workflow for it.
    - [ ] Add a workflow to run the end-to-end tests.
    - [ ] Setup renovatebot.
    - [ ] Setup GitHub's default security scanning. This might only be needed for immich and other hard-coded versions/installs.
    - [ ] Add a workflow to run the plays when things are merged to `main`.
    - [ ] Setup GitHub branch protections.
- [ ] Make the playbook idempotent and ensure it doesn't report changes when nothing has actually changed.
    - [ ] Update my Kerberos tasks to be idempotent and correctly report changes or the lack thereof.
- [ ] Consider moving most Vault secrets to 1Password.
    - Unclear if this will block the test/deploy workflows I'm considering. It might.
- [ ] Update things to use the new version of my dotfiles, which moved from rcm to chezmoi.
- [ ] Yoink out the "workstation apps" setup. Probably. Doubt it's useful anymore.
