#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [Parameter(Mandatory=$true)][string]$NetworkPlan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedNetworkPlanSha256,
    [Parameter(Mandatory=$true)][string]$Python,
    [ValidateSet('NetworkAndLauncher','Activate','Complete','ResumeAfterLauncher')][string]$Phase='NetworkAndLauncher'
)
$ErrorActionPreference='Stop'
function Invoke-Step([string]$Name,[string[]]$Arguments) {
    $log=Join-Path $run ($Name+'.log')
    if(Test-Path -LiteralPath $log) {throw 'Step evidence already exists; inspect the previous outcome before retrying.'}
    $priorPreference=$ErrorActionPreference
    try {
        # Windows PowerShell wraps child stderr as ErrorRecord. Preserve it and
        # wait for the child's exit code instead of aborting on its first line.
        $ErrorActionPreference='Continue'
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned @Arguments *> $log
        $stepExit=$LASTEXITCODE
    } finally {$ErrorActionPreference=$priorPreference}
    if($stepExit -ne 0) {throw ('Release step failed: '+$Name)}
}
function Receipt([string]$Name,$Value) {
    $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $run ($Name+'.json')),($Value|ConvertTo-Json -Depth 10),$utf8)
}
try {
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Normal Windows administrator elevation required.'}
    if((Get-FileHash -LiteralPath $Plan -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPlanSha256 -or
       (Get-FileHash -LiteralPath $NetworkPlan -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedNetworkPlanSha256) {throw 'A reviewed plan changed.'}
    $p=Get-Content -LiteralPath $Plan -Raw|ConvertFrom-Json
    $run=$p.runtime_directory
    if($Phase -in @('NetworkAndLauncher','Complete','ResumeAfterLauncher')) {
        $resume=$Phase -eq 'ResumeAfterLauncher'
        $suffix=if($resume){'-resume-'+[guid]::NewGuid().ToString('N')}else{''}
        if($resume) {
            if(Test-Path -LiteralPath (Join-Path $run 'activate.log')) {throw 'Activation already attempted; reconcile its authority before resuming.'}
            & $Python (Join-Path $PSScriptRoot 'singlecore_host_cutover.py') validate --plan $Plan
            if($LASTEXITCODE -ne 0) {throw 'Native resume plan validation failed.'}
            $staged=Get-Content -LiteralPath (Join-Path $run 'launcher-staged.json') -Raw|ConvertFrom-Json
            $actualLauncher=(Get-FileHash -LiteralPath 'C:\ProgramData\RealYuServices\service_entry.py' -Algorithm SHA256).Hash.ToLowerInvariant()
            if($staged.status -ne 'STAGED' -or $actualLauncher -ne $staged.launcher_sha256 -or
               $actualLauncher -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'service_entry_singlecore.py') -Algorithm SHA256).Hash.ToLowerInvariant()) {throw 'The staged launcher changed.'}
            $installedDependencies=@((Get-Service RealYuApi).RequiredServices|ForEach-Object {$_.Name}|Sort-Object)
            if(($installedDependencies -join ',') -ne (@($staged.dependencies|Sort-Object) -join ',')) {throw 'Staged API dependencies changed.'}
        } else {
            Invoke-Step 'launcher-stage' @('-File',(Join-Path $PSScriptRoot 'Install-SingleCoreHostStage.ps1'),'-Plan',$Plan,'-ExpectedPlanSha256',$ExpectedPlanSha256,'-Python',$Python)
        }
        $networkConfig=Get-Content -LiteralPath $NetworkPlan -Raw|ConvertFrom-Json
        foreach($role in @('Replica','Primary')) {
            $stepArguments=@('-File',(Join-Path $PSScriptRoot 'Switch-SgHy2Tunnel.ps1'),'-Plan',$NetworkPlan,'-ExpectedPlanSha256',$ExpectedNetworkPlanSha256,'-Role',$role)
            if($resume) {
                $state=Get-Content -LiteralPath (Join-Path $networkConfig.operation_root 'rolling-state.json') -Raw|ConvertFrom-Json
                $roleState=$state.roles.('tunnel-'+$role.ToLowerInvariant())
                if($roleState -in @('CHANGING','VERIFIED')) {$stepArguments+='-VerifyOnly'}
                elseif($roleState -ne 'ORIGINAL') {throw 'Unexpected rolling role state; inspect before resuming.'}
            }
            Invoke-Step ('network-'+$role.ToLowerInvariant()+$suffix) $stepArguments
        }
        Invoke-Step ('network-monitor'+$suffix) @('-File',(Join-Path $PSScriptRoot 'Set-SgHy2Monitoring.ps1'),'-Plan',$NetworkPlan,'-ExpectedPlanSha256',$ExpectedNetworkPlanSha256)
        Invoke-Step ('network-release-pause'+$suffix) @('-File',(Join-Path $PSScriptRoot 'Switch-SgHy2Tunnel.ps1'),'-Plan',$NetworkPlan,'-ExpectedPlanSha256',$ExpectedNetworkPlanSha256,'-Role','Primary','-ReleaseOnly')
        Receipt 'network-launcher-complete' @{at=[DateTime]::UtcNow.ToString('o');status='NETWORK_AND_LAUNCHER_VERIFIED';customer_authority_changed=$false}
    }
    if($Phase -in @('Activate','Complete','ResumeAfterLauncher')) {
        if(-not (Test-Path -LiteralPath (Join-Path $run 'network-launcher-complete.json'))) {throw 'Network and launcher verification required.'}
        $log=Join-Path $run 'activate.log'
        if(Test-Path -LiteralPath $log) {throw 'An activation attempt already exists; reconcile its authority receipt before retrying.'}
        $priorPreference=$ErrorActionPreference
        try {
            $ErrorActionPreference='Continue'
            & $Python (Join-Path $PSScriptRoot 'singlecore_host_cutover.py') activate --plan $Plan --expected-plan-sha256 $ExpectedPlanSha256 *> $log
            $activationExit=$LASTEXITCODE
        } finally {$ErrorActionPreference=$priorPreference}
        if($activationExit -ne 0) {throw 'Activation did not complete; inspect authority-receipt.json before any recovery action.'}
        $authority=Get-Content -LiteralPath (Join-Path $run 'authority-receipt.json') -Raw|ConvertFrom-Json
        if($authority.phase -ne 'ACTIVE' -or $authority.opened -ne $true) {throw 'Native authority was not confirmed ACTIVE.'}
        # Never let a later host boot restart the superseded OAuth refresh owner.
        foreach($name in @('RealYuSub2API20261009','RealYuSub2APIPrewarm20261009')) {
            if((Get-Service $name).Status -ne 'Stopped') {throw 'A superseded service is still running.'}
            Set-Service -Name $name -StartupType Disabled
            $svc=Get-CimInstance Win32_Service -Filter ("Name='"+$name+"'")
            if($svc.StartMode -ne 'Disabled' -or $svc.State -ne 'Stopped') {throw 'Cannot retire the superseded supply writer.'}
        }
        Receipt 'native-release-complete' @{at=[DateTime]::UtcNow.ToString('o');status='NATIVE_ACTIVE';old_supply_services_disabled=$true;public_e2e='PENDING'}
    }
    'Requested release phase completed; inspect its private receipt and perform the next acceptance step.'
} catch {
    if(Get-Variable run -ErrorAction SilentlyContinue) {
        $first=Join-Path $run ($Phase+'-first-error.json')
        if(-not (Test-Path -LiteralPath $first)) {Receipt ($Phase+'-first-error') @{at=[DateTime]::UtcNow.ToString('o');error_type=$_.Exception.GetType().Name;script_stack=$_.ScriptStackTrace}}
    }
    [Console]::Error.WriteLine('Release phase did not complete. Preserve first errors and inspect the private state; this orchestrator never rolls back a database.')
    exit 1
}
