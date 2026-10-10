#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [Parameter(Mandatory=$true)][ValidateSet('Replica','Primary')][string]$Role,
    [ValidateSet('Apply','Rollback')][string]$Action='Apply',
    [switch]$ValidateOnly,
    [switch]$ReleaseOwnedGuardPause,
    [switch]$ReleaseOnly
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'SgHy2.Common.ps1')

function Get-TunnelIdentity([string]$Name) {
    $service=Get-CimInstance Win32_Service -Filter ("Name='"+$Name+"'")
    if(-not $service -or $service.State -ne 'Running') {throw 'A Tunnel service is not running.'}
    $processes=@(Get-CimInstance Win32_Process)
    $parents=@([uint32]$service.ProcessId)
    for($depth=0;$depth -lt 4;$depth++) {
        $children=@($processes | Where-Object {$_.ParentProcessId -in $parents})
        $cloud=@($children | Where-Object {$_.Name -eq 'cloudflared.exe'})
        if($cloud.Count -eq 1) {return $cloud[0]}
        $parents=@($children | ForEach-Object {$_.ProcessId})
    }
    throw 'Cannot identify exactly one cloudflared child of the selected SCM service.'
}

function Assert-TunnelReady([string]$Name, [int]$Port, [int[]]$ExpectedEdges) {
    $ready=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$Port+'/ready') -TimeoutSec 4
    if($ready.status -ne 200 -or $ready.readyConnections -ne 4) {throw 'Tunnel must have four ready edge connections.'}
    $identity=Get-TunnelIdentity $Name
    $connections=@(Get-NetTCPConnection -State Established -OwningProcess $identity.ProcessId -ErrorAction SilentlyContinue |
        Where-Object {$_.RemoteAddress -eq '127.0.0.1' -and $_.RemotePort -in $ExpectedEdges})
    if(@($connections.RemotePort | Sort-Object -Unique).Count -ne 4) {
        throw 'Tunnel is not connected through all four expected GOST loopback ports.'
    }
    [pscustomobject]@{process_id=$identity.ProcessId;created=$identity.CreationDate.ToUniversalTime().ToString('o');
        ready_connections=4;edge_ports=@($ExpectedEdges)}
}

function Set-TunnelDependency([string]$Name, [string[]]$Dependencies) {
    $value=if($Dependencies.Count){$Dependencies -join '/'}else{'/'}
    & sc.exe config $Name 'depend=' $value >$null
    if($LASTEXITCODE -ne 0) {throw 'SCM dependency update failed.'}
    $actual=@((Get-Service -Name $Name).RequiredServices | ForEach-Object {$_.Name} | Sort-Object)
    if(($actual -join ',') -ne (($Dependencies | Sort-Object) -join ',')) {throw 'SCM dependency verification failed.'}
}

function Release-SgHy2OwnedPause($Config, $State, [string]$Mode, [switch]$CheckOnly) {
    if($State.plan_id -ne $Config.id -or (Get-SgHy2Hash $Config.manifest_path) -ne $State.current_manifest_sha256) {
        throw 'Cannot release a pause for a different or drifted operation.'
    }
    if($Mode -eq 'Apply' -and ($State.roles.'tunnel-primary' -ne 'VERIFIED' -or $State.roles.'tunnel-replica' -ne 'VERIFIED')) {
        throw 'Both roles must be verified before releasing the owned guard pause.'
    }
    if($Mode -eq 'Rollback' -and ($State.roles.'tunnel-primary' -notin @('ORIGINAL','ROLLED_BACK') -or $State.roles.'tunnel-replica' -notin @('ORIGINAL','ROLLED_BACK'))) {
        throw 'Both roles must be back on their original route before releasing the guard pause.'
    }
    $guard=Get-Content -LiteralPath $Config.guard_config -Raw -Encoding UTF8 | ConvertFrom-Json
    $proxy=if($Mode -eq 'Apply'){'http://127.0.0.1:17897'}else{$State.original_guard.proxy}
    $proxyConfig=if($Mode -eq 'Apply'){Join-Path $Config.install_root 'config.yaml'}else{$State.original_guard.proxy_config_path}
    if($guard.proxy -ne $proxy -or $guard.proxy_config_path -ne $proxyConfig) {
        throw 'Update and verify monitoring paths before releasing the owned guard pause.'
    }
    $pausePath=Join-Path $guard.runtime_dir 'pause'
    $pause=Get-Content -LiteralPath $pausePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($pause.operation_id -ne $Config.id) {throw 'Guard pause ownership changed.'}
    if(-not $CheckOnly) {Remove-Item -LiteralPath $pausePath}
}

$lock=$null
$changed=$false
try {
    $config=Read-SgHy2Plan $Plan $ExpectedPlanSha256
    if($ReleaseOnly) {
        if(-not $ValidateOnly) {
            Assert-SgHy2Administrator
            $lock=[IO.File]::Open((Join-Path $config.operation_root 'rolling.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        }
        $state=Get-Content -LiteralPath (Join-Path $config.operation_root 'rolling-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        Release-SgHy2OwnedPause $config $state $Action -CheckOnly:$ValidateOnly
        'Owned guard pause release completed or validated. No Tunnel was restarted.'
        exit 0
    }
    if($Action -eq 'Apply') {
        Assert-SgHy2Services $config
        $null=Get-SgHy2Evidence $config
    } elseif((Get-Service -Name 'RealYuEdgeProxy').Status -ne 'Running') {
        throw 'The preserved original GOST service must be running before rollback.'
    }
    $roleName='tunnel-'+$Role.ToLowerInvariant()
    $serviceName='RealYuTunnel'+$Role
    $peerName=if($Role -eq 'Replica'){'RealYuTunnelPrimary'}else{'RealYuTunnelReplica'}
    $metrics=if($Role -eq 'Replica'){18432}else{20242}
    $peerMetrics=if($Role -eq 'Replica'){20242}else{18432}
    $statePath=Join-Path $config.operation_root 'rolling-state.json'
    $manifest=Get-Content -LiteralPath $config.manifest_path -Raw -Encoding UTF8 | ConvertFrom-Json
    $guard=Get-Content -LiteralPath $config.guard_config -Raw -Encoding UTF8 | ConvertFrom-Json
    $pausePath=Join-Path $guard.runtime_dir 'pause'
    $backupRoot=Join-Path $config.operation_root 'rolling-backup'
    $state=$null
    if(Test-Path -LiteralPath $statePath) {
        $state=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if($state.plan_id -ne $config.id) {throw 'Rolling state belongs to another plan.'}
        if((Get-SgHy2Hash $config.manifest_path) -ne $state.current_manifest_sha256) {
            throw 'Production manifest changed outside this operation; do not overwrite it.'
        }
    } else {
        if($Action -ne 'Apply' -or $Role -ne 'Replica') {throw 'Start with Apply Replica.'}
        if((Get-SgHy2Hash $config.manifest_path) -ne $config.manifest_sha256 -or
           (Get-SgHy2Hash $config.guard_config) -ne $config.guard_config_sha256) {
            throw 'Production inputs changed after review; prepare a fresh plan.'
        }
    }
    if($state -and $Action -eq 'Apply' -and $Role -eq 'Primary' -and $state.roles.'tunnel-replica' -ne 'VERIFIED') {
        throw 'Replica must pass before primary can switch.'
    }
    if($state -and $Action -eq 'Rollback' -and $Role -eq 'Replica' -and $state.roles.'tunnel-primary' -eq 'VERIFIED') {
        throw 'Rollback primary before replica.'
    }
    $xmlPath=$config.tunnel_xml.$roleName.path
    $expectedXml=if($state){$state.current_xml_sha256.$roleName}else{$config.tunnel_xml.$roleName.sha256}
    if((Get-SgHy2Hash $xmlPath) -ne $expectedXml) {throw 'Tunnel wrapper XML changed outside this operation.'}
    $peerRole='tunnel-'+$(if($Role -eq 'Replica'){'primary'}else{'replica'})
    $peerEdges=@($manifest.tunnel_edge_addrs_by_role.$peerRole | ForEach-Object {[int]($_.Split(':')[-1])})
    $peerBefore=$null
    if($Action -eq 'Apply') {$peerBefore=Assert-TunnelReady $peerName $peerMetrics $peerEdges}
    else {
        # A broken new route must not prevent restoring the preserved old route.
        try {$identity=Get-TunnelIdentity $peerName;$peerBefore=[pscustomobject]@{process_id=$identity.ProcessId;created=$identity.CreationDate.ToUniversalTime().ToString('o')}} catch {}
    }
    if(Test-Path -LiteralPath $pausePath) {
        $pause=Get-Content -LiteralPath $pausePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if($pause.operation_id -ne $config.id) {throw 'A guard pause is owned by another maintenance operation.'}
    }
    if($ValidateOnly) {'Rolling stage validated. No production file or service changed.';exit 0}
    Assert-SgHy2Administrator
    $lock=[IO.File]::Open((Join-Path $config.operation_root 'rolling.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    # Re-check the owner guard after acquiring the operation lock.
    if(Test-Path -LiteralPath $statePath) {
        $locked=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if(-not $state -or $locked.current_manifest_sha256 -ne $state.current_manifest_sha256 -or
           $locked.current_manifest_sha256 -ne (Get-SgHy2Hash $config.manifest_path)) {throw 'Concurrent rolling state or manifest change.'}
    } elseif($state -or (Get-SgHy2Hash $config.manifest_path) -ne $config.manifest_sha256) {
        throw 'Concurrent rolling state or manifest change.'
    }
    if((Get-SgHy2Hash $xmlPath) -ne $expectedXml) {throw 'Concurrent wrapper change.'}
    if(Test-Path -LiteralPath $pausePath) {
        $pause=Get-Content -LiteralPath $pausePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if($pause.operation_id -ne $config.id) {throw 'Concurrent maintenance pause by another owner.'}
    }
    if(-not $state) {
        $null=New-Item -ItemType Directory -Path $backupRoot
        Copy-Item -LiteralPath $config.manifest_path -Destination (Join-Path $backupRoot 'manifest.json')
        $originalDependencies=@{}
        $originalEdges=@{}
        $xmlHashes=@{}
        foreach($name in @('Primary','Replica')) {
            $key='tunnel-'+$name.ToLowerInvariant()
            $entry=$config.tunnel_xml.$key
            if((Get-SgHy2Hash $entry.path) -ne $entry.sha256) {throw 'A peer wrapper changed after review.'}
            Copy-Item -LiteralPath $entry.path -Destination (Join-Path $backupRoot ($name+'.xml'))
            $originalDependencies[$key]=@((Get-Service -Name ('RealYuTunnel'+$name)).RequiredServices | ForEach-Object {$_.Name})
            $originalEdges[$key]=@($manifest.tunnel_edge_addrs_by_role.$key)
            $xmlHashes[$key]=$entry.sha256
        }
        $state=[pscustomobject]@{plan_id=$config.id;current_manifest_sha256=$config.manifest_sha256;
            current_xml_sha256=[pscustomobject]$xmlHashes;original_dependencies=[pscustomobject]$originalDependencies;
            original_edges=[pscustomobject]$originalEdges;
            original_guard=[pscustomobject]@{proxy=$guard.proxy;proxy_config_path=$guard.proxy_config_path};
            roles=[pscustomobject]@{'tunnel-primary'='ORIGINAL';'tunnel-replica'='ORIGINAL'}}
        Write-SgHy2Json $statePath $state
    }
    Write-SgHy2Json $pausePath ([ordered]@{operation_id=$config.id;at=[DateTime]::UtcNow.ToString('o');reason='SG-HY2 rolling network cutover'})
    # Never stop/restart RealYuEdgeProxy. The unselected connector stays alive.
    $targetEdges=if($Action -eq 'Apply'){@($config.ports[2..5] | ForEach-Object {'127.0.0.1:'+$_})}else{@($state.original_edges.$roleName)}
    $dependencies=if($Action -eq 'Apply'){
        @(@($state.original_dependencies.$roleName | Where-Object {$_ -ne 'RealYuEdgeProxy'}) + 'RealYuSgHy2EdgeProxy' | Sort-Object -Unique)
    }else{@($state.original_dependencies.$roleName)}
    $state.roles.$roleName='CHANGING'
    Write-SgHy2Json $statePath $state
    $manifest.tunnel_edge_addrs_by_role.$roleName=$targetEdges
    Write-SgHy2Json $config.manifest_path $manifest
    $changed=$true
    $state.current_manifest_sha256=Get-SgHy2Hash $config.manifest_path
    Write-SgHy2Json $statePath $state
    [xml]$wrapper=Get-Content -LiteralPath (Join-Path $backupRoot ($Role+'.xml')) -Raw -Encoding UTF8
    foreach($dependency in @($wrapper.service.SelectNodes('depend'))) {$null=$wrapper.service.RemoveChild($dependency)}
    foreach($dependency in $dependencies) {$node=$wrapper.CreateElement('depend');$node.InnerText=$dependency;$null=$wrapper.service.AppendChild($node)}
    $xmlTemp=$xmlPath+'.sg-hy2.tmp'
    $wrapper.Save($xmlTemp)
    $xmlBackup=$xmlPath+'.replace-'+[guid]::NewGuid().ToString('N')+'.bak'
    [IO.File]::Replace($xmlTemp,$xmlPath,$xmlBackup)
    [IO.File]::Delete($xmlBackup)
    $state.current_xml_sha256.$roleName=Get-SgHy2Hash $xmlPath
    Write-SgHy2Json $statePath $state
    Set-TunnelDependency $serviceName $dependencies
    Restart-Service -Name $serviceName
    $deadline=[DateTime]::UtcNow.AddSeconds(55)
    do {
        try {$selected=Assert-TunnelReady $serviceName $metrics @($targetEdges | ForEach-Object {[int]($_.Split(':')[-1])});break}
        catch {if([DateTime]::UtcNow -ge $deadline){throw};Start-Sleep -Seconds 1}
    } while($true)
    $peerAfter=$null
    if($Action -eq 'Apply') {$peerAfter=Assert-TunnelReady $peerName $peerMetrics $peerEdges}
    else {
        try {$identity=Get-TunnelIdentity $peerName;$peerAfter=[pscustomobject]@{process_id=$identity.ProcessId;created=$identity.CreationDate.ToUniversalTime().ToString('o')}} catch {}
    }
    if($peerBefore -and $peerAfter -and ($peerBefore.process_id -ne $peerAfter.process_id -or $peerBefore.created -ne $peerAfter.created)) {
        throw 'The peer connector changed during this operation; stop the rolling cutover.'
    }
    $evidence=$null
    if($Action -eq 'Apply') {
        $evidence=Get-SgHy2Evidence $config
        if(@($evidence.flows | Where-Object {
            'RealYu-SG-HY2' -in $_.chains -and $_.upload -gt 0 -and $_.download -gt 0
        }).Count -lt 4) {throw 'Missing live SG-HY2 edge traffic evidence.'}
    }
    $public=@()
    $explicitProxy=if($Action -eq 'Apply'){'http://127.0.0.1:17897'}else{$state.original_guard.proxy}
    foreach($proxy in @('direct',$explicitProxy)) {
        $probe=[guid]::NewGuid().ToString('N')
        $args=@('-q','-sS','--fail','--max-time','15','--max-filesize','1048576','-H',('X-Realyu-Probe-Id: '+$probe))
        if($proxy -eq 'direct') {$args+=@('--noproxy','*')}else{$args+=@('--proxy',$proxy,'--noproxy','realyu.invalid')}
        $body=& curl.exe @args 'https://api.realyu.fun/api/status' 2>$null
        if($LASTEXITCODE -ne 0 -or ($body | ConvertFrom-Json).success -ne $true) {throw 'Public complete status body failed after the rolling stage.'}
        $public+=@([pscustomobject]@{observer=$proxy;probe_id=$probe;complete_status_body=$true})
    }
    $state.roles.$roleName=if($Action -eq 'Apply'){'VERIFIED'}else{'ROLLED_BACK'}
    Write-SgHy2Json $statePath $state
    Write-SgHy2Json (Join-Path $config.operation_root ($Action.ToLowerInvariant()+'-'+$Role.ToLowerInvariant()+'-receipt.json')) ([ordered]@{
        status=$state.roles.$roleName;at=[DateTime]::UtcNow.ToString('o');selected=$selected;
        peer_unchanged=$(if($peerBefore -and $peerAfter){$true}else{$null});
        evidence=$evidence;public=$public;acceptance_scope='SCM identity, route, edge traffic and complete status only; long SSE/model acceptance remains separate'
    })
    if($ReleaseOwnedGuardPause) {
        Release-SgHy2OwnedPause $config $state $Action
    }
    'Selected Tunnel stage completed. Review its private receipt before the next stage.'
} catch {
    if($changed) {
        [Console]::Error.WriteLine('Rolling stage failed after changing this role. The peer was not deliberately restarted. Keep the guard pause, inspect rolling-state.json, and run this role with -Action Rollback after verifying the expected hashes.')
    } else {[Console]::Error.WriteLine('Rolling preflight failed. No selected Tunnel change was applied by this invocation.')}
    exit 1
} finally {
    if($lock) {$lock.Dispose()}
}
