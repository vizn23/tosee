<# 
.SYNOPSIS
    Deploy ToSee Doorbell integration to Odroid N2+ running Home Assistant

.DESCRIPTION
    Copies the custom component to a remote Odroid and restarts Home Assistant

.PARAMETER OdroidHost
    SSH host (e.g., root@192.168.1.100 or root@odroid.local)

.PARAMETER SshKey
    Path to SSH private key (optional, uses ssh-agent by default)

.EXAMPLE
    .\deploy_to_odroid.ps1 -OdroidHost "root@192.168.1.50"

.EXAMPLE
    .\deploy_to_odroid.ps1 -OdroidHost "root@odroid.local" -SshKey "C:\Users\kovar\.ssh\id_ed25519"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$OdroidHost,
    
    [string]$SshKey
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TempArchive = "$env:TEMP\tosee_doorbell.tar.gz"

Write-Host "🚀 Deploying ToSee Doorbell to $OdroidHost..." -ForegroundColor Cyan

# Vytvoř archiv
Write-Host "📦 Creating archive..." -ForegroundColor Yellow
if (Test-Path $TempArchive) { Remove-Item $TempArchive -Force }
tar -czf $TempArchive -C $ScriptDir custom_components

# Zkopíruj na Odroid
Write-Host "📤 Copying to Odroid..." -ForegroundColor Yellow
$ScpArgs = @($TempArchive, "$OdroidHost:/tmp/tosee_doorbell.tar.gz")
if ($SshKey) { $ScpArgs += "-i", $SshKey }
scp @ScpArgs

# Rozbal a restartuj HA
Write-Host "📂 Extracting and restarting HA on Odroid..." -ForegroundColor Yellow
$SshArgs = @($OdroidHost)
if ($SshKey) { $SshArgs += "-i", $SshKey }
$SshArgs += @"
    mkdir -p /config/custom_components/tosee_doorbell
    tar -xzf /tmp/tosee_doorbell.tar.gz -C /config
    echo '✅ Files extracted'
    
    # Restart HA
    if (command -v ha &> /dev/null) { ha core restart }
    elseif (systemctl is-active --quiet home-assistant) { systemctl restart home-assistant }
    elseif (docker ps | grep -q homeassistant) { docker restart homeassistant }
    else { echo '⚠️ Restart HA manually' }
"@

ssh @SshArgs

Write-Host "✅ Deploy complete! Check HA logs for 'tosee_doorbell'" -ForegroundColor Green