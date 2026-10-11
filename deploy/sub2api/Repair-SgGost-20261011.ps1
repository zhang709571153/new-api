#requires -Version 5.1
[CmdletBinding()]
param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$source = 'C:\ProgramData\RealYuServices\bin\gost-3.3.0\gost.exe'
$target = 'C:\ProgramData\RealYuNetwork\sg-hy2-20261010\bin\gost.exe'
$expected = '6f6b66cbdc4a5aa5685d0017abd3f65843a28bcc2bb4e5b78d6d12a844a51aa8'
$incident = 'C:\srv\realyu-observability\incidents\outage-20261011-1454'
$receipt = Join-Path $incident ('repair-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$result = [ordered]@{ started_at = (Get-Date).ToString('o'); status = 'validating'; expected_sha256 = $expected; steps = @() }
try {
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        throw 'The preserved original GOST does not match the deployed SG package digest.'
    }
    $xmlPath = 'C:\ProgramData\RealYuNetwork\sg-hy2-20261010\RealYuSgHy2EdgeProxy.xml'
    [xml]$wrapper = Get-Content -LiteralPath $xmlPath -Raw
    if ($wrapper.service.id -ne 'RealYuSgHy2EdgeProxy' -or $wrapper.service.executable -ne $target -or
        $wrapper.service.arguments -notmatch '-F socks5://127\.0\.0\.1:17897$') {
        throw 'The installed forwarding service differs from the expected SG service.'
    }
    $services = @(Get-CimInstance Win32_Service -Filter "Name='RealYuApi' OR Name='RealYuBridge' OR Name='RealYuSgHy2' OR Name='RealYuSgHy2EdgeProxy'")
    $result.before_services = @($services | Select-Object Name,State,ProcessId)
    if ($ValidateOnly) { 'Validated original hash and exact SG service target; no changes made.'; exit 0 }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Normal Windows administrator elevation is required.'
    }
    $result.status = 'repairing'
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receipt -Encoding UTF8
    $prefs = Get-MpPreference
    $alreadyExcluded = @($prefs.ExclusionPath) -contains $target
    $result.exact_file_exclusion_preexisting = $alreadyExcluded
    if (-not $alreadyExcluded) { Add-MpPreference -ExclusionPath $target }
    if (@((Get-MpPreference).ExclusionPath) -notcontains $target) { throw 'The exact-file exclusion is not effective.' }
    $defenderCli = 'C:\ProgramData\Microsoft\Windows Defender\Platform\4.18.26080.4-0\MpCmdRun.exe'
    $result.exclusion_check = @(& $defenderCli -CheckExclusion -Path $target 2>&1 | ForEach-Object { $_.ToString() })
    $result.exclusion_check_exit = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Defender does not confirm the exact file exclusion.' }
    $result.steps += 'Verified exact SG gost.exe exclusion; no directory or process exclusion added.'
    if (Test-Path -LiteralPath $target) {
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
            throw 'An unexpected target binary exists; refusing to overwrite.'
        }
    } else { Copy-Item -LiteralPath $source -Destination $target }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Restored GOST hash mismatch.' }
    $result.steps += 'Restored original GOST matching the deployed SG package SHA256.'
    $dependencies = @((Get-Service -Name RealYuSgHy2EdgeProxy).DependentServices | Where-Object { $_ })
    if (@($dependencies | Where-Object Name -notin @('RealYuTunnelPrimary','RealYuTunnelReplica')).Count -gt 0) {
        throw 'Unexpected service depends on the SG forwarder; refusing broad force-stop.'
    }
    foreach ($dependency in $dependencies) {
        Stop-Service -Name $dependency.Name
        (Get-Service -Name $dependency.Name).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        $result.steps += ('Stopped dependent ' + $dependency.Name + ' before SG forwarder recovery.')
    }
    Restart-Service -Name RealYuSgHy2EdgeProxy
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(19464,19465,19466,19467) })
        if ($listeners.Count -eq 4) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    if ($listeners.Count -ne 4 -or @($listeners | Where-Object LocalAddress -ne '127.0.0.1').Count -gt 0) { throw 'The four exact SG loopback forwarding listeners did not recover.' }
    $workerIds = @($listeners.OwningProcess | Select-Object -Unique)
    if ($workerIds.Count -ne 1) { throw 'SG listener ownership is ambiguous.' }
    $worker = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $workerIds[0])
    $forwarder = Get-CimInstance Win32_Service -Filter "Name='RealYuSgHy2EdgeProxy'"
    if ($worker.ExecutablePath -ne $target -or $worker.SessionId -ne 0 -or $worker.ParentProcessId -ne $forwarder.ProcessId) {
        throw 'SG listener ownership does not match the expected service worker.'
    }
    $result.forwarder_worker_pid = $worker.ProcessId
    $result.listeners = @($listeners | Select-Object LocalAddress,LocalPort,OwningProcess)
    foreach ($tunnel in @(@{name='RealYuTunnelPrimary';port=20242},@{name='RealYuTunnelReplica';port=18432})) {
        if ((Get-Service -Name $tunnel.name).Status -ne 'Running') {
            Start-Service -Name $tunnel.name
            $result.steps += ('Started ' + $tunnel.name + ' after forwarding recovered.')
            continue
        }
        $ready = $null
        try { $ready = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $tunnel.port + '/ready') -TimeoutSec 3 } catch {}
        if (-not $ready -or $ready.readyConnections -lt 1) {
            Restart-Service -Name $tunnel.name
            $result.steps += ('Restarted disconnected ' + $tunnel.name + ' after forwarding recovered.')
        }
    }
    $result.after_services = @(Get-CimInstance Win32_Service -Filter "Name='RealYuApi' OR Name='RealYuBridge' OR Name='RealYuSgHy2' OR Name='RealYuSgHy2EdgeProxy'" | Select-Object Name,State,ProcessId)
    foreach ($name in @('RealYuApi','RealYuBridge','RealYuSgHy2')) {
        $before = $result.before_services | Where-Object Name -eq $name
        $after = $result.after_services | Where-Object Name -eq $name
        if ($before.ProcessId -ne $after.ProcessId -or $after.State -ne 'Running') { throw ('Unexpected service state change: ' + $name) }
    }
    $mp = Get-MpComputerStatus
    $result.defender = @{ antivirus_enabled = $mp.AntivirusEnabled; realtime_protection_enabled = $mp.RealTimeProtectionEnabled; exact_file_exclusion = @((Get-MpPreference).ExclusionPath) -contains $target }
    $result.status = 'forwarder_restored_public_verification_pending'
} catch {
    $result.status = 'failed'
    $result.error = $_.Exception.Message
} finally {
    if (-not $ValidateOnly) {
        $result.finished_at = (Get-Date).ToString('o')
        $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receipt -Encoding UTF8
    }
}
if ($result.status -eq 'failed') { throw $result.error }
