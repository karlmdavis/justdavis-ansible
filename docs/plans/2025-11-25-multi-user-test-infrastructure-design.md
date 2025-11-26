# Multi-User Test Infrastructure Design

**Date:** 2025-11-25
**Status:** Approved

## Overview

Enable multiple contributors to run AWS-based test infrastructure from their workstations using their own SSH keys and IAM credentials, while sharing the same AWS account, VPC, and other infrastructure resources.

## Problem Statement

Currently, the test infrastructure in `test/test.sh` is hardcoded to use Karl's personal SSH key (`karlmdavis-mantis`) via the source-controlled file `test/vars_aws_provisioning_karlmdavis.yml`. This creates barriers for new contributors who need to:
- Create their own EC2 key pair in AWS
- Create their own user-specific vars file
- Override environment variables manually

## Requirements

- User specifies SSH public key (auto-detect with override capability).
- Only SSH key and username vary per user; AWS settings have sensible defaults.
- Auto-create persistent EC2 key pair from user's SSH public key if it doesn't exist.
- Maintain existing test isolation via random DNS prefix (`DOMAIN_TEST_PREFIX`).
- Fully automatic after initial SSH key detection/specification.
- No source-controlled files require editing for new contributors.

## Constraints

- All users share the same AWS account, VPC, and subnet.
- Each user has their own IAM credentials (AWS profile).
- Each user has their own SSH key pair.
- Test environments are already isolated via `DOMAIN_TEST_PREFIX`.

## Architecture

### Detect-Once-Store-Reuse Pattern

**First Run (no test.env or incomplete):**
1. Auto-detect SSH public key from standard locations.
2. Auto-detect username via `whoami`.
3. Apply default values for AWS profile, region, and VPC subnet.
4. Store all values in `test/test.env` (gitignored).
5. Provision using these values.

**Subsequent Runs (test.env exists):**
1. Load SSH key path, username, and AWS settings from `test/test.env`.
2. Environment variables can override loaded values.
3. Provision using stored/overridden values.

### Configuration Storage

**test/test.env (gitignored):**
```bash
DOMAIN_TEST_PREFIX="tests42.tests."  # Already exists
SSH_KEY_PATH="/Users/karl/.ssh/id_ed25519.pub"
USERNAME="karl"
AWS_PROFILE="justdavis"
AWS_REGION="us-east-1"
AWS_VPC_SUBNET="subnet-9a1dfeb0"
```

**Override Precedence:**
1. Environment variable (highest priority).
2. Value from test.env.
3. Auto-detected/hardcoded default (first run only).

### File Changes

**Deletions:**
- `test/vars_aws_provisioning_karlmdavis.yml` (no longer needed).
- `AWS_PROVISIONING_VARS_FILE` logic in `test.sh`.
- `--extra-vars "@${AWS_PROVISIONING_VARS_FILE}"` from playbook invocations.

**Variable Sources:**
- User-specific runtime config: `test/test.env` (gitignored).
- Shared constants: `group_vars/all/main.yml` (source controlled, contains AMI IDs).

## Component Design

### SSH Key Auto-Detection (test.sh)

**Detection Algorithm:**
```bash
# Check for environment variable override first
if [[ -n "$TEST_SSH_KEY_PATH" ]]; then
  SSH_KEY_PATH="$TEST_SSH_KEY_PATH"
else
  # Try standard locations in order of preference
  for key in ~/.ssh/id_ed25519.pub ~/.ssh/id_ecdsa.pub ~/.ssh/id_rsa.pub; do
    if [[ -f "$key" ]]; then
      SSH_KEY_PATH="$key"
      break
    fi
  done
fi

# Error if no key found
if [[ -z "$SSH_KEY_PATH" || ! -f "$SSH_KEY_PATH" ]]; then
  echo "ERROR: No SSH public key found."
  echo "Searched: ~/.ssh/id_ed25519.pub, ~/.ssh/id_ecdsa.pub, ~/.ssh/id_rsa.pub"
  echo "Create a key with: ssh-keygen -t ed25519"
  echo "Or set TEST_SSH_KEY_PATH=/path/to/your/key.pub"
  exit 1
fi
```

**Username Detection:**
```bash
USERNAME="${USERNAME:-$(whoami)}"
```

**EC2 Key Pair Naming:**
```bash
AWS_EC2_KEY_NAME="ansible-test-${USERNAME}"
```

### test.env Management (test.sh)

**Load or Create Logic:**
```bash
if [[ -f "${scriptDirectory}/test.env" ]]; then
  source "${scriptDirectory}/test.env"
else
  # Generate domain prefix (already exists)
  DOMAIN_TEST_PREFIX="tests$[RANDOM%100].tests."

  # Auto-detect SSH key (see detection algorithm above)
  # ...

  # Set defaults (allow env var overrides)
  USERNAME="${USERNAME:-$(whoami)}"
  AWS_PROFILE="${AWS_PROFILE:-justdavis}"
  AWS_REGION="${AWS_REGION:-us-east-1}"
  AWS_VPC_SUBNET="${AWS_VPC_SUBNET:-subnet-9a1dfeb0}"

  # Write test.env
  cat > "${scriptDirectory}/test.env" <<EOF
DOMAIN_TEST_PREFIX="${DOMAIN_TEST_PREFIX}"
SSH_KEY_PATH="${SSH_KEY_PATH}"
USERNAME="${USERNAME}"
AWS_PROFILE="${AWS_PROFILE}"
AWS_REGION="${AWS_REGION}"
AWS_VPC_SUBNET="${AWS_VPC_SUBNET}"
EOF
fi
```

**Export Variables for Ansible:**
```bash
export AWS_PROFILE
export AWS_REGION
export AWS_VPC_SUBNET
export SSH_KEY_PATH
export AWS_EC2_KEY_NAME="ansible-test-${USERNAME}"
```

### EC2 Key Pair Management (provision.yml)

**New Tasks (before EC2 instance provisioning):**
```yaml
- name: Check if EC2 key pair exists
  amazon.aws.ec2_key:
    name: "{{ lookup('env', 'AWS_EC2_KEY_NAME') }}"
    region: "{{ lookup('env', 'AWS_REGION') }}"
    state: present
  check_mode: yes
  register: ec2_key_check
  ignore_errors: yes

- name: Upload SSH public key if key pair doesn't exist
  amazon.aws.ec2_key:
    name: "{{ lookup('env', 'AWS_EC2_KEY_NAME') }}"
    region: "{{ lookup('env', 'AWS_REGION') }}"
    key_material: "{{ lookup('file', lookup('env', 'SSH_KEY_PATH')) }}"
    state: present
  when: ec2_key_check is failed or ec2_key_check.changed
```

**Instance Provisioning Updates:**
```yaml
- name: EC2 - Provision 'eddings'
  ec2_instance:
    key_name: "{{ lookup('env', 'AWS_EC2_KEY_NAME') }}"
    region: "{{ lookup('env', 'AWS_REGION') }}"
    network:
      vpc_subnet_id: "{{ lookup('env', 'AWS_VPC_SUBNET') }}"
      # ... rest unchanged
```

Similar changes for the `jordan-u` instance provisioning.

## Error Handling

### Error Cases

1. **No SSH Key Found:**
   - Clear error message with searched paths.
   - Instructions: `ssh-keygen -t ed25519` or set `TEST_SSH_KEY_PATH`.

2. **EC2 Key Pair Conflict:**
   - If key pair exists in AWS with different fingerprint.
   - Error message: "Key pair 'ansible-test-karl' exists but doesn't match your local key".
   - Suggest: Delete old key pair in AWS console or use different username.

3. **AWS Permissions Issues:**
   - If user can't create/upload key pairs.
   - Show AWS error, suggest checking IAM permissions for `ec2:ImportKeyPair`.

4. **Invalid test.env:**
   - If test.env exists but SSH key path no longer valid.
   - Auto-regenerate or prompt user.

### First Run User Experience

```
$ ./test/test.sh
Initializing test environment...
✓ SSH key detected: /Users/karl/.ssh/id_ed25519.pub
✓ Username: karl
✓ AWS settings: us-east-1, subnet-9a1dfeb0
Configuration saved to test/test.env
Registering EC2 key pair 'ansible-test-karl'...
[provisioning continues...]
```

## User Documentation Updates

Add section to README.md: "Running Tests (Multiple Contributors)"

- Explain test.env auto-generation on first run.
- Document environment variable overrides.
- Show example: Different AWS region/VPC usage.
- Clarify shared vs. per-user settings.

## Migration Path

For existing users (Karl):
1. First run after implementation will auto-detect current SSH key.
2. test.env will be created with current defaults.
3. Old `vars_aws_provisioning_karlmdavis.yml` can be deleted.
4. Existing test environments (if any) continue working.

## Security Considerations

- SSH private keys never leave user's local machine.
- Only public key material uploaded to AWS.
- EC2 key pairs are persistent (not deleted on teardown) for convenience.
- Each user's key pair is isolated by username prefix.
- test.env is gitignored to prevent accidental credential exposure.

## Future Enhancements

- Auto-cleanup of old/unused EC2 key pairs.
- Support for SSH agent key detection.
- Validation of key pair fingerprint match before use.
- Option to use temporary (session-lifetime) key pairs instead of persistent.
