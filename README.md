Davis Ansible Repo
----------------------------------

This repository contains the Ansible playbooks, roles, etc. used by the Davis family (`justdavis.com`) systems. These do two super cool things:

1. Automatically set up my home network, including the servers and workstations I have there.
2. Allow me to test changes to all of that in temporary cloud VMs.

## Development Environment

In order to use and/or modify this repository, a number of tools need to be installed.

### Python

This project requires Python 3. On Ubuntu/Debian 18.04+ systems, it can be installed as follows:

    $ sudo apt install python3 python-virtualenv

The following packages will also be required by `pip` (see below) to build/install some of the required Python modules:

    $ sudo apt install build-essential python3-dev libpq-dev

### virtualenv

This project has some dependencies that have to be installed via `pip` (as opposed to `apt`). Accordingly, it's strongly recommended that you make use of a [Python virtual environment](http://docs.python-guide.org/en/latest/dev/virtualenvs/) to manage those dependencies.

Create a virtual environment for this project and install the project's dependencies into it:

    $ cd justdavis-ansible.git
    $ virtualenv -p /usr/bin/python3 venv
    $ source venv/bin/activate
    $ pip install -r requirements.txt

The `source` command above will need to be run every time you open a new terminal to work on this project (after `cd`ing to the directory).

Be sure to update the `requirements.frozen.txt` file after `pip install`ing a new dependency for this project, just to help keep track of specific working dependency versions:

    $ pip freeze > requirements.frozen.txt

### Ansible Roles

This project also makes use of a number of Ansible roles, which are reusable pieces of Ansible logic. Run the following command to download and install the roles required by this project into the project-specific `roles_external` directory:

    $ ansible-galaxy install -r install_roles.yml

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

    $ ./test/test.sh --configure=true --teardown=true

### Production

Running the following command will run the `site.yml` Ansible playbook against the production systems specified in the `hosts` file:

    $ ./ansible-playbook-wrapper site.yml

## Running Ad-Hoc Commands

Ansible can also be used to run ad-hoc commands against all of the production systems specified in the `hosts` file, as follows:

    $ ansible all -m shell -a 'echo $TERM'
