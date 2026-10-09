[CmdletBinding()]
param([string]$OutputPath)

# Read-only inventory. Never install roles, start VMs/services, expose ports,
# enumerate credentials, or dump process command lines / environment variables.
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
    captured_utc = [DateTime]::UtcNow.ToString('o')
    purpose = 'Sub2API migration preflight; no deployment performed'
    windows = $null
    cpu = @()
    disks = @()
    virtualization_features = @()
    tooling = @()
    listeners = @()
    errors = @()
}
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $system = Get-CimInstance Win32_ComputerSystem
    $result.windows = [ordered]@{
        caption = $os.Caption
        version = $os.Version
        build = $os.BuildNumber
        architecture = $os.OSArchitecture
        physical_memory_bytes = [long]$system.TotalPhysicalMemory
        free_memory_kib = [long]$os.FreePhysicalMemory
        hypervisor_present = [bool]$system.HypervisorPresent
        powershell_version = $PSVersionTable.PSVersion.ToString()
        elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    $result.cpu = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, VirtualizationFirmwareEnabled, SecondLevelAddressTranslationExtensions)
    $result.disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, FileSystem, Size, FreeSpace)
} catch { $result.errors += 'OS inventory unavailable; rerun with appropriate read access.' }

foreach ($name in @('docker', 'wsl', 'git', 'go', 'bun', 'python')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    $result.tooling += [ordered]@{ name = $name; available = [bool]$cmd }
}
try {
    if (Get-Command Get-WindowsFeature -ErrorAction SilentlyContinue) {
        $result.virtualization_features = @(Get-WindowsFeature -Name Hyper-V, Containers | Select-Object Name, InstallState)
    } else {
        $result.virtualization_features = @('Get-WindowsFeature unavailable; guest/container support is unverified.')
    }
} catch { $result.errors += 'Virtualization feature inventory requires additional read access.' }

try {
    $ports = @(80, 443, 18300, 18301, 23000, 28080, 5432, 6379)
    $result.listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $ports -contains $_.LocalPort } | Select-Object LocalAddress, LocalPort)
} catch { $result.errors += 'Listener inventory unavailable.' }

# Do not invoke Docker/WSL here: installed clients do not establish a running,
# supported Linux engine and may start background components on some hosts.
$result.next_checks = @(
    'Confirm Windows Server edition/build support and nested virtualization from the host provider.',
    'Select an already supported Linux VM or WSL deployment; Docker Desktop is not assumed.',
    'Inside the guest verify docker version, docker compose version, Docker OSType=linux, and disk capacity.',
    'Verify DNS/TLS egress to GitHub, Docker registry and approved upstream endpoints from the selected guest.',
    'Record domain/TLS/payment callbacks and the future cutover plan without changing them.'
)
$json = $result | ConvertTo-Json -Depth 7
if ($OutputPath) {
    $destination = [IO.Path]::GetFullPath($OutputPath)
    if (Test-Path -LiteralPath $destination) { throw 'Refusing to overwrite an existing preflight report.' }
    [IO.File]::WriteAllText($destination, $json, (New-Object Text.UTF8Encoding($false)))
    Write-Output ('Report saved: ' + $destination)
} else { Write-Output $json }
