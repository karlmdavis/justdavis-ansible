Davis Ansible Repo
----------------------------------

This repository contains the Ansible playbooks, roles, etc. used by the Davis family (`justdavis.com`) systems. These do two super cool things:

1. Automatically set up my home network, including the servers and workstations I have there.
2. Allow me to test changes to all of that in temporary cloud VMs.

## Development Environment

In order to use and/or modify this repository, a number of tools need to be installed.

### Python Dependencies

This project requires Python 3 and uses `uv` to manage Ansible and related tools:

```bash
# Install uv (one-time setup)
brew install uv  # macOS
# For Linux: curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync all Python dependencies from lockfile
uv sync

# Install external Ansible roles
uv run ansible-galaxy install -r install_roles.yml
```

This synchronizes all project dependencies from the lockfile, including Ansible, AWS CLI, linting tools, and related utilities.

The following system packages will also be required by some of the Ansible tasks:

    $ sudo apt install build-essential python3-dev libpq-dev swaks

### Ansible Roles

This project uses external Ansible roles (reusable pieces of Ansible logic). The `uv run ansible-galaxy install` command above downloads these roles into the project-specific `roles_external` directory.

### AWS Credentials

Per <http://blogs.aws.amazon.com/security/post/Tx3D6U6WSFGOK2H/A-New-and-Standardized-Way-to-Manage-Credentials-in-the-AWS-SDKs>, create `~/.aws/credentials` and populate it with your AWS access key and secret (obtained from IAM):

    [justdavis]
    # These AWS keys are for the john.smith@justdavis.com account.
    aws_access_key_id = secret
    aws_secret_access_key = supersecret

Ensure that the EC2 key to be used is loaded into SSH Agent:

    $ ssh-add foo.pem

## Running the Ansible Plays

### Test

When testing this playbook, running it against the actual production systems is clearly not a great idea. Instead, the `./test/test.sh` script can be used to 1) provision some temporary AWS EC2 VMs to test against, 2) run the plays against those EC2 instances, and finally 3) tear down the EC2 instances afterwards. See here:

    $ ./test/test.sh -c true -t true

Set `-c` to `false` to skip the configuration playbook, e.g. if you just want to teardown the test instances ASAP. Set `-t` to `false` to skip the teardown phase, if you want the test instances to stick around after a successful configuration run.

To SSH into a test instance for debugging:

In Bash/ZSH:

    $ ssh ubuntu@$(sed -n 's/^eddings.justdavis.com *ansible_host=\([^ ]*\).*/\1/p' test/hosts-test)

In nu shell:

    $ ssh $"ubuntu@((open test/hosts-test | lines | where {|l| $l | str starts-with 'eddings.justdavis.com'} | parse --regex '^.*?\bansible_host=(?P<h>\S+)' | get h | first))"

### Production

#### Bootstrapping Hosts

Before the plays can be run against a new/clean host,
  some initial bootstrapping is required,
  as documented in [Bootstrap Ansible Environment](./bootstrap/README.md).

#### Running the Plays

Running the following command will run the `site.yml` Ansible playbook against the production systems specified in the `hosts` file:

    $ ./ansible-playbook-wrapper site.yml

Or, if you'd like to run against only a single host, you can use the `--limit` option, e.g.:

    $ ./ansible-playbook-wrapper site.yml --limit=brust.karlanderica.justdavis.com

(Note that some parts of the playbook are known to fail unless `eddings` is included in the run. Specifically, the VPN client will fail to configure.)

#### Running Ad-Hoc Commands

Ansible can also be used to run ad-hoc commands against all of the production systems specified in the `hosts` file, as follows:

    $ ansible all -m shell -a 'echo $TERM'
