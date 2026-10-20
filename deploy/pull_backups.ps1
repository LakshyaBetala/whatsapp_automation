# Copy the newest nightly database dump from the ASVA server to this laptop,
# over Tailscale, and keep the last 14. Safe to run any time; it does nothing
# when the server is unreachable. Scheduled daily by Windows Task Scheduler.
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\pull_backups.ps1
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:USERPROFILE 'Documents\ASVA-backups'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$remoteDir = '/home/server-asva/asva/backups'
try {
    $newest = (ssh -o BatchMode=yes -o ConnectTimeout=15 server-asva@asva-vps "ls -1t $remoteDir/asva_db_*.sql.gz* 2>/dev/null | head -1")
} catch { Write-Output 'server unreachable, skipping'; exit 0 }
if (-not $newest) { Write-Output 'no dump on the server yet'; exit 0 }
$newest = $newest.Trim()
$name = Split-Path $newest -Leaf
$target = Join-Path $dest $name
if (-not (Test-Path $target)) {
    scp -q -o BatchMode=yes "server-asva@asva-vps:$newest" "$target.partial"
    Move-Item -Force "$target.partial" $target
    Write-Output "pulled $name"
} else {
    Write-Output "already have $name"
}
Get-ChildItem $dest -Filter 'asva_db_*' | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 | Remove-Item -Force
