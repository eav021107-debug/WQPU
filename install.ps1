$ErrorActionPreference = 'Stop'
$raw = if ($env:WQPU_RAW_BASE) { $env:WQPU_RAW_BASE.TrimEnd('/') } else { 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main' }
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
$bin = Join-Path $root 'bin'
$join = $env:WQPU_JOIN
$expectedWqpu = 'WQPU 0.6.0-dev'
$cacheBuster = 'autopay-0.6.0-dev-r1'
$chainState = Join-Path $HOME '.wqpu\chain.json'

New-Item -ItemType Directory -Force -Path $root,$bin | Out-Null

function Test-Python($exe, $extra) {
  try {
    if ($extra) { & $exe $extra -c "import sys; raise SystemExit(0 if sys.version_info >= (3,6) else 1)" 2>$null }
    else { & $exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3,6) else 1)" 2>$null }
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Find-Python {
  $private = Join-Path $root 'python\python.exe'
  if ((Test-Path $private) -and (Test-Python $private '')) { return @{Exe=$private; Extra=''} }
  foreach ($cmd in @('py','python','python3')) {
    $f = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $f) { continue }
    if ($cmd -eq 'py') {
      if (Test-Python $f.Source '-3') { return @{Exe=$f.Source; Extra='-3'} }
    } elseif (Test-Python $f.Source '') {
      return @{Exe=$f.Source; Extra=''}
    }
  }
  return $null
}

function Install-PrivatePython {
  Write-Host 'WQPU: compatible Python not found; installing a private Python for WQPU only...'
  $target = Join-Path $root 'python'
  $installer = Join-Path $env:TEMP 'wqpu-python-installer.exe'
  $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
  if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe'
  }
  Invoke-WebRequest -UseBasicParsing $url -OutFile $installer
  $args = @('/quiet','InstallAllUsers=0',"TargetDir=$target",'Include_launcher=0','Include_pip=0','PrependPath=0','Shortcuts=0')
  $p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
  Remove-Item $installer -Force -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) { throw "Python installer failed with exit code $($p.ExitCode)." }
  $exe = Join-Path $target 'python.exe'
  if (-not (Test-Python $exe '')) { throw 'Private Python installation did not produce a usable Python.' }
}

$py = Find-Python
if (-not $py) {
  Install-PrivatePython
  $py = Find-Python
}
if (-not $py) { throw 'WQPU could not prepare Python 3.6+.' }

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) {
    $gitRoot = Split-Path (Split-Path $git.Source -Parent) -Parent
    $gitOpenSsl = Join-Path $gitRoot 'usr\bin'
    if (Test-Path (Join-Path $gitOpenSsl 'openssl.exe')) { $env:Path="$gitOpenSsl;$env:Path" }
  }
}
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
  throw 'WQPU needs OpenSSL. Install Git for Windows or OpenSSL, then run the same command again.'
}

Write-Host 'WQPU: downloading runtime...'
$pythonFiles = @('wqpu.py','wqpu_chain.py','wqpu_wallet.py','wqpu_session.py','wqpu_meter.py','wqpu_payments.py','wqpu_claim.py','wqpu_vouchers.py','wqpu_runtime.py','wqpu_autopay.py')
$files = $pythonFiles + @('network-config.json')
foreach ($file in $files) {
  Invoke-WebRequest -UseBasicParsing "$raw/$file`?installer=$cacheBuster" -OutFile (Join-Path $root $file)
}

$exe = $py.Exe
$extra = $py.Extra
$compileArgs = @()
if ($extra) { $compileArgs += $extra }
$compileArgs += @('-m','py_compile')
foreach ($file in $pythonFiles) { $compileArgs += (Join-Path $root $file) }
& $exe @compileArgs
if ($LASTEXITCODE -ne 0) { throw 'Downloaded WQPU runtime did not pass the Python compatibility check.' }

$configPath = Join-Path $root 'network-config.json'
try {
  $networkConfig = Get-Content -Raw $configPath | ConvertFrom-Json
} catch {
  throw 'Downloaded WQPU network configuration is invalid.'
}
$publicEnabled = [bool]$networkConfig.public.enabled

$runtime = Join-Path $root 'wqpu_autopay.py'
$versionArgs = @()
if ($extra) { $versionArgs += $extra }
$versionArgs += @($runtime,'--version')
$coreVersion = (& $exe @versionArgs 2>&1 | Out-String).Trim()
if ($coreVersion -ne $expectedWqpu) {
  throw "WQPU version mismatch: expected '$expectedWqpu', got '$coreVersion'."
}

$launcher = Join-Path $bin 'wqpu.cmd'
if ($extra) {
  Set-Content -Encoding ASCII -Path $launcher -Value "@echo off`r`n`"$exe`" $extra `"$runtime`" %*"
} else {
  Set-Content -Encoding ASCII -Path $launcher -Value "@echo off`r`n`"$exe`" `"$runtime`" %*"
}

$userPath=[Environment]::GetEnvironmentVariable('Path','User')
if (-not (($userPath -split ';') -contains $bin)) {
  [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $bin).TrimStart(';')), 'User')
}
$env:Path="$bin;$env:Path"

$ver = if ($extra) { (& $exe $extra --version 2>&1) } else { (& $exe --version 2>&1) }
Write-Host "WQPU installed: $coreVersion with $ver." -ForegroundColor Green

if ($env:WQPU_NO_START -eq '1') {
  Write-Host 'WQPU install-only mode: not starting the node.'
  exit 0
}

if (-not [string]::IsNullOrWhiteSpace($join)) {
  & $launcher --join $join
} elseif (($env:WQPU_RPC_URL -and $env:WQPU_REGISTRY) -or (Test-Path $chainState) -or $publicEnabled) {
  & $launcher
} else {
  Write-Host 'WQPU public chain is not published yet; starting the existing private mesh.'
  & $launcher --legacy
}