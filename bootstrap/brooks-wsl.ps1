<#
.SYNOPSIS
Install Ubuntu (WSL) on brooks.
.DESCRIPTION
Configures brooks with an Ubuntu installation via the Windows Subsystem for Linux.
.EXAMPLE
Run the script from the PowerShell ISE:

PS C:\> powershell.exe -ExecutionPolicy Bypass justdavis-ansible.git\bootstrap\brooks-wsl.ps1
#>

$wslInstallState = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
if ($wslInstallState.State -ne 'Enabled') {
  Read-Host "The '$($wslInstallState.FeatureName)' feature needs to be installed, which will require a restart afterwards. Rerun this script after the updates and restart.`nPress any key to continue with this install or CTRL+C to exit."
  Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
  Write-Host "The '$($wslInstallState.FeatureName)' feature has been installed. Restarting computer..."
  Restart-Computer
} else {
  Write-Host "The '$($wslInstallState.FeatureName)' feature was already installed."
}

