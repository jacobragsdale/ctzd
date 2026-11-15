# The installer proof on a Windows desktop: a person who has never had uv, Python, git or ctzd installs ctzd the way the
# bootstrap skill says (`powershell -c "irm SITE/install.ps1 | iex"`), and then makes and checks an app. The platform's
# site and internal index are stood in for by scripts/site_standin.py on localhost, serving the install.ps1 that
# `ctzd ci site` writes from a test platform config whose site is the stand-in and whose index is its /simple. Run it as
# an administrator on the Windows stand-in for the firm's desktops, with the wheels `uv build --wheel` makes for ctzd,
# ctzd-kit, ctzd-common, ctzd-fi, ctzd-mas and fi-risklib in ROOT\wheels and scripts/site_standin.py copied to ROOT:
#   powershell -NoProfile -ExecutionPolicy Bypass -File prove-install.ps1 [-Root C:\ctzd-install-proof] [-Port 8765] [-Keep]
# It makes a new local user with a random password, runs the install and the app as that user through a scheduled
# task (with its own profile, so no uv, Python, uv.toml or ctzd of the administrator's is in reach), hides the
# machine's git from it so `ctzd setup` must fetch MinGit, and then removes the user, its profile, the task and the
# stand-in again (unless -Keep). It writes ROOT\prove-install.log: each step, the user's own log, every request the
# stand-in answered, and one PASS or FAIL line for each check, the last line saying whether all passed.
# The user gets the administrator's proxy settings (Internet Settings in HKCU), which a firm's desktops get from policy;
# the stand-in's downloads are redirects to GitHub and PyPI, which only the proxy reaches.
param([string]$Root = "C:\ctzd-install-proof", [int]$Port = 8765, [string]$User = "ctzdfresh", [switch]$Keep)
$ErrorActionPreference = "Continue"
$log = Join-Path $Root "prove-install.log"
$wheels = Join-Path $Root "wheels"
$out = Join-Path $Root "out"
$site = "http://localhost:$Port"
$task = "ctzd-install-proof"
$env:UV_SYSTEM_CERTS = "1"   # for the administrator's own uv, which runs `ctzd ci site` and the stand-in
function Say($line) { "$(Get-Date -Format HH:mm:ss) $line" | Out-File -Append -Encoding utf8 $log }
function Step($name, [scriptblock]$body) {
    $started = Get-Date
    Say "=== $name"
    & $body 2>&1 | Out-String | Out-File -Append -Encoding utf8 $log
    Say "--- $name took $([int]((Get-Date) - $started).TotalSeconds) s, exit $LASTEXITCODE"
}

Remove-Item -Recurse -Force (Join-Path $Root "site"), $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null
"started $(Get-Date -Format o)" | Out-File -Encoding utf8 $log
$wheel = (Get-ChildItem (Join-Path $wheels "ctzd-*.whl") | Select-Object -First 1).FullName

# 1. The test platform config: the bundled one, with the stand-in as the site and its /simple as the only index.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($wheel)
$bundled = (New-Object IO.StreamReader(($zip.Entries | Where-Object FullName -eq "ctzd_cli/platform.toml").Open())).ReadToEnd()
$zip.Dispose()
$config = ($bundled -replace '(?m)^site = .*$', "site = `"$site`"") + "`n[build_overrides]`npython_index = [{name = `"standin`", url = `"$site/simple`", default = true}]`n"
$platform = Join-Path $Root "platform.toml"
[IO.File]::WriteAllText($platform, $config)  # no byte-order mark, which TOML refuses

# 2. What the site serves, written by the CLI from that config; then the stand-in.
$env:CTZD_PLATFORM_CONFIG = $platform
Step "ctzd ci site" { uv tool run --python 3.14 --from $wheel ctzd ci site --out (Join-Path $Root "site") --json }
Remove-Item Env:\CTZD_PLATFORM_CONFIG
$standinLog = Join-Path $Root "standin.log"
$standin = Start-Process -PassThru -WindowStyle Hidden -RedirectStandardError $standinLog -RedirectStandardOutput (Join-Path $Root "standin.out") -FilePath uv -ArgumentList @(
    "run", "--python", "3.14", "--no-project", (Join-Path $Root "site_standin.py"), "--site", (Join-Path $Root "site"), "--wheels", $wheels, "--port", $Port)
$up = $false
foreach ($i in 1..60) {
    try { $up = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$site/install.ps1").StatusCode -eq 200 } catch { $up = $false }
    if ($up) { break }
    Start-Sleep 1
}
Say "stand-in at $site answering: $up"

# 3. The fresh user. Performance Log Users holds the right to log on as a batch job, which a scheduled task needs.
$password = -join ((1..20 | ForEach-Object { [char](Get-Random -Minimum 33 -Maximum 127) }) + "aA1!")
$secure = ConvertTo-SecureString $password -AsPlainText -Force
Remove-LocalUser -Name $User -ErrorAction SilentlyContinue
New-LocalUser -Name $User -Password $secure -PasswordNeverExpires -AccountNeverExpires -Description "ctzd installer proof; removed when it ends" | Out-Null
Add-LocalGroupMember -Group "Users" -Member $User -ErrorAction SilentlyContinue
Add-LocalGroupMember -SID "S-1-5-32-559" -Member $User
icacls $Root /grant "${User}:(OI)(CI)RX" | Out-Null
icacls $out /grant "${User}:(OI)(CI)M" | Out-Null
$inet = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
# Windows PowerShell's web requests follow WinINet, which reads this binary value rather than ProxyServer.
$connection = ((Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings\Connections").DefaultConnectionSettings | ForEach-Object { $_.ToString() }) -join ","

# 4. What the user runs: the installer as the bootstrap skill says, then the checks, each writing a line to its log.
$asUser = @"
`$ErrorActionPreference = 'Continue'
`$log = '$out\user.log'
function Say(`$line) { "`$(Get-Date -Format HH:mm:ss) `$line" | Out-File -Append -Encoding utf8 `$log }
function Check(`$what, `$ok, `$detail) { if (`$ok) { Say "PASS `$what `$detail" } else { Say "FAIL `$what `$detail" } }
`$inet = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
Set-ItemProperty `$inet ProxyEnable $($inet.ProxyEnable) -Type DWord
Set-ItemProperty `$inet ProxyServer '$($inet.ProxyServer)'
Set-ItemProperty `$inet ProxyOverride '$($inet.ProxyOverride)'
New-Item -Force "`$inet\Connections" | Out-Null
Set-ItemProperty "`$inet\Connections" DefaultConnectionSettings ([byte[]]@($connection)) -Type Binary
Say "user `$env:USERNAME, profile `$env:USERPROFILE"
Check 'no uv before' (-not (Get-Command uv -ErrorAction SilentlyContinue)) ''
Check 'no ctzd before' (-not (Get-Command ctzd -ErrorAction SilentlyContinue)) ''
Check 'no uv.toml before' (-not (Test-Path "`$env:APPDATA\uv\uv.toml")) ''
# The machine's git is on every user's PATH: hide it, as on a desktop without git.
`$env:Path = (`$env:Path -split ';' | Where-Object { `$_ -and `$_ -notlike '*\Git\cmd*' }) -join ';'
Check 'no git before' (-not (Get-Command git -ErrorAction SilentlyContinue)) ''
`$env:CTZD_PLATFORM_CONFIG = '$platform'
[Environment]::SetEnvironmentVariable('CTZD_PLATFORM_CONFIG', '$platform', 'User')
`$started = Get-Date
powershell -c "irm $site/install.ps1 | iex" *> '$out\install.out'
Say "installer exit `$LASTEXITCODE, `$([int]((Get-Date) - `$started).TotalSeconds) s"
`$bin = Join-Path `$HOME '.local\bin'
`$env:Path = "`$bin;`$env:LOCALAPPDATA\ctzd\mingit\cmd;`$env:Path"
Check 'uv in ~/.local/bin' (Test-Path "`$bin\uv.exe") (& "`$bin\uv.exe" --version)
`$bytes = [IO.File]::ReadAllBytes("`$env:APPDATA\uv\uv.toml")
Check 'uv.toml has no byte-order mark' (-not (`$bytes[0] -eq 0xEF -and `$bytes[1] -eq 0xBB -and `$bytes[2] -eq 0xBF)) ''
`$listed = & uv tool list 2>&1 | Out-String
Check 'uv reads uv.toml' (-not (`$listed -match 'Failed to parse')) `$listed.Trim()
`$pythons = & uv python list --only-installed 2>&1 | Out-String
Check 'Python 3.14 installed by uv' (`$pythons -match 'cpython-3\.14') `$pythons.Trim()
Check 'user PATH has ~/.local/bin' ([Environment]::GetEnvironmentVariable('Path', 'User') -like "*`$bin*") ''
`$setup = & "`$bin\ctzd.exe" setup --json | Out-String
`$setup | Out-File -Encoding utf8 '$out\setup.json'
`$checks = (`$setup | ConvertFrom-Json).checks
Say "ctzd setup: `$((`$checks | ForEach-Object { "`$(`$_.name)=`$(`$_.state)" }) -join ', ')"
Check 'ctzd setup reports the GitHub login missing' ((`$checks | Where-Object name -eq 'login').state -eq 'missing') ''
Check 'MinGit installed for this user' (Test-Path "`$env:LOCALAPPDATA\ctzd\mingit\cmd\git.exe") ((`$checks | Where-Object name -eq 'git').detail)
`$started = Get-Date
`$made = & "`$bin\ctzd.exe" new fresh-app --sbu fi --kind dash --offline --owner a@contoso.com --dir "`$HOME\apps" --json | Out-String
`$made | Out-File -Encoding utf8 '$out\new.json'
Check 'ctzd new' ((`$made | ConvertFrom-Json).ok) "`$([int]((Get-Date) - `$started).TotalSeconds) s"
Set-Location "`$HOME\apps\fresh-app"
`$started = Get-Date
`$checked = & "`$bin\ctzd.exe" check --json | Out-String
`$checked | Out-File -Encoding utf8 '$out\check.json'
Check 'ctzd check' ((`$checked | ConvertFrom-Json).ok) "`$([int]((Get-Date) - `$started).TotalSeconds) s"
`$index = Get-Content pyproject.toml | Select-String 'standin|$site'
Check 'the app installs from the stand-in index' ([bool]`$index) "`$index"
Say 'done'
"@
[IO.File]::WriteAllText((Join-Path $Root "as-user.ps1"), $asUser)

# 5. Run it as that user, with its profile loaded, and wait.
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Root 'as-user.ps1')`""
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -User $User -Password $password -RunLevel Limited | Out-Null
Start-ScheduledTask -TaskName $task
Say "task started as $User"
$deadline = (Get-Date).AddMinutes(50)
do {
    Start-Sleep 10
    $state = (Get-ScheduledTask -TaskName $task).State
    $done = (Test-Path "$out\user.log") -and (Select-String -Quiet -Path "$out\user.log" -Pattern " done$")
} while (-not $done -and $state -eq "Running" -and (Get-Date) -lt $deadline)
Say "task state $state, last result $((Get-ScheduledTaskInfo -TaskName $task).LastTaskResult)"
Say "=== the user's log"
Get-Content "$out\user.log" -ErrorAction SilentlyContinue | Out-File -Append -Encoding utf8 $log
Say "=== the installer's output"
Get-Content "$out\install.out" -ErrorAction SilentlyContinue | Out-File -Append -Encoding utf8 $log
Say "=== ctzd setup's output, after the installer's"
Get-Content "$out\setup.json" -ErrorAction SilentlyContinue | Out-File -Append -Encoding utf8 $log
Say "=== ctzd new's output"
Get-Content "$out\new.json" -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch '"guide"' } | Out-File -Append -Encoding utf8 $log
Say "=== ctzd check's output"
Get-Content "$out\check.json" -ErrorAction SilentlyContinue | Out-File -Append -Encoding utf8 $log
Say "=== what the stand-in answered"
Get-Content $standinLog -ErrorAction SilentlyContinue | Out-File -Append -Encoding utf8 $log
$mirror = Select-String -Quiet -Path $standinLog -Pattern 'GET /downloads/python/\S+ HTTP/1\.1" 302'
if ($mirror) { Say "PASS Python came through the site's mirror" } else { Say "FAIL Python came through the site's mirror" }
$uvZip = Select-String -Quiet -Path $standinLog -Pattern 'GET /downloads/uv-x86_64-pc-windows-msvc\.zip HTTP/1\.1" 302'
if ($uvZip) { Say "PASS uv came from the site" } else { Say "FAIL uv came from the site" }

# 6. Clean up: the task, the stand-in, the user and its profile, and what this run wrote.
if (-not $Keep) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*site_standin.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $standin.Id -Force -ErrorAction SilentlyContinue
    $sid = (Get-LocalUser -Name $User).SID.Value
    Remove-LocalUser -Name $User
    # Windows can keep a task's user's registry loaded after the task ends; the profile goes once it lets go.
    $gone = $false
    foreach ($i in 1..24) {
        try { Get-CimInstance Win32_UserProfile | Where-Object SID -eq $sid | Remove-CimInstance -ErrorAction Stop; $gone = $true; break } catch { Start-Sleep 5 }
    }
    if ($gone) { Say "removed the profile of $User" } else {
        # Still loaded: remove it, and any earlier run's, when the machine next starts, then remove this task too.
        $cleanup = "C:\ProgramData\ctzd-install-proof-cleanup.ps1"
        $body = "Get-CimInstance Win32_UserProfile | Where-Object { `$_.LocalPath -like 'C:\Users\$User*' } | Remove-CimInstance`n" +
            "Unregister-ScheduledTask -TaskName '$task-cleanup' -Confirm:`$false`nRemove-Item -Force '$cleanup'`n"
        [IO.File]::WriteAllText($cleanup, $body)
        $boot = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File $cleanup"
        Register-ScheduledTask -TaskName "$task-cleanup" -Action $boot -Trigger (New-ScheduledTaskTrigger -AtStartup) -User "SYSTEM" -RunLevel Highest -Force | Out-Null
        Say "the profile of $User is still loaded by Windows: task $task-cleanup removes it at the next start"
    }
    Remove-Item -Recurse -Force (Join-Path $Root "site"), $out, $platform, (Join-Path $Root "as-user.ps1"), $standinLog, (Join-Path $Root "standin.out") -ErrorAction SilentlyContinue
    Say "removed $User, the task and the stand-in"
}
$failed = @(Select-String -Path $log -Pattern '\bFAIL ' | ForEach-Object Line)
if ($failed.Count -eq 0) { Say "PASS every check" } else { Say "FAIL $($failed.Count) check(s)" }
"finished $(Get-Date -Format o)" | Out-File -Append -Encoding utf8 $log
