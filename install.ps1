$ErrorActionPreference = 'Stop'
$repo = 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main'
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $root, $bin | Out-Null

function Find-Python {
    foreach ($cmd in @('python','python3','py')) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            if ($cmd -eq 'py') { return @($found.Source, '-3') }
            return @($found.Source)
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host 'WQPU: Python not found. Installing Python for the current user...'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'Python is missing and winget is unavailable. Install Python 3.10+ once, then run the WQPU command again.'
    }
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    $py = Find-Python
    if (-not $py) { throw 'Python installation finished but python was not found in PATH. Open a new PowerShell window and run the WQPU command again.' }
}

$script = Join-Path $root 'wqpu.py'
Invoke-WebRequest -UseBasicParsing "$repo/wqpu.py" -OutFile $script

$launcher = Join-Path $bin 'wqpu.cmd'
$pyExe = $py[0]
$pyExtra = if ($py.Count -gt 1) { $py[1] } else { '' }
$cmdText = "@echo off`r`n`"$pyExe`" $pyExtra `"$script`" %*`r`n"
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
