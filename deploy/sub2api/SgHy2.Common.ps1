#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'

function Get-SgHy2Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-SgHy2Plan([string]$Path, [string]$ExpectedHash) {
    if((Get-SgHy2Hash $Path) -ne $ExpectedHash) {throw 'The reviewed SG-HY2 plan changed.'}
    $plan=Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $expectedRoot=Join-Path $env:ProgramData 'RealYuNetwork\sg-hy2-20261010'
    if($plan.schema -ne 1 -or [IO.Path]::GetFullPath($plan.install_root) -ne $expectedRoot) {
        throw 'Unexpected SG-HY2 plan or installation target.'
    }
    if([IO.Path]::GetFullPath($plan.package_root) -ne (Join-Path ([IO.Path]::GetFullPath($plan.operation_root)) 'package')) {
        throw 'The private package must be inside its operation directory.'
    }
    if(($plan.services -join ',') -ne 'RealYuSgHy2,RealYuSgHy2EdgeProxy' -or
       ($plan.ports -join ',') -ne '17897,17898,19464,19465,19466,19467') {
        throw 'The reviewed service names or ports changed.'
    }
    $expectedFiles=@('bin/mihomo.exe','bin/gost.exe','RealYuSgHy2.exe','RealYuSgHy2.xml',
                     'RealYuSgHy2EdgeProxy.exe','RealYuSgHy2EdgeProxy.xml','config.yaml')
    if(@($plan.files.PSObject.Properties).Count -ne $expectedFiles.Count) {throw 'Unexpected package entries.'}
    foreach($name in $expectedFiles) {
        if(-not $plan.files.PSObject.Properties[$name] -or
           (Get-SgHy2Hash (Join-Path $plan.package_root $name)) -ne $plan.files.$name) {
            throw 'A reviewed package artifact changed.'
        }
    }
    foreach($name in @('SgHy2.Common.ps1','Install-SgHy2Service.ps1','Switch-SgHy2Tunnel.ps1','Set-SgHy2Monitoring.ps1')) {
        $scriptPath=Join-Path $PSScriptRoot $name
        if(-not $plan.verified_scripts.PSObject.Properties[$scriptPath] -or
           (Get-SgHy2Hash $scriptPath) -ne $plan.verified_scripts.$scriptPath) {
            throw 'A reviewed deployment script changed; prepare a new plan.'
        }
    }
    $plan
}

function Assert-SgHy2Administrator {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Service installation and rolling cutover require normal Windows administrator elevation.'
    }
}

function Assert-SgHy2PortsFree($Plan) {
    $occupied=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object {$_.LocalPort -in $Plan.ports})
    if($occupied.Count) {throw 'A candidate proxy port is already in use.'}
}

function Write-SgHy2Json([string]$Path, $Value) {
    $temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 32), $utf8)
    if(Test-Path -LiteralPath $Path) {
        # Windows PowerShell 5.1 binds a null String argument as an empty path.
        $backup=$Path+'.replace-'+[guid]::NewGuid().ToString('N')+'.bak'
        [IO.File]::Replace($temp,$Path,$backup)
        [IO.File]::Delete($backup)
    }
    else {[IO.File]::Move($temp,$Path)}
}

function Get-SgHy2ControllerHeaders([string]$ConfigPath) {
    $line=@(Get-Content -LiteralPath $ConfigPath -Encoding UTF8 | Where-Object {$_ -match '^secret: '})
    if($line.Count -ne 1) {throw 'Controller credential is unavailable.'}
    $secret=$line[0].Substring(8) | ConvertFrom-Json
    if(-not $secret -or $secret.Length -lt 32) {throw 'Invalid controller credential.'}
    @{Authorization='Bearer '+$secret}
}

function Get-SgHy2Evidence($Plan, [switch]$SkipExitProbe) {
    $headers=Get-SgHy2ControllerHeaders (Join-Path $Plan.install_root 'config.yaml')
    $base='http://127.0.0.1:17898'
    $proxies=Invoke-RestMethod -Uri ($base+'/proxies') -Headers $headers -TimeoutSec 5
    $configs=Invoke-RestMethod -Uri ($base+'/configs') -Headers $headers -TimeoutSec 5
    if($proxies.proxies.REALYU.now -ne 'RealYu-SG-HY2' -or $configs.mode -ne 'rule' -or
       $configs.'allow-lan' -ne $false) {throw 'The dedicated core is not on the fixed SG-HY2 route.'}
    $connections=Invoke-RestMethod -Uri ($base+'/connections') -Headers $headers -TimeoutSec 5
    $flows=@($connections.connections | Where-Object {
        $null -ne $_ -and $_.metadata.destinationIP -in $Plan.edges -and $_.metadata.destinationPort -eq '7844'
    } | ForEach-Object {
        [pscustomobject]@{id=$_.id;start=$_.start;destination=$_.metadata.destinationIP;
            source_port=$_.metadata.sourcePort;chains=@($_.chains);upload=$_.upload;download=$_.download}
    })
    if(-not $SkipExitProbe) {
        $trace=& curl.exe -q -sS --fail --max-time 15 --max-filesize 65536 --proxy 'http://127.0.0.1:17897' --noproxy 'realyu.invalid' 'https://www.cloudflare.com/cdn-cgi/trace' 2>$null
        if($LASTEXITCODE -ne 0 -or ($trace -join "`n") -notmatch '(?m)^ip=43\.160\.230\.142\s*$') {
            throw 'The independent core did not prove the SG exit with a complete HTTPS trace.'
        }
    }
    [pscustomobject]@{at=[DateTime]::UtcNow.ToString('o');node='RealYu-SG-HY2';flows=$flows;
        exit_checked=(-not $SkipExitProbe)}
}

function Assert-SgHy2Services($Plan) {
    foreach($name in $Plan.services) {
        $service=Get-CimInstance Win32_Service -Filter ("Name='"+$name+"'")
        if(-not $service -or $service.State -ne 'Running' -or $service.StartMode -ne 'Auto' -or
           $service.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE')) {
            throw 'The independent network services must be Running, Automatic, LocalService.'
        }
        $expected=Join-Path $Plan.install_root ($name+'.exe')
        if($service.PathName.Trim('"') -ne $expected -or (Get-SgHy2Hash $expected) -ne $Plan.files.($name+'.exe')) {
            throw 'The installed service identity differs from the reviewed package.'
        }
    }
    foreach($file in $Plan.files.PSObject.Properties) {
        if((Get-SgHy2Hash (Join-Path $Plan.install_root $file.Name)) -ne $file.Value) {
            throw 'An installed network artifact changed.'
        }
    }
    foreach($port in $Plan.ports) {
        $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') {
            throw 'A network listener is missing, duplicated, or not loopback-only.'
        }
        $owner=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listeners[0].OwningProcess)
        $exe=if($port -lt 19000){'mihomo.exe'}else{'gost.exe'}
        if($owner.ExecutablePath -ne (Join-Path $Plan.install_root ('bin\'+$exe)) -or $owner.SessionId -ne 0) {
            throw 'A candidate listener has the wrong owning process or is not in service Session 0.'
        }
    }
}
