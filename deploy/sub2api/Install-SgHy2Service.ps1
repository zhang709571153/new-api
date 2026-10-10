#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'SgHy2.Common.ps1')
try {
    $config=Read-SgHy2Plan $Plan $ExpectedPlanSha256
    Assert-SgHy2PortsFree $config
    foreach($name in $config.services) {
        if(Get-Service -Name $name -ErrorAction SilentlyContinue) {throw 'A candidate service already exists; inspect it before retrying.'}
    }
    if(Test-Path -LiteralPath $config.install_root) {throw 'The installation target already exists; inspect the private receipt.'}
    if($ValidateOnly) {'SG-HY2 package validated. No service, production configuration, or file changed.';exit 0}
    Assert-SgHy2Administrator
    $root=New-Item -ItemType Directory -Path $config.install_root
    # Shared LocalService can read this node credential. It cannot rewrite the
    # executable or configuration. The node is never installed under AppData.
    & icacls.exe $root.FullName /inheritance:r /grant:r ('*'+$config.operator_sid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)RX' >$null
    if($LASTEXITCODE -ne 0) {throw 'Cannot restrict the network installation ACL.'}
    foreach($entry in $config.files.PSObject.Properties) {
        $destination=Join-Path $config.install_root $entry.Name
        $null=New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force
        Copy-Item -LiteralPath (Join-Path $config.package_root $entry.Name) -Destination $destination
        if((Get-SgHy2Hash $destination) -ne $entry.Value) {throw 'Installed artifact hash mismatch.'}
    }
    foreach($directory in @('state','logs')) {
        $writable=New-Item -ItemType Directory -Path (Join-Path $config.install_root $directory)
        & icacls.exe $writable.FullName /grant:r '*S-1-5-19:(OI)(CI)M' >$null
        if($LASTEXITCODE -ne 0) {throw 'Cannot grant the service its bounded runtime directory.'}
    }
    $logPath=Join-Path $config.operation_root 'install.log'
    foreach($name in $config.services) {
        $wrapper=Join-Path $config.install_root ($name+'.exe')
        & $wrapper install *>> $logPath
        if($LASTEXITCODE -ne 0) {throw 'Network SCM registration failed; inspect install.log.'}
        Start-Service -Name $name
        (Get-Service -Name $name).WaitForStatus('Running',[TimeSpan]::FromSeconds(30))
    }
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do {
        try {Assert-SgHy2Services $config;break} catch {
            if([DateTime]::UtcNow -ge $deadline) {throw}
            Start-Sleep -Seconds 1
        }
    } while($true)
    $evidence=Get-SgHy2Evidence $config
    Write-SgHy2Json (Join-Path $config.operation_root 'install-receipt.json') ([ordered]@{
        status='INSTALLED';at=[DateTime]::UtcNow.ToString('o');plan_sha256=$ExpectedPlanSha256;
        production_tunnels_changed=$false;no_login_boot_test='NOT_RUN';evidence=$evidence
    })
    'Independent SG-HY2 services installed. Production Tunnel and Sub2API routes were not switched.'
} catch {
    if(Get-Variable config -ErrorAction SilentlyContinue) {
        Write-SgHy2Json (Join-Path $config.operation_root 'install-failure.json') ([ordered]@{
            status='FAILED';at=[DateTime]::UtcNow.ToString('o');error_type=$_.Exception.GetType().Name;
            error_stack=$_.ScriptStackTrace;production_tunnels_changed=$false
        })
    }
    [Console]::Error.WriteLine('SG-HY2 installation failed. Inspect the private operation directory; existing production was not switched.')
    exit 1
}
