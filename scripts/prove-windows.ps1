# The ctzd proof on a Windows desktop: install the CLI from local wheels, make one app of each template on each kit
# offline, run every gate in each, and drive a dash app's dev server: its health, the reload after an edit, a syntax
# error and its fix, render, and every page in Edge (scripts/browser_check.py --channel msedge --reload). Run it on the
# Windows stand-in for the firm's desktops, with the wheels `uv build --wheel` makes for ctzd, ctzd-kit, ctzd-common,
# ctzd-fi, ctzd-mas and fi-risklib in ROOT\wheels, and scripts/browser_check.py copied to ROOT:
#   powershell -NoProfile -ExecutionPolicy Bypass -File prove-windows.ps1 [-Root DIR] [-NoBrowser]
# It writes ROOT\prove.log: each step's output, how long it took, and its exit code, then one PASS or FAIL line for
# each check that has no exit code of its own (the reload's), and the browser's screenshots under ROOT\browser\APP.
# Start it detached (Invoke-CimMethod Win32_Process Create) when driving it over ssh: a dev server started inside an
# ssh session ends with that session.
param([string]$Root = (Join-Path $env:USERPROFILE "ctzd-proof"), [switch]$NoBrowser)
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
function Say($line) { "$(Get-Date -Format HH:mm:ss.fff) $line" | Out-File -Append -Encoding utf8 $log }

# One request: its status and body, a 503 included (Windows PowerShell's Invoke-WebRequest throws on it), 0 when
# nothing answers. localhost is on the proxy's bypass list.
function Get-Http([string]$url) {
    try {
        $answer = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $url
        return @{ Status = [int]$answer.StatusCode; Body = [string]$answer.Content }
    } catch [System.Net.WebException] {
        $response = $_.Exception.Response
        if ($null -eq $response) { return @{ Status = 0; Body = $_.Exception.Message } }
        return @{ Status = [int]$response.StatusCode; Body = [string]$_.ErrorDetails.Message }
    }
}
# Wait until $test holds, up to $seconds; the seconds it took, or $null.
function Wait-For([scriptblock]$test, [int]$seconds = 90) {
    $started = Get-Date
    while (((Get-Date) - $started).TotalSeconds -lt $seconds) {
        if (& $test) { return [math]::Round(((Get-Date) - $started).TotalSeconds, 1) }
        Start-Sleep -Milliseconds 250
    }
    return $null
}
function Check($what, $took) {
    if ($null -eq $took) { Say "FAIL $what"; $script:failed += $what } else { Say "PASS $what in $took s" }
}

# The dev server reloads after an edit with no restart by hand, and comes back after a syntax error is fixed.
function Prove-Reload($ctzd, $port) {
    $base = "http://localhost:$port"
    $page = (Resolve-Path "src\app\pages\positions.py").Path
    $source = [IO.File]::ReadAllText($page)
    $shows = { param($name) (Get-Http "$base/_dash-layout").Body.Contains("`"$name`"") }
    Check "layout names the page Positions" (Wait-For { & $shows "Positions" } 30)
    # The shell's header lists every page by its name=, so a rename shows in /_dash-layout once the server reloads.
    [IO.File]::WriteAllText($page, $source.Replace('name="Positions"', 'name="Positions reloaded"'))
    Check "rename served after the edit" (Wait-For { & $shows "Positions reloaded" })
    [IO.File]::WriteAllText($page, $source)
    Check "rename undone after the edit back" (Wait-For { (& $shows "Positions") -and -not (& $shows "Positions reloaded") })
    # The case an agent hits: a syntax error in a page, then its fix.
    [IO.File]::WriteAllText($page, $source.Replace("dash.register_page(", "dash.register_page(("))
    Check "/health/ready is 503 while the page has a syntax error" (Wait-For { (Get-Http "$base/health/ready").Status -eq 503 })
    $ready = Get-Http "$base/health/ready"
    Say "/health/ready meanwhile: $($ready.Status) $($ready.Body)"
    Say "/_ctzd/dev meanwhile: $((Get-Http "$base/_ctzd/dev").Status)"
    Say "dev.log meanwhile: $((Get-Content .ctzd\dev.log | Select-String 'ctzd dev:|SyntaxError' | ForEach-Object Line) -join ' | ')"
    # An agent that runs ctzd dev again now is told what is wrong, and the server stays up waiting for the fix.
    $again = & $ctzd dev --fake --json | ConvertFrom-Json
    Say "ctzd dev while broken: ok=$($again.ok) $($again.summary)"
    if ($again.ok -or -not "$($again.output)".Contains("SyntaxError")) { Say "FAIL ctzd dev while broken names the SyntaxError"; $script:failed += "ctzd dev while broken" }
    $port = (Get-Content .ctzd\dev.json | ConvertFrom-Json).port
    $base = "http://localhost:$port"
    [IO.File]::WriteAllText($page, $source)
    Check "the app is back after the fix" (Wait-For { (Get-Http "$base/health/ready").Status -eq 200 -and (& $shows "Positions") })
    return $port
}

$script:failed = @()
Remove-Item -Recurse -Force (Join-Path $Root "apps"), (Join-Path $Root "browser"), $env:CTZD_HOME -ErrorAction SilentlyContinue
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
# The browser check's own environment: Playwright drives the installed Edge, so no browser is downloaded.
$browserPython = Join-Path $Root "browser-venv\Scripts\python.exe"
if (-not $NoBrowser) {
    Step "browser check environment" {
        uv venv --python 3.14 --allow-existing (Join-Path $Root "browser-venv")
        uv pip install --python $browserPython playwright pydantic
    }
}
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
            Step "health $app" { (Get-Http "http://localhost:$port/health/ready").Status }
            "=== reload $app" | Out-File -Append -Encoding utf8 $log
            $port = Prove-Reload $ctzd $port
            Step "render $app" { & $ctzd render / --json }
            Step "stop $app" { & $ctzd dev --stop --json }
            Step "git status after reload $app" { git status --porcelain }
            if (-not $NoBrowser) {
                Step "browser $app" { & $browserPython (Join-Path $Root "browser_check.py") . --out (Join-Path $Root "browser\$app") --ctzd $ctzd --channel msedge --reload }
            }
        } else {
            Step "run $app" { & $ctzd run daily_positions --local --json }
        }
        Pop-Location
    }
}
if ($script:failed.Count -eq 0) { Say "PASS every reload check" } else { Say "FAIL $($script:failed.Count): $($script:failed -join '; ')" }
"finished $(Get-Date -Format o)" | Out-File -Append -Encoding utf8 $log
