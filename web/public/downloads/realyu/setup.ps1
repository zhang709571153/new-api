param(
    [string]$ApiKey = '',
    [string]$PackagePath = '',
    [string]$PackageUrl = '',
    [string]$CacheDirectory = '',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$packageSha = '6da1a0d7b8d0b7c0e09f1030d48269d12ce6a55a3e622d37aac92aa144a6a93b'
$packageBytes = 135922
$packageName = 'realyu-setup-windows.zip'

function Assert-RealyuLocalPath([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '[CACHE_PATH_UNSAFE] 缓存路径包含链接。' }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Get-RealyuDownloadProxyArguments([Uri]$Uri) {
    # Match the .NET requests used for key checks and image generation.
    # An explicit curl option prevents inherited shell proxy variables diverging.
    $proxy = [Net.WebRequest]::DefaultWebProxy
    if ($null -eq $proxy) { return @('--noproxy', '*') }
    try { $target = $proxy.GetProxy($Uri) }
    catch { throw '[PROXY_CONFIG_INVALID] 无法读取系统代理，请检查 Windows 代理设置。' }
    if ($null -eq $target -or $target.Equals($Uri)) { return @('--noproxy', '*') }
    if ($target.Scheme -notin @('http','https') -or $target.UserInfo) {
        throw '[PROXY_CONFIG_INVALID] 当前系统代理类型或认证格式不受支持，请检查 Windows 代理设置。'
    }
    # Separate option/value works with Windows curl versions before 8.16.
    return @('--proxy', $target.AbsoluteUri)
}

function Test-RealyuPackage([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    Assert-RealyuLocalPath $Path
    if ((Get-Item -LiteralPath $Path).Length -ne $packageBytes) { return $false }
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
    return ($digest -eq $packageSha)
}

Write-Host ("配置与图片组件：{0:N0} bytes（{1:N1} KB），无需安装 Python 或 Node。" -f $packageBytes, ($packageBytes/1000))
if ($CheckOnly) { return }
if (-not $CacheDirectory) { $CacheDirectory = Join-Path $env:LOCALAPPDATA 'Realyu\setup-windows' }
$folder = Join-Path ([IO.Path]::GetFullPath($CacheDirectory)) $packageSha
Assert-RealyuLocalPath $folder
$null = New-Item -ItemType Directory -Path $folder -Force
$security = [Security.AccessControl.DirectorySecurity]::new()
$security.SetAccessRuleProtection($true, $false)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', $inherit, 'None', 'Allow')
    $security.AddAccessRule($rule)
}
if ($PSVersionTable.PSEdition -eq 'Core') { [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($folder), $security) }
else { [IO.Directory]::SetAccessControl($folder, $security) }
$archive = Join-Path $folder $packageName
Assert-RealyuLocalPath $archive
if (-not (Test-RealyuPackage $archive)) {
    if (-not $PackagePath -and -not $PackageUrl -and $PSScriptRoot) {
        $localPackage = Join-Path $PSScriptRoot $packageName
        if (Test-Path -LiteralPath $localPackage -PathType Leaf) { $PackagePath = $localPackage }
    }
    if ($PackagePath) {
        if (-not (Test-RealyuPackage $PackagePath)) { throw '[PACKAGE_INVALID] 本地配置包大小或 SHA256 不匹配。' }
        Copy-Item -LiteralPath $PackagePath -Destination $archive -Force
    } else {
        if (-not $PackageUrl) { $PackageUrl = 'https://api.realyu.fun/downloads/realyu/realyu-setup-windows.zip?v=6da1a0d7b8d0b7c0e09f1030d48269d12ce6a55a3e622d37aac92aa144a6a93b' }
        $uri = [Uri]$PackageUrl
        if ($uri.UserInfo -or ($uri.Scheme -ne 'https' -and -not ($uri.Scheme -eq 'http' -and $uri.Host -in @('127.0.0.1','[::1]')))) {
            throw '[PACKAGE_URL_INVALID] 仅接受 HTTPS 地址或本地回环测试地址。'
        }
        $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
        if (-not (Test-Path -LiteralPath $curl)) { throw '[CURL_MISSING] 未找到 Windows curl.exe。' }
        $partial = Join-Path $folder ([Guid]::NewGuid().ToString('N') + '.download')
        Write-Host ("下载总大小：{0:N0} bytes。下方 curl 显示总量、平均/当前速度及剩余时间。" -f $packageBytes)
        try {
            $proxyArgs = @(Get-RealyuDownloadProxyArguments $uri)
            Write-Host '[网络] 下载遵循 Windows 系统代理设置。'
            # A fixed stdin config preserves the empty bypass list in PS5.1 and old curl.
            'noproxy = ""' | & $curl --disable --config - @proxyArgs --fail --show-error --connect-timeout 10 --max-time 180 --max-filesize "$packageBytes" --output $partial $PackageUrl
            if ($LASTEXITCODE -ne 0) {
                $reason=switch ($LASTEXITCODE) { 2 {'下载参数不兼容，请重新获取最新版安装入口'} 6 {'DNS 无法解析下载域名'} 7 {'无法连接下载服务器'} 22 {'服务器返回 HTTP 错误，状态见上方 curl 输出'} 23 {'无法写入文件，请检查空间和目录权限'} 28 {'连接或下载超时'} 35 {'TLS 连接失败'} 60 {'HTTPS 证书校验失败，请检查系统时间与证书'} 63 {'服务器返回内容超过预期大小'} default { 'curl 错误码 '+$LASTEXITCODE } }
                throw ('[DOWNLOAD_INTERRUPTED] '+$reason+'。未自动重试；排除该原因后重新执行原命令。')
            }
            if (-not (Test-RealyuPackage $partial)) { throw '[PACKAGE_INVALID] 配置包大小或 SHA256 不匹配，未执行。请重新下载。' }
            Move-Item -LiteralPath $partial -Destination $archive -Force
        } finally { if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force } }
    }
}
if (-not (Test-RealyuPackage $archive)) { throw '[PACKAGE_INVALID] 配置包最终校验失败。' }
Write-Host '基础配置包大小与 SHA256 校验通过。'
Write-Host '[准备] 正在解压配置与图片组件…'
$null = [Reflection.Assembly]::Load('System.IO.Compression.FileSystem, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089')
$stage = Join-Path $folder ([Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $stage
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    $expected = @('common.ps1','native.cs','image.cs','setup-core.ps1','realyu-images.ps1','tools.json','models.json','errors.json','workbuddy-core.ps1','workbuddy-config.ps1')
    if ($zip.Entries.Count -ne $expected.Count) { throw '[PACKAGE_CONTENTS_INVALID] 配置包内容不匹配。' }
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName -notin $expected -or $entry.Length -gt 1000000) { throw '[PACKAGE_CONTENTS_INVALID] 配置包内容不匹配。' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $stage $entry.FullName), $false)
    }
} finally { $zip.Dispose() }
Write-Host '[准备] 解压完成。'
if (-not $ApiKey) {
    $secure = Read-Host '请输入 API Key（不回显）' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$start.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $stage 'setup-core.ps1') + '"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$start.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
$start.EnvironmentVariables['PSModulePath'] = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules'
foreach ($name in @('REALYU_IMAGE_RUNTIME','REALYU_CA_BUNDLE','OPENAI_API_KEY','REALYU_API_KEY')) { $start.EnvironmentVariables.Remove($name) }
Write-Host '[启动] 正在启动 Windows PowerShell 配置进程…'
$process = [Diagnostics.Process]::Start($start)
$timer = [Diagnostics.Stopwatch]::StartNew()
$lastOutput = 0.0; $lastStage = '初始化系统组件'; $initialized = $false; $exitAt = -1.0
try {
    $process.StandardInput.WriteLine($ApiKey.TrimStart([char]65279))
    $ApiKey = $null
    $process.StandardInput.Close()
    $outRead = $process.StandardOutput.ReadLineAsync()
    $errRead = $process.StandardError.ReadLineAsync()
    while ($true) {
        foreach ($streamName in @('outRead','errRead')) {
          for ($drain = 0; $drain -lt 100; $drain++) {
            $pending = Get-Variable -Name $streamName -ValueOnly
            if ($null -eq $pending -or -not $pending.IsCompleted) { break }
            $line = $pending.GetAwaiter().GetResult()
            if ($null -eq $line) { Set-Variable -Name $streamName -Value $null; continue }
            # Child diagnostics are sanitized; also redact any accidental key-shaped output.
            $line = $line -replace 'sk-[A-Za-z0-9_-]{8,}', '[REDACTED_KEY]'
            Write-Host $line
            $lastOutput = $timer.Elapsed.TotalSeconds
            if ($line -match '^\[[1-4]/4\]') { $initialized = $true; $lastStage = $line }
            elseif ($line -match '^旧会话 ') { $lastStage = $line }
            $reader = if ($streamName -eq 'outRead') { $process.StandardOutput } else { $process.StandardError }
            Set-Variable -Name $streamName -Value ($reader.ReadLineAsync())
          }
        }
        if ($process.HasExited) {
            if ($exitAt -lt 0) { $exitAt = $timer.Elapsed.TotalSeconds }
            if (($null -eq $outRead -and $null -eq $errRead) -or $timer.Elapsed.TotalSeconds - $exitAt -ge 2) { break }
        } elseif (-not $initialized -and $timer.Elapsed.TotalSeconds -ge 90) {
            # No configuration stage has started; stop only this owned initialization process.
            $process.Kill()
            $null = $process.WaitForExit(5000)
            throw '[SETUP_INITIALIZATION_TIMEOUT] 初始化系统组件超过 90 秒。尚未进入配置阶段；请检查 PowerShell/.NET、系统权限及安全软件拦截记录。'
        }
        if ($timer.Elapsed.TotalSeconds - $lastOutput -ge 10) {
            Write-Host ('仍在执行：{0}（配置进程已运行 {1:N0} 秒）' -f $lastStage,$timer.Elapsed.TotalSeconds)
            $lastOutput = $timer.Elapsed.TotalSeconds
        }
        Start-Sleep -Milliseconds 50
    }
    $result = $process.ExitCode
} finally { $ApiKey = $null; $timer.Stop(); $process.Dispose() }
if ($result -ne 0) { throw '[SETUP_FAILED] 配置未完成，请查看上方分类错误与诊断编号。' }
