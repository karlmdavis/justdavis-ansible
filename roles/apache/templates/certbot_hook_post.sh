#!/bin/bash

##
# Sets permissions and restarts services after Certbot creates/renews certificates.
#
# Should be configured as `post-hook` in `/etc/letsencrypt/cli.ini`.
##

set -e
set -o pipefail

echo 'Certbot post-hook Script: start'
if [ $(getent group openldap) ]; then
  echo 'The openldap group exists.'
  setfacl -m g:openldap:rx /etc/letsencrypt/live
  setfacl -m g:openldap:rx /etc/letsencrypt/archive
  systemctl restart slapd
else
  echo 'The openldap group does not exist.'
fi
echo 'Certbot post-hook Script: end'
