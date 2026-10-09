#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DeploymentRoot,
    [Parameter(Mandatory=$true)][string]$ReleaseRoot,
    [switch]$IncludeWorker
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=[IO.Path]::GetFullPath($DeploymentRoot)
$release=[IO.Path]::GetFullPath($ReleaseRoot)
$checks=New-Object System.Collections.Generic.List[object]
function Check([string]$Name,[bool]$Passed){
    $checks.Add([pscustomobject]@{name=$Name;status=$(if($Passed){'PASS'}else{'FAIL'})})
    if(-not $Passed){throw 'Read-only service check failed'}
}
try{
    $spec=@(
        @{name='postgres';service='RealYuSub2APIPostgres20261009';port=28490;exe='postgres\bin\postgres.exe';sha='14817df435104dcfe05ad8343fda200930abf778553ab60154bd285aea6833e1'},
        @{name='redis';service='RealYuSub2APIRedis20261009';port=28391;exe='redis\redis-server.exe';sha='5bc44512f4ff7828f0d65e59ad712c1b66f692c4f827c966123d3ece4ec5945c'},
        @{name='sub2api';service='RealYuSub2API20261009';port=28090;exe='sub2api\sub2api.exe';sha='c624c115dd7e5dd1a33c083d3ef601599338f9ed986c2cb007a5663d375f9978'}
    )
    foreach($entry in $spec){
        $managed=Get-CimInstance Win32_Service -Filter ("Name='"+$entry.service+"'")
        Check ($entry.name+' SCM running and automatic') ($managed -and $managed.State -eq 'Running' -and $managed.StartMode -eq 'Auto')
        $owners=@(Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $entry.port | Select-Object -ExpandProperty OwningProcess -Unique)
        Check ($entry.name+' one loopback listener') ($owners.Count -eq 1)
        $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$owners[0])
        Check ($entry.name+' executable and SCM parent identity') ($process.ExecutablePath -eq (Join-Path $release $entry.exe) -and $process.ParentProcessId -eq $managed.ProcessId)
        Check ($entry.name+' LocalService base identity') ($managed.StartName -eq 'NT AUTHORITY\LocalService')
        Check ($entry.name+' pinned application digest') ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $release $entry.exe)).Hash.ToLowerInvariant() -eq $entry.sha)
    }
    $health=Invoke-RestMethod -Uri 'http://127.0.0.1:28090/health' -TimeoutSec 15
    Check 'Sub2API health endpoint' ($health.status -eq 'ok')
    if($IncludeWorker){
        $managed=Get-CimInstance Win32_Service -Filter "Name='RealYuSub2APIPrewarm20261009'"
        Check 'Worker SCM running and automatic' ($managed -and $managed.State -eq 'Running' -and $managed.StartMode -eq 'Auto')
        $children=@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+[int]$managed.ProcessId) | Where-Object {$_.ExecutablePath -eq (Join-Path $release 'sub2api-prewarm.exe')})
        Check 'Worker executable and SCM parent identity' ($children.Count -eq 1)
        Check 'Worker LocalService base identity' ($managed.StartName -eq 'NT AUTHORITY\LocalService')
        Check 'Worker pinned application digest' ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $release 'sub2api-prewarm.exe')).Hash.ToLowerInvariant() -eq '8ac607bf72faee338238b5393555d326d910662b76503d4cc4854d0dda75710c')
        $heartbeat=Get-Content -Raw -LiteralPath (Join-Path $root 'integration\state\heartbeat.json') | ConvertFrom-Json
        $age=([datetime]::UtcNow-([datetimeoffset]$heartbeat.last_seen).UtcDateTime).TotalSeconds
        $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $root 'integration\bindings.json')).Hash.ToLowerInvariant()
        Check 'Worker heartbeat current configuration and freshness' ($heartbeat.version -eq 1 -and $heartbeat.config_sha256 -eq $hash -and $age -ge -10 -and $age -lt 60)
        $creditProperty=$heartbeat.PSObject.Properties['credit_status']
        $creditStatus=if($null -eq $creditProperty){''}else{[string]$creditProperty.Value}
        Check 'Worker not blocked or errored' ($heartbeat.status -in @('idle','running') -and $creditStatus -notin @('blocked','error'))
        $progress=Get-Content -Raw -LiteralPath (Join-Path $root ('integration\state\'+$hash+'.json')) | ConvertFrom-Json
        Check 'Projection state configuration identity' ($progress.version -eq 1 -and $progress.config_sha256 -eq $hash)
        $unfinished=@($progress.tasks.PSObject.Properties | Where-Object {$_.Value.status -ne 'DONE'})
        Check 'Persisted projection tasks completed' ($unfinished.Count -eq 0)
    }
    [pscustomobject]@{status='PASS';read_only=$true;checks=$checks.ToArray();scope='SCM, loopback identity, health and optional worker metadata only; no inference, accounting, reboot or public-network acceptance'} | ConvertTo-Json -Depth 5
}catch{
    [pscustomobject]@{status='FAIL';read_only=$true;checks=$checks.ToArray();error='Service verification incomplete; inspect protected local state without printing credentials'} | ConvertTo-Json -Depth 5
    exit 1
}
