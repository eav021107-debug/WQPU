$ErrorActionPreference = 'Stop'
$repo = 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main'
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $root, $bin | Out-Null

function Find-Python {
    foreach ($cmd in @('py','python','python3')) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        try {
            if ($cmd -eq 'py') {
                & $found.Source -3 -c "import sys; assert sys.version_info >= (3,10)" 2>$null
                if ($LASTEXITCODE -eq 0) { return [pscustomobject]@{ Exe = $found.Source; Extra = '-3' } }
            } else {
                & $found.Source -c "import sys; assert sys.version_info >= (3,10)" 2>$null
                if ($LASTEXITCODE -eq 0) { return [pscustomobject]@{ Exe = $found.Source; Extra = '' } }
            }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host 'WQPU: Python 3.10+ not found. Installing Python for the current user...'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'Python is missing and winget is unavailable. Install Python 3.10+ once, then run the WQPU command again.'
    }
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    $py = Find-Python
    if (-not $py) { throw 'Python installation finished but was not found in this shell. Open a new PowerShell window and run the WQPU command again.' }
}

$script = Join-Path $root 'wqpu.py'
Invoke-WebRequest -UseBasicParsing "$repo/wqpu.py" -OutFile $script

$launcher = Join-Path $bin 'wqpu.cmd'
$pyExe = $py.Exe
$pyExtra = $py.Extra
$cmdText = @"
@echo off
"$pyExe" $pyExtra "$script" %*
"@
Set-Content -Path $launcher -Value $cmdText -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable('Path','User')
if (-not (($userPath -split ';') -contains $bin)) {
    $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $bin } else { "$userPath;$bin" }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
}
$env:Path = "$bin;$env:Path"

Write-Host ''
Write-Host 'WQPU installed.' -ForegroundColor Green
Write-Host 'This window will now join the cluster. Keep it open while this PC contributes resources.'
Write-Host 'If Windows Firewall asks, allow Private networks only.'
Write-Host ''
& $launcher start
