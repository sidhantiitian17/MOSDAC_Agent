<#
.SYNOPSIS
    Free Windows TCP 8080 for the WSL-hosted Tabby LLM by moving the EnterpriseDB
    PEM Apache (service PEMHTTPD-x64 / httpd.exe) to another port.

.DESCRIPTION
    Docker Desktop forwards host.docker.internal:8080 to the Windows host. When PEM
    Apache binds 0.0.0.0:8080 it shadows the WSL localhost-forward that fronts Tabby,
    so containers hitting host.docker.internal:8080 reach Apache (404) instead of
    Tabby. (Ollama on 11434 works precisely because nothing on Windows squats it.)
    Moving Apache off 8080 lets the SAME forwarding mechanism expose Tabby on 8080 -
    no change to Tabby, the container, or any hardcoded IP.

    Idempotent and reversible: keeps "<conf>.bak-tabbyfix"; re-running is safe.
    Run ELEVATED (writes under C:\Program Files and restarts a service).
    After this, the PEM web console moves to http://localhost:<NewPort> .

    NOTE: ASCII-only on purpose - Windows PowerShell 5.1 reads .ps1 as ANSI, so any
    non-ASCII char (em dash, smart quote) corrupts the parse.
#>
param(
    [int]$NewPort   = 8083,
    [string]$Conf   = 'C:\Program Files\edb\pem\httpd\apache\conf\httpd.conf',
    [string]$Service= 'PEMHTTPD-x64',
    # C:\Users\Public is readable+writable by EVERY account (incl. whichever admin
    # UAC elevates to) and is visible from WSL at /mnt/c/Users/Public.
    [string]$LogFile= 'C:\Users\Public\fix-tabby-port.log'
)
$ErrorActionPreference = 'Stop'
function Log($m) {
    $line = '{0}  {1}' -f (Get-Date -Format o), $m
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}
try {
    Set-Content -Path $LogFile -Value ''   # reset log each run
    Log ("START NewPort=$NewPort Conf=$Conf Service=$Service whoami=" + [System.Security.Principal.WindowsIdentity]::GetCurrent().Name)

    if (-not (Test-Path $Conf)) { throw "Apache config not found: $Conf" }

    $bak = "$Conf.bak-tabbyfix"
    if (-not (Test-Path $bak)) { Copy-Item -LiteralPath $Conf -Destination $bak; Log "Backup created: $bak" }
    else { Log "Backup already present: $bak" }

    $content = Get-Content -LiteralPath $Conf -Raw
    $orig    = $content
    $content = $content -replace '(?m)^\s*Listen\s+0\.0\.0\.0:8080\b',     "Listen 0.0.0.0:$NewPort"
    $content = $content -replace '(?m)^\s*ServerName\s+localhost:8080\b',  "ServerName localhost:$NewPort"

    if ($content -ne $orig) {
        # UTF-8 without BOM (a BOM would break Apache's first directive).
        [System.IO.File]::WriteAllText($Conf, $content, (New-Object System.Text.UTF8Encoding($false)))
        Log "httpd.conf rewritten: 8080 -> $NewPort"
    } else {
        Log "No '8080' Listen/ServerName lines found to change (already moved?)."
    }

    Log "Restarting $Service ..."
    Restart-Service -Name $Service -Force
    Start-Sleep -Seconds 3
    Log ("Service status: " + (Get-Service -Name $Service).Status)

    $on8080 = @(Get-NetTCPConnection -LocalPort 8080     -State Listen -ErrorAction SilentlyContinue)
    $onNew  = @(Get-NetTCPConnection -LocalPort $NewPort -State Listen -ErrorAction SilentlyContinue)
    foreach ($c in $on8080) { Log ("8080 listener -> PID {0} ({1})" -f $c.OwningProcess, (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName) }
    foreach ($c in $onNew)  { Log ("$NewPort listener -> PID {0} ({1})" -f $c.OwningProcess, (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName) }

    $apacheStill8080 = $on8080 | Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName -eq 'httpd' }
    if ($apacheStill8080) { Log "WARNING: httpd still on 8080 - change may not have applied." }
    else { Log "OK: httpd no longer on 8080." }
    Log "DONE"
    exit 0
} catch {
    Log ("ERROR: " + $_.Exception.Message)
    exit 1
}
