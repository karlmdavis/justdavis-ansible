#!/bin/bash

##
# Removes a TXT record from a BIND zone file after Certbot's DNS-01 challenge validation.
#
# Should be configured as the `--manual-auth-hook` for Certbot.
##

set -e
set -o pipefail

ZONE_FILE="/etc/bind/db.$CERTBOT_DOMAIN"
TXT_RECORD="_acme-challenge.$CERTBOT_DOMAIN. IN TXT \"$CERTBOT_VALIDATION\""

echo "Removing TXT record for $CERTBOT_DOMAIN..."

# Remove the TXT record
sed -i "/$TXT_RECORD/d" "$ZONE_FILE"

# Extract and increment the serial number
SERIAL=$(grep -E '^[[:space:]]*[0-9]+[[:space:]]*;[[:space:]]*sn = serial' "$ZONE_FILE" | awk '{print $1}')
if [[ -z "$SERIAL" ]]; then
    echo "Error: Could not find serial number in $ZONE_FILE"
    exit 1
fi

DATE_PART=$(date +%Y%m%d)
SERIAL_DATE=$(echo "$SERIAL" | cut -c1-8)
SERIAL_INCREMENT=$(echo "$SERIAL" | cut -c9-10)

if [[ "$SERIAL_DATE" == "$DATE_PART" ]]; then
    # Increment the counter if it's the same day
    NEW_SERIAL="${DATE_PART}$(printf "%02d" $((SERIAL_INCREMENT + 1)))"
else
    # Reset to NN=01 if it's a new day
    NEW_SERIAL="${DATE_PART}01"
fi

# Replace the old serial with the new one
sed -i "s/$SERIAL/$NEW_SERIAL/; s/sn = serial/sn = serial/" "$ZONE_FILE"

# Reload BIND
systemctl reload bind9
