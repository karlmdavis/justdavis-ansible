#!/bin/bash

##
# This script uses `kadmin.local` to add a Kerberos principal to a keytab file, 
# if that principal isn't already present in the file.
##

# Verify that the script is being run with root permissions.
if [[ $EUID -ne 0 ]]; then
	>&2 echo "This script must be run as root."
	exit 1
fi

# Use GNU getopt to parse the options passed to this script.
TEMP=`getopt \
	-o p:k: \
	--long principal:,keytab: \
	-n 'add-keytab.sh' -- "$@"`
if [ $? != 0 ] ; then echo "Terminating." >&2 ; exit 1 ; fi

# Note the quotes around `$TEMP': they are essential!
eval set -- "$TEMP"

# Parse the getopt results.
principal=
keytab=
while true; do
	case "$1" in
		-p | --principal )
			principal="$2"; shift 2 ;;
		-k | --keytab )
			keytab="$2"; shift 2 ;;
		-- ) shift; break ;;
		* ) break ;;
	esac
done

# Verify that all required options were specified.
if [[ -z "${principal}" ]]; then >&2 echo 'The --principal option is required.'; exit 1; fi

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

# Check to see if the principal is listed in the keytab file.
if [[ -z "${keytab}" ]]; then
	keytabFile="/etc/krb5.keytab"
else
	keytabFile="${keytab}"
fi
if klist -k "${keytabFile}" |& grep --quiet "${principal}"; then
	echo "Principal already in keytab."
else
	echo "Principal not listed in keytab yet. Adding..."
	if [[ -z "${keytab}" ]]; then
		kadmin.local -q "ktadd ${principal}"
	else
		kadmin.local -q "ktadd -keytab ${keytab} ${principal}"
	fi
	echo "Principal added to keytab: '${keytabFile}'."
fi

