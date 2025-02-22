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

# Calculate the directory that this script is in.
scriptDirectory="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default values
configure=true
teardown=true
verbosity=0

# Use getopts (short options only)
while getopts "c:t:v:" opt; do
  case $opt in
    c) configure="$OPTARG" ;;
    t) teardown="$OPTARG" ;;
    v) verbosity="$OPTARG" ;;
    \?) echo "Invalid option: -$OPTARG" >&2; exit 1 ;;
  esac
done

# Shift away processed options
shift $((OPTIND - 1))

verboseArg=""
if [[ "${verbosity}" -eq 1 ]]; then verboseArg="-v"; fi
if [[ "${verbosity}" -eq 2 ]]; then verboseArg="-vv"; fi
if [[ "${verbosity}" -eq 3 ]]; then verboseArg="-vvv"; fi
if [[ "${verbosity}" -eq 4 ]]; then verboseArg="-vvvv"; fi

# Set default environment variables. These can be overridden on the command line.
if [[ -z "$AWS_PROFILE" ]]; then
  export AWS_PROFILE=justdavis
fi
if [[ -z "$AWS_PROVISIONING_VARS_FILE" ]]; then
  AWS_PROVISIONING_VARS_FILE="${scriptDirectory}/vars_aws_provisioning_karlmdavis.yml"
fi

# Doesn't work well with throwaway EC2 instances.
export ANSIBLE_HOST_KEY_CHECKING=False

# Grab a timestamp now, which will be used later for the log.
timestamp=$(date +"%Y-%m-%dT%H:%M:%S%z")

# The `ansible.cfg` file tells Ansible to log to this file automatically.
originalLog="${scriptDirectory}/../logs/ansible.log"

# Create the file if it doesn't exist yet.
if [ ! -f "${originalLog}" ]; then
  touch "${originalLog}"
fi

# Call this function to rename the `ansible.log` file to include a timesatamp.
timestampLog() {
  # Timestamp the log file.
  timestampedLog="${scriptDirectory}/../logs/ansible-test-${timestamp}.log"
  mv "${originalLog}" "${timestampedLog}"
  echo "Log written to: ${timestampedLog}"
}

# This function will be called if the script is interrupted by a `ctrl+c`.
interrupt() {
  timestampLog
  exit 130
}
trap interrupt SIGINT

# Handle errors manually, so that we can still manage the logs if things fail.
set +e
errorCode=0

cd "${scriptDirectory}/.."

# Generate a random domain prefix for use in tests, to play nice with Let's
# Encrypt's rate limits.
if [[ -f "${scriptDirectory}/test.env" ]]; then
  source "${scriptDirectory}/test.env"
else
  DOMAIN_TEST_PREFIX="tests$[RANDOM%100].tests."
  echo "DOMAIN_TEST_PREFIX=\"${DOMAIN_TEST_PREFIX}\"" > "${scriptDirectory}/test.env"
fi

# If there is no test inventory, provision the test systems.
if [[ ! -e ./test/hosts-test ]]; then
  echo "$ ansible-playbook test/provision.yml --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars \""{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}"\" ${verboseArg}" | tee -a "${originalLog}"
  ansible-playbook test/provision.yml --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars "{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
  errorCode=$?
  echo -e "\n" | tee -a "${originalLog}"
fi

# Run our Ansible plays against the test systems.
if [ $errorCode -eq 0 ] && [ "${configure}" = true ]; then
  echo "$ ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars \""{is_test: true, domain_test_prefix: ${DOMAIN_TEST_PREFIX}}"\" ${verboseArg}" | tee -a "${originalLog}"
  ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars "{is_test: true, domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
  errorCode=$?
  echo -e "\n" | tee -a "${originalLog}"
fi

# Tear down the test systems.
if [ $errorCode -eq 0 ] && [ "${teardown}" = true ]; then
  echo "$ ansible-playbook test/teardown.yml --inventory-file=test/hosts-test --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars \""{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}"\" ${verboseArg}" | tee -a "${originalLog}"
  ansible-playbook test/teardown.yml --inventory-file=test/hosts-test --extra-vars "@${AWS_PROVISIONING_VARS_FILE}" --extra-vars "{domain_test_prefix: ${DOMAIN_TEST_PREFIX}}" ${verboseArg}
  errorCode=$?
  echo -e "\n" | tee -a "${originalLog}"

  # If everything tore down okay, remove the test env file.
  if [ $errorCode -eq 0 ]; then
    rm "${scriptDirectory}/test.env"
  fi
fi

timestampLog
exit ${errorCode}
