# IWXA Browser 0.2 — installed NSIS lifecycle test, per-user Windows only.
# Runs in an ephemeral GitHub-hosted Windows runner, never on user machines.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path ".").Path
$nsis = @(Get-ChildItem "$root\browser-lab\src-tauri\target\release\bundle\nsis\*.exe" -File)
if ($nsis.Count -ne 1) { throw "Expected exactly one Windows NSIS setup; found $($nsis.Count)" }
$setup = $nsis[0].FullName
$sha256 = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLowerInvariant()
if ($nsis[0].Length -lt 1000000) { throw "NSIS setup unexpectedly small" }
$reportDir = Join-Path $root "artifacts\browser-lab\windows-installer"
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$report = [ordered]@{
    branch = $env:GITHUB_REF_NAME
    head = $env:GITHUB_SHA
    setupName = $nsis[0].Name
    setupSize = $nsis[0].Length
    sha256 = $sha256
    installMode = "currentUser"
    source = "NSIS, release Tauri with bundled patched Obscura"
    signed = $false
    checks = [ordered]@{}
}
$sentinel = Join-Path $env:LOCALAPPDATA "ObscuraBrowserLab\profile-v1\installer-lifecycle-sentinel.txt"
$createdSentinel = $false
try {
    $registryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    $start = Start-Process -FilePath $setup -ArgumentList "/S" -Wait -PassThru
    if ($start.ExitCode -ne 0) { throw "Silent per-user NSIS install exited $($start.ExitCode)" }
    $match = @(Get-ChildItem $registryPath | ForEach-Object { Get-ItemProperty $_.PSPath } |
        Where-Object { $_.DisplayName -eq "IWXA Browser 0.2 Preview" -and $_.DisplayVersion -eq "0.2.0" })
    if ($match.Count -ne 1) { throw "Installed version not registered exactly once in HKCU: count=$($match.Count)" }
    $app = $match[0]
    $report.checks.HKCURegistration = "PASS"
    $installDir = [string]$app.InstallLocation
    if ([string]::IsNullOrWhiteSpace($installDir)) {
        $installDir = Join-Path $env:LOCALAPPDATA "Programs\IWXA Browser 0.2 Preview"
    }
    $installDir = [IO.Path]::GetFullPath($installDir.Trim('"'))
    if (-not $installDir.StartsWith([IO.Path]::GetFullPath($env:LOCALAPPDATA), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Installer did not use a per-user LocalAppData location"
    }
    if (-not (Test-Path $installDir)) { throw "Installed application directory not found" }
    $appExe = Join-Path $installDir "obscura-browser-lab.exe"
    if (-not (Test-Path $appExe)) { throw "Installed Tauri application executable missing" }
    $engine = @(Get-ChildItem $installDir -File -Filter "obscura.exe" -Recurse)
    if ($engine.Count -ne 1) { throw "Exactly one bundled Obscura engine expected, found $($engine.Count)" }
    $expected = (Get-Content "$root\browser-lab\src-tauri\resources\obscura\obscura.sha256" -Raw).Trim()
    $actual = (Get-FileHash $engine[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -ne $actual) { throw "Installed Obscura SHA-256 differs from tested bundled source" }
    $report.checks.BundledEngineAndChecksum = "PASS"
    $report.checks.InstalledBinary = "PASS"
    $report.checks.UserOnlyInstall = "PASS"

    # Real installed release, not debug binary or fixture, without site access.
    $config = Get-Content "$root\browser-lab\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json
    $expectedTitle = [string]$config.app.windows[0].title
    $live = & "$root\browser-lab\tests\windows_installed_smoke.ps1" -AppExe $appExe -EngineExe $engine[0].FullName -ExpectedTitle $expectedTitle
    foreach ($name in @("InstalledReleaseWindow", "InstalledOwnedObscuraProcess", "NormalCloseCleansOwnedEngine")) {
        if ($live.$name -ne "PASS") { throw "Installed release verification missing $name" }
        $report.checks[$name] = "PASS"
    }

    # Preserving the profile is deliberate. Never delete user workspace data
    # without a separate informed request and backup.
    New-Item -ItemType Directory -Path (Split-Path $sentinel) -Force | Out-Null
    if (Test-Path $sentinel) { throw "Installer sentinel already exists" }
    Set-Content $sentinel "Do not delete application data on uninstall" -Encoding UTF8
    $createdSentinel = $true

    $uninstall = [string]$app.UninstallString
    if ([string]::IsNullOrWhiteSpace($uninstall)) { throw "Registered uninstaller missing" }
    # Tauri NSIS registers a quoted executable path, optionally with flags.
    $uninstaller = if ($uninstall -match '^"([^"]+)"') { $Matches[1] } elseif ($uninstall -match '^(.*?\.exe)') { $Matches[1] } else { "" }
    if (-not (Test-Path $uninstaller)) { throw "Registered uninstaller executable missing" }
    $remove = Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -PassThru
    if ($remove.ExitCode -ne 0) { throw "NSIS uninstall exited $($remove.ExitCode)" }
    $deadline = (Get-Date).AddSeconds(40)
    do {
        $registered = @(Get-ChildItem $registryPath | ForEach-Object { Get-ItemProperty $_.PSPath } |
            Where-Object { $_.DisplayName -eq "IWXA Browser 0.2 Preview" })
        if ($registered.Count -eq 0 -and -not (Test-Path $appExe) -and -not (Test-Path $engine[0].FullName)) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if ($registered.Count -ne 0) { throw "Uninstall registry entry remains" }
    if (Test-Path $appExe) { throw "Tauri application remains after uninstall" }
    if (Test-Path $engine[0].FullName) { throw "Obscura remains after uninstall" }
    $report.checks.UninstallBinaryAndHKCU = "PASS"
    if (-not (Test-Path $sentinel)) { throw "Uninstaller deleted the user profile without consent" }
    $report.checks.UserProfilePreserved = "PASS"
    $report.status = "PASS_INSTALL_AND_UNINSTALL"
}
catch {
    $report.status = "FAIL"
    $report.error = $_.Exception.Message
    throw
}
finally {
    if ($createdSentinel -and (Test-Path $sentinel)) {
        Remove-Item $sentinel -Force
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $reportDir "report.json") -Encoding UTF8
    Write-Host ($report | ConvertTo-Json -Depth 8)
}
