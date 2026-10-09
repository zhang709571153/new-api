#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DeploymentRoot,
    [Parameter(Mandatory=$true)][string]$ReleaseRoot
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($DeploymentRoot)
$release=[IO.Path]::GetFullPath($ReleaseRoot)
$spec=@(@{name='postgres';port=28490;path=(Join-Path $release 'postgres\bin\postgres.exe')},
        @{name='redis';port=28391;path=(Join-Path $release 'redis\redis-server.exe')},
        @{name='sub2api';port=28090;path=(Join-Path $release 'sub2api\sub2api.exe')})
try{
    foreach($id in @('RealYuSub2APIPostgres20261009','RealYuSub2APIRedis20261009','RealYuSub2API20261009')){
        if(Get-Service -Name $id -ErrorAction SilentlyContinue){throw 'Cannot record service-owned processes as temporary'}
    }
    $entries=foreach($s in $spec){
        $listener=Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $s.port
        $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
        if($process.ExecutablePath -ne $s.path){throw 'Wrong process executable'}
        [pscustomobject]@{name=$s.name;port=$s.port;pid=$process.ProcessId;executable=$process.ExecutablePath;creation_time=$process.CreationDate.ToUniversalTime().ToString('o')}
    }
    @{version=1;processes=@($entries)}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath (Join-Path $root 'temporary-owned-processes.json') -Encoding UTF8
    'New-installation temporary process identities recorded; no process changed.'
}catch{
    [Console]::Error.WriteLine('Could not validate every new-installation temporary process; no process changed.')
    exit 1
}
