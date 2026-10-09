#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DeploymentRoot,
    [Parameter(Mandatory=$true)][string]$ReleaseRoot,
    [switch]$IncludeWorker,
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
# This reviewed host profile retains its exact service IDs, ports and binary pins.
# It consumes pre-existing private XML/configuration; it never creates credentials.
$root=[IO.Path]::GetFullPath($DeploymentRoot)
$release=[IO.Path]::GetFullPath($ReleaseRoot)
$safe='Unexpected installation failure; inspect protected service logs'
function Reject([string]$Message){$script:safe=$Message; throw 'Safe installation rejection'}
function Grant-Path([string]$Path,[string]$Principal,[string]$Rights,[bool]$Inherit=$true){
    if(-not(Test-Path -LiteralPath $Path)){Reject 'Expected installation path is absent'}
    $acl=Get-Acl -LiteralPath $Path
    $sid=(New-Object Security.Principal.NTAccount($Principal)).Translate([Security.Principal.SecurityIdentifier])
    $flags=if($Inherit -and (Get-Item -LiteralPath $Path).PSIsContainer){'ContainerInherit,ObjectInherit'}else{'None'}
    $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,$Rights,$flags,'None','Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Invoke-SafeNative([string]$Exe,[string[]]$Arguments,[string]$LogName){
    & $Exe @Arguments *> (Join-Path $root ('logs\'+$LogName+'.log'))
    if($LASTEXITCODE -ne 0){Reject ('Native service operation failed: '+$LogName)}
}
function Protect-Path([string]$Path){
    $isDirectory=(Get-Item -LiteralPath $Path).PSIsContainer
    $acl=if($isDirectory){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    foreach($sidValue in @('S-1-5-18','S-1-5-32-544',[Security.Principal.WindowsIdentity]::GetCurrent().User.Value)){
        $sid=New-Object Security.Principal.SecurityIdentifier($sidValue)
        $flags=if($isDirectory){'ContainerInherit,ObjectInherit'}else{'None'}
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl',$flags,'None','Allow')))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
try{
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
    $services=[ordered]@{postgres='RealYuSub2APIPostgres20261009';redis='RealYuSub2APIRedis20261009';sub2api='RealYuSub2API20261009'}
    if($IncludeWorker){$services.Add('prewarm','RealYuSub2APIPrewarm20261009')}
    $expectedExecutables=@{postgres=(Join-Path $release 'postgres\bin\postgres.exe');redis=(Join-Path $release 'redis\redis-server.exe');sub2api=(Join-Path $release 'sub2api\sub2api.exe');prewarm=(Join-Path $release 'sub2api-prewarm.exe')}
    $expectedHashes=@{postgres='14817df435104dcfe05ad8343fda200930abf778553ab60154bd285aea6833e1';redis='5bc44512f4ff7828f0d65e59ad712c1b66f692c4f827c966123d3ece4ec5945c';sub2api='c624c115dd7e5dd1a33c083d3ef601599338f9ed986c2cb007a5663d375f9978';prewarm='8ac607bf72faee338238b5393555d326d910662b76503d4cc4854d0dda75710c'}
    foreach($path in @($root,$release)){
        $item=Get-Item -LiteralPath $path
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){Reject 'Reparse point deployment directory rejected'}
    }
    foreach($name in $services.Keys){
        $wrapper=Join-Path $root ('services\'+$name+'.exe')
        if((Get-FileHash -Algorithm SHA256 -LiteralPath $wrapper).Hash.ToLowerInvariant() -ne '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da'){Reject 'WinSW digest differs from pinned version'}
        if((Get-FileHash -Algorithm SHA256 -LiteralPath $expectedExecutables[$name]).Hash.ToLowerInvariant() -ne $expectedHashes[$name]){Reject 'Application digest differs from validated binary'}
        $xml=New-Object System.Xml.XmlDocument
        $xml.Load((Join-Path $root ('services\'+$name+'.xml')))
        if($xml.service.id -ne $services[$name] -or $xml.service.executable -ne $expectedExecutables[$name]){Reject 'Service XML identity or executable mismatch'}
        if($xml.service.serviceaccount.domain -ne 'NT AUTHORITY' -or $xml.service.serviceaccount.user -ne 'LocalService'){Reject 'Unexpected service account'}
        if(Get-Service -Name $services[$name] -ErrorAction SilentlyContinue){Reject 'A new installation service already exists; inspect it before retrying'}
    }
    if($IncludeWorker){
        $binding=Get-Content -Raw -LiteralPath (Join-Path $root 'integration\bindings.json') | ConvertFrom-Json
        if($binding.version -ne 1 -or $binding.provision.mode -ne 'prewarmed' -or -not $binding.provision.enabled){Reject 'Worker requires explicit enabled prewarmed configuration'}
    }
    if($ValidateOnly){[pscustomobject]@{validation='PASS';services=@($services.Values);mutations=$false}|ConvertTo-Json; exit 0}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    if(-not(New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){Reject 'Administrator elevation is required to install the new services; no existing services were changed'}
    foreach($port in @(28090,28490,28391)){
        if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue){Reject 'Stop only the verified temporary installation processes before service installation'}
    }
    # New services only. Per-service SID grants keep PG/Redis/Sub data distinct.
    # LocalService is shared with legacy services, so this is not full account isolation.
    foreach($name in $services.Keys){
        Invoke-SafeNative (Join-Path $root ('services\'+$name+'.exe')) @('install') ('install-'+$name)
        Invoke-SafeNative "$env:SystemRoot\System32\sc.exe" @('sidtype',$services[$name],'unrestricted') ('sid-'+$name)
        $principal='NT SERVICE\'+$services[$name]
        Grant-Path $root $principal 'ReadAndExecute' $false
        Grant-Path (Join-Path $root 'services') $principal 'ReadAndExecute' $false
        Grant-Path (Join-Path $root ('services\'+$name+'.exe')) $principal 'ReadAndExecute' $false
        Grant-Path (Join-Path $root ('services\'+$name+'.xml')) $principal 'Read' $false
        Grant-Path $release $principal 'ReadAndExecute'
        Grant-Path (Join-Path $root ('logs\'+$name)) $principal 'Modify'
        if($name -ne 'prewarm'){Grant-Path (Join-Path $root $name) $principal 'Modify'}
        # Delegate only read/status/start/stop for these newly installed services.
        $sd='D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCRPWPLO;;;'+$identity.User.Value+')'
        Invoke-SafeNative "$env:SystemRoot\System32\sc.exe" @('sdset',$services[$name],$sd) ('service-access-'+$name)
    }
    if($IncludeWorker){
        $worker='NT SERVICE\'+$services.prewarm
        Grant-Path (Join-Path $root 'integration') $worker 'Modify'
        # Existing RealYu service must have its service SID enabled by the parent
        # release operation; this installer never changes that existing service.
        $portal='NT SERVICE\RealYuApi'
        Grant-Path $root $portal 'ReadAndExecute' $false
        Grant-Path (Join-Path $root 'integration') $portal 'ReadAndExecute' $false
        Protect-Path (Join-Path $root 'integration\bindings.json')
        Grant-Path (Join-Path $root 'integration\bindings.json') $worker 'Read' $false
        Grant-Path (Join-Path $root 'integration\bindings.json') $portal 'Read' $false
        Protect-Path (Join-Path $root 'integration\queue')
        Grant-Path (Join-Path $root 'integration\queue') $worker 'ReadAndExecute'
        Grant-Path (Join-Path $root 'integration\queue') $portal 'Modify'
        Protect-Path (Join-Path $root 'integration\state')
        Grant-Path (Join-Path $root 'integration\state') $worker 'Modify'
        Grant-Path (Join-Path $root 'integration\state') $portal 'ReadAndExecute'
    }
    # WinSW may create its own wrapper log beside the XML; precreate each log
    # with write access, without granting write permission to executable/XML.
    foreach($name in $services.Keys){
        $wrapperLog=Join-Path $root ('services\'+$name+'.wrapper.log')
        if(-not(Test-Path -LiteralPath $wrapperLog)){[IO.File]::WriteAllText($wrapperLog,'')}
        Grant-Path $wrapperLog ('NT SERVICE\'+$services[$name]) 'Modify' $false
    }
    foreach($name in $services.Keys){
        Start-Service -Name $services[$name]
        (Get-Service -Name $services[$name]).WaitForStatus('Running',[TimeSpan]::FromSeconds(90))
        if($name -ne 'prewarm'){
            $port=@{postgres=28490;redis=28391;sub2api=28090}[$name]
            $ready=$false
            for($attempt=0;$attempt -lt 120;$attempt++){
                if(Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $port -ErrorAction SilentlyContinue){$ready=$true;break}
                Start-Sleep -Milliseconds 500
            }
            if(-not $ready){Reject ('Service did not reach its loopback listener: '+$name)}
        }
    }
    $health=Invoke-RestMethod -Uri 'http://127.0.0.1:28090/health' -TimeoutSec 15
    if($health.status -ne 'ok'){Reject 'Sub2API final health check failed'}
    [pscustomobject]@{version=1;installed=@($services.Values);automatic_startup_configured=$true;host_reboot_tested=$false;timestamp=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $root 'service-install-receipt.json') -Encoding UTF8
    'Sub2API services installed and listening; reboot recovery still requires a scheduled host test.'
}catch{
    [Console]::Error.WriteLine('Sub2API service installation failed: '+$safe)
    exit 1
}
