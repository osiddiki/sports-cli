# Remove Sports CLI from Windows: the app, the sports command, its Start menu entry, settings,
# source-check memory and the optional ad detector. Python and mpv stay, since other apps may use them.
#   sports --uninstall               asks first
#   uninstall.ps1 -Yes               does not ask
param([switch]$Yes)
$ErrorActionPreference = 'Stop'

$Root = Join-Path $env:LOCALAPPDATA 'sports-cli'
$Bin = Join-Path $Root 'bin'
$targets = @(
  $Root,
  (Join-Path ([Environment]::GetFolderPath('Programs')) 'Sports CLI.lnk'),
  (Join-Path $env:USERPROFILE '.config\nba-tui'),
  (Join-Path $env:USERPROFILE '.cache\sports-live'),
  (Join-Path $env:USERPROFILE '.local\share\sports-live')
)
$found = @($targets | Where-Object { Test-Path $_ })
if (-not $found) { Write-Host 'Sports CLI is not installed on this computer.'; exit 0 }

Write-Host "`nThis removes Sports CLI and its settings:"
$found | ForEach-Object { Write-Host "  $_" }
Write-Host 'Python and mpv stay, since other apps may use them.'
if (-not $Yes) { Read-Host 'Press Enter to remove it, or close this window to keep it' | Out-Null }

foreach ($target in $found) { Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue }
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
  [Environment]::SetEnvironmentVariable('Path', (($userPath -split ';' | Where-Object { $_ -and $_ -ne $Bin }) -join ';'), 'User')
}
Write-Host "`nSports CLI is removed. To install it again, use the installer from the README."
