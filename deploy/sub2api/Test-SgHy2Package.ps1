#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Plan,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$CloudflareCa,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedCloudflareCaSha256
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'SgHy2.Common.ps1')
$children=@()
$receipt=[ordered]@{status='STARTING';at=[DateTime]::UtcNow.ToString('o');production_changed=$false;
    scope='headless process only; no SCM installation, Tunnel creation, paid model, or production-route test'}
try {
    $config=Read-SgHy2Plan $Plan $ExpectedPlanSha256
    if((Get-SgHy2Hash $CloudflareCa) -ne $ExpectedCloudflareCaSha256) {throw 'The reviewed cloudflared CA bundle changed.'}
    Assert-SgHy2PortsFree $config
    $receipt.production_manifest_sha256_before=Get-SgHy2Hash $config.manifest_path
    $state=Join-Path $config.operation_root 'headless-state'
    $null=New-Item -ItemType Directory -Path $state -Force
    $mihomo=Join-Path $config.package_root 'bin\mihomo.exe'
    $yaml=Join-Path $config.package_root 'config.yaml'
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$mihomo;$start.Arguments='-t -d "'+$state+'" -f "'+$yaml+'"'
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $test=New-Object Diagnostics.Process;$test.StartInfo=$start;$null=$test.Start()
    $stdout=$test.StandardOutput.ReadToEndAsync();$stderr=$test.StandardError.ReadToEndAsync()
    if(-not $test.WaitForExit(15000)) {$test.Kill();throw 'Candidate config validation timed out.'}
    [IO.File]::WriteAllText((Join-Path $state 'check.out.log'),$stdout.Result)
    [IO.File]::WriteAllText((Join-Path $state 'check.err.log'),$stderr.Result)
    if($test.ExitCode -ne 0) {throw 'Candidate config validation failed.'}
    $core=Start-Process -FilePath $mihomo -ArgumentList @('-d',('"'+$state+'"'),'-f',('"'+$yaml+'"')) -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state 'mihomo.out.log') -RedirectStandardError (Join-Path $state 'mihomo.err.log')
    $children+=@($core)
    [xml]$wrapper=Get-Content -LiteralPath (Join-Path $config.package_root 'RealYuSgHy2EdgeProxy.xml') -Raw
    $gost=Start-Process -FilePath (Join-Path $config.package_root 'bin\gost.exe') -ArgumentList $wrapper.service.arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state 'gost.out.log') -RedirectStandardError (Join-Path $state 'gost.err.log')
    $children+=@($gost)
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    do {
        $listeners=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.LocalPort -in $config.ports})
        if($listeners.Count -eq 6) {break}
        if($core.HasExited -or $gost.HasExited -or [DateTime]::UtcNow -ge $deadline) {throw 'Candidate listeners failed to start.'}
        Start-Sleep -Milliseconds 250
    } while($true)
    foreach($listener in $listeners) {
        if($listener.LocalAddress -ne '127.0.0.1' -or $listener.OwningProcess -notin @($core.Id,$gost.Id)) {
            throw 'Candidate listener has an unexpected address or owner.'
        }
    }
    $candidate=$config.PSObject.Copy()
    $candidate.install_root=$config.package_root
    $receipt.network=Get-SgHy2Evidence $candidate
    $tls=& $Python -B (Join-Path $PSScriptRoot 'sg_hy2_tls_probe.py') --ca $CloudflareCa
    $receipt.edge_tls=@($tls | ConvertFrom-Json | ForEach-Object {$_})
    if($LASTEXITCODE -ne 0) {throw 'Candidate GOST edge TLS verification failed.'}
    $receipt.status='PASS'
} catch {
    $receipt.status='FAIL'
    $receipt.error_type=$_.Exception.GetType().Name
    $receipt.error_stack=$_.ScriptStackTrace
} finally {
    foreach($child in $children) {
        $child.Refresh()
        if(-not $child.HasExited) {$child.Kill();$null=$child.WaitForExit(10000)}
    }
    if(Get-Variable config -ErrorAction SilentlyContinue) {
        $receipt.production_manifest_sha256_after=Get-SgHy2Hash $config.manifest_path
        $remaining=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.LocalPort -in $config.ports})
        $receipt.temporary_listeners_closed=($remaining.Count -eq 0)
        if($remaining.Count -or $receipt.production_manifest_sha256_before -ne $receipt.production_manifest_sha256_after) {$receipt.status='FAIL'}
        Write-SgHy2Json (Join-Path $config.operation_root 'headless-receipt.json') $receipt
    }
}
$receipt | ConvertTo-Json -Depth 8
if($receipt.status -ne 'PASS') {exit 1}
