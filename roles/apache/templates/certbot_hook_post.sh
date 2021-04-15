#!/bin/bash

##
# Sets permissions and restarts services after Certbot creates/renews certificates.
#
# Should be configured as `post-hook` in `/etc/letsencrypt/cli.ini`.
##

set -e
set -o pipefail

echo 'Certbot post-hook Script: start'

if [ -f /etc/letsencrypt/live/{{ domain }}/privkey.pem ]; then
  # This is required for the ACL below to take effect.
  # The directory permissions ensure that it's safe to do.
  chmod o+r /etc/letsencrypt/live/{{ domain }}/privkey.pem

  if [ $(getent group openldap) ]; then
    echo 'The openldap group exists.'
    setfacl -m g:openldap:rx /etc/letsencrypt/live
    setfacl -m g:openldap:rx /etc/letsencrypt/archive
    systemctl restart slapd
    echo 'Restarted slapd.'
  else
    echo 'The openldap group does not exist.'
  fi
else
  echo 'No certificates found.'
fi
echo 'Certbot post-hook Script: end'
