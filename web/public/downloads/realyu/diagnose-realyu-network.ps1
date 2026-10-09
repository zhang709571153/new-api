param(
    [string]$ProxyUrl = '',
    [string]$Label = 'current',
    [string]$OutputDirectory = ''
)

# Public read-only probes. No API keys, installs, retries, or network changes.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$realyuCurl = Join-Path $env:SystemRoot 'System32\curl.exe'
if (-not (Test-Path -LiteralPath $realyuCurl)) { throw 'System curl.exe was not found.' }
if (-not $OutputDirectory) { $OutputDirectory = [Environment]::GetFolderPath('Desktop') }
if (-not $OutputDirectory) { $OutputDirectory = $env:TEMP }
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$realyuSafeLabel = $Label -replace '[^a-zA-Z0-9_-]', '_'
$realyuStamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$realyuReport = Join-Path $OutputDirectory ('realyu-network-' + $realyuSafeLabel + '-' + $realyuStamp + '.txt')
$realyuWork = Join-Path $env:TEMP ('realyu-network-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $realyuWork
$realyuLines = [Collections.Generic.List[string]]::new()
$realyuRows = [Collections.Generic.List[object]]::new()

function Write-RealyuReport([string]$Text) {
    Write-Host $Text
    $realyuLines.Add($Text)
    [IO.File]::WriteAllLines($realyuReport, $realyuLines, [Text.UTF8Encoding]::new($true))
}

function Hide-RealyuProxySecrets([string]$Value) {
    # Keep endpoint information, strip credentials and PAC query/fragment data.
    if (-not $Value) { return '(not set)' }
    return (($Value -replace '[^/\s;=]+@', '[redacted]@') -replace '[?#][^\s;]*', '[redacted]')
}

function Invoke-RealyuProbe {
    param([string]$Name, [string]$Url, [string[]]$NetworkArgs = @(), [switch]$Download)
    $headers = Join-Path $realyuWork ($Name + '.headers')
    $errors = Join-Path $realyuWork ($Name + '.stderr')
    $format = 'http=%{http_code} remote=%{remote_ip} dns_s=%{time_namelookup} tcp_s=%{time_connect} tls_s=%{time_appconnect} ttfb_s=%{time_starttransfer} total_s=%{time_total} bytes=%{size_download} speed_Bps=%{speed_download}'
    # --disable MUST be first: exclude .curlrc settings from this controlled probe.
    $curlArgs = @('--disable', '--silent', '--show-error', '--connect-timeout', '6', '--max-time', '15',
        '--proto', '=https', '--header', 'Accept-Encoding: identity', '--user-agent', 'Realyu-Setup/1.0',
        '--dump-header', $headers, '--output', 'NUL', '--write-out', $format)
    if ($Download) { $curlArgs += @('--range', '0-2097151', '--max-filesize', '2097152') }
    $curlArgs += $NetworkArgs
    $curlArgs += $Url
    Write-RealyuReport ('--- ' + $Name + ' / ' + [DateTimeOffset]::Now.ToString('o'))
    $metrics = (& $realyuCurl @curlArgs 2> $errors) -join ' '
    $exitCode = $LASTEXITCODE
    Write-RealyuReport ('curl_exit=' + $exitCode + ' ' + $metrics)
    if (Test-Path -LiteralPath $headers) {
        foreach ($line in Get-Content -LiteralPath $headers) {
            if ($line -match '^(HTTP/|CF-RAY:|CF-Cache-Status:|Content-Range:|Content-Length:|Content-Type:|Age:|X-Oneapi-Request-Id:|X-New-Api-Version:)') {
                Write-RealyuReport $line
            }
        }
    }
    # Avoid collecting arbitrary proxy error text or credentials in a proxy URL.
    if ($exitCode -ne 0) {
        $meaning = switch ($exitCode) {
            5 { 'Proxy DNS failure' }
            6 { 'Target DNS failure' }
            7 { 'TCP connect failure' }
            18 { 'Incomplete response' }
            28 { 'Time limit reached; bytes/timings distinguish no connection from slow transfer' }
            35 { 'TLS handshake failure' }
            56 { 'Connection receive failure' }
            60 { 'TLS certificate validation failure' }
            63 { 'Response exceeded the 2 MiB probe cap; server may have ignored Range' }
            default { 'curl failure; correlate code with timings and response headers' }
        }
        Write-RealyuReport ('error=' + $meaning)
    }
    $realyuRows.Add([pscustomobject]@{Name=$Name; ExitCode=$exitCode; Metrics=$metrics})
    Remove-Item -LiteralPath $headers, $errors -Force -ErrorAction SilentlyContinue
}

Write-RealyuReport ('Realyu network diagnostic / ' + [DateTimeOffset]::Now.ToString('o'))
Write-RealyuReport ('PowerShell=' + $PSVersionTable.PSVersion.ToString() + ' Label=' + $realyuSafeLabel)
Write-RealyuReport ((& $realyuCurl --version | Select-Object -First 1) -join '')
Write-RealyuReport 'No API key needed. Requests are public and unbilled. Per curl probe: 15 seconds maximum, no retries.'
Write-RealyuReport 'noproxy only bypasses explicit HTTP/SOCKS proxies; it does NOT bypass VPN/TUN/router routing.'
Write-RealyuReport 'Timings are cumulative seconds from request start. Download samples are at most 2 MiB each.'
Write-RealyuReport '--- DNS as currently configured (no resolver changes)'
foreach ($recordType in @('A', 'AAAA')) {
    try {
        $answers = Resolve-DnsName -Name 'api.realyu.fun' -Type $recordType -QuickTimeout -ErrorAction Stop
        foreach ($answer in $answers) {
            if ($answer.IPAddress) { Write-RealyuReport ($recordType + ' ' + $answer.IPAddress) }
            elseif ($answer.NameHost) { Write-RealyuReport ($recordType + ' CNAME=' + $answer.NameHost) }
        }
    } catch { Write-RealyuReport ($recordType + ' DNS lookup failed: ' + $_.Exception.GetType().Name) }
}
Write-RealyuReport '--- Proxy settings (read only)'
foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    Write-RealyuReport ($name + '=' + (Hide-RealyuProxySecrets $value))
}
$inet = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
Write-RealyuReport ('WindowsProxyEnabled=' + [string]$inet.ProxyEnable)
Write-RealyuReport ('WindowsProxyServer=' + (Hide-RealyuProxySecrets ([string]$inet.ProxyServer)))
Write-RealyuReport ('WindowsPAC=' + (Hide-RealyuProxySecrets ([string]$inet.AutoConfigURL)))

$manifestWatch = [Diagnostics.Stopwatch]::StartNew()
$manifest = $null
try {
    $manifest = Invoke-RestMethod -Uri 'https://api.realyu.fun/downloads/realyu/windows-client.json' -TimeoutSec 15 -MaximumRedirection 0 -ErrorAction Stop
    if ($manifest -is [string]) { $manifest = $manifest.TrimStart([char]65279) | ConvertFrom-Json -ErrorAction Stop }
    if ($manifest.sha256 -notmatch '^[0-9a-f]{64}$' -or $manifest.file -ne 'realyu-client.exe') { throw 'Unexpected public manifest' }
    Write-RealyuReport ('PowerShell_manifest=OK seconds=' + [math]::Round($manifestWatch.Elapsed.TotalSeconds, 3) + ' version=' + $manifest.version + ' bytes=' + $manifest.bytes)
} catch {
    $manifest = $null
    Write-RealyuReport ('PowerShell_manifest=FAIL seconds=' + [math]::Round($manifestWatch.Elapsed.TotalSeconds, 3) + ' error_type=' + $_.Exception.GetType().Name)
}

foreach ($round in 1..3) {
    Invoke-RealyuProbe -Name ('status_default_' + $round) -Url 'https://api.realyu.fun/api/status'
}
Invoke-RealyuProbe -Name 'status_ipv4_noproxy' -Url 'https://api.realyu.fun/api/status' -NetworkArgs @('--ipv4','--noproxy','*')
Invoke-RealyuProbe -Name 'status_ipv6_noproxy' -Url 'https://api.realyu.fun/api/status' -NetworkArgs @('--ipv6','--noproxy','*')
Invoke-RealyuProbe -Name 'control_microsoft' -Url 'https://www.microsoft.com/'
Invoke-RealyuProbe -Name 'control_baidu' -Url 'https://www.baidu.com/'
$downloadUrl = 'https://api.realyu.fun/downloads/realyu/realyu-client.exe'
if ($manifest) { $downloadUrl += '?v=' + $manifest.sha256 + '&download=2' }
else { Write-RealyuReport 'Manifest unavailable: download probe uses unversioned URL; its CDN cache state may differ from the installer.' }
Invoke-RealyuProbe -Name 'download_default' -Url $downloadUrl -Download
Invoke-RealyuProbe -Name 'download_ipv4_noproxy' -Url $downloadUrl -Download -NetworkArgs @('--ipv4','--noproxy','*')
if ($ProxyUrl) {
    Write-RealyuReport ('ExplicitProxy=' + (Hide-RealyuProxySecrets $ProxyUrl))
    # PowerShell 5.1 drops empty native arguments, so use a nonmatching bypass name.
    Invoke-RealyuProbe -Name 'status_explicit_proxy' -Url 'https://api.realyu.fun/api/status' -NetworkArgs @('--proxy',$ProxyUrl,'--noproxy','realyu-no-bypass.invalid')
    Invoke-RealyuProbe -Name 'download_explicit_proxy' -Url $downloadUrl -Download -NetworkArgs @('--proxy',$ProxyUrl,'--noproxy','realyu-no-bypass.invalid')
}
Write-RealyuReport '--- Complete. HTTP 000 means no HTTP response. HTTP 502/530 requires edge/origin correlation; it does not by itself identify the fault.'
Write-RealyuReport 'IPv6 failure alone may simply mean this network has no usable IPv6. A short successful sample is not proof of long-term health.'
$csv = [IO.Path]::ChangeExtension($realyuReport, '.csv')
$realyuRows | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
Remove-Item -LiteralPath $realyuWork -ErrorAction SilentlyContinue
Write-Host ('Report: ' + $realyuReport) -ForegroundColor Green
