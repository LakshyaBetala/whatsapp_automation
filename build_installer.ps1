# ============================================================
#  Build ASVA-Setup.exe - the ONE public installer every shop downloads.
#
#  It carries NO secret: no database key, no agent token, no config. A fresh
#  install knows nothing until the owner types their pairing code, which is why
#  the download can be public and the website Download button just works.
#
#  Bundles everything so the shop laptop needs NOTHING preinstalled:
#    - Electron  -> supplies the Node runtime for the WhatsApp service
#    - PyInstaller -> the Tally reader as a standalone .exe (no Python needed)
#
#  Run from the repo root on Windows:
#      powershell -ExecutionPolicy Bypass -File build_installer.ps1
#  Output: dist_installer\ASVA-Setup-<version>.exe
# ============================================================
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

Write-Host "==> [1/5] Building the Tally reader (PyInstaller)..." -ForegroundColor Cyan
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pip install pyinstaller --quiet
if (Test-Path "$root\dist_agent") { Remove-Item "$root\dist_agent" -Recurse -Force }
& $py -m PyInstaller --onefile --noconfirm --clean `
    --name asva-agent `
    --distpath "$root\dist_agent" `
    --workpath "$root\build_agent" `
    --specpath "$root\build_agent" `
    --paths "$root\tally_agent" `
    "$root\tally_agent\agent.py"
if (-not (Test-Path "$root\dist_agent\asva-agent.exe")) {
    throw "PyInstaller did not produce dist_agent\asva-agent.exe"
}

Write-Host "==> [2/5] WhatsApp service dependencies (production only)..." -ForegroundColor Cyan
Push-Location "$root\wa_service"
npm install --omit=dev --no-audit --no-fund
Pop-Location

Write-Host "==> [3/5] Desktop app dependencies..." -ForegroundColor Cyan
Push-Location "$root\desktop"
npm install --no-audit --no-fund

# electron-builder fetches a code-signing bundle that contains macOS symlinks
# (libcrypto.dylib / libssl.dylib). Windows refuses to create symlinks without
# Developer Mode or Administrator, so the build dies with "A required privilege
# is not held by the client" - even though those macOS files are useless here.
# Prime the cache ourselves, excluding darwin, so a normal user can build.
Write-Host "==> [4/5] Preparing the signing cache (skipping macOS files)..." -ForegroundColor Cyan
$signCache = "$env:LOCALAPPDATA\electron-builder\Cache\winCodeSign"
$signDir = "$signCache\winCodeSign-2.6.0"
if (-not (Test-Path "$signDir\rcedit-x64.exe")) {
    New-Item -ItemType Directory -Force $signCache | Out-Null
    $archive = "$signCache\winCodeSign-2.6.0.7z"
    if (-not (Test-Path $archive)) {
        Invoke-WebRequest -UseBasicParsing -OutFile $archive `
            -Uri "https://github.com/electron-userland/electron-builder-binaries/releases/download/winCodeSign-2.6.0/winCodeSign-2.6.0.7z"
    }
    if (Test-Path $signDir) { Remove-Item $signDir -Recurse -Force }
    & "$root\desktop\node_modules\7zip-bin\win\x64\7za.exe" x -bd -y "-o$signDir" $archive "-x!darwin" | Out-Null
    Write-Host "    signing cache ready."
} else {
    Write-Host "    already cached."
}

Write-Host "==> [5/5] Packaging the installer (electron-builder)..." -ForegroundColor Cyan
npm run dist
Pop-Location

$out = Get-ChildItem "$root\dist_installer\ASVA-Setup-*.exe" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($out) {
    $mb = [math]::Round($out.Length / 1MB, 1)
    Write-Host ""
    Write-Host "==> Installer ready: $($out.FullName)  ($mb MB)" -ForegroundColor Green
    Write-Host "    Publish it so the website Download button serves it:"
    Write-Host "      scp `"$($out.FullName)`" <user>@<i3>:~/asva/downloads/ASVA-Setup.exe"
} else {
    throw "electron-builder finished but no installer was found in dist_installer\"
}
