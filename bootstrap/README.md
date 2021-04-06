# Bootstrap Ansible Environment

This directory contains scripts that can be used to bootstrap the Ansible environment on various systems.

## Setup brust on Ubuntu 20.04

A clean Ubuntu 20.04 install on `brust` can be prepared to run the Ansible plays against itself, as follows:

1. Install Ubuntu, as normal, naming the system `brust` and the first user `localadmin`.
2. Ensure that the `localadmin` user can run `sudo` without having to enter a password:
    
    ```
    $ echo -e "# Allow localadmin to run sudo without requiring a password.\nlocaladmin ALL=(ALL) NOPASSWD: ALL" | sudo tee -a /etc/sudoers.d/localadmin
    ```
    
3. Install the pre-requisites that you'll need for the Ansible run:
    
    ```
    $ sudo apt install python3 python3-virtualenv build-essential python3-dev libpq-dev openssh-server
    ```
    
4. Add a couple of entries to `/etc/hosts`, to workaround a bug with the Amplifi router's DNS server/relay:
    
    ```
    $ echo -e "10.0.0.2 eddings.karlanderica.justdavis.com\n10.0.0.53 brust.karlanderica.justdavis.com" | sudo tee -a /etc/hosts
    ```
    
5. Create an SSH key (make sure to give it a secure passphrase, and to store that passphrase somewhere secure),
   load it into the SSH agent, and then make note of its public key:
    
    ```
    $ ssh-keygen -t ed25519
    $ ssh-add ~/.ssh/id_ed25519
    $ cat ~/.ssh/id_ed25519.pub
    ```
    
6. Add that public key to GitHub, via [GitHub: SSH and GPG keys](https://github.com/settings/keys).
7. Clone the Ansible repo and setup its Python virtual environment:
    
    ```
    $ mkdir -p ~/workspaces/justdavis
    $ cd ~/workspaces/justdavis
    $ git clone git@github.com:karlmdavis/justdavis-ansible.git justdavis-ansible.git
    $ cd justdavis-ansible.git
    $ virtualenv -p /usr/bin/python3 venv
    $ source venv/bin/activate
    $ pip install -r requirements.txt
    $ ansible-galaxy install -r install_roles.yml
    ```
    
8. Create the `vault.password` file in the `justdavis-ansible.git` project/repo,
   containing the proper password for it.

## Setup brooks on WSL 1

The Windows Subsystem for Linux (v1) environment on `brooks` can be bootstrapped as follows:

1. Download this project and run its first bootstrap script (which just installs the WSL feature) by running this from an admin-privileged PowerShell prompt:
    
    ```
    PS C:\> Write-Host "Downloading and extracting justdavis-ansible.git..."; [Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://github.com/karlmdavis/justdavis-ansible/archive/master.zip', 'Downloads\justdavis-ansible.git.zip'); If (!(Test-Path -Path 'Downloads\justdavis-ansible.git')) { Expand-Archive 'Downloads\justdavis-ansible.git.zip' -DestinationPath 'Downloads\justdavis-ansible.git' }; Write-Host "Extracted justdavis-ansible.git to 'Downloads\justdavis-ansible.git'."; powershell.exe -ExecutionPolicy Bypass -File 'Downloads\justdavis-ansible.git\justdavis-ansible-master\bootstrap\wsl-config-1.ps1'
    ```
    
    * Note that this won't install a Linux distribution by itself; it's just an important prerequisite.
2. Install Ubuntu 18.04 from the Microsoft Store: <https://www.microsoft.com/en-us/p/ubuntu-1804-lts/9n9tngvndl3q>.
    * As of 2018-03-11, there doesn't appear to be any way to automate this. Just a couple clicks, though.
3. After install, launch it by doing any of the following **as the Windows user that installed the app in the Microsoft Store**:
    * Use the **Ubuntu 18.04** application in the Start menu.
    * Run `C:\Windows\System32\bash.exe`.
    * Run `C:\Users\<username>\AppData\Local\Microsoft\WindowsApps\ubuntu1804.exe`.
        * Note: Use the "`/?`" flag to see the (very useful) options for this command.
        * This directory should actually be on the default path, so you should be able to just run "`> ubuntu1804`".
4. On first launch, you will be prompted to create a user account. Name the account "`localuser`".
5. Once that account is created, run the second bootstrap script from a PowerShell prompt running as the user that installed Ubuntu (must be a non-admin prompt):
    
    ```
    PS C:\> ubuntu1804 run /mnt/c/Users/karl/Downloads/justdavis-ansible.git/bootstrap/wsl-config-2.bash
    ```
    
    * Note: You will need to update the `/mnt/c/Users/karl/...` path to match your Windows username, before running the command.
6. If you're running a Windows 10 Build < 17046, you must start the OpenSSH server by running the following command in a WSL window and leaving that window open:
    
    ```
    $ sudo service ssh --full-restart
    ```
    
    * Reference: <https://blogs.msdn.microsoft.com/commandline/2017/12/04/background-task-support-in-wsl/>

With the OpenSSH server now properly configured and running, the WSL system's remaining configuration can be handled by a normal run of the `ansible-justdavis.git/site.yml` Ansible playbook.

Note that, for some reason, WSL 1 is unable to use the `JUSTDAVIS.COM` realm's LDAP/Kerberos user accounts as the default user for the WSL environment. This can be worked around, however, by first appending the user manually to `/etc/passwd`, using these commands from within the WSL environment:

```
$ echo 'karl:x:10000:10000:,,,:/home/karl:/bin/bash' | sudo tee --append /etc/passwd > /dev/null
```

After that's been done, the default WSL user can be set, using these commands from within a non-privileged PowerShell session:

```
> ubuntu1804 config --default-user karl
> sc restart LxssManager
```

## Setup lawson on WSL 1

The Windows Subsystem for Linux (v1) environment on `lawson` can be bootstrapped exactly the same as for `brooks` above.

## WSL Notes

Some miscellaneous notes on WSL:

* This all still feels pretty immature to me.
    * Cygwin is definitely a smoother experience, but I suspect that won't be true for too much longer. MS seems to be committed to making WSL work well.
* Microsoft uses this GitHub repo to publicly manage bugs with WSL: <https://github.com/Microsoft/WSL/issues>.
* [ConEmu](https://conemu.github.io/) seems to be a decent command prompt/terminal application, with support for PowerShell and WSL.
* You can get an OpenSSH server running if you jump through some hoops:
    * Note: It will only stay running as long as the WSL session/window is left open.
    * These seem to be a decent set of instructions: <https://gist.github.com/wsargent/072319c2100ac0aea4305d6f6eeacc08#set-up-sshd>.
    * This seems to be the main thread for whining about this: <https://github.com/Microsoft/WSL/issues/612>.
* WSL doesn't yet have any good support for running persistent services at boot such as an SSH server.
    * MS has said this isn't a priority for them.
    * This application sounds like it might be a solid attempt at closing that gap: <https://github.com/cerebrate/wabash>.
* Given the permissions issues (WSL can only be run as the user that installed it), it's best to thnik of it as more of a self-contained VM than as an integrated component of the Windows system.
    * If Microsoft gets privilege escalation/sudo working properly in the future, this might change.
