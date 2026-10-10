# RealYu 单核心迁移到另一台 Windows Server

本文是另一台机器上 Codex 的部署交接入口，适用于 **已经完成单核心切换的 RealYu → 新 Windows Server**。整理日期：2026-10-10。本机已于 20:30:20 r3 ACTIVE，公网模型/协议及账务验收通过；先读 [LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md) 和 [HANDOFF-SUB2API.md](../../HANDOFF-SUB2API.md) 的最新发布记录，再核对源机当前服务、运行文件哈希、数据库和私有 `authority-receipt.json`。首次切换已完成，此后应迁移当前 PG/Redis，不能拿初次 S0/S1 覆盖新增交易。后续无 schema 更新见[原生更新教程](SINGLECORE-NATIVE-UPDATE.md)；UX 306/307 使用[增量教程](SINGLECORE-ADDITIVE-UPDATE.md)。当前默认重建输入为完整 UX 候选，冻结 CNY-only 另存 `singlecore-cny-admin-source.json`；重建时显式核对版本，不以 Git 最新候选推断运行版本。原生 PG 的[一致快照恢复验证](POSTGRES-PRE306-BACKUP-20261010.md)和[旧套餐目录恢复](RESTORE-CATALOG.md)已具备可复核工具，后者默认下架、不启用商户、不发放资金。

**这不是一键迁机安装器。** 已有源码重建、同机切换和 SG 服务脚本可以复用，但新机器的运行依赖包、服务计划、私有资料传递、原生数据库恢复及最终切流需要按下述步骤准备、验证和留存回执。若源机还没有单核心 `ACTIVE` 证据，不要把旧 Sub2API 的影子库当成客户数据库恢复。

## 1. 目标架构与权威数据

```text
api.realyu.fun / Cloudflare DNS + Tunnel
    → 127.0.0.1:18301 透明 bridge（HTTP / SSE / WebSocket）
    → 127.0.0.1:18300 Sub2API + RealYu 单核心
        → PostgreSQL 127.0.0.1:28490：客户、供给、权限、资金及历史账本
        → Redis      127.0.0.1:28391 DB 1：缓存、协调及续聊状态
        → SG-HY2     127.0.0.1:17897：真实上游出站代理

Tunnel 连接 → GOST 127.0.0.1:19464–19467 → SG-HY2 17897 → Cloudflare edge
```

| 组件 | 当前主机约定 | 新机器要求 |
|---|---|---|
| 单核心 API | `RealYuApi`，18300；`C:\ProgramData\RealYu\singlecore-production-20261010` | 独立 WinSW/SCM 服务；运行审核后的原生二进制和私有 env |
| 透明入口 | `RealYuBridge`，18301；状态/排空接口 18302 | 保留流式、WS、维护门及下载等实际使用行为；不另起 New API 业务核心 |
| PostgreSQL | 28490；客户库 `realyu_singlecore_20261010_candidate`，owner `realyu_singlecore_owner_20261010` | 通过原生 dump/restore 保留全部 ID 和业务数据；名称中的 candidate 不决定是否为线上库 |
| Redis | 28391，DB 1 | 独立、认证、loopback、持久化；按同核心跨机规则恢复必要状态 |
| SG 代理 | `RealYuSgHy2`；mixed 17897、controller 17898 | 开机 SCM 服务，不依赖 Clash Verge、登录桌面或用户会话 |
| Tunnel 出站 | `RealYuSgHy2EdgeProxy`；19464–19467 | 独立 GOST 服务；两条 connector 使用经验证的新路径 |
| 公网 connector | `RealYuTunnelPrimary` / `RealYuTunnelReplica` | 入口仍指向 18301；不要在准备阶段加入线上 Tunnel 接收客户流量 |
| 健康观测 | public-direct、public-via-local-proxy、backend、bridge、两个 Tunnel | 保留完整响应、采样新鲜度和实际节点/流量证据，不只看 ready=200 |

以上名称和端口是当前方案的实际约定，不是所有 Windows 机器的默认值。新机先确认端口、服务名、磁盘和目录归属；若改变约定，须同时改并测试 launcher、私有计划、SCM、监控和依赖项。

单核心接管后，旧 `new-api.db` 是历史来源/审计资料，旧 `sub2api_production` 是原供给及影子身份库，均不是新客户权威。新机不要启动旧 New API、`RealYuSub2API20261009` 或 `RealYuSub2APIPrewarm20261009`，不要重新跑旧 bindings/credit-worker。源机实际 `ACTIVE` 后的退役状态由发布回执确认；保留旧库备份不代表允许继续写入。

## 2. 先拿齐两份交付物

### Git 内：源码、固定版本和教程

取得用户指定仓库的最新已提交版本，记录 commit，并读取 [AGENTS.md](../../AGENTS.md)。重建以 [singlecore-source.json](singlecore-source.json) 为准：固定 Sub2API `v0.2.15` 的 peeled commit `f2669c8cf62555cd92389b3f55920e9e6e7c6ff2`、补丁 SHA256 和逐文件哈希。不要直接拉取上游 latest 覆盖 RealYu 修改；升级上游另作变更。

| 文件/工具 | 可以复用的部分 | 不能据此推断 |
|---|---|---|
| [Prepare-SingleCore.ps1](Prepare-SingleCore.ps1)、[候选重建说明](SINGLECORE-CANDIDATE.md) | 固定源码、应用补丁、构建和相关回归 | 不安装服务、不恢复数据库、不迁流量 |
| [NATIVE-WINDOWS.md](NATIVE-WINDOWS.md) | Windows 原生 PG/免费 Redis、WinSW、时区和持久化经验 | 其中旧 RealYu + Sub + prewarm 拓扑不是本次目标 |
| [host/README.md](host/README.md) | SCM、SID/ACL、进程归属和哈希验证模式 | 脚本固定旧 28090 端口、服务名和旧二进制；不能直接给新机安装全套 |
| [service_entry_singlecore.py](service_entry_singlecore.py) | 原生 API 分支、env/数据库/哈希校验、SCM 生命周期 | 单独复制此文件不能构成完整运行包 |
| [SG-HY2-SERVICE-HANDOFF.md](SG-HY2-SERVICE-HANDOFF.md) | 独立 Mihomo/GOST、TLS、开机服务和真实路径验收 | 准备器依赖当前宿主的 profile/manifest/guard；新机须生成经过审阅的自身计划 |
| [SINGLECORE-HOST-CUTOVER.md](SINGLECORE-HOST-CUTOVER.md)、`Install-SingleCoreHostStage.ps1`、`Invoke-SingleCoreRelease.ps1` | 同一主机 New API/SQLite → 新客户 PG 的切换合同与恢复边界 | **不是跨机 pg_restore 工具**；不要在新机重跑客户导入、S0/S1 或旧 launcher 替换步骤 |
| `New-NativeCandidate.ps1`、`Install-HostRelease.ps1`、旧 route/bootstrap 示例 | 历史桥接部署参考 | 不应照抄启用 type59、影子账户/余额、28090 和 prewarm |

Git 不包含数据库、私有 env、运行凭据或服务私有 manifest。实际 launcher/bridge Python 源码已补齐，映射及逐文件哈希见 [host-runtime/README.md](host-runtime/README.md) 和 [source-manifest.json](host-runtime/source-manifest.json)：包括 `job_guard.py`、`service_identity.py`，以及与线上 `lab/image_bridge.py` 字节一致的 `edge_proxy.py`。原生 API 与 bridge 启动分支均不再依赖旧 New API 二进制、SQLite 或 `.lab/credentials.json`。新机仍须安装已审核的 Python/WinSW/网络二进制并生成私有 manifest、XML、Tunnel 配置和 ACL；这些源码不是一键安装器。不要用空文件、临时脚本或恢复旧 API 进程来绕过缺件。

### 私下安全传递：真实数据与私有配置

使用用户认可的受控传输渠道，校验文件哈希，目录仅管理员/指定操作员可访问。不要把以下资料放进 Git、公开工单、聊天正文或公开附件：

| 资料 | 必须保留的内容/边界 |
|---|---|
| 当前单核心 PostgreSQL 完整备份 | 原生表、RealYu 扩展表、迁移版本、用户/Key、团队成员、订阅实例、余额/赠金/当期已用、用量/结算/历史账本、设置、订单与旧支付 inbox；不能只导 users/balance |
| 最终上游状态 | accounts、groups、proxies、关联 ID、当前 OAuth 凭据及必要平台设置；最终以旧写者停止后的 PG 快照为准 |
| 稳定安全材料 | `JWT_SECRET`、`TOTP_ENCRYPTION_KEY`、`REALYU_LEGACY_IDENTITY_SECRET`、`REALYU_LEGACY_NAMESPACE` 等当前实际使用项；不能用示例随机值替换后声称旧登录/续聊不受影响 |
| 新机私有 env 与服务配置 | 数据库/Redis 账号、密码、路径、监听、资源、代理、时区、服务 XML/manifest、文件哈希；机器路径和数据库连接密码可按计划调整，客户身份/签名材料单独保护 |
| ZPay 与旧回调资料 | 商户配置、签名密钥、原回调 URL、待处理订单/人工 inbox；恢复数据不等于已完成商户付款/退款 E2E 或自动履约 |
| 网络凭据 | SG 节点配置及 TLS 校验材料、controller secret、Cloudflare Tunnel 凭据；保持证书校验，不提供 DIRECT 降级绕过 |
| 运行文件 | 经哈希验证的原生 binary、pricing fallback、IANA `zoneinfo.zip` 及许可、bridge/launcher 依赖、客户端安装包/下载资源及实际必要的私有状态目录 |
| Redis 状态与证据 | 受保护的恢复材料、各条有效状态的 TTL/导出时间、源/目标 ID 校验和恢复回执；规则见第 6 节 |
| 审计/回退材料 | 原机最新发布回执、服务/进程身份、配置哈希、最终冻结时间、私有备份与恢复结果；原 SQLite/影子库只作为历史归档 |

旧用户名、密码校验材料、昵称和 API Key 随 PG 原样迁移，不创建替代客户或重发 Key。用户 ID、Key ID、团队/成员、订阅实例和上游 account/group ID 均保持。昵称不是登录名。禁用/删除、无邮箱、MFA、角色限制必须保持；原 role10 的受限团队管理不能在迁机时变成全局 admin。浏览器可要求重新登录，不承诺保留所有旧 cookie；既有 API Key、域名和已支持的续聊连续性优先。

## 3. 新机准备与可复现构建

重新只读盘点 Windows Server 版本、资源、重启状态、端口占用、出站和管理员权限。历史目标盘点为 Windows Server 2025 x64、4 vCPU/16 GiB；这不是对新机当前状态的证明。保留云厂商 agent、远程管理和现有服务。数据库/缓存/控制器端口只绑定 loopback，不为方便部署开放公网。

使用持久安装目录中的 Git/Node/Go/Python，不让 SCM 指向 Codex 缓存、交互用户 profile、开发虚拟环境或下载临时目录。当前已验证构建使用 Go 模块要求的 1.27.2、Node 24.18.0、pnpm 9.15.9；Sub 前端用 pnpm，旧 New API 的 Bun 约定不用于此构建。新目录重建：

```powershell
# 从本仓库根目录执行；Destination 必须不存在。
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned `
  -File .\deploy\sub2api\Prepare-SingleCore.ps1 `
  -Destination C:\srv\realyu-singlecore-build -Build -Test
# 需要代理时加 -Proxy 'http://127.0.0.1:<已验证端口>'，不要在 URL 中放密码。
```

保留首错，不覆盖失败目录或跳过哈希校验。结果位于 `candidate-output`，包括 `sub2api.exe`、`resources` 和 source manifest；记录 binary SHA256、源 commit、补丁 SHA、工具版本和测试命令。构建回归不替代新机真实 PG、服务和公网验收。

新机固定并验证 PG 18.x（当前主机 18.6）、免费 Redis Windows 8.10.2、WinSW 2.12.0 与 SG 网络组件；具体已验证工件及哈希见 [NATIVE-WINDOWS.md](NATIVE-WINDOWS.md) 和 [SG 服务交接](SG-HY2-SERVICE-HANDOFF.md)。不能用未打 RealYu 补丁的官方 Sub2API exe 替代新 binary。Redis 采用 loopback、认证和经验证的 AOF/RDB 配置，不复用隔离测试的禁用持久化设置。其 Windows 社区构建需要自行验收，不等于官方 Linux 生产支持。

## 4. 安装目录、私有 env 和无人登录服务

建议保留第 1 节已审阅的 loopback 端口/客户库名，减少偏移。当前原生 launcher 对这些值有**精确校验**；若新机改库名、用户或端口，必须修改对应受控源码/计划并跑回归，不能只改 env 后反复重启。

| env 字段 | 新机处理 |
|---|---|
| `SERVER_HOST/PORT` | `127.0.0.1` / `18300`；bridge 仍转发到这里 |
| `DATABASE_HOST/PORT/DBNAME/USER/PASSWORD/SSLMODE` | 目标本机 PG、专用最小权限 owner、完整恢复后的客户库；不指向影子库 |
| `REDIS_HOST/PORT/DB/USERNAME/PASSWORD/ENABLE_TLS` | 目标本机 28391、DB `1`；以已验证本机配置为准 |
| `REALYU_FUNDING_ENABLED`、`REALYU_WS_FUNDING_ENABLED` | 均保持 `true`，不能因启动方便退回原生免费/余额路径 |
| `JWT_SECRET`、`TOTP_ENCRYPTION_KEY`、`REALYU_LEGACY_IDENTITY_SECRET/NAMESPACE` | 从私有交付保留原值，日志不输出 |
| `TOKEN_REFRESH_ENABLED` | 准备阶段关闭后台刷新；这**不会**禁止请求触发的懒刷新，仍需限制副本请求和供给 |
| `HTTP_PROXY/HTTPS_PROXY`、`NO_PROXY` | SG `http://127.0.0.1:17897`；本地依赖绕过代理；同时核对 DB 中各 account 的显式 proxy 关联 |
| `DATA_DIR`、`PRICING_FALLBACK_FILE` | 新机已安装的 data 目录及完整 pricing fallback 文件 |
| `REALYU_CLIENT_ASSETS_DIR/DIAGNOSTICS_DIR` | 前者保留公开下载内容/哈希，后者单独可写，不从任意请求映射文件系统路径 |
| `TIMEZONE/TZ/ZONEINFO` | `Asia/Shanghai` 的实际有效配置；`ZONEINFO` 指向独立 IANA zip，不依赖用户 GOROOT |
| `AUTO_SETUP`、`ADMIN_EMAIL/PASSWORD` | 恢复生产库时关闭自动初始化；不执行首次创建管理员、赠额或默认订阅。历史初始化字段不应作为迁机初始化指令 |
| `REALYU_LEGACY_PAYMENT_*` | 原已启用功能、商户和回调材料按私有清单迁移，不能将收件模式扩大成已验证自动履约 |

完整 env 字段以源机审核后的私有配置和当前源码为准，上表不替代完整导出。原生 API 不应继承 `SQLITE_PATH`、`SQL_DSN`、旧 bindings/queue/credit-worker 或 New API 端口环境。确认 launcher 校验失败会停止，不能静默回落旧核心。

分别安装 PG、Redis、SG Mihomo、GOST、API、bridge、两条 Tunnel 和观测的 WinSW 服务，生成新机自己的私有计划/回执。API 依赖 PG/Redis/SG；Tunnel 依赖 bridge 和实际 GOST 路径；SCM 管理恢复，禁止另起桌面 watchdog。先完成隔离验收，再按计划设置自动启动/延迟启动及恢复策略。

Bridge 的 WinSW XML 必须显式设置 `REALYU_UPSTREAM_DRIVER=sub2api`，与当前线上 XML 一致；透明入口缺少该值会拒绝启动。该变量只选择转发契约，bridge 不接管客户资金或上游账户。运行源码和实际安装名映射见 [host-runtime](host-runtime/README.md)。

服务账号对 binary、config、pricing/zoneinfo/客户端资源只读或 RX；仅对各自 data、state、logs、diagnostics、PID/生命周期回执目录授予必要写权限。迁移备份、cluster-admin 凭据和操作员私有包不授予业务服务。共享 LocalService 加 service SID 也不等于独立 Windows 用户隔离；记录实际权限边界，不声称凭据天然互相隔离。日志配置轮转、容量和保留策略。

**必须实测无登录开机：**在新机维护窗口重启后、不登录交互桌面前，确认 SCM、Session 0、listener 的真实 exe/哈希、API 数据库健康及完整公网测试。SG 不得依赖 7897 Clash Verge；只确认服务 Running 或登录之后才可用均不合格。PG/Redis 另测优雅重启、异常恢复和备份恢复。

## 5. PostgreSQL 原生快照与恢复

先做在线预演快照，计时传输、完整恢复和匿名账本核对；此快照仅用于演练。`pg_dump` 提供单库一致快照，但不包含之后的新写入，也不包含集群角色。最终仍要冻结所有业务写者后重新导出。参考 [PostgreSQL 18 pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html)。不要复制运行中的 PG data 目录，不能拿旧 SQLite 重跑成新线上库。

先通过受控方式创建目标专用 owner 和**全新空数据库**，核对 UTF-8、locale/collation、所需扩展及迁移版本。业务 owner 不具备 superuser、createdb、createrole、replication 或 bypassrls 权限；收紧 PUBLIC 的数据库/schema 权限。角色创建与私有密码分发不放进可公开脚本。预演与最终恢复分别使用新鲜目标/清晰回执，禁止向承载新交易的库使用 `--clean`。

以下是已具备私有参数后的原生命令模板，不是自动发现生产库的脚本。`$PgBin`、`$DatabaseName`、`$DbOwner`、`$PrivateTransferDir`、`$PrivatePgpass` 必须来自该次审核后的本机计划；示例不含密码。PGPASSFILE 用受控文件，不能把密码放命令参数或打印 env。参见 [PostgreSQL 密码文件](https://www.postgresql.org/docs/18/libpq-pgpass.html)。

```powershell
# 源机：目录预先设好 ACL；最终执行前已冻结/排空写者。
$savedPgpass = $env:PGPASSFILE
try {
    $env:PGPASSFILE = $PrivatePgpass
    $dumpPath = Join-Path $PrivateTransferDir 'customer-final.dump'
    if (Test-Path -LiteralPath $dumpPath) { throw 'Refuse to overwrite an existing snapshot.' }
    & (Join-Path $PgBin 'pg_dump.exe') --host=127.0.0.1 --port=28490 `
        --username=$DbOwner --dbname=$DatabaseName --no-password `
        --format=custom --file=$dumpPath
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed; preserve the first error.' }
    Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256
} finally {
    $env:PGPASSFILE = $savedPgpass
}
```

```powershell
# 目标机：已创建归属正确的空库；API/后台写者尚未启动。
# 先核对私下传来的 dump SHA256，再运行；PGPASSFILE 属于目标机。
$dumpPath = Join-Path $PrivateTransferDir 'customer-final.dump'
$savedPgpass = $env:PGPASSFILE
try {
    $env:PGPASSFILE = $PrivatePgpass
    & (Join-Path $PgBin 'pg_restore.exe') --host=127.0.0.1 --port=28490 `
        --username=$DbOwner --dbname=$DatabaseName --no-password `
        --no-owner --no-privileges --exit-on-error --single-transaction $dumpPath
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore failed; do not start the API.' }
} finally {
    $env:PGPASSFILE = $savedPgpass
}
```

`--no-owner/--no-privileges` 由目标 owner 接管对象，须另核对目标的收紧 ACL，不把权限恢复“成功”当成默认公开授权。保留 stderr 和退出码，失败不继续启动。恢复工具的事务/错误选项见 [PostgreSQL 18 pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html)。

恢复后至少按主体核对：用户/Key 与身份映射、团队 owner/member/成员 cap、个人/团队订阅实例与有效期、余额/赠金/当期已用、结算/hold 状态、历史记录和序列、订单/支付 inbox、上游 account/group/proxy ID、定价/倍率及 migration version。保留匿名数量、金额精度、校验摘要和差异，不能仅核总余额或行数。线上有更新后的数据，不能用开发 S0 的固定数量当验收目标。

## 6. Redis、续聊与 OAuth 单写者

**本次是同一单核心的跨机迁移。** 旧桥接→单核心工具只拷贝供给 affinity、排除旧影子 owner；那条规则不能照搬到现在，否则会丢失单核心已生成的客户续聊归属。

当前源码将原生 Response 状态存为以下 Redis 键族，`<digest>` 是 response ID 的 SHA256，不在公开记录中输出其内容：

- `sticky_session:<group-id>:openai:response:<digest>`：上游 account affinity。
- `sticky_session:<group-id>:openai:http-response-owner:user:<digest>`：当前客户 user ID。
- `sticky_session:<group-id>:openai:http-response-owner:key:<digest>`：当前 API Key ID。

新机完整恢复 PG 的同一 ID 空间后，应将有效 affinity 与成对 owner 一起纳入受审阅的状态传递，核对 account/group/user/key 归属、键型、TTL 和上限；TTL 扣除传输耗时，不延长寿命，已过期的不复活。旧签名 `resp_ry1_` 还依赖原 namespace/identity secret 和 PG 原 actor/workspace 映射。WS 的进程内连接池不能跨机器复制，客户端需要重连；持久化归属和相应上游可用性决定能否恢复上下文。

不要直接复制整个旧 Redis 实例所有逻辑库，或把旧 refresh lock、运行中并发/hold、调度 lease、缓存中的旧权限带到新机。按实际启用功能列出需要保留的安全状态，例如 session/撤销记录、幂等和仍有效的限流窗口；允许客户端重新登录不意味着可任意丢弃撤销/安全状态。源机冻结并排空后，再导出经过分类的 DB 1 状态；目标先空库，按审核名单恢复，验证对账/续聊/拒绝路径。不使用 `FLUSHALL` 清理共享实例。

**当前仓库没有通用的“单核心→单核心 Redis 状态迁移器”。** 接手机器需要补齐这份范围明确的导出/恢复计划与实际测试，或使用经过演练的独立 DB 1 备份恢复和受控状态清理；不能把已有同机 affinity 脚本改目标地址后宣称无缝。若必要 owner/affinity 未恢复或只保留部分，将续聊标为未验收，不绕过归属验证。

同一套上游 OAuth 在任意时刻只能由一台业务实例刷新。准备/演练使用专门测试供给，或禁止副本访问真实供给；单设 `TOKEN_REFRESH_ENABLED=false` 不够，因为真实请求可触发懒刷新。最终冻结时停止源机所有刷新者和请求写者，确认无孤儿进程，再导出最新账户状态；新机开门后只由新机负责。不要让旧 Sub、prewarm、恢复演练副本开机后自动刷新。

## 7. 新机隔离验收清单

先使用独立测试数据库/Redis、合成客户和受控测试上游，配临时测试 hostname/Tunnel；随后对最终恢复数据只做允许的核验。**不要提前把新 connector 注册到正在服务的同一生产 Tunnel**，否则可能在数据完成前接到真实流量。真实模型测试控制费用、保留首次失败，不能以 mock 或本机 HTTP 200 替代。

| 项目 | 放行证据 |
|---|---|
| 源码与服务 | 源 commit/patch/资源/binary 哈希匹配；WinSW/监听/进程归属；无人登录启动、重启恢复；数据库/Redis 持久化 |
| 登录与客户 | 旧用户名/原密码、无邮箱用户、昵称变化不改登录名；新用户名注册；禁用用户拒绝；MFA 不绕过；Key 不重发 |
| 团队/资金 | owner/member 权限隔离、个人/团队 scope、成员额度与订阅实例；成功扣款、失败不乱扣、幂等不双扣；匿名账本一致 |
| 常用 API | 文本 HTTP/SSE、工具往返、PDF inline/URL、真实联网及引用、native Images/Responses 图片能力；结果正文/图片有效 |
| 续聊 | 同域名/原 Key/同 Codex 配置无需重装；HTTP 与 WS 两轮、跨进程恢复、旧签名 ID、新原生 ID；跨用户/团队/Key scope 非授权访问拒绝且不收费 |
| 链路 | 直连及显式 SG 代理完整正文；120 秒 SSE 完整结束/事件数；并行请求；Tunnel 四连接真实 GOST→17897 路径和收发增量 |
| 网站与下载 | 浏览器真实登录/团队/用量/管理入口，资源和客户端下载正确；只跑 HTTP 时明确浏览器未验收 |
| 支付 | 原回调域名/路径/签名与幂等收件、待处理订单保存；真实商户 E2E 未跑时不标自动履约/退款成功 |
| 健康观测 | 六条路径、真实响应完整性、采样时钟/新鲜度/最大间隔；controller 实际节点与连接流量，不依赖 ready=200 |

原主机的候选测试可作为回归基线，不替代此新机器。SG headless 已有短时完整流证据，桌面 7897 的首次长流失败仍保留，见 [SG 验证记录](SG-HY2-VALIDATION-20261010.json)；这不保证新机的 UDP/网络路径或长期稳定性。不能把未完成的浏览器、商户或无人登录测试写成 PASS。

## 8. 最终短冻结与域名/Tunnel 迁移

在用户授权的切流窗口执行。提前完成 binary、资源、服务、ACL、独立链路、恢复演练和 DNS/Tunnel 控制准备，不要求客户空闲 20 分钟。窗口长短以预演的 dump + 传输 + restore + 核验时间决定；同机切换脚本的 120 秒预算不适用于跨机全库恢复。若实测超出可接受窗口，先改进传输/恢复方案或单独实现并验证增量同步，不承诺不存在的秒级切换。

1. 记录旧机当前实际核心、服务、文件哈希和回执，确认其为待迁出的单核心；冻结本次配置/版本变更。新机尚未接收正式业务。
2. 关闭旧入口写入准入，包括推理、注册/管理、支付回调等所有会改变客户状态的路径；排空 HTTP/SSE/WS、后台结算/队列和 hold，确认状态可核对。停止旧 API/刷新者等剩余写者并检查子进程；不能仅关首页。
3. 生成最终 PG 快照、必要私有运行状态和 Redis 状态；保留冻结时刻、哈希和源对账摘要。旧机保持冻结，支付回调重试/人工收件范围有明确记录，不能在冻结期间静默丢单。
4. 新机完成最终恢复、ID/资金/状态核验、私有 env 和代理检查；启动后仍关闭公网业务准入，先做不产生真实业务写入或 OAuth 刷新的健康检查。确认唯一客户权威和唯一 OAuth 写者已交给新机。
5. 在开门前持久记录进入切流阶段，然后调整 Cloudflare 受控路由/connector 或 DNS。域名继续为 `api.realyu.fun`，URL、客户 Key、用户名不变。DNS 缓存或 Tunnel connector 并存期间，旧机不得继续使用自己的数据库接受新业务；旧入口保持维护响应，或使用另行测试的透明转发到新权威。
6. 新机开门，立即从真实外部客户端做完整文本/续聊与关键功能、身份拒绝及账务核对。原生产 connector 的切换/长连接关闭可能影响在途流，客户重试或重连要可辨认，不能宣称零中断。
7. 验收后确认旧业务核心、旧供给/prewarm 和相关刷新计划为停止/禁用；保留原机只读归档、回执和受控恢复入口。另机稳定观察和备份恢复验收完成后再下线原机。

Cloudflare 变更计划必须说明采用新 Tunnel 还是接管既有 Tunnel、DNS/路由回退点和 connector 身份。不要删非本次所有的 Tunnel/DNS/进程，不把另一台机器上开着的同名服务当作可覆盖资源。操作若部分成功，保留首次回执，先核对实际状态再决定继续，不能重复“全量安装/迁移”覆盖证据。

## 9. 恢复边界、剩余能力和最终交付回执

**新机未开门、无新增业务写入且未刷新供给凭据时**，失败可以停止新写者，确认进程/数据库操作已退出、源机状态仍可用后恢复旧机准入。数据库超时或命令失败不等于后台操作已停止；恢复前须确认实际状态。

**新机开门、开始新结算/订单/账户写入或刷新凭据后**，进入向前恢复：优先修复新机；若必须迁回，应再次冻结并传回最新 PG/安全状态，按新的受控迁移处理。不能直接恢复开门前备份、重新启用旧 SQLite/影子库或让两边独立写账。任何未知开门结果按可能已产生新写入处理。

迁机不自动补齐产品 P1。当前已知范围以 [单核心说明](SINGLECORE-CANDIDATE.md)、[供给与价格说明](SINGLECORE-SUPPLY-PRICING.md) 和最新更新日志为准，包括旧支付人工 inbox/自动履约差异、WS 异常终止的耐久结算、图片工具附加费/少量最小计费单位差异，以及 role10 仅保留限定团队管理。不可通过迁机顺手放宽角色、免费结算或删历史来“解决”。

接手方最后提交脱敏回执，至少包含：

- Git commit、上游 commit、补丁/逐文件/binary/资源哈希和各工具版本；私有包只记录哈希/受控保管位置，不贴内容。
- 新旧服务、实际进程路径/启动模式、端口/依赖、数据库权威和角色 ACL、SG 路径；无人登录开机证据。
- 预演及最终 dump/restore 命令、退出码、耗时和快照哈希；ID/资金/历史/订单的匿名逐主体校验结论。
- Redis 恢复分类、TTL/数量、续聊与非授权访问验证；OAuth 仅一机刷新及旧写者停用证据。
- 开始冻结、最后旧写入、新机开门、正式域名接管和验收时间；首次错误、CF-Ray/探针 ID、恢复动作和实际用户影响。
- 所有 E2E 的 PASS/FAIL/NOT_RUN、费用/结算核对、短期稳定性采样和仍需处理项。没有完成的验收不写成长期稳定保证。

将无敏感信息的发布结论同步到 [LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md)、[DEVLOG.md](../../DEVLOG.md)、[UPDATELOG.md](../../UPDATELOG.md) 和 [HANDOFF-SUB2API.md](../../HANDOFF-SUB2API.md)，与该次实际代码/脚本一并 commit。私有数据、数据库备份和凭据始终另行保管。
