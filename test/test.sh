#!/bin/bash

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

# Function to detect SSH public key from standard locations.
detect_ssh_key() {
  # Check for environment variable override first.
  if [[ -n "$SSH_KEY_PATH" ]]; then
    if [[ -f "$SSH_KEY_PATH" ]]; then
      echo "$SSH_KEY_PATH"
      return 0
    else
      echo "ERROR: SSH_KEY_PATH is set but file does not exist: $SSH_KEY_PATH" >&2
      exit 1
    fi
  fi

  # Try standard locations in order of preference.
  for key in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_ecdsa.pub" "$HOME/.ssh/id_rsa.pub"; do
    if [[ -f "$key" ]]; then
      echo "$key"
      return 0
    fi
  done

  # No key found.
  echo "ERROR: No SSH public key found." >&2
  echo "Searched: $HOME/.ssh/id_ed25519.pub, $HOME/.ssh/id_ecdsa.pub, $HOME/.ssh/id_rsa.pub" >&2
  echo "Create a key with: ssh-keygen -t ed25519" >&2
  echo "Or set SSH_KEY_PATH=/path/to/your/key.pub" >&2
  exit 1
}

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

# Load or create user configuration (durable).
if [[ -f "${scriptDirectory}/user-config.env" ]]; then
  echo "Loading user configuration..."
  source "${scriptDirectory}/user-config.env"
else
  echo "Initializing user configuration..."

  # Detect SSH public key.
  SSH_KEY_PATH=$(detect_ssh_key)
  echo "✓ SSH key detected: ${SSH_KEY_PATH}"

  # Detect username.
  USERNAME="${USERNAME:-$(whoami)}"
  echo "✓ Username: ${USERNAME}"

  # Set AWS defaults (can be overridden via environment variables).
  AWS_PROFILE="${AWS_PROFILE:-justdavis}"
  AWS_REGION="${AWS_REGION:-us-east-1}"
  AWS_VPC_SUBNET="${AWS_VPC_SUBNET:-subnet-9a1dfeb0}"
  echo "✓ AWS settings: ${AWS_REGION}, ${AWS_VPC_SUBNET}"

  # Write configuration to user-config.env.
  cat > "${scriptDirectory}/user-config.env" <<EOF
SSH_KEY_PATH="${SSH_KEY_PATH}"
USERNAME="${USERNAME}"
AWS_PROFILE="${AWS_PROFILE}"
AWS_REGION="${AWS_REGION}"
AWS_VPC_SUBNET="${AWS_VPC_SUBNET}"
EOF
  echo "User configuration saved to test/user-config.env"
fi

# Load or create test session data (ephemeral).
if [[ -f "${scriptDirectory}/test-session.env" ]]; then
  echo "Loading test session..."
  source "${scriptDirectory}/test-session.env"
else
  echo "Starting new test session..."
  DOMAIN_TEST_PREFIX="tests$[RANDOM%100].tests."
  cat > "${scriptDirectory}/test-session.env" <<EOF
DOMAIN_TEST_PREFIX="${DOMAIN_TEST_PREFIX}"
EOF
fi

# Export only AWS SDK requirements.
export AWS_PROFILE
export AWS_REGION

# Build extra-vars for Ansible.
EXTRA_VARS=$(cat <<EOF
{
  "ssh_key_path": "${SSH_KEY_PATH}",
  "aws_vpc_subnet": "${AWS_VPC_SUBNET}",
  "aws_ec2_key_name": "ansible-test-${USERNAME}",
  "domain_test_prefix": "${DOMAIN_TEST_PREFIX}"
}
EOF
)

echo ""

# If there is no test inventory, provision the test systems.
if [[ ! -e ./test/hosts-test ]]; then
  echo "$ uv run ansible-playbook test/provision.yml --extra-vars '${EXTRA_VARS}' ${verboseArg}" | tee -a "${originalLog}"
  uv run ansible-playbook test/provision.yml --extra-vars "${EXTRA_VARS}" ${verboseArg}
  errorCode=$?
  echo -e "\n" | tee -a "${originalLog}"
fi

# Run our Ansible plays against the test systems.
if [ $errorCode -eq 0 ] && [ "${configure}" = true ]; then
  echo "$ uv run ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars '${EXTRA_VARS}' --extra-vars \"{is_test: true}\" ${verboseArg}" | tee -a "${originalLog}"
  uv run ansible-playbook site.yml --inventory-file=test/hosts-test --extra-vars "${EXTRA_VARS}" --extra-vars "{is_test: true}" ${verboseArg}
  errorCode=$?
  echo -e "\n" | tee -a "${originalLog}"
fi

# Tear down the test systems.
if [ $errorCode -eq 0 ] && [ "${teardown}" = true ]; then
  echo "$ uv run ansible-playbook test/teardown.yml --inventory-file=test/hosts-test --extra-vars '${EXTRA_VARS}' ${verboseArg}" | tee -a "${originalLog}"
  uv run ansible-playbook test/teardown.yml --inventory-file=test/hosts-test --extra-vars "${EXTRA_VARS}" ${verboseArg}
  errorCode=$?
  echo -e "\n" | tee -a "${originalLog}"

  # If everything tore down okay, remove the test session file.
  # User configuration is preserved for future test runs.
  if [ $errorCode -eq 0 ]; then
    rm "${scriptDirectory}/test-session.env"
  fi
fi

timestampLog
exit ${errorCode}
