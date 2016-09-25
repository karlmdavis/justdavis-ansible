#!/bin/bash

##
# This script uses `kadmin.local` to add a Kerberos principal, if that 
# principal doesn't already exist. It also handles secrets securely.
##

# Verify that the script is being run with root permissions.
if [[ $EUID -ne 0 ]]; then
	>&2 echo "This script must be run as root"
	exit 1
fi

# To avoid exposing secrets in the system's process list, this script accepts
# some of its arguments via environment variables.
# The script's (non-secret) command line arguments will be passed through 
# unchanged to the `add_principal` command.
if [[ -z "${KRB_PRINCIPAL_NAME}" ]]; then >&2 echo 'The KRB_PRINCIPAL_NAME environment variable is required.'; exit 1; fi
name="${KRB_PRINCIPAL_NAME}"
if [[ ! -z "${KRB_PRINCIPAL_PASSWORD}" ]]; then password="${KRB_PRINCIPAL_PASSWORD}"; fi

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

if ! kadmin.local -q "get_principal ${name}" |& grep --quiet "Principal does not exist"; then
	echo "Principal not found. Creating..."

	# If the principal has a password, we'll pass that in via an `expect` script.
	# If it doesn't, we'll just call `kadmin.local` directly.
	if [[ ! -z "${password}" ]]; then
		/usr/bin/expect <<-EOF
			set timeout 20
			spawn kadmin.local -q "add_principal ${@} '${name}'"
			expect "Enter password for principal *:"
			send "${password}\r"
			expect "Re-enter password for principal *:"
			send "${password}\r"
			EOF
	else
		kadmin.local -q "add_principal ${@} '${name}'"
	fi
	
	echo "Principal created."
else
	echo "Principal already exists."
fi

