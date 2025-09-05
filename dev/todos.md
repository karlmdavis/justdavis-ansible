# To-Dos

This document is used to keep track of open ideas, tasks, etc. related to this project.


## Questions

(none at this time)


## Quick Tasks

- [X] Add an immich user for Erica.
- [X] Verify that the user-related roles aren't pushing out any secrets by default.
      This is needed to make it safe to run Claude Code and friends in a sandboxed VM/system.
- [X] Move the `.gitconfig` stuff to my dotfiles.
- [ ] Create a cheat sheet for keyboard shortcuts: Amethyst, zellij, nu line editor, Helix, VS Code.
- [X] Finish my PR #30 for the switch to chezmoi for dotfiles management.
    - [X] Run another test of my chezmoi changes (PR #30).
    - [X] Run `rcdn -v` on the server to unlink all the old rcm dotfiles.
    - [X] Uninstall rcm on the server.
    - [X] Apply the new setup to the server by running the playbook.


## Larger Tasks

- [ ] Make the project more resilient to tech debt.
    - [ ] Add a linter, with a pre-commit hook and workflow for it.
    - [ ] Add a workflow to run the end-to-end tests.
    - [ ] Setup renovatebot.
    - [ ] Setup GitHub's default security scanning. This might only be needed for immich and other hard-coded versions/installs.
    - [ ] Add a workflow to run the plays when things are merged to `main`.
    - [ ] Setup GitHub branch protections.
    - [ ] Add missing tests for `arr_suite` role.
    - [ ] Add missing tests for `jellyfin` role.
- [ ] Make the playbook idempotent and ensure it doesn't report changes when nothing has actually changed.
    - [ ] Update my Kerberos tasks to be idempotent and correctly report changes or the lack thereof.
- [ ] Consider moving most Vault secrets to 1Password.
    - Unclear if this will block the test/deploy workflows I'm considering. It might.
- [X] Update things to use the new version of my dotfiles, which moved from rcm to chezmoi.
- [X] Yoink out the "workstation apps" setup. Probably. Doubt it's useful anymore.
- [ ] Set disk encryption to unlock with USB key.
