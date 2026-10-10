#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [ValidateSet('Apply','Rollback')][string]$Action='Apply',
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'SgHy2.Common.ps1')
$lock=$null
try {
    $config=Read-SgHy2Plan $Plan $ExpectedPlanSha256
    $rolling=Get-Content -LiteralPath (Join-Path $config.operation_root 'rolling-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($rolling.plan_id -ne $config.id -or (Get-SgHy2Hash $config.manifest_path) -ne $rolling.current_manifest_sha256) {
        throw 'Rolling operation identity changed before monitoring update.'
    }
    $required=if($Action -eq 'Apply'){@('VERIFIED')}else{@('ORIGINAL','ROLLED_BACK')}
    if($rolling.roles.'tunnel-primary' -notin $required -or $rolling.roles.'tunnel-replica' -notin $required) {
        throw 'Complete the matching Tunnel stages before changing monitoring paths.'
    }
    $guard=Get-Content -LiteralPath $config.guard_config -Raw -Encoding UTF8 | ConvertFrom-Json
    $pause=Get-Content -LiteralPath (Join-Path $guard.runtime_dir 'pause') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($pause.operation_id -ne $config.id) {throw 'The matching guard remediation pause is required.'}
    $allowed=@{
        'guard-config'=[IO.Path]::GetFullPath($config.guard_config)
        'collector-live'=Join-Path $config.observability_root 'collect_evidence.py'
        'collector-guard'=Join-Path (Split-Path -Parent $config.guard_config) 'collect_evidence.py'
    }
    if(@($config.monitor_inputs).Count -ne 3 -or @($config.monitor_inputs.name | Sort-Object -Unique).Count -ne 3) {
        throw 'Unexpected monitoring input set.'
    }
    $backupRoot=Join-Path $config.operation_root 'monitor-backup'
    foreach($entry in $config.monitor_inputs) {
        if(-not $allowed.ContainsKey($entry.name) -or [IO.Path]::GetFullPath($entry.target) -ne $allowed[$entry.name] -or
           [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($entry.staged_file)) -ne (Join-Path $config.operation_root 'monitor') -or
           (Get-SgHy2Hash $entry.staged_file) -ne $entry.after_sha256) {throw 'A reviewed monitoring artifact changed.'}
        $expected=if($Action -eq 'Apply'){@($entry.before_sha256)}else{@($entry.after_sha256,$entry.before_sha256)}
        if((Get-SgHy2Hash $entry.target) -notin $expected) {throw 'Monitoring source drift; do not overwrite it.'}
        if($Action -eq 'Rollback' -and (Get-SgHy2Hash (Join-Path $backupRoot $entry.name)) -ne $entry.before_sha256) {
            throw 'An original monitoring backup is missing or changed.'
        }
    }
    if($ValidateOnly) {'Monitoring paths validated. No files or services changed.';exit 0}
    Assert-SgHy2Administrator
    $lock=[IO.File]::Open((Join-Path $config.operation_root 'rolling.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    if((Get-SgHy2Hash $config.manifest_path) -ne $rolling.current_manifest_sha256) {throw 'Concurrent manifest change.'}
    $pause=Get-Content -LiteralPath (Join-Path $guard.runtime_dir 'pause') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($pause.operation_id -ne $config.id) {throw 'Concurrent maintenance pause by another owner.'}
    $before=@(Get-CimInstance Win32_Service -Filter "Name LIKE 'RealYu%'" | Where-Object {
        $_.Name -notin @('RealYuEvidence','RealYuTunnelGuard')
    } | Select-Object Name,ProcessId,State)
    if($Action -eq 'Apply') {
        if(Test-Path -LiteralPath $backupRoot) {throw 'A monitoring operation already exists; inspect before retrying.'}
        $null=New-Item -ItemType Directory -Path $backupRoot
        foreach($entry in $config.monitor_inputs) {
            if((Get-SgHy2Hash $entry.target) -ne $entry.before_sha256) {throw 'Concurrent monitoring source change.'}
            Copy-Item -LiteralPath $entry.target -Destination (Join-Path $backupRoot $entry.name)
        }
    }
    # Always retain all three original backups before replacing any target.
    $started=[DateTime]::UtcNow
    Write-SgHy2Json (Join-Path $config.operation_root 'monitor-state.json') @{action=$Action;phase='CHANGING';at=$started.ToString('o')}
    foreach($entry in $config.monitor_inputs) {
        $expected=if($Action -eq 'Apply'){@($entry.before_sha256)}else{@($entry.after_sha256,$entry.before_sha256)}
        $currentHash=Get-SgHy2Hash $entry.target
        if($currentHash -notin $expected) {throw 'Concurrent monitoring source change.'}
        if($Action -eq 'Rollback' -and $currentHash -eq $entry.before_sha256) {continue}
        $source=if($Action -eq 'Apply'){$entry.staged_file}else{Join-Path $backupRoot $entry.name}
        $temporary=$entry.target+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
        $previous=$entry.target+'.'+[guid]::NewGuid().ToString('N')+'.bak'
        Copy-Item -LiteralPath $source -Destination $temporary
        [IO.File]::Replace($temporary,$entry.target,$previous)
        [IO.File]::Delete($previous)
        $wanted=if($Action -eq 'Apply'){$entry.after_sha256}else{$entry.before_sha256}
        if((Get-SgHy2Hash $entry.target) -ne $wanted) {throw 'Monitoring replacement verification failed.'}
    }
    Restart-Service -Name 'RealYuEvidence'
    Restart-Service -Name 'RealYuTunnelGuard'
    $expectedPort=if($Action -eq 'Apply'){17897}else{([Uri]$rolling.original_guard.proxy).Port}
    $deadline=[DateTime]::UtcNow.AddSeconds(55)
    do {
        try {
            $latest=Get-Content -LiteralPath (Join-Path $config.observability_root 'evidence\latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $path=Get-Content -LiteralPath (Join-Path $guard.runtime_dir 'path-observer.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $proxy=@($latest.samples | Where-Object {$_.target -eq 'public-via-local-proxy'})
            if([DateTime]::Parse($latest.timestamp).ToUniversalTime() -le $started -or
               [DateTime]::Parse($path.at).ToUniversalTime() -le $started -or $proxy.Count -ne 1 -or
               $proxy[0].transport.remote_port -ne $expectedPort -or -not $proxy[0].healthy -or -not $path.ok) {
                throw 'Waiting for fresh samples on the selected proxy route.'
            }
            $healthy=@($latest.samples | Where-Object {$_.target -in @('public-direct','public-via-local-proxy','backend','bridge','tunnel-primary','tunnel-replica') -and $_.healthy})
            if($healthy.Count -ne 6 -or $path.connection_count -lt 8) {throw 'Incomplete path coverage after monitoring update.'}
            if($Action -eq 'Apply' -and @($path.connections | Where-Object {
                'RealYu-SG-HY2' -in $_.chains -and $_.upload -gt 0 -and $_.download -gt 0
            }).Count -lt 8) {throw 'Missing both connectors from the fresh SG path observation.'}
            break
        } catch {if([DateTime]::UtcNow -ge $deadline){throw};Start-Sleep -Seconds 1}
    } while($true)
    $trafficDeltas=@()
    if($Action -eq 'Apply') {
        # A nonzero lifetime counter does not prove current traffic. Reuse the
        # fresh observer's flow IDs and require increases on both directions.
        $baseline=@{}
        foreach($flow in @($path.connections | Where-Object {'RealYu-SG-HY2' -in $_.chains})) {
            $baseline[$flow.id]=$flow
        }
        $trafficDeadline=[DateTime]::UtcNow.AddSeconds(35)
        do {
            Start-Sleep -Seconds 2
            $current=Get-SgHy2Evidence $config -SkipExitProbe
            $trafficDeltas=@($current.flows | Where-Object {
                $baseline.ContainsKey($_.id) -and 'RealYu-SG-HY2' -in $_.chains -and
                $_.upload -gt $baseline[$_.id].upload -and $_.download -gt $baseline[$_.id].download
            } | ForEach-Object {
                [pscustomobject]@{id=$_.id;edge=$_.destination;
                    upload_delta=($_.upload-$baseline[$_.id].upload);
                    download_delta=($_.download-$baseline[$_.id].download)}
            })
            $covered=@($config.edges | Where-Object {
                $edge=$_; @($trafficDeltas | Where-Object {$_.edge -eq $edge}).Count -ge 2
            })
            if($covered.Count -eq 4) {break}
            if([DateTime]::UtcNow -ge $trafficDeadline) {throw 'SG edge traffic did not advance for both connectors on all four edges.'}
        } while($true)
    }
    $after=@(Get-CimInstance Win32_Service -Filter "Name LIKE 'RealYu%'" | Where-Object {
        $_.Name -notin @('RealYuEvidence','RealYuTunnelGuard')
    } | Select-Object Name,ProcessId,State)
    if(Compare-Object $before $after -Property Name,ProcessId,State) {throw 'An unrelated service changed during monitoring update.'}
    Write-SgHy2Json (Join-Path $config.operation_root 'monitor-state.json') @{action=$Action;phase='VERIFIED';at=[DateTime]::UtcNow.ToString('o');
        sample_at=$latest.timestamp;observer_at=$path.at;proxy_port=$expectedPort;healthy_paths=6;
        traffic_deltas=$trafficDeltas;guard_pause_retained=$true}
    'Existing monitoring services now sample the verified path. Owned guard pause remains until explicitly released.'
} catch {
    if(Get-Variable config -ErrorAction SilentlyContinue) {
        $firstError=Join-Path $config.operation_root 'monitor-first-error.json'
        if(-not (Test-Path -LiteralPath $firstError)) {
            Write-SgHy2Json $firstError @{at=[DateTime]::UtcNow.ToString('o');action=$Action;
                error_type=$_.Exception.GetType().Name;error_message=$_.Exception.Message;error_stack=$_.ScriptStackTrace}
        }
    }
    [Console]::Error.WriteLine('Monitoring update did not complete. Preserve the owned guard pause and inspect private monitor-state/backups; no API or Tunnel restart is performed by this script.')
    exit 1
} finally {if($lock){$lock.Dispose()}}
