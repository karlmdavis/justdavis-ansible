#!/bin/bash

set -e

# References:
# - http://stackoverflow.com/questions/637827/redirect-stderr-and-stdout-in-a-b$
# - http://linuxcommando.blogspot.com/2008/03/how-to-check-exit-status-code.html
# - http://stackoverflow.com/questions/64786/error-handling-in-bash
# - http://stackoverflow.com/questions/2275593/email-from-bash-script

# Call this function when an error occurs to log it and exit the script.
function error() {
	local CALLER_LINENO="$1"
	local MESSAGE="$2"
	local CODE="${3:-1}"

	local FULL_MESSAGE="Error on or near line ${CALLER_LINENO}: \"${MESSAGE}\". Exiting with status: ${CODE}."
	echo -e "${FULL_MESSAGE}"
	exit "${CODE}"
}

# Trap any errors, calling error() when they're caught.
trap 'error ${LINENO}' ERR

backupTargets=( "/var/fileshares" "/var/vmail" "/var/nexus-backup" "/var/lib/jenkins" "/var/lib/postgresql/backups" )
for backupTarget in "${backupTargets[@]}"; do
	echo "SpiderOak Backup: starting ${backupTarget}..."
	/usr/bin/SpiderOakONE --backup=${backupTarget}
	echo "SpiderOak Backup: complete ${backupTarget}."
done

# Check the SpiderOak logs to see if any errors were thrown there. This is done
# last to ensure that the backups for everything still at least try to run.
if grep --quiet "[[:space:]]ERROR[[:space:]]" ~/.config/SpiderOakONE/spider_*.log; then
	error ${LINENO} "SpiderOak backup log has errors."
fi
