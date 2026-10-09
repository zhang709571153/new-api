#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$safeError='Host release installation failed; inspect the private operation log.'
try {
    $resolvedPlan=(Resolve-Path -LiteralPath $Plan).ProviderPath
    if((Get-FileHash -LiteralPath $resolvedPlan -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPlanSha256) {
        throw 'The reviewed deployment plan changed.'
    }
    $config=Get-Content -LiteralPath $resolvedPlan -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach($entry in $config.verified_files.PSObject.Properties) {
        if((Get-FileHash -LiteralPath $entry.Name -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) {
            throw 'A reviewed deployment input changed.'
        }
    }
    $installer=Join-Path $PSScriptRoot 'host\Install-Sub2ApiServices.ps1'
    $cutover=Join-Path $PSScriptRoot 'host_cutover.py'
    $adminHelper=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\lab\verify_provider_release.py'))
    foreach($requiredInput in @($installer,$cutover,$adminHelper)) {
        if(-not $config.verified_files.PSObject.Properties[$requiredInput]) {
            throw 'The deployment plan must pin the exact installer, cutover and dynamically imported admin helper.'
        }
    }
    $subRoot=[IO.Path]::GetFullPath($config.sub2api_state_root)
    $releaseRoot=[IO.Path]::GetFullPath($config.sub2api_release_root)
    if($ValidateOnly) {
        'Host release inputs match the reviewed plan. No services or files changed.'
        exit 0
    }
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Installing the new services requires normal Windows administrator elevation.'
    }
    $operationRoot=[IO.Path]::GetFullPath($config.operation_directory)
    $logPath=Join-Path $operationRoot 'elevated-install.log'
    # The new dependency services have already passed isolated persistence and
    # customer preparation checks. Stop only their explicitly recorded temporary
    # processes before this entry; existing production services remain available.
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File $installer -DeploymentRoot $subRoot -ReleaseRoot $releaseRoot -IncludeWorker *> $logPath
    if($LASTEXITCODE -ne 0) {throw 'New dependency service installation failed; existing production was not switched.'}
    # This helper owns the release lock, maintenance gate, backups, exact input
    # checks, ledger verification and bounded public acceptance rollback window.
    & $config.python -X utf8 $cutover --plan $resolvedPlan *>> $logPath
    if($LASTEXITCODE -ne 0) {throw 'Cutover did not finish; inspect cutover-receipt.json for rollback or recovery status.'}
    'Host release published and accepted.'
} catch {
    $safeError=$_.Exception.Message
    if(Test-Path variable:operationRoot) {
        [pscustomobject]@{status='FAILED';message=$safeError;timestamp=[DateTime]::UtcNow.ToString('o')} |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $operationRoot 'elevated-failure.json') -Encoding UTF8
    }
    [Console]::Error.WriteLine($safeError)
    exit 1
}
