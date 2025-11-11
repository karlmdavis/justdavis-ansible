#!/bin/bash
##
# Cleanup stale DNS records from previous test runs.
#
# This script deletes all randomly-suffixed test DNS records (tests[0-9]+) from
# Route53 zones used for testing. It can optionally exclude specific test IDs
# to preserve records from currently running tests.
#
# Usage:
#   ./cleanup-dns.sh              # Delete all test records
#   ./cleanup-dns.sh 56           # Delete all except tests56
#   ./cleanup-dns.sh 56 42 87     # Delete all except tests56, tests42, tests87
##

set -e

PROFILE="justdavis"

# Parse exclusion list from arguments.
EXCLUDE_IDS=("$@")

echo "DNS Test Record Cleanup"
echo "======================="
if [ ${#EXCLUDE_IDS[@]} -gt 0 ]; then
  echo "Excluding test IDs: ${EXCLUDE_IDS[*]}"
else
  echo "No exclusions - will delete ALL test records"
fi
echo ""

# Function to check if a test ID should be excluded.
should_exclude() {
  local test_id=$1
  for exclude_id in "${EXCLUDE_IDS[@]}"; do
    if [ "$test_id" = "$exclude_id" ]; then
      return 0
    fi
  done
  return 1
}

# Function to delete a DNS record.
delete_record() {
  local zone_id=$1
  local name=$2
  local type=$3
  local ttl=$4
  local value=$5

  echo "  Deleting: $name ($type)"

  cat > /tmp/dns-change-batch.json << EOF
{
  "Changes": [{
    "Action": "DELETE",
    "ResourceRecordSet": {
      "Name": "$name",
      "Type": "$type",
      "TTL": $ttl,
      "ResourceRecords": [{"Value": "$value"}]
    }
  }]
}
EOF

  if aws route53 change-resource-record-sets \
    --hosted-zone-id "$zone_id" \
    --change-batch file:///tmp/dns-change-batch.json \
    --profile "$PROFILE" > /dev/null 2>&1; then
    echo "    ✓ Deleted"
  else
    echo "    ✗ Failed (may have already been deleted)"
  fi
}

# Function to clean up records in a zone.
cleanup_zone() {
  local zone_name=$1
  local zone_id=$2

  echo "Cleaning zone: $zone_name"

  # Get all records matching the pattern tests[0-9]+.
  aws route53 list-resource-record-sets \
    --hosted-zone-id "$zone_id" \
    --profile "$PROFILE" \
    --output json | \
    jq -r '.ResourceRecordSets[] |
      select(.Name | test("(ns1\\.)?tests[0-9]+\\.tests\\.'$zone_name'\\.")) |
      "\(.Name)|\(.Type)|\(.TTL)|\(.ResourceRecords[0].Value)"' | \
    while IFS='|' read -r name type ttl value; do
      # Extract test ID from the record name.
      if [[ $name =~ tests([0-9]+)\.tests\. ]]; then
        test_id="${BASH_REMATCH[1]}"

        if should_exclude "$test_id"; then
          echo "  Skipping: $name (excluded test ID: $test_id)"
        else
          delete_record "$zone_id" "$name" "$type" "$ttl" "$value"
          sleep 0.5
        fi
      fi
    done

  echo ""
}

# Get zone IDs.
echo "Looking up Route53 hosted zone IDs..."
ZONE_JUSTDAVIS=$(aws route53 list-hosted-zones-by-name \
  --dns-name tests.justdavis.com \
  --profile $PROFILE \
  --query 'HostedZones[0].Id' \
  --output text)

ZONE_DAVISONLINE=$(aws route53 list-hosted-zones-by-name \
  --dns-name tests.davisonlinehome.name \
  --profile $PROFILE \
  --query 'HostedZones[0].Id' \
  --output text)

ZONE_RPSTOURNEY=$(aws route53 list-hosted-zones-by-name \
  --dns-name tests.rpstourney.com \
  --profile $PROFILE \
  --query 'HostedZones[0].Id' \
  --output text)

ZONE_STORYWYRM=$(aws route53 list-hosted-zones-by-name \
  --dns-name tests.storywyrm.com \
  --profile $PROFILE \
  --query 'HostedZones[0].Id' \
  --output text)

echo ""

# Clean up each zone.
cleanup_zone "justdavis.com" "$ZONE_JUSTDAVIS"
cleanup_zone "davisonlinehome.name" "$ZONE_DAVISONLINE"
cleanup_zone "rpstourney.com" "$ZONE_RPSTOURNEY"
cleanup_zone "storywyrm.com" "$ZONE_STORYWYRM"

echo "Cleanup complete!"
