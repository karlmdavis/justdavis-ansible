# Bootstrap Ansible Environment

This directory contains scripts that can be used to bootstrap the Ansible environment on various systems.

## brooks-wsl

The Windows Subsystem for Linux environment on `brooks` can be bootstrapped as follows:

1. Install WSL by running this from an admin-privileged PowerShell prompt:
    
    ```
    PS C:\> powershell.exe -ExecutionPolicy Bypass justdavis-ansible.git\bootstrap\brooks-wsl.ps1
    ```
    
    * Note that this won't install a Linux distribution by itself; it's just an important prerequisite.
2. Install Ubuntu from the Microsoft Store: <https://www.microsoft.com/store/productId/9NBLGGH4MSV6>
    * As of 2018-03-11, there doesn't appear to be any way to automate this. Just a couple clicks, though.
    * Once installed, it can be launched by doing any of the following **as the Windows user that installed the app in the Microsoft Store**:
        * Using the **Ubuntu** application in the Start menu.
        * Running `C:\Windows\System32\bash.exe`.
        * Running `C:\Users\<username>\AppData\Local\Microsoft\WindowsApps\ubuntu.exe`.
            * Use the "`/?`" flag to see the (very useful) options for this command.

Some miscellaneous notes on WSL:

* This all still feels pretty immature to me.
    * Cygwin is definitely a smoother experience, but I suspect that won't be true for too much longer. MS seems to be committed to making WSL work well.
* Microsoft uses this GitHub repo to publicly manage bugs with WSL: <https://github.com/Microsoft/WSL/issues>.
* [ConEmu](https://conemu.github.io/) seems to be a decent command prompt/terminal application, with support for PowerShell and WSL.
* You can get an OpenSSH server running:
    * Note: It will only stay running as long as the WSL session/window is left open.
    * These seem to be a decent set of instructions: <https://gist.github.com/wsargent/072319c2100ac0aea4305d6f6eeacc08#set-up-sshd>.
* WSL doesn't yet have any good support for running persistent services such as an SSH server.
    * MS has said this isn't a priority for them.
    * This application sounds like it might be a solid attempt at closing that gap: <https://github.com/cerebrate/wabash>.
