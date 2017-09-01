Davis Ansible Repo
----------------------------------

This repository contains the Ansible playbooks, roles, etc. used by the Davis family (`justdavis.com`) systems.

## Development Environment

In order to use and/or modify this repository, a number of tools need to be installed.

### Python

This project requires Python 2.7. It can be installed as follows:

    $ sudo apt-get install python

The following packages are also required by some of the Python modules that will be used:

    $ sudo apt-get install libpq-dev

### virtualenv

This project has some dependencies that have to be installed via `pip` (as opposed to `apt-get`). Accordingly, it's strongly recommended that you make use of a [Python virtual environment](http://docs.python-guide.org/en/latest/dev/virtualenvs/) to manage those dependencies.

If it isn't already installed, install the `virtualenv` package. On Ubuntu, this is best done via:

    $ sudo apt-get install python-virtualenv

Next, create a virtual environment for this project and install the project's dependencies into it:

    $ cd justdavis-ansible.git
    $ virtualenv -p /usr/bin/python2.7 venv
    $ source venv/bin/activate
    $ pip install -r requirements.txt

The `source` command above will need to be run every time you open a new terminal to work on this project.

Be sure to update the `requirements.frozen.txt` file after `pip install`ing a new dependency for this project:

    $ pip freeze > requirements.frozen.txt

### Ansible Roles

Run the following command to download and install the roles required by this project into `~/.ansible/roles/`:

    $ ansible-galaxy install -r install_roles.yml

### AWS Credentials

Per <http://blogs.aws.amazon.com/security/post/Tx3D6U6WSFGOK2H/A-New-and-Standardized-Way-to-Manage-Credentials-in-the-AWS-SDKs>, create `~/.aws/credentials` and populate it with your AWS access key and secret (obtained from IAM):

    [justdavis]
    # These AWS keys are for the john.smith@justdavis.com account.
    aws_access_key_id = secret
    aws_secret_access_key = supersecret

Ensure that the EC2 key to be used is loaded into SSH Agent:

    $ ssh-add foo.pem

## Configuring Hosts

### Production

Running the following command will run the `site.yml` Ansible playbook against the hosts specified in the `hosts` file:

    $ ansible-playbook site.yml

### Test

When testing this playbook, running it against the actual production hosts is clearly not a great idea. Instead, the `test-provision.yml` and `test-teardown.yml` playbooks can be used to create tests host in AWS before the tests and then terminate them afterwards, respectively. Overall, the Ansible configs can be tested, as follows:

    $ AWS_PROFILE=justdavis ansible-playbook test-provision.yml
    $ ansible-playbook site.yml --inventory-file=hosts-test
    $ AWS_PROFILE=justdavis ansible-playbook test-teardown.yml

## Running Ad-Hoc Commands

Ansible can also be used to run ad-hoc commands against all of the systems specified in the `hosts` file, as follows:

    $ ansible all -m shell -a 'echo $TERM'

