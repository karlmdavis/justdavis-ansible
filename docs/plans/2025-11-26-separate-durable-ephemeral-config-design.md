# Separate Durable and Ephemeral Test Configuration

**Date:** 2025-11-26
**Status:** Approved

## Problem Statement

The current implementation stores both durable user configuration (SSH key path, username, AWS settings) and ephemeral test session data (DOMAIN_TEST_PREFIX) in a single `test/test.env` file. This causes users to reconfigure their settings on every test run because successful teardown deletes the entire file.

## Requirements

- Preserve user configuration (SSH key, AWS settings) across test runs
- Regenerate ephemeral session data (domain prefix) for each test session
- Allow manual reset of user configuration when needed
- Maintain current auto-detection behavior for first-time setup
- Use more explicit Ansible variable passing (--extra-vars vs environment variables)

## Design

### File Structure

**Two Configuration Files:**

1. **`test/user-config.env`** (Durable, gitignored)
   - Contains: `SSH_KEY_PATH`, `USERNAME`, `AWS_PROFILE`, `AWS_REGION`, `AWS_VPC_SUBNET`
   - Created once on first run or after manual reset
   - Persists across all test runs
   - Survives teardown
   - Manual reset: `rm test/user-config.env`

2. **`test/test-session.env`** (Ephemeral, gitignored)
   - Contains: `DOMAIN_TEST_PREFIX`
   - Created at start of each test session
   - Reused within a session (e.g., partial runs with `-t false`)
   - Deleted on successful teardown
   - Preserved on failed teardown for debugging

### Startup Logic (test.sh)

```bash
# Load or create user configuration (durable)
if [[ -f "${scriptDirectory}/user-config.env" ]]; then
  echo "Loading user configuration..."
  source "${scriptDirectory}/user-config.env"
else
  echo "Initializing user configuration..."

  # Auto-detect SSH key
  SSH_KEY_PATH=$(detect_ssh_key)
  echo "✓ SSH key detected: ${SSH_KEY_PATH}"

  # Detect username
  USERNAME="${USERNAME:-$(whoami)}"
  echo "✓ Username: ${USERNAME}"

  # Set AWS defaults (can be overridden via env vars)
  AWS_PROFILE="${AWS_PROFILE:-justdavis}"
  AWS_REGION="${AWS_REGION:-us-east-1}"
  AWS_VPC_SUBNET="${AWS_VPC_SUBNET:-subnet-9a1dfeb0}"
  echo "✓ AWS settings: ${AWS_REGION}, ${AWS_VPC_SUBNET}"

  # Write user config
  cat > "${scriptDirectory}/user-config.env" <<EOF
SSH_KEY_PATH="${SSH_KEY_PATH}"
USERNAME="${USERNAME}"
AWS_PROFILE="${AWS_PROFILE}"
AWS_REGION="${AWS_REGION}"
AWS_VPC_SUBNET="${AWS_VPC_SUBNET}"
EOF
  echo "User configuration saved to test/user-config.env"
fi

# Load or create test session data (ephemeral)
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

# Export only AWS SDK requirements
export AWS_PROFILE
export AWS_REGION

# Build extra-vars for Ansible
EXTRA_VARS=$(cat <<EOF
{
  "ssh_key_path": "${SSH_KEY_PATH}",
  "username": "${USERNAME}",
  "aws_vpc_subnet": "${AWS_VPC_SUBNET}",
  "aws_ec2_key_name": "ansible-test-${USERNAME}",
  "domain_test_prefix": "${DOMAIN_TEST_PREFIX}"
}
EOF
)
```

### Teardown Logic (test.sh)

```bash
# In teardown section - only delete session file on success
if [ $errorCode -eq 0 ]; then
  rm "${scriptDirectory}/test-session.env"
  # user-config.env is preserved for next test run
fi
```

### Hybrid Variable Passing Strategy

**Environment Variables (for AWS SDK only):**
- `AWS_PROFILE` - Required by boto3/AWS SDK
- `AWS_REGION` - Used by AWS SDK for API endpoint selection

**Ansible Extra Vars (everything else):**

```bash
# Provision call
./ansible-playbook-wrapper test/provision.yml --extra-vars "${EXTRA_VARS}"

# Site call
./ansible-playbook-wrapper site.yml --inventory-file=test/hosts-test \
  --extra-vars "${EXTRA_VARS}" --extra-vars "{is_test: true}"

# Teardown call
./ansible-playbook-wrapper test/teardown.yml --inventory-file=test/hosts-test \
  --extra-vars "${EXTRA_VARS}"
```

**Rationale:**
- **More explicit**: Variables clearly visible in command invocation
- **Ansible-native**: Standard pattern using --extra-vars
- **AWS SDK compatible**: Still exports required environment variables
- **Playbook flexibility**: Playbooks can work with different variable sources

### Ansible Playbook Updates

**provision.yml changes:**
- `lookup('env', 'AWS_EC2_KEY_NAME')` → `{{ aws_ec2_key_name }}`
- `lookup('env', 'AWS_VPC_SUBNET')` → `{{ aws_vpc_subnet }}`
- `lookup('file', lookup('env', 'SSH_KEY_PATH'))` → `lookup('file', ssh_key_path)`
- Keep: `lookup('env', 'AWS_REGION')` (still from environment for boto)

**teardown.yml changes:**
- Keep: `lookup('env', 'AWS_REGION')` (still from environment for boto)

### .gitignore Updates

```diff
# Line 17 - Current:
- test/test.env

# Replace with:
+ test/user-config.env
+ test/test-session.env
```

## Migration Path

**For existing users (manual):**
1. Delete existing `test/test.env`
2. Next test run will auto-detect and create new config files

## Benefits

1. **Better UX**: Users configure once, run tests many times
2. **Clear separation**: Durable vs ephemeral configuration explicit in file names
3. **Session continuity**: Partial test runs reuse same domain prefix
4. **Debugging friendly**: Failed teardowns preserve session data
5. **More explicit**: Ansible variables visible in command line
6. **Ansible-native**: Uses standard --extra-vars pattern
7. **Easy reset**: Delete `user-config.env` to reconfigure

## Trade-offs

**Two files vs one:**
- ✅ Clear separation of concerns
- ✅ Self-documenting (file names explain purpose)
- ❌ Two files to manage (minor)

**Hybrid variable passing:**
- ✅ Explicit Ansible variable passing
- ✅ AWS SDK compatibility maintained
- ❌ Slightly longer command lines
- ❌ Need to construct JSON for --extra-vars
