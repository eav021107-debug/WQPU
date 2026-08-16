$ErrorActionPreference = 'Stop'
$raw = 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main'
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
$bin = Join-Path $root 'bin'
$join = $env:WQPU_JOIN

function Find-Python {
  foreach ($cmd in @('py','python','python3')) {
    $f = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $f) { continue }
    try {
      if ($cmd -eq 'py') {
        & $f.Source -3 -c "import sys; assert sys.version_info >= (3,10)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @{Exe=$f.Source; Extra='-3'} }
      } else {
        & $f.Source -c "import sys; assert sys.version_info >= (3,10)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @{Exe=$f.Source; Extra=''} }
      }
    } catch {}
  }
  return $null
}

$py = Find-Python
if (-not $py) { throw 'WQPU needs Python 3.10+.' }
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) { throw 'WQPU needs OpenSSL in PATH.' }

New-Item -ItemType Directory -Force -Path $root,$bin | Out-Null
$script = Join-Path $root 'wqpu.py'
Invoke-WebRequest -UseBasicParsing "$raw/wqpu.py" -OutFile $script
$launcher = Join-Path $bin 'wqpu.cmd'
$exe=$py.Exe; $extra=$py.Extra
Set-Content -Encoding ASCII -Path $launcher -Value "@echo off`r`n`"$exe`" $extra `"$script`" %*"

$userPath=[Environment]::GetEnvironmentVariable('Path','User')
if (-not (($userPath -split ';') -contains $bin)) {
  [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $bin).TrimStart(';')), 'User')
}
$env:Path="$bin;$env:Path"

Write-Host 'WQPU installed. Starting this computer as an equal peer...' -ForegroundColor Green
if ([string]::IsNullOrWhiteSpace($join)) {
  & $launcher
} else {
  & $launcher --join $join
}
