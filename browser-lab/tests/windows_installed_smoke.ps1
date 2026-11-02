# Verify installed release desktop lifecycle; never substitute engine or site data.
param(
    [Parameter(Mandatory=$true)][string]$AppExe,
    [Parameter(Mandatory=$true)][string]$EngineExe,
    [Parameter(Mandatory=$true)][string]$ExpectedTitle
)
$ErrorActionPreference = "Stop"
$app = $null
$enginePid = $null
try {
    $app = Start-Process -FilePath $AppExe -WorkingDirectory (Split-Path $AppExe) -PassThru
    $deadline = (Get-Date).AddSeconds(70)
    $hasWindow = $false
    while ((Get-Date) -lt $deadline) {
        $app.Refresh()
        if ($app.HasExited) { throw "Installed release exited during startup: $($app.ExitCode)" }
        $current = Get-Process -Id $app.Id -ErrorAction Stop
        if ($current.MainWindowTitle -ceq $ExpectedTitle) {
            $hasWindow = $true
            break
        }
        Start-Sleep -Milliseconds 350
    }
    if (-not $hasWindow) { throw "Installed Tauri window not visible with configured title" }
    $deadline = (Get-Date).AddSeconds(40)
    $children = @()
    do {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($app.Id)" |
            Where-Object { $_.Name -ieq "obscura.exe" })
        if ($children.Count -eq 1) { break }
        Start-Sleep -Milliseconds 350
    } while ((Get-Date) -lt $deadline)
    if ($children.Count -ne 1) { throw "Expected one owned Obscura, found $($children.Count)" }
    if ([string]::IsNullOrWhiteSpace([string]$children[0].ExecutablePath)) {
        throw "Owned engine executable path unavailable for verification"
    }
    $actual = [IO.Path]::GetFullPath([string]$children[0].ExecutablePath)
    $expected = [IO.Path]::GetFullPath($EngineExe)
    if (-not $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Installed app launched a different engine: $actual"
    }
    $enginePid = [int]$children[0].ProcessId
    # Only about:blank is open. The normal window close must work.
    if (-not $app.CloseMainWindow()) { throw "Normal desktop close not accepted" }
    if (-not $app.WaitForExit(15000)) { throw "Installed app failed normal close" }
    $deadline = (Get-Date).AddSeconds(12)
    do {
        if (-not (Get-Process -Id $enginePid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 350
    } while ((Get-Date) -lt $deadline)
    if (Get-Process -Id $enginePid -ErrorAction SilentlyContinue) {
        throw "Owned Obscura remained after Tauri normal shutdown"
    }
    [pscustomobject]@{
        InstalledReleaseWindow = "PASS"
        InstalledOwnedObscuraProcess = "PASS"
        NormalCloseCleansOwnedEngine = "PASS"
    }
}
finally {
    # Forced termination only for CI failure cleanup, never counted as PASS.
    if ($app) {
        try {
            $app.Refresh()
            if (-not $app.HasExited) {
                Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
    if ($enginePid) {
        Stop-Process -Id $enginePid -Force -ErrorAction SilentlyContinue
    }
}
