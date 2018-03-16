#!/bin/bash

adminUser='localadmin'
sshServerPort=2222

sshAuthorizedKeys[0]='ssh-rsa AAAAB3NzaC1yc2EAAAABIwAAAfsA5B5cndBaI3XHMlSm6tbYJa+nwT+6j8EBOesbPKsxtghYDhCR5TII7TcDXnKPqWMmgSoLpYygYnoJpmrycXOFjC2ue7aSoD/KXxxDaNjml50pwvJlcwdxRhryXMhoc/tVQZ47HBnIy/KKF2TaHqqjXvwOJZflCl1jHuS7Z7j0MsyFxWpPVAu1SKKEVteCzTN5pKskigYKkJGggpkhthhOsM9XpPfXFZmlfYF4kEBhOnucRI9fWnjgz3aw+V3oStLSQqlFTdwFEVOdLpZyZDl+XTpF7+RPbl3ChkD9XgG9Q/ViJLSrVrITtgjzASbOIMF+ZGodUvIuWDuaCUX1lSr9YEWEkxKpVSVHO3JVMS+X+QqOkNvMffvrjxlIZ20C3ESSZ/PwVpZJRue9YmR/zBALT5AHcWAgH01265yIkmGz+2ZPpYPGn5YT6Ly0PP0yT1GSOBxhW55zeOwyhqade1XnLLw7Cz188z5OZ4O6U4ndZqaNpBU7tWK8C1vX6VILdibxhoCXLox6seSS27894YwqD4lA6YYifYKAQ/kZ/OVlCvhu4F/PetEgoVBivxADbWHKFcqF0okObvYLSRXeGMNKSuTqBrOimBsCekEzeGmMvvRt32CIUYHSzlVoAo1Jwgi7TLVTlXm0/rhAzDs2wq3xfhLRM85BertozqU= The personal, passphrase-protected SSH key for Karl M. Davis.'

sudo tee "/etc/sudoers.d/${adminUser}" > /dev/null <<EOF
# Allow ${adminUser} to sudo without entering a password (required for Ansible).
${adminUser} ALL=(ALL) NOPASSWD:ALL
EOF
echo "Configured NOPASSWD sudo for '${adminUser}'."

if grep --quiet "^Port 22$" /etc/ssh/sshd_config; then
  sudo sed --in-place "s/^Port 22$/Port ${sshServerPort}/g" /etc/ssh/sshd_config
  sudo service ssh --full-restart &> /dev/null
  echo "Changed SSH server port to '${sshServerPort}'."
fi

if [[ ! -f ~/.ssh/authorized_keys ]]; then
  touch ~/.ssh/authorized_keys
  chmod u=rw,g=,o= ~/.ssh/authorized_keys
fi

for authorizedKey in "${sshAuthorizedKeys[@]}"
do
  if ! grep --quiet --no-messages --fixed-strings "${authorizedKey}" ~/.ssh/authorized_keys; then
    echo "$authorizedKey" >> ~/.ssh/authorized_keys
    echo "Added SSH public key to 'authorized_keys': ${authorizedKey}"
  fi
done
