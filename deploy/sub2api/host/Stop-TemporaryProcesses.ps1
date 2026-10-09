#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DeploymentRoot,
    [Parameter(Mandatory=$true)][string]$ReleaseRoot,
    [switch]$StopValidatedProcesses
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($DeploymentRoot)
$release=[IO.Path]::GetFullPath($ReleaseRoot)
try{
    $records=Get-Content -Raw -LiteralPath (Join-Path $root 'temporary-owned-processes.json')|ConvertFrom-Json
    $expected=@{postgres=@{port=28490;executable=(Join-Path $release 'postgres\bin\postgres.exe')};redis=@{port=28391;executable=(Join-Path $release 'redis\redis-server.exe')};sub2api=@{port=28090;executable=(Join-Path $release 'sub2api\sub2api.exe')}}
    if($records.version -ne 1 -or @($records.processes).Count -ne 3 -or @($records.processes.name|Select-Object -Unique).Count -ne 3){throw 'Inventory mismatch'}
    foreach($id in @('RealYuSub2APIPostgres20261009','RealYuSub2APIRedis20261009','RealYuSub2API20261009')){
        if(Get-Service -Name $id -ErrorAction SilentlyContinue){throw 'Service-owned processes must use their service lifecycle'}
    }
    foreach($entry in $records.processes){
        if(-not $expected.ContainsKey($entry.name) -or $entry.port -ne $expected[$entry.name].port -or $entry.executable -ne $expected[$entry.name].executable){throw 'Unexpected temporary process scope'}
        $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$entry.pid)
        if(-not $process -or $process.ExecutablePath -ne $entry.executable -or $process.CreationDate.ToUniversalTime().Ticks -ne ([datetime]$entry.creation_time).ToUniversalTime().Ticks){throw 'Identity mismatch'}
        $listener=Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $entry.port
        if($listener.OwningProcess -ne $entry.pid){throw 'Listener mismatch'}
    }
    if(-not $StopValidatedProcesses){'Temporary process identity and port ownership validated; no process stopped.';exit 0}
    # Stop only the three recorded, revalidated processes created for this new installation.
    $sub=$records.processes|Where-Object {$_.name -eq 'sub2api'}
    Stop-Process -Id $sub.pid
    Wait-Process -Id $sub.pid -Timeout 60 -ErrorAction SilentlyContinue
    $cfg=Get-Content -Raw -LiteralPath (Join-Path $root 'runtime-private.json')|ConvertFrom-Json
    $oldRedisAuth=$env:REDISCLI_AUTH
    try{
        $env:REDISCLI_AUTH=$cfg.redis_password
        Push-Location (Join-Path $root 'redis')
        try{& (Join-Path $release 'redis\redis-cli.exe') -h 127.0.0.1 -p 28391 SHUTDOWN SAVE *> (Join-Path $root 'logs\redis-pre-service-stop.log');if($LASTEXITCODE -ne 0){throw 'Redis stop failed'}}finally{Pop-Location}
    }finally{$env:REDISCLI_AUTH=$oldRedisAuth}
    & (Join-Path $release 'postgres\bin\pg_ctl.exe') -D (Join-Path $root 'postgres\data') -m fast -w stop *> (Join-Path $root 'logs\pg-pre-service-stop.log')
    if($LASTEXITCODE -ne 0){throw 'Postgres stop failed'}
    foreach($port in @(28090,28490,28391)){if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue){throw 'Port remains bound'}}
    'Only verified new-installation temporary processes were stopped.'
}catch{
    [Console]::Error.WriteLine('Temporary process validation or shutdown failed. Inspect protected deployment state; no broader process termination was attempted.')
    exit 1
}
