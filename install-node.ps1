$ErrorActionPreference = 'Stop'

$token = $env:WQPU_JOIN
if ([string]::IsNullOrWhiteSpace($token)) {
  throw 'Set $env:WQPU_JOIN to the token printed by the relay installer.'
}

$raw = 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main'
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $root,$bin | Out-Null

function Find-Python {
  foreach ($cmd in @('py','python','python3')) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    try {
      if ($cmd -eq 'py') {
        & $found.Source -3 -c "import sys; assert sys.version_info >= (3,10)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @{Exe=$found.Source; Extra='-3'} }
      } else {
        & $found.Source -c "import sys; assert sys.version_info >= (3,10)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @{Exe=$found.Source; Extra=''} }
      }
    } catch {}
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.10+ first.'
  }
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
  $py = Find-Python
  if (-not $py) { throw 'Open a new PowerShell and run the command again.' }
}

$script = Join-Path $root 'wqpu.py'
Invoke-WebRequest -UseBasicParsing "$raw/wqpu.py" -OutFile $script

$launcher = Join-Path $bin 'wqpu.cmd'
$exe = $py.Exe
$extra = $py.Extra
Set-Content -Encoding ASCII -Path $launcher -Value "@echo off`r`n`"$exe`" $extra `"$script`" %*"

$userPath = [Environment]::GetEnvironmentVariable('Path','User')
if (-not (($userPath -split ';') -contains $bin)) {
  [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $bin).TrimStart(';')), 'User')
}
$env:Path = "$bin;$env:Path"

& $launcher join $token
Write-Host 'WQPU installed. This computer is an equal peer.' -ForegroundColor Green
Write-Host 'Keep this window open to contribute.'
Write-Host 'In another PowerShell: wqpu ask "your question"'
& $launcher start
