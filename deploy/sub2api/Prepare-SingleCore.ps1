[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [switch]$Build,
    [switch]$Test,
    [string]$Proxy = '',
    [string]$SourceManifest = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

if (-not $SourceManifest) { $SourceManifest = Join-Path $PSScriptRoot 'singlecore-source.json' }
$manifestPath = (Resolve-Path -LiteralPath $SourceManifest -ErrorAction Stop).Path
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$patch = Join-Path (Split-Path -Parent $manifestPath) $manifest.patch
if ((Get-FileHash -LiteralPath $patch -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.patch_sha256) { throw 'Candidate patch checksum mismatch' }
if ($manifest.upstream_url -ne 'https://github.com/Wei-Shaw/sub2api.git' -or $manifest.upstream_commit -notmatch '^[a-f0-9]{40}$') { throw 'Invalid pinned upstream' }
$candidatePath = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $candidatePath) { throw 'Destination must not exist; existing checkouts and runtime directories are never reset' }
if ($Proxy) {
    $proxyUri = [Uri]$Proxy
    if ($proxyUri.Scheme -notin @('http','https') -or $proxyUri.UserInfo) { throw 'Use a credential-free HTTP proxy URL' }
}
$savedEnvironment = @{}
foreach ($name in @('HTTP_PROXY','HTTPS_PROXY','NODE_USE_ENV_PROXY','NODE_USE_SYSTEM_CA')) { $savedEnvironment[$name]=[Environment]::GetEnvironmentVariable($name,'Process') }
$gitOptions = @()
if ($Proxy) { $gitOptions=@('-c',"http.proxy=$Proxy"); $env:HTTP_PROXY=$Proxy; $env:HTTPS_PROXY=$Proxy; $env:NODE_USE_ENV_PROXY='1' }
$env:NODE_USE_SYSTEM_CA='1'
try {
    New-Item -ItemType Directory -Path $candidatePath | Out-Null
    Invoke-Native git (@('-C',$candidatePath,'init','--initial-branch=codex/realyu-single-core-candidate'))
    Invoke-Native git (@('-C',$candidatePath,'config','core.autocrlf','false'))
    Invoke-Native git (@('-C',$candidatePath,'remote','add','origin',$manifest.upstream_url))
    Invoke-Native git ($gitOptions + @('-C',$candidatePath,'fetch','--depth=1','origin',$manifest.upstream_commit))
    Invoke-Native git (@('-C',$candidatePath,'checkout','--detach',$manifest.upstream_commit))
    $head = (& git -C $candidatePath rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -ne $manifest.upstream_commit) { throw 'Upstream commit mismatch' }
    Invoke-Native git (@('-C',$candidatePath,'switch','-c','codex/realyu-single-core-candidate'))
    Invoke-Native git (@('-C',$candidatePath,'apply','--check','--whitespace=nowarn',$patch))
    Invoke-Native git (@('-C',$candidatePath,'apply','--whitespace=nowarn',$patch))
    foreach ($file in $manifest.changed_files) {
        $filePath = [IO.Path]::GetFullPath((Join-Path $candidatePath $file.path))
        if (-not $filePath.StartsWith($candidatePath.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes checkout' }
        if ((Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw "Patched file checksum mismatch: $($file.path)" }
    }
    if ($Build -or $Test) {
        Push-Location (Join-Path $candidatePath 'frontend')
        try {
            Invoke-Native corepack @('pnpm@9.15.9','install','--frozen-lockfile','--prefer-offline')
            Invoke-Native corepack @('pnpm@9.15.9','exec','vitest','run','src/i18n/__tests__/localeKeyCompleteness.spec.ts')
            Invoke-Native corepack @('pnpm@9.15.9','exec','vue-tsc','-b')
            Invoke-Native corepack @('pnpm@9.15.9','exec','vite','build')
            if ($Test) {
                # Profile money regressions are already included by the profile directory below.
                $frontendRegressionTests = @(
                    'src/utils/__tests__/branding.spec.ts'
                    'src/views/user/__tests__/PaymentResultView.spec.ts'
                    'src/views/auth/__tests__'
                    'src/components/user/profile/__tests__'
                    'src/components/modelPlaza/__tests__/PlazaGroupSection.spec.ts'
                    'src/components/modelPlaza/__tests__/ModelPlazaContent.realyu.spec.ts'
                    'src/components/modelPlaza/__tests__/PlazaModelPricingTable.spec.ts'
                    'src/views/user/__tests__/SubscriptionsView.loading.spec.ts'
                    'src/utils/__tests__/customerMoney.spec.ts'
                    'src/views/user/__tests__/RealYuTeamsView.spec.ts'
                    'src/views/user/__tests__/KeysView.spec.ts'
                    'src/components/keys/__tests__/BulkEditKeysModal.spec.ts'
                    'src/views/__tests__/KeyUsageView.spec.ts'
                    'src/views/user/__tests__/RedeemView.spec.ts'
                    'src/views/user/__tests__/PaymentView.spec.ts'
                    'src/components/payment/__tests__/SubscriptionPlanCard.spec.ts'
                    'src/views/admin/__tests__/RedeemView.batchUpdate.spec.ts'
                    'src/views/admin/__tests__/SettingsView.spec.ts'
                    'src/components/admin/usage/__tests__/UsageStatsCards.spec.ts'
                    'src/components/admin/usage/__tests__/UsageTable.spec.ts'
                    'src/components/admin/usage/__tests__/UserTokenRanking.spec.ts'
                    'src/components/charts/__tests__/GroupDistributionChart.spec.ts'
                    'src/components/charts/__tests__/ModelDistributionChart.spec.ts'
                    'src/components/charts/__tests__/TokenUsageTrend.spec.ts'
                    'src/components/user/dashboard/__tests__/UserDashboardStats.spec.ts'
                    'src/views/admin/__tests__/DashboardView.spec.ts'
                    'src/views/admin/__tests__/UsageView.spec.ts'
                    'src/views/user/__tests__/UsageView.spec.ts'
                    'src/components/admin/user/__tests__/UserBalanceModal.spec.ts'
                    'src/components/admin/user/__tests__/UserCreateModal.spec.ts'
                    'src/components/admin/user/__tests__/UserPlatformQuotaModal.spec.ts'
                    'src/components/user/__tests__/UserPlatformQuotaCell.spec.ts'
                    'src/components/user/__tests__/PlatformMoneyCells.spec.ts'
                    'src/components/account/__tests__/AccountStatsModal.currency.spec.ts'
                    'src/components/account/__tests__/AccountUsageCell.spec.ts'
                    'src/components/account/__tests__/AccountStatusIndicator.locales.spec.ts'
                    'src/__tests__/App.admin-entry.spec.ts'
                    'src/router/__tests__/feature-access.spec.ts'
                    'src/api/__tests__/client.spec.ts'
                    'src/components/realyu/__tests__'
                    'src/utils/__tests__/realyuPurchase.spec.ts'
                    'src/utils/__tests__/realyuSetupCommand.spec.ts'
                    'src/views/admin/orders/__tests__/RealYuPlanEditDialog.spec.ts'
                    'src/i18n/__tests__'
                    'src/components/realyu/public/__tests__'
                    'src/components/admin/usage/__tests__'
                    'src/components/user/dashboard/__tests__'
                    'src/utils/__tests__/realyuSeo.spec.ts'
                    'src/utils/__tests__/realyuUsageDisplay.spec.ts'
                    'src/views/user/__tests__/RealYuUsageView.spec.ts'
                    'src/router/__tests__/realyu-usage-access.spec.ts'
                )
                # A frozen older manifest may predate the UX-only test files.
                $availableRegressionTests = @($frontendRegressionTests | Where-Object { Test-Path -LiteralPath $_ })
                if ($availableRegressionTests.Count -eq 0) { throw 'No frontend regression tests found in the pinned source' }
                Invoke-Native corepack (@('pnpm@9.15.9','exec','vitest','run') + $availableRegressionTests + @('--maxWorkers=2','--minWorkers=1'))
            }
        } finally { Pop-Location }
        Push-Location (Join-Path $candidatePath 'backend')
        try {
            if ($Test) { Invoke-Native go @('test','-p','2','-tags','unit,embed','./internal/service','./internal/handler','./internal/handler/admin','./internal/repository','./internal/server/middleware','./internal/server/routes','./internal/payment/provider','./internal/web','-run','TestRealYu|TestRealyu|TestZPay|TestGatewayRoutesGroupModelAllowlist','-count=1') }
            if ($Build) {
                $out = Join-Path $candidatePath 'candidate-output'
                New-Item -ItemType Directory -Path $out | Out-Null
                Invoke-Native go @('build','-p','2','-trimpath','-tags','embed','-ldflags',('-s -w -X main.Version='+$manifest.candidate_version),'-o',(Join-Path $out 'sub2api.exe'),'./cmd/server')
                Copy-Item -LiteralPath (Join-Path $candidatePath 'backend\resources') -Destination $out -Recurse
                Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $out 'singlecore-source.json')
                Get-FileHash -LiteralPath (Join-Path $out 'sub2api.exe') -Algorithm SHA256 | Format-List
            }
        } finally { Pop-Location }
    }
    Write-Output "Candidate prepared: $candidatePath"
    Write-Output 'No service, database, secret, listener, domain, or production route was changed.'
} finally {
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name,$savedEnvironment[$name],'Process') }
}
