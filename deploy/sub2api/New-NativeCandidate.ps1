#requires -Version 5.1
<#
.SYNOPSIS
Build an isolated Windows candidate directory; never install/start services.
.DESCRIPTION
The private JSON contains secrets. Output WinSW XML also contains secrets.
Only a NEW local path containing a candidates directory is accepted. A fresh
SQLite path is generated; this tool cannot select or overwrite an existing DB.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PrivateConfig,
    [Parameter(Mandatory = $true)][string]$CandidateRoot,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:SafeFailure = $null
function Stop-Candidate([string]$Message) {
    $script:SafeFailure = $Message
    throw 'Candidate validation failed'
}
# A PowerShell 5.1 child of PowerShell 7 may inherit only the latter's module
# search paths. Load the built-in ACL commands explicitly from this runtime.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')

function Require-Text([object]$Value, [string]$Label, [int]$Min = 1) {
    if ($null -eq $Value -or $Value -isnot [string] -or $Value.Length -lt $Min) {
        Stop-Candidate "Missing or invalid field: $Label"
    }
    # WinSW expands %NAME% even inside XML values. Do not silently change keys.
    if ($Value -match '[%\x00-\x1f]') { Stop-Candidate "Unsupported expansion/control character: $Label" }
    return $Value
}
function Check-PathAncestors([string]$Path) {
    $item = $Path
    while ($item) {
        if (Test-Path -LiteralPath $item) {
            if ((Get-Item -Force -LiteralPath $item).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                Stop-Candidate 'Reparse-point paths are not accepted'
            }
            if (Test-Path -LiteralPath (Join-Path $item '.git')) { Stop-Candidate 'Private candidates must be outside Git checkouts' }
        }
        $next = Split-Path -Parent $item
        if ($next -eq $item) { break }
        $item = $next
    }
}
function Add-TextElement($Doc, $Parent, [string]$Name, [string]$Value) {
    $node = $Doc.CreateElement($Name)
    $node.InnerText = $Value
    [void]$Parent.AppendChild($node)
}
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}
function Write-Service([string]$Path, [string]$Id, [string]$Exe, [string]$Working,
                       [string]$LogPath, [Collections.IDictionary]$Env, [string[]]$Dependencies,
                       [string]$Arguments = '') {
    $doc = New-Object System.Xml.XmlDocument
    $service = $doc.CreateElement('service')
    [void]$doc.AppendChild($service)
    Add-TextElement $doc $service 'id' $Id
    Add-TextElement $doc $service 'name' "RealYu candidate $Id"
    Add-TextElement $doc $service 'description' 'Isolated rehearsal only; no public ingress or production cutover.'
    Add-TextElement $doc $service 'executable' $Exe
    if ($Arguments) { Add-TextElement $doc $service 'arguments' $Arguments }
    Add-TextElement $doc $service 'workingdirectory' $Working
    Add-TextElement $doc $service 'startmode' 'Manual'
    Add-TextElement $doc $service 'stoptimeout' '150 sec'
    Add-TextElement $doc $service 'stopparentprocessfirst' 'true'
    Add-TextElement $doc $service 'logpath' $LogPath
    foreach ($dependency in $Dependencies) { Add-TextElement $doc $service 'depend' $dependency }
    $account = $doc.CreateElement('serviceaccount')
    [void]$service.AppendChild($account)
    Add-TextElement $doc $account 'domain' 'NT AUTHORITY'
    Add-TextElement $doc $account 'user' 'LocalService'
    foreach ($delay in @('10 sec', '30 sec', '60 sec')) {
        $failure = $doc.CreateElement('onfailure')
        $failure.SetAttribute('action', 'restart')
        $failure.SetAttribute('delay', $delay)
        [void]$service.AppendChild($failure)
    }
    Add-TextElement $doc $service 'resetfailure' '1 hour'
    $log = $doc.CreateElement('log')
    $log.SetAttribute('mode', 'roll-by-size')
    [void]$service.AppendChild($log)
    Add-TextElement $doc $log 'sizeThreshold' '20480'
    Add-TextElement $doc $log 'keepFiles' '5'
    foreach ($key in $Env.Keys) {
        $node = $doc.CreateElement('env')
        $node.SetAttribute('name', $key)
        $node.SetAttribute('value', [string]$Env[$key])
        [void]$service.AppendChild($node)
    }
    $doc.Save($Path)
}
function Set-PrivateAcl([string]$Path, [bool]$WritableByService) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $sids = @('S-1-5-18', 'S-1-5-32-544', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    foreach ($sid in ($sids | Select-Object -Unique)) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    $serviceSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
    $rights = if ($WritableByService) { 'Modify' } else { 'ReadAndExecute' }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($serviceSid, $rights, 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}

try {
    if ($env:OS -ne 'Windows_NT') { Stop-Candidate 'This packager requires Windows' }
    if ($CandidateRoot -notmatch '^[A-Za-z]:[\\/]' -or $CandidateRoot -match '[%\x00-\x1f]') { Stop-Candidate 'Use an absolute local Windows path' }
    $root = [IO.Path]::GetFullPath($CandidateRoot).TrimEnd('\')
    if ($root -notmatch '(?i)\\candidates\\[^\\]+$' -or $root -match '(?i)^C:\\srv(?:\\|$)') {
        Stop-Candidate 'Use a new path such as C:\RealYu\candidates\rehearsal01, never a production tree'
    }
    if (Test-Path -LiteralPath $root) { Stop-Candidate 'Candidate directory already exists; refusing overwrite' }
    Check-PathAncestors $root
    try { $config = Get-Content -Raw -LiteralPath $PrivateConfig | ConvertFrom-Json }
    catch { Stop-Candidate 'Cannot read or parse the private configuration' }
    if ($config.version -ne 1) { Stop-Candidate 'Unsupported configuration version' }
    $version = Require-Text $config.candidate_version 'candidate_version'
    if ($version -notmatch '^realyu-sub2api-candidate-[A-Za-z0-9._-]+$') { Stop-Candidate 'Use a unique candidate version marker' }
    $suffix = Require-Text $config.service_suffix 'service_suffix'
    if ($suffix -notmatch '^[A-Za-z][A-Za-z0-9]{0,30}$') { Stop-Candidate 'Invalid service suffix' }
    $realyuPort = [int]$config.ports.realyu
    $subPort = [int]$config.ports.sub2api
    foreach ($port in @($realyuPort, $subPort)) {
        if ($port -lt 1024 -or $port -gt 65535 -or $port -in @(18300,18301)) { Stop-Candidate 'Unsafe candidate port' }
    }
    if ($realyuPort -eq $subPort) { Stop-Candidate 'Candidate ports must differ' }
    $binaries = @{}
    foreach ($name in @('realyu', 'sub2api', 'prewarm', 'winsw')) {
        $entry = $config.binaries.$name
        $source = Require-Text $entry.path "binaries.$name.path"
        $hash = Require-Text $entry.sha256 "binaries.$name.sha256"
        if ($hash -notmatch '^[a-fA-F0-9]{64}$' -or -not (Test-Path -LiteralPath $source -PathType Leaf)) { Stop-Candidate "Invalid binary: $name" }
        if ((Get-Sha256 $source) -ne $hash) { Stop-Candidate "Binary hash mismatch: $name" }
        if ($name -eq 'winsw' -and $hash -ne '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da') { Stop-Candidate 'WinSW must match the reviewed v2.12.0 x64 artifact' }
        $binaries[$name] = [IO.Path]::GetFullPath($source)
    }
    # The stock Windows Sub2API binary does not embed IANA time zone data. Keep
    # its reviewed archive and accompanying Go license in the portable package;
    # never depend on this machine's GOROOT or a developer-installed Go runtime.
    $dependenciesProperty = $config.PSObject.Properties['dependencies']
    if ($null -eq $dependenciesProperty) { Stop-Candidate 'Missing dependency manifest' }
    $runtimeDependencies = @{}
    $dependencyHashes = @{}
    foreach ($name in @('zoneinfo', 'go_license')) {
        $property = $dependenciesProperty.Value.PSObject.Properties[$name]
        if ($null -eq $property) { Stop-Candidate "Missing dependency: $name" }
        $entry = $property.Value
        $source = Require-Text $entry.path "dependencies.$name.path"
        $hash = Require-Text $entry.sha256 "dependencies.$name.sha256"
        if ($hash -notmatch '^[a-fA-F0-9]{64}$' -or -not (Test-Path -LiteralPath $source -PathType Leaf)) { Stop-Candidate "Invalid dependency: $name" }
        try { $actualHash = Get-Sha256 $source }
        catch { Stop-Candidate "Cannot read dependency: $name" }
        if ($actualHash -ne $hash) { Stop-Candidate "Dependency hash mismatch: $name" }
        $runtimeDependencies[$name] = [IO.Path]::GetFullPath($source)
        $dependencyHashes[$name] = $actualHash
    }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($runtimeDependencies.zoneinfo)
        try {
            $zone = $archive.GetEntry('Asia/Shanghai')
            if ($null -eq $zone -or $zone.Length -lt 4) { Stop-Candidate 'Timezone archive lacks Asia/Shanghai' }
            $stream = $zone.Open()
            try {
                $magic = New-Object byte[] 4
                if ($stream.Read($magic, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($magic) -ne 'TZif') { Stop-Candidate 'Timezone archive has invalid zone data' }
            } finally { $stream.Dispose() }
        } finally { $archive.Dispose() }
        $license = Get-Content -Raw -LiteralPath $runtimeDependencies.go_license
        if ($license.Length -lt 64 -or $license -notmatch 'The Go Authors') { Stop-Candidate 'Go timezone license is missing or invalid' }
    } catch {
        if (-not $script:SafeFailure) { $script:SafeFailure = 'Cannot read the reviewed timezone archive or license' }
        throw
    }
    $realyuEnv = [ordered]@{
        BIND_ADDRESS='127.0.0.1'; PORT=[string]$realyuPort; VERSION=$version; GIN_MODE='release'
        SQLITE_PATH=(Join-Path $root 'state\realyu\new-api.db') + '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate'
        SQL_DSN=''; LOG_SQL_DSN=''; REDIS_CONN_STRING=''; TRUSTED_PROXIES='none'
        SESSION_SECRET=(Require-Text $config.realyu.SESSION_SECRET 'SESSION_SECRET' 32)
        CRYPTO_SECRET=(Require-Text $config.realyu.CRYPTO_SECRET 'CRYPTO_SECRET' 32)
        USER_SESSION_ACTIVE_LIMIT='500'; REALYU_UPSTREAM_DRIVER=''
        REALYU_SUB2API_ADMIN_URL="http://127.0.0.1:$subPort"
        REALYU_SUB2API_BINDINGS_FILE=(Join-Path $root 'state\integration\sub2api.json')
        REALYU_SUB2API_QUEUE_DIR=(Join-Path $root 'state\enrollment-queue')
        REALYU_SUB2API_STATE_DIR=(Join-Path $root 'state\prewarm')
        SHUTDOWN_TIMEOUT_SECONDS='120'; TZ='Asia/Shanghai'
    }
    foreach ($key in @('HTTP_PROXY','HTTPS_PROXY')) {
        $property = $config.realyu.PSObject.Properties[$key]
        $value = if ($null -eq $property) { '' } else { Require-Text $property.Value "realyu.$key" 0 }
        if ($value) {
            $uri = $null
            if (-not [Uri]::TryCreate($value, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http','https') -or -not $uri.Host) { Stop-Candidate "Invalid outbound proxy URI: realyu.$key" }
        }
        $realyuEnv[$key] = $value
    }
    $noProxyProperty = $config.realyu.PSObject.Properties['NO_PROXY']
    $noProxy = if ($null -eq $noProxyProperty) { 'localhost,127.0.0.1,::1' } else { Require-Text $noProxyProperty.Value 'realyu.NO_PROXY' }
    $bypasses = @($noProxy.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() })
    foreach ($local in @('localhost','127.0.0.1','::1')) {
        if ($local -notin $bypasses) { Stop-Candidate 'realyu.NO_PROXY must include localhost, 127.0.0.1 and ::1' }
    }
    $realyuEnv['NO_PROXY'] = $noProxy
    $subEnv = [ordered]@{
        SERVER_HOST='127.0.0.1'; SERVER_PORT=[string]$subPort; SERVER_MODE='release'; RUN_MODE='standard'
        AUTO_SETUP='true'; DATA_DIR=(Join-Path $root 'state\sub2api'); TOKEN_REFRESH_ENABLED='false'; TZ='Asia/Shanghai'
        IDEMPOTENCY_DEFAULT_TTL_SECONDS='86400'
        ZONEINFO=(Join-Path $root 'bin\deps\zoneinfo.zip')
        DATABASE_MAX_OPEN_CONNS='50'; DATABASE_MAX_IDLE_CONNS='10'
        REDIS_POOL_SIZE='128'; REDIS_MIN_IDLE_CONNS='16'
    }
    $required = @('DATABASE_HOST','DATABASE_PORT','DATABASE_USER','DATABASE_PASSWORD','DATABASE_DBNAME','DATABASE_SSLMODE',
                  'REDIS_HOST','REDIS_PORT','REDIS_PASSWORD','REDIS_DB','REDIS_ENABLE_TLS',
                  'ADMIN_EMAIL','ADMIN_PASSWORD','JWT_SECRET','TOTP_ENCRYPTION_KEY')
    foreach ($key in $required) { $subEnv[$key] = Require-Text $config.sub2api.$key "sub2api.$key" }
    $subEnv['REDIS_USERNAME'] = Require-Text $config.sub2api.REDIS_USERNAME 'sub2api.REDIS_USERNAME' 0
    foreach ($key in @('ADMIN_PASSWORD','JWT_SECRET','TOTP_ENCRYPTION_KEY')) {
        [void](Require-Text $subEnv[$key] $key 32)
    }
    if ($subEnv.TOTP_ENCRYPTION_KEY -notmatch '^[a-fA-F0-9]{64}$') { Stop-Candidate 'TOTP_ENCRYPTION_KEY must be 32 bytes encoded as 64 hexadecimal characters' }
    # Candidate databases must have unmistakable names; never aim auto-setup at production.
    if ($subEnv.DATABASE_DBNAME -notmatch '^[a-zA-Z0-9_]*(candidate|rehearsal|e2e)[a-zA-Z0-9_]*$') { Stop-Candidate 'Use a dedicated candidate PostgreSQL database' }
    if ($subEnv.DATABASE_HOST -ne '127.0.0.1' -and $subEnv.DATABASE_SSLMODE -ne 'verify-full') { Stop-Candidate 'Remote PostgreSQL requires verify-full TLS' }
    if ($subEnv.REDIS_HOST -ne '127.0.0.1' -and $subEnv.REDIS_ENABLE_TLS -ne 'true') { Stop-Candidate 'Remote Redis requires TLS' }
    $supply = Require-Text $config.redis_supply 'redis_supply'
    if ($supply -notin @('community-windows-candidate','external-supported','memurai-enterprise-licensed','isolated-test-only')) { Stop-Candidate 'Select and document the Redis supply and its acceptance/support boundary' }
    $dependencies = @($config.sub2api_windows_service_dependencies)
    foreach ($dependency in $dependencies) {
        if ($dependency -isnot [string] -or $dependency -notmatch '^[a-zA-Z0-9_.-]+$') { Stop-Candidate 'Invalid Windows dependency service name' }
    }
    if ($ValidateOnly) {
        [pscustomobject]@{valid=$true; services_installed=$false; services_started=$false; production_changes=$false} | ConvertTo-Json
        exit 0
    }
    [void](New-Item -ItemType Directory -Path $root)
    # Apply restrictive ACL before writing anything sensitive. On failure, leave
    # the new directory for inspection; never recursively remove a computed path.
    Set-PrivateAcl $root $false
    foreach ($directory in @('bin','bin\deps','services','state','state\realyu','state\sub2api','state\integration','state\enrollment-queue','state\prewarm','logs','logs\realyu','logs\sub2api','logs\prewarm')) {
        [void](New-Item -ItemType Directory -Path (Join-Path $root $directory))
    }
    # This candidate shares LocalService between processes. Production account
    # separation requires reviewed service SIDs and per-directory ACLs on target.
    foreach ($directory in @('state\realyu','state\sub2api','state\integration','state\enrollment-queue','state\prewarm','logs')) { Set-PrivateAcl (Join-Path $root $directory) $true }
    Copy-Item -LiteralPath $binaries.realyu -Destination (Join-Path $root 'bin\realyu.exe')
    Copy-Item -LiteralPath $binaries.sub2api -Destination (Join-Path $root 'bin\sub2api.exe')
    Copy-Item -LiteralPath $binaries.prewarm -Destination (Join-Path $root 'bin\sub2api-prewarm.exe')
    $dependencyTargets = @{zoneinfo='bin\deps\zoneinfo.zip'; go_license='bin\deps\GO-LICENSE.txt'}
    foreach ($name in @('zoneinfo', 'go_license')) {
        $destination = Join-Path $root $dependencyTargets[$name]
        Copy-Item -LiteralPath $runtimeDependencies[$name] -Destination $destination
        if ((Get-Sha256 $destination) -ne $dependencyHashes[$name]) { Stop-Candidate "Copied dependency hash mismatch: $name" }
    }
    foreach ($name in @('realyu-service','sub2api-service','prewarm-service')) {
        Copy-Item -LiteralPath $binaries.winsw -Destination (Join-Path $root "services\$name.exe")
    }
    $subId = "RealYuSub2APICandidate$suffix"
    $realyuId = "RealYuPortalCandidate$suffix"
    $prewarmId = "RealYuPrewarmCandidate$suffix"
    $realArgs = '--log-dir "' + (Join-Path $root 'logs\realyu') + '"'
    Write-Service (Join-Path $root 'services\sub2api-service.xml') $subId (Join-Path $root 'bin\sub2api.exe') (Join-Path $root 'state\sub2api') (Join-Path $root 'logs\sub2api') $subEnv $dependencies
    Write-Service (Join-Path $root 'services\realyu-service.xml') $realyuId (Join-Path $root 'bin\realyu.exe') (Join-Path $root 'state\realyu') (Join-Path $root 'logs\realyu') $realyuEnv @($subId) $realArgs
    $realyuEnv.REALYU_UPSTREAM_DRIVER='sub2api'
    Write-Service (Join-Path $root 'services\realyu-service.sub2api.xml.pending') $realyuId (Join-Path $root 'bin\realyu.exe') (Join-Path $root 'state\realyu') (Join-Path $root 'logs\realyu') $realyuEnv @($subId) $realArgs
    $workerEnv = [ordered]@{
        REALYU_SUB2API_BINDINGS_FILE=$realyuEnv.REALYU_SUB2API_BINDINGS_FILE
        REALYU_SUB2API_QUEUE_DIR=$realyuEnv.REALYU_SUB2API_QUEUE_DIR
        REALYU_SUB2API_STATE_DIR=$realyuEnv.REALYU_SUB2API_STATE_DIR
        TZ='Asia/Shanghai'
    }
    Write-Service (Join-Path $root 'services\prewarm-service.xml') $prewarmId (Join-Path $root 'bin\sub2api-prewarm.exe') (Join-Path $root 'state\prewarm') (Join-Path $root 'logs\prewarm') $workerEnv @($subId) '--watch'
    $receipt = [ordered]@{
        kind='realyu-native-isolated-candidate'; version=1; candidate_root=$root; candidate_version=$version
        realyu_service=$realyuId; sub2api_service=$subId; prewarm_service=$prewarmId; mode='legacy-bootstrap'; redis_supply=$supply
        realyu_url="http://127.0.0.1:$realyuPort"; sub2api_url="http://127.0.0.1:$subPort"
        services_installed=$false; services_started=$false; production_changes=$false
        timezone_sha256=$dependencyHashes.zoneinfo; go_license_sha256=$dependencyHashes.go_license
    }
    $receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'candidate.json') -Encoding UTF8
    $receipt | ConvertTo-Json
} catch {
    # Error messages name fields only; never echo JSON or XML containing secrets.
    $reason = if ($script:SafeFailure) { $script:SafeFailure } else { 'Unclassified failure; inspect the private inputs locally without sharing them' }
    [Console]::Error.WriteLine('Native candidate generation failed: ' + $reason)
    exit 1
}
