# Install or update Sports CLI on Windows with one command, in PowerShell:
#   irm https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.ps1 | iex
# Options when run as a file: -Ppv adds Node.js and the PPV resolver, -Ads adds the local ad detector, -Yes skips the confirmation.
param([switch]$Ppv, [switch]$Ads, [switch]$Yes)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = 'osiddiki/sports-cli'
$Archive = if ($env:SPORTS_CLI_ARCHIVE) { $env:SPORTS_CLI_ARCHIVE } else { "https://github.com/$Repo/archive/refs/heads/main.zip" }
$ResolverCommit = '0f91ece96dc33cfda83948264948452bc988bb54'
$Root = Join-Path $env:LOCALAPPDATA 'sports-cli'
$App = Join-Path $Root 'app'
$Bin = Join-Path $Root 'bin'
$WingetLinks = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'

function Say($text) { Write-Host "`n==> $text" }
function Fail($text) { Write-Host "`nSetup stopped: $text" -ForegroundColor Red; exit 1 }
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
  # winget installs do not reach this window's PATH until it is reopened.
  $env:Path = "$WingetLinks;" + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
}

function Find-Python([string[]]$versions) {
  # The py launcher finds real installs; a bare "python" may be the Microsoft Store shortcut.
  if (-not (Has 'py')) { return $null }
  # Windows PowerShell stops on a native command's redirected error output; a missing version is expected here.
  $ErrorActionPreference = 'Continue'
  foreach ($version in $versions) {
    $path = & py "-$version" -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0 -and $path) { return "$path".Trim() }
  }
  return $null
}

function Winget-Install($id, $label) {
  Say "Installing $label"
  winget install --id $id --exact --scope user --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) {
    # Some packages only offer a machine-wide installer.
    winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fail "$label could not be installed (winget exit code $LASTEXITCODE)." }
  }
  Refresh-Path
}

Refresh-Path
# 3.12 is the newest Python the one-time ad detector conversion supports, so prefer it.
$Python = Find-Python @('3.12', '3.13', '3.14', '3.11', '3.10', '3.9')
if ($Ads) { $AdsPython = Find-Python @('3.12', '3.11', '3.10') }
$needs = @()
if (-not $Python) { $needs += 'Python' }
if ($Ads -and -not $AdsPython -and $Python) { $needs += 'Python 3.12 (for the ad detector)' }
if (-not (Has 'mpv')) { $needs += 'mpv (the video player)' }
if ($Ppv -and -not (Has 'node')) { $needs += 'Node.js (for PPV sources)' }

Say "Sports CLI will be installed in $App and started with the command: sports"
if ($needs) { Write-Host ('It will also install: ' + ($needs -join ', ') + '.') }
if ($Ads) { Write-Host 'The ad detector setup downloads about 1.5 GB of tools once and keeps a 22 MB model.' }
if (-not $Yes) { Read-Host 'Press Enter to continue, or Ctrl-C to cancel' | Out-Null }

if ($needs -and -not (Has 'winget')) {
  Fail 'winget is missing. Install "App Installer" from the Microsoft Store, then run this again.'
}
if (-not $Python -or ($Ads -and -not $AdsPython)) {
  Winget-Install 'Python.Python.3.12' 'Python 3.12'
  $Python = Find-Python @('3.12', '3.13', '3.14', '3.11', '3.10', '3.9')
  if (-not $Python) { Fail 'Python was installed but could not be found. Close this window, open a new one, and run this again.' }
}
# The ad detector needs Python 3.10 to 3.12; run everything on that one so the compiled packages match.
if ($Ads) {
  $Python = Find-Python @('3.12', '3.11', '3.10')
  if (-not $Python) { Fail 'Python 3.12 was installed but could not be found. Close this window, open a new one, and run this again.' }
}
if (-not (Has 'mpv')) {
  Winget-Install 'shinchiro.mpv' 'mpv'
  if (-not (Has 'mpv')) { Fail 'mpv was installed but could not be found. Close this window, open a new one, and run this again.' }
}
if ($Ppv -and -not (Has 'node')) { Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js' }

Say 'Downloading Sports CLI'
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$work = Join-Path $Root ('.download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
  $zip = Join-Path $work 'app.zip'
  if ($Archive -like 'http*') { Invoke-WebRequest -UseBasicParsing $Archive -OutFile $zip } else { Copy-Item $Archive $zip }
  Expand-Archive $zip -DestinationPath (Join-Path $work 'unpacked')
  $unpacked = Get-ChildItem (Join-Path $work 'unpacked') -Directory | Select-Object -First 1
  if (-not $unpacked -or -not (Test-Path (Join-Path $unpacked.FullName 'sports-cli'))) { Fail 'the download did not contain Sports CLI.' }
  # Keep what earlier runs installed: the PPV resolver and the optional Python packages.
  foreach ($kept in 'resolver', '.deps') {
    if (Test-Path (Join-Path $App $kept)) { Move-Item (Join-Path $App $kept) (Join-Path $unpacked.FullName $kept) }
  }
  if (Test-Path $App) { Remove-Item -Recurse -Force $App }
  Move-Item $unpacked.FullName $App
} finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Say 'Setting up the terminal interface and search packages'
& $Python -m pip install --quiet --disable-pip-version-check --no-cache-dir --upgrade --target (Join-Path $App '.deps') -r (Join-Path $App 'sportscli\requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'the Python packages could not be installed; the Windows terminal interface needs them.' }

if ($Ppv -or (Has 'node')) {
  $resolver = Join-Path $App 'resolver'
  if (-not (Test-Path (Join-Path $resolver 'src\server.js'))) {
    Say 'Setting up the PPV resolver'
    $tarball = Join-Path $Root 'resolver.zip'
    Invoke-WebRequest -UseBasicParsing "https://github.com/Lunatic16/ppv-hls-proxy/archive/$ResolverCommit.zip" -OutFile $tarball
    $unpack = Join-Path $Root '.resolver'
    Remove-Item -Recurse -Force $unpack -ErrorAction SilentlyContinue
    Expand-Archive $tarball -DestinationPath $unpack
    Move-Item (Get-ChildItem $unpack -Directory | Select-Object -First 1).FullName $resolver
    Remove-Item -Recurse -Force $unpack, $tarball
  }
  npm install --prefix $resolver --ignore-scripts --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { Write-Host 'The PPV resolver could not be set up; everything else still works.' }
}

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$installer = Join-Path $App 'install.ps1'
$launcher = @"
@echo off
rem Sports CLI launcher written by install.ps1.
set "PATH=$WingetLinks;%PATH%"
set "SPORTS_CLI_COMMAND=sports"
if "%~1"=="--update" ( powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/$Repo/main/install.ps1 | iex" & exit /b )
if "%~1"=="--setup" ( powershell -NoProfile -ExecutionPolicy Bypass -File "$installer" -Yes & exit /b )
if "%~1"=="--setup-ppv" ( powershell -NoProfile -ExecutionPolicy Bypass -File "$installer" -Yes -Ppv & exit /b )
if "%~1"=="--setup-ads" ( powershell -NoProfile -ExecutionPolicy Bypass -File "$installer" -Yes -Ads & exit /b )
"$Python" "$App\sports-cli" %*
"@
Set-Content -Path (Join-Path $Bin 'sports.cmd') -Value $launcher -Encoding ASCII

# A Start menu entry, so the app opens without typing a command. Windows 11 opens it in Windows Terminal.
try {
  $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Sports CLI.lnk'))
  $shortcut.TargetPath = Join-Path $Bin 'sports.cmd'
  $shortcut.WorkingDirectory = $env:USERPROFILE
  $shortcut.Description = 'Live games, replays and TV'
  $shortcut.Save()
} catch { Write-Host 'The Start menu entry could not be made; type "sports" in a terminal instead.' }

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $Bin)) {
  [Environment]::SetEnvironmentVariable('Path', ($(if ($userPath) { "$userPath;" } else { '' }) + $Bin), 'User')
}

if ($Ads) {
  Say 'Setting up the local ad detector'
  $env:SPORTS_CLI_COMMAND = 'sports'
  & $Python (Join-Path $App 'sports-cli') --install-ad-model
  if ($LASTEXITCODE -ne 0) { Fail 'the ad detector could not be set up. Everything else works; run "sports --setup-ads" to try again.' }
}

Say 'Sports CLI is ready'
Write-Host 'Open "Sports CLI" from the Start menu, or open a new terminal window and type: sports'
Write-Host 'Later: "sports --update" updates it, "sports --setup-ppv" adds PPV sources, "sports --setup-ads" adds ad detection.'
