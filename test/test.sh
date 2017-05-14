#/bin/bash

##
# Provisions temporary systems in EC2, runs the `site.yml` Ansible playbook
# against them, tests the results, and then—if everything worked as expected—
# removes the temporary hosts. However, if anything fails, the temporary hosts
# will be left in place (and recorded in the `test/hosts-test` file) and future
# runs of this script will re-use those hosts.
# 
# Usage:
# 
#     $ cd justdavis-ansible.git/
#     $ ./test/test.sh
##

set -e

# Set default option values. These can be overridden on the command line.
if [[ -z "$AWS_PROFILE" ]]; then
  AWS_PROFILE=justdavis
fi
if [[ -z "$AWS_REGION" ]]; then
  AWS_REGION=us-east-1
fi
if [[ -z "$EC2_KEY_NAME" ]]; then
  EC2_KEY_NAME=karlmdavis-personal
fi

# If there is no test inventory, provision the test systems and create it.
if [[ ! -e ./test/hosts-test ]]; then
  AWS_PROFILE=${AWS_PROFILE} ansible-playbook test/test-provision.yml --extra-vars "region=${AWS_REGION} ec2_key_name=${EC2_KEY_NAME}"
fi
ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars "is_test=true"
#AWS_PROFILE=${AWS_PROFILE} ansible-playbook test/test-teardown.yml --inventory-file=test/hosts-test --extra-vars "region=${AWS_REGION} ec2_key_name=${EC2_KEY_NAME}"

