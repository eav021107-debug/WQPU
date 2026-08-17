$ErrorActionPreference = 'Stop'
$raw = if ($env:WQPU_RAW_BASE) { $env:WQPU_RAW_BASE.TrimEnd('/') } else { 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main' }
$repo = 'eav021107-debug/WQPU'
$sourceRef = if ($env:WQPU_SOURCE_REF) { $env:WQPU_SOURCE_REF } else { 'main' }
if ($env:WQPU_RAW_BASE -and $raw -match '^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/(.+)$') {
  $repo = $Matches[1]
  $sourceRef = $Matches[2]
}
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
$bin = Join-Path $root 'bin'
$join = $env:WQPU_JOIN
$expectedWqpu = 'WQPU 0.6.0'
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
    if ($cmd -eq 'py') { if (Test-Python $f.Source '-3') { return @{Exe=$f.Source; Extra='-3'} } }
    elseif (Test-Python $f.Source '') { return @{Exe=$f.Source; Extra=''} }
  }
  return $null
}
function Install-PrivatePython {
  Write-Host 'WQPU: compatible Python not found; installing a private Python for WQPU only...'
  $target = Join-Path $root 'python'; $installer = Join-Path $env:TEMP 'wqpu-python-installer.exe'
  $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
  if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe' }
  Invoke-WebRequest -UseBasicParsing $url -OutFile $installer
  $args = @('/quiet','InstallAllUsers=0',"TargetDir=$target",'Include_launcher=0','Include_pip=0','PrependPath=0','Shortcuts=0')
  $p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
  Remove-Item $installer -Force -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) { throw "Python installer failed with exit code $($p.ExitCode)." }
  if (-not (Test-Python (Join-Path $target 'python.exe') '')) { throw 'Private Python installation did not produce a usable Python.' }
}
function Download-WithRetry([string]$Url, [string]$OutFile, [int]$Attempts = 4) {
  $last = $null
  for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
    try {
      Remove-Item $OutFile -Force -ErrorAction SilentlyContinue
      Invoke-WebRequest -UseBasicParsing $Url -OutFile $OutFile
      return
    } catch {
      $last = $_
      Remove-Item $OutFile -Force -ErrorAction SilentlyContinue
      if ($attempt + 1 -lt $Attempts) { Start-Sleep -Seconds ([Math]::Min(8, [Math]::Pow(2, $attempt))) }
    }
  }
  throw $last
}
function Copy-WqpuFiles([string]$SourceRoot, $FileList, [string]$DestinationRoot) {
  foreach ($file in $FileList) {
    $sourceFile = Join-Path $SourceRoot $file
    if (-not (Test-Path $sourceFile)) { throw "WQPU source is missing $file." }
  }
  foreach ($file in $FileList) {
    Copy-Item -Force (Join-Path $SourceRoot $file) (Join-Path $DestinationRoot $file)
  }
}
function Install-FromGit([string]$Repository, [string]$Ref, $FileList, [string]$DestinationRoot) {
  $gitCommand = Get-Command git -ErrorAction SilentlyContinue
  if (-not $gitCommand) { return $false }
  $tmpGit = Join-Path $env:TEMP ("wqpu-git-" + [Guid]::NewGuid().ToString('N'))
  try {
    New-Item -ItemType Directory -Force -Path $tmpGit | Out-Null
    & $gitCommand.Source -C $tmpGit init -q
    if ($LASTEXITCODE -ne 0) { return $false }
    & $gitCommand.Source -C $tmpGit remote add origin "https://github.com/$Repository.git"
    if ($LASTEXITCODE -ne 0) { return $false }
    & $gitCommand.Source -C $tmpGit fetch --depth=1 origin $Ref 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    & $gitCommand.Source -C $tmpGit checkout -q --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { return $false }
    Copy-WqpuFiles $tmpGit $FileList $DestinationRoot
    return $true
  } catch {
    return $false
  } finally {
    Remove-Item $tmpGit -Recurse -Force -ErrorAction SilentlyContinue
  }
}

$py = Find-Python
if (-not $py) { Install-PrivatePython; $py = Find-Python }
if (-not $py) { throw 'WQPU could not prepare Python 3.6+.' }

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) {
    $gitRoot = Split-Path (Split-Path $git.Source -Parent) -Parent
    $gitOpenSsl = Join-Path $gitRoot 'usr\bin'
    if (Test-Path (Join-Path $gitOpenSsl 'openssl.exe')) { $env:Path="$gitOpenSsl;$env:Path" }
  }
}
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) { throw 'WQPU needs OpenSSL. Install Git for Windows or OpenSSL, then run the same command again.' }

Write-Host 'WQPU: downloading runtime...'
$pythonFiles = @('wqpu.py','wqpu_accel.py','wqpu_gpu_patch.py','wqpu_chain.py','wqpu_wallet.py','wqpu_session.py','wqpu_meter.py','wqpu_accounting.py','wqpu_attestation.py','wqpu_payments.py','wqpu_claim.py','wqpu_vouchers.py','wqpu_runtime.py','wqpu_autopay.py','wqpu_multistream.py','wqpu_runtime_pin.py','wqpu_network_guard.py','wqpu_node_identity.py','wqpu_node_status.py','wqpu_public_config.py','wqpu_public_security.py','wqpu_entry.py')
$files = $pythonFiles + @('network-config.json')

$sourceInstalled = $false
if ($repo -and $sourceRef) {
  $tmpRoot = Join-Path $env:TEMP ("wqpu-source-" + [Guid]::NewGuid().ToString('N'))
  $archive = Join-Path $tmpRoot 'wqpu.tar.gz'
  $extract = Join-Path $tmpRoot 'src'
  try {
    New-Item -ItemType Directory -Force -Path $tmpRoot,$extract | Out-Null
    $archiveUrl = "https://codeload.github.com/$repo/tar.gz/$sourceRef"
    Download-WithRetry $archiveUrl $archive
    & tar.exe -xzf $archive -C $extract
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract WQPU source archive.' }
    $source = Get-ChildItem -Path $extract -Directory | Select-Object -First 1
    if (-not $source) { throw 'WQPU source archive is empty.' }
    Copy-WqpuFiles $source.FullName $files $root
    $sourceInstalled = $true
  } catch {
    Write-Host "WQPU: source archive unavailable ($($_.Exception.Message)); trying git transport..."
  } finally {
    Remove-Item $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
if (-not $sourceInstalled -and $repo -and $sourceRef) {
  $sourceInstalled = Install-FromGit $repo $sourceRef $files $root
  if ($sourceInstalled) { Write-Host 'WQPU: source installed through git fallback.' }
}
if (-not $sourceInstalled) {
  Write-Host 'WQPU: git source unavailable; falling back to individual files.'
  foreach ($file in $files) { Download-WithRetry "$raw/$file" (Join-Path $root $file) }
}

$exe = $py.Exe; $extra = $py.Extra; $compileArgs = @()
if ($extra) { $compileArgs += $extra }
$compileArgs += @('-m','py_compile')
foreach ($file in $pythonFiles) { $compileArgs += (Join-Path $root $file) }
& $exe @compileArgs
if ($LASTEXITCODE -ne 0) { throw 'Downloaded WQPU runtime did not pass the Python compatibility check.' }

$configPath = Join-Path $root 'network-config.json'
try { $networkConfig = Get-Content -Raw $configPath | ConvertFrom-Json } catch { throw 'Downloaded WQPU network configuration is invalid.' }
$publicEnabled = [bool]$networkConfig.public.enabled
$runtime = Join-Path $root 'wqpu_entry.py'
$versionArgs = @(); if ($extra) { $versionArgs += $extra }; $versionArgs += @($runtime,'--version')
$coreVersion = (& $exe @versionArgs 2>&1 | Out-String).Trim()
if ($coreVersion -ne $expectedWqpu) { throw "WQPU version mismatch: expected '$expectedWqpu', got '$coreVersion'." }

$launcher = Join-Path $bin 'wqpu.cmd'
if ($extra) { Set-Content -Encoding ASCII -Path $launcher -Value "@echo off`r`n`"$exe`" $extra `"$runtime`" %*" }
else { Set-Content -Encoding ASCII -Path $launcher -Value "@echo off`r`n`"$exe`" `"$runtime`" %*" }

$userPath=[Environment]::GetEnvironmentVariable('Path','User')
if (-not (($userPath -split ';') -contains $bin)) { [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $bin).TrimStart(';')), 'User') }
$env:Path="$bin;$env:Path"
$ver = if ($extra) { (& $exe $extra --version 2>&1) } else { (& $exe --version 2>&1) }
Write-Host "WQPU installed: $coreVersion with $ver." -ForegroundColor Green

if ($env:WQPU_NO_START -eq '1') { Write-Host 'WQPU install-only mode: not starting the node.'; exit 0 }
if (-not [string]::IsNullOrWhiteSpace($join)) { & $launcher --join $join }
elseif (($env:WQPU_RPC_URL -and $env:WQPU_REGISTRY) -or (Test-Path $chainState) -or $publicEnabled) { & $launcher }
else { Write-Host 'WQPU public chain is not published yet; starting the existing private mesh.'; & $launcher --legacy }