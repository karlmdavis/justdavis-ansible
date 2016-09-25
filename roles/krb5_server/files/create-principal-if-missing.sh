#!/bin/sh

##
# This script uses `kadmin.local` to add a Kerberos principal, if that 
# principal doesn't already exist.
##

# To avoid exposing secrets in the system's process list, this script accepts
# its arguments via environment variables.
if [[ -z "${KRB_PRINCIPAL_NAME}" ]]; then >&2 echo 'The KRB_PRINCIPAL_NAME environment variable is required.'; exit 1; fi
name="${KRB_PRINCIPAL_NAME}"
if [[ -z "${KRB_PRINCIPAL_PASSWORD}" ]]; then >&2 echo 'The KRB_PRINCIPAL_PASSWORD environment variable is required.'; exit 1; fi
password="${KRB_PRINCIPAL_PASSWORD}"

# Exit immediately if something fails.
error() {
	local parent_lineno="$1"
	local message="$2"
	local code="${3:-1}"

	if [[ -n "$message" ]] ; then
		>&2 echo "Error on or near line ${parent_lineno}: ${message}; exiting with status ${code}"
	else
		>&2 echo "Error on or near line ${parent_lineno}; exiting with status ${code}"
	fi

	exit "${code}"
}
trap 'error ${LINENO}' ERR

if ! kadmin.local -q "get_principal ${name}" | grep --quiet "Principal does not exist"; then
	echo "Principal not found. Creating..."
	/usr/bin/expect <<-EOF
		set timeout 20
		spawn kadmin.local -q "add_principal -pwexpire '0 seconds' -policy default '${name}'"
		expect "Enter password for principal *:"
		send "${password}\r"
		expect "Re-enter password for principal *:"
		send "${password}\r"
		EOF
	echo "Principal created."
fi

