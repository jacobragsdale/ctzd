# The ctzd proof on a Windows desktop: install the CLI from local wheels, make one app of each template on each kit
# offline, run every gate in each, and drive a dash app's dev server and render. Run it on the Windows stand-in for
# the firm's desktops, with the wheels `uv build --wheel` makes for ctzd, ctzd-kit, ctzd-common, ctzd-fi, ctzd-mas and
# fi-risklib in ROOT\wheels:
#   powershell -NoProfile -ExecutionPolicy Bypass -File prove-windows.ps1 [-Root DIR]
# It writes ROOT\prove.log: each step's output, how long it took, and its exit code.
param([string]$Root = (Join-Path $env:USERPROFILE "ctzd-proof"))
$ErrorActionPreference = "Continue"
$log = Join-Path $Root "prove.log"
$wheels = Join-Path $Root "wheels"
$env:UV_SYSTEM_CERTS = "1"   # behind a TLS-inspecting proxy, uv trusts the system's certificates
$env:CTZD_HOME = Join-Path $Root "home"
$env:PYTHONUTF8 = "1"
function Step($name, [scriptblock]$body) {
    $started = Get-Date
    "=== $name" | Out-File -Append -Encoding utf8 $log
    & $body 2>&1 | Out-String | Out-File -Append -Encoding utf8 $log
    "--- $name took $([int]((Get-Date) - $started).TotalSeconds) s, exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
}
Remove-Item -Recurse -Force (Join-Path $Root "apps"), $env:CTZD_HOME -ErrorAction SilentlyContinue
"started $(Get-Date -Format o)" | Out-File -Encoding utf8 $log
# Unreleased wheels keep one version number while they change: drop uv's copies so each run installs these.
Step "clean uv cache" { uv cache clean ctzd ctzd-kit ctzd-common ctzd-fi ctzd-mas fi-risklib }
Step "install ctzd" { uv tool install --force --python 3.14 (Get-ChildItem (Join-Path $wheels "ctzd-*.whl") | Select-Object -First 1).FullName }
$ctzd = Join-Path (uv tool dir --bin) "ctzd.exe"
# The bundled platform config, with the internal index swapped for the local wheels plus PyPI.
$bundled = Join-Path (uv tool dir) "ctzd\Lib\site-packages\ctzd_cli\platform.toml"
$env:CTZD_PLATFORM_CONFIG = Join-Path $Root "platform.toml"
$index = ($wheels -replace "\\", "/")
$config = (Get-Content -Raw $bundled) + "`n[build_overrides]`npython_index = [{name = `"local`", url = `"$index`", format = `"flat`"}, {name = `"pypi`", url = `"https://pypi.org/simple`", default = true}]`n"
[IO.File]::WriteAllText($env:CTZD_PLATFORM_CONFIG, $config)  # no byte-order mark, which TOML refuses
Step "ctzd version" { & $ctzd --version }
foreach ($sbu in "fi", "mas") {
    foreach ($kind in "dash", "job") {
        $app = "win-$sbu-$kind"
        Step "new $app" { & $ctzd new $app --sbu $sbu --kind $kind --offline --owner e2e@contoso.com --dir (Join-Path $Root "apps") --json }
        Push-Location (Join-Path $Root "apps\$app")
        Step "check $app" { & $ctzd check --json }
        Step "git status $app" { git status --porcelain }
        if ($kind -eq "dash") {
            Step "dev $app" { & $ctzd dev --fake --json }
            $port = (Get-Content .ctzd\dev.json | ConvertFrom-Json).port
            Step "health $app" { (Invoke-WebRequest -UseBasicParsing -NoProxy "http://localhost:$port/health/ready").StatusCode }
            Step "render $app" { & $ctzd render / --json }
            Step "stop $app" { & $ctzd dev --stop --json }
        } else {
            Step "run $app" { & $ctzd run daily_positions --local --json }
        }
        Pop-Location
    }
}
"finished $(Get-Date -Format o)" | Out-File -Append -Encoding utf8 $log
