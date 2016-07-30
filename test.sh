#/bin/bash

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
if [[ ! -e ./hosts-test ]]; then
  AWS_PROFILE=${AWS_PROFILE} ansible-playbook test-provision.yml --extra-vars "region=${AWS_REGION} ec2_key_name=${EC2_KEY_NAME}"
fi
ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook site.yml --inventory-file=hosts-test --extra-vars "is_test=true"
AWS_PROFILE=${AWS_PROFILE} ansible-playbook test-teardown.yml --inventory-file=hosts-test --extra-vars "region=${AWS_REGION} ec2_key_name=${EC2_KEY_NAME}"

