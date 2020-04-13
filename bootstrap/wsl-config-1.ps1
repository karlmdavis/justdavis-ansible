<#
.SYNOPSIS
Install WSL and configure firewall on Windows systems.
.DESCRIPTION
Prepares Windows systems to run Ubuntu via WSL by enabling the required Windows features.
Also adds a firewall rule to allow WSL's OpenSSH Server to work.
.EXAMPLE
Run the script from the PowerShell ISE:

PS C:\> powershell.exe -ExecutionPolicy Bypass -File justdavis-ansible.git\bootstrap\wsl-config-1.ps1
#>

$wslInstallState = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
if ($wslInstallState.State -ne 'Enabled') {
  Read-Host "The '$($wslInstallState.FeatureName)' feature needs to be installed, which will require a restart afterwards. Rerun this script after the updates and restart.`nPress any key to continue with this install or CTRL+C to exit."
  Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
  Write-Host "The '$($wslInstallState.FeatureName)' feature has been installed. Restarting computer..."
  Restart-Computer
} else {
  Write-Host "The '$($wslInstallState.FeatureName)' feature was already instaled."
}

$sshServerPort = 2222
$sshServerFirewallRuleName = "WSL OpenSSH Server Port $($sshServerPort)"
try {
  Get-NetFirewallRule -DisplayName $sshServerFirewallRuleName -ErrorAction Stop | Out-Null
  Write-Host "The '$($sshServerFirewallRuleName)' firewall rule already exists."
}
catch [Exception] {
  New-NetFirewallRule -Name Allow_WSL_OpenSSH -DisplayName $sshServerFirewallRuleName `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $sshServerPort
  Write-Host "Created the '$($sshServerFirewallRuleName)' firewall rule."
}
