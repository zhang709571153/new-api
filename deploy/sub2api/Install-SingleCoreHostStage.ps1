#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [Parameter(Mandatory=$true)][string]$Python,
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
function Hash([string]$Path) {(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Write-Receipt([string]$Path,$Value) {
    $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 12),$utf8)
}
try {
    if((Hash $Plan) -ne $ExpectedPlanSha256) {throw 'Pinned private plan changed.'}
    $p=Get-Content -LiteralPath $Plan -Raw -Encoding UTF8|ConvertFrom-Json
    & $Python (Join-Path $PSScriptRoot 'singlecore_host_cutover.py') validate --plan $Plan
    if($LASTEXITCODE -ne 0) {throw 'Native plan validation failed.'}
    $entry='C:\ProgramData\RealYuServices\service_entry.py'
    $source=Join-Path $PSScriptRoot 'service_entry_singlecore.py'
    $root='C:\ProgramData\RealYu\singlecore-production-20261010'
    $backup=Join-Path $p.runtime_directory 'launcher-backup'
    if((Hash $entry) -ne $p.installed_launcher_sha256 -or
       (Hash $source) -ne $p.verified_files.$source) {throw 'Launcher inputs changed.'}
    if([IO.Path]::GetFullPath($p.native_manifest_entry.exe) -ne (Join-Path $root 'bin\sub2api.exe') -or
       [IO.Path]::GetFullPath($p.native_manifest_entry.env_file) -ne (Join-Path $root 'config\api-env.json') -or
       [IO.Path]::GetFullPath($p.native_manifest_entry.working_dir) -ne (Join-Path $root 'data')) {throw 'Unexpected native installation paths.'}
    foreach($name in @('RealYuApi','RealYuSub2APIPostgres20261009','RealYuSub2APIRedis20261009','RealYuSgHy2')) {
        if((Get-Service $name).Status -ne 'Running') {throw 'A required existing service is not Running.'}
    }
    if(Test-Path -LiteralPath $backup) {throw 'Launcher staging has already been attempted; inspect the first receipt.'}
    if($ValidateOnly) {'Native host staging validated; no production pointer or service changed.';exit 0}
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Normal Windows administrator elevation required.'}
    $null=New-Item -ItemType Directory -Path $backup
    Copy-Item -LiteralPath $entry -Destination (Join-Path $backup 'service_entry.py')
    $xml='C:\ProgramData\RealYuServices\RealYuApi.xml'
    Copy-Item -LiteralPath $xml -Destination (Join-Path $backup 'RealYuApi.xml')
    $dependencies=@((Get-Service RealYuApi).RequiredServices|ForEach-Object {$_.Name})
    Write-Receipt (Join-Path $backup 'dependencies.json') @{services=$dependencies}
    # Grant no access to the separate migration snapshots or cluster-admin key.
    & icacls.exe $root /grant:r '*S-1-5-19:RX' >$null
    if($LASTEXITCODE -ne 0) {throw 'Cannot grant bounded root traversal.'}
    foreach($sub in @('bin','config','client-assets')) {
        & icacls.exe (Join-Path $root $sub) /grant:r '*S-1-5-19:(OI)(CI)RX' >$null
        if($LASTEXITCODE -ne 0) {throw 'Cannot grant read-only native artifacts.'}
    }
    foreach($sub in @('data','logs','client-diagnostics')) {
        & icacls.exe (Join-Path $root $sub) /grant:r '*S-1-5-19:(OI)(CI)M' >$null
        if($LASTEXITCODE -ne 0) {throw 'Cannot grant bounded native runtime writes.'}
    }
    $temp=$entry+'.singlecore-new'
    Copy-Item -LiteralPath $source -Destination $temp
    [IO.File]::Replace($temp,$entry,(Join-Path $backup 'replaced-service_entry.py'))
    if((Hash $entry) -ne (Hash $source)) {throw 'Launcher replacement hash mismatch.'}
    $newDependencies=@($dependencies+'RealYuSub2APIPostgres20261009'+'RealYuSub2APIRedis20261009'+'RealYuSgHy2'|Sort-Object -Unique)
    [xml]$wrapper=Get-Content -LiteralPath $xml -Raw
    foreach($node in @($wrapper.service.SelectNodes('depend'))) {$null=$wrapper.service.RemoveChild($node)}
    foreach($name in $newDependencies) {$node=$wrapper.CreateElement('depend');$node.InnerText=$name;$null=$wrapper.service.AppendChild($node)}
    $wrapper.Save($xml+'.singlecore-new')
    [IO.File]::Replace($xml+'.singlecore-new',$xml,(Join-Path $backup 'replaced-RealYuApi.xml'))
    & sc.exe config RealYuApi 'depend=' ($newDependencies -join '/') >$null
    if($LASTEXITCODE -ne 0) {throw 'SCM dependency configuration failed.'}
    $actual=@((Get-Service RealYuApi).RequiredServices|ForEach-Object {$_.Name}|Sort-Object)
    if(($actual -join ',') -ne ($newDependencies -join ',')) {throw 'SCM dependency verification failed.'}
    Write-Receipt (Join-Path $p.runtime_directory 'launcher-staged.json') @{
        status='STAGED';at=[DateTime]::UtcNow.ToString('o');launcher_sha256=(Hash $entry);
        dependencies=$actual;production_api_restarted=$false;production_pointer_changed=$false
    }
    'Native launcher staged with least-privilege runtime access. The current API worker and production authority remain unchanged.'
} catch {
    [Console]::Error.WriteLine('Native launcher staging failed. Inspect the private staging/backup directory before retrying; this script does not activate the native API.')
    exit 1
}
