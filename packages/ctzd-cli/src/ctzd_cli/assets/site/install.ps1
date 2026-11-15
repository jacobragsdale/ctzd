# Installs ctzd for the current user, with no admin rights. An AI agent runs this; the person never has to:
#   powershell -c "irm {{site}}/install.ps1 | iex"
# Every download comes from the platform's site and package index, so desktops need no access to github.com or
# pypi.org. Written by `ctzd ci site` from the platform's settings.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Downloads = '{{downloads}}'
$Index = '{{index_url}}'
$Bin = Join-Path $HOME '.local\bin'

function Add-UserPath([string] $Folder) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not ($current -split ';' | Where-Object { $_ -eq $Folder })) {
        [Environment]::SetEnvironmentVariable('Path', "$Folder;$current", 'User')
    }
    if (-not ($env:Path -split ';' | Where-Object { $_ -eq $Folder })) {
        $env:Path = "$Folder;$env:Path"
    }
}

# 1. uv, the Python tool manager, into ~/.local/bin.
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $zip = Join-Path $env:TEMP 'ctzd-uv.zip'
    Invoke-WebRequest -Uri "$Downloads/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $Bin -Force
    Remove-Item $zip
}
Add-UserPath $Bin
# `ctzd setup` unpacks a private git here when the desktop has none; agents may need it for a merge conflict.
Add-UserPath (Join-Path $env:LOCALAPPDATA 'ctzd\mingit\cmd')

# 2. Point uv at the internal package index and the internal mirror of Python builds, trusting the certificates
#    Windows trusts: the firm's own authority, and the proxy's when it inspects TLS.
$uvConfigDir = Join-Path $env:APPDATA 'uv'
New-Item -ItemType Directory -Force -Path $uvConfigDir | Out-Null
$uvConfig = @"
system-certs = true
python-install-mirror = "$Downloads/python"

[[index]]
name = "internal"
url = "$Index"
default = true
"@
# Without a byte-order mark, which Windows PowerShell's utf8 encoding writes and TOML does not allow.
[IO.File]::WriteAllText((Join-Path $uvConfigDir 'uv.toml'), $uvConfig)

# 3. ctzd itself, as a uv tool (its own Python and environment, upgraded in place), on the platform's Python: uv
#    would otherwise take the newest it can download.
uv tool install --python 3.14 --upgrade ctzd
if ($LASTEXITCODE -ne 0) { throw 'uv could not install ctzd from the internal package index.' }

# 4. Say where ctzd is: the agent's shell keeps the PATH it started with, so it finds `ctzd` by name only after a restart.
$Ctzd = Join-Path $Bin 'ctzd.exe'
Write-Output "ctzd is installed at $Ctzd. If ``ctzd`` is not found by name, run it by this full path until the agent restarts."

# 5. Check the rest (git, login, agent setup) and tell the agent what to do next.
& $Ctzd setup --json
