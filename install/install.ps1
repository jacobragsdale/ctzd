# Installs ctzd for the current user, with no admin rights. An AI agent runs this; the person never has to:
#   powershell -c "irm https://ctzd.contoso.com/install.ps1 | iex"
# Every download comes from the platform host, so desktops need no access to github.com or pypi.org.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Platform = 'https://ctzd.contoso.com'
$Index = 'https://pypi.contoso.com/simple'
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
    Invoke-WebRequest -Uri "$Platform/downloads/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $Bin -Force
    Remove-Item $zip
}
Add-UserPath $Bin
# `ctzd setup` unpacks a private git here when the desktop has none; agents may need it for a merge conflict.
Add-UserPath (Join-Path $env:LOCALAPPDATA 'ctzd\mingit\cmd')
# `ctzd setup` also puts Node here (from the platform host's zip, or the Node wheel through uv) and pnpm, from the
# internal npm registry, for a React app's pages. Unverified on Windows: see docs/spikes/desktop-toolchain.md.
Add-UserPath (Join-Path $env:LOCALAPPDATA 'ctzd\node')
Add-UserPath (Join-Path $env:LOCALAPPDATA 'ctzd\npm')

# 2. Point uv at the internal package index and the internal mirror of Python builds.
$uvConfigDir = Join-Path $env:APPDATA 'uv'
New-Item -ItemType Directory -Force -Path $uvConfigDir | Out-Null
@"
python-install-mirror = "$Platform/downloads/python"

[[index]]
name = "internal"
url = "$Index"
default = true
"@ | Set-Content -Path (Join-Path $uvConfigDir 'uv.toml') -Encoding utf8

# 3. ctzd itself, as a uv tool (its own Python and environment, upgraded in place).
uv tool install --upgrade ctzd
if ($LASTEXITCODE -ne 0) { throw 'uv could not install ctzd from the internal package index.' }

# 4. Say where ctzd is: the agent's shell keeps the PATH it started with, so it finds `ctzd` by name only after a restart.
$Ctzd = Join-Path $Bin 'ctzd.exe'
Write-Output "ctzd is installed at $Ctzd. If ``ctzd`` is not found by name, run it by this full path until the agent restarts."

# 5. Check the rest (git, Node, pnpm, login, agent setup) and tell the agent what to do next.
& $Ctzd setup --json
