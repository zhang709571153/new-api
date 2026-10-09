# Windows Server 原生候选交接

用户后续已授权并完成当前主机的正式发布；准确版本、测试结果和历史失败见 [LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md)。本指南用于另一台 Windows Server 的独立部署和迁移，当前主机成功不代表目标机已部署。接手者使用最终交付的 `codex/sub2api-handoff-20261009` 分支和提交；原仓库默认 `realyu` 分支不是本次交接版本。跨机切换按届时指令执行，保持单一账本写入者和单一 OAuth 刷新者。

## 目标机已知环境

依据负责人提供的 2026-10-09 只读盘点，目标是腾讯云 CVM 上的 Windows Server 2025 Datacenter 24H2，Build 26100.32860，x64，4 vCPU、16 GiB 内存，C 盘约 158 GiB 空闲。当前操作员具管理员权限；存在待处理文件改名的重启迹象，需要安排维护窗口核实。

没有发现可用 Docker/WSL/Linux 引擎；来宾系统没有报告嵌套虚拟化所需扩展，因此主方案改为原生 Windows。当前也没有 PostgreSQL、Redis/Memurai、WinSW、Go、Bun、Cloudflared 或 Web 反向代理。Codex 用户缓存中的 Git/Node/Python 只适合交接检查，不能作为长期服务依赖。服务安装、后台账号出网、系统重启恢复、实际 OAuth 和模型请求均未在目标机验证。

现有云平台代理、监控、远程维护、RDP/WinRM、Codex 服务和计划任务应保留。公开 Git 仅记录此脱敏摘要；IP、账号名、访问凭据和完整机器清单留在私有交接渠道。

目标无独立备份盘，C 盘备份只能暂存；加密异机备份目的地待负责人提供。公网入站映射、安全组、DNS/TLS/隧道和私有 Sub2API 管理入口仍待确认。宿主防火墙当前状态不能代替云侧网络验收，候选服务始终绑定回环，不自动开放端口。

## 原生组件与许可边界

| 组件 | 候选方案 | 仍需验证 |
| --- | --- | --- |
| RealYu / New API | 由准确候选提交构建 Windows amd64 程序，包含完整前端和公开下载资源 | 记录二进制 SHA-256、版本和构建输入；不能使用旧现网 exe |
| Sub2API | 官方 v0.2.15 Windows amd64 包 | ZIP 校验后解包，再记录实际 exe SHA-256 |
| PostgreSQL | 原生 PostgreSQL 18 x64，使用 PostgreSQL 官网指向的 EDB 发行包 | 官方平台表含 Server 2025；固定实际补丁版本、校验和及数据库主版本，专用账号和数据库 |
| Redis | 免费优先：先验收社区 redis-windows；也可用外部 Redis，Memurai Enterprise 为可选商业支持方案 | 社区 Windows 构建须通过持久化、崩溃/重启、服务账号、备份恢复与负载验收；不得冒称官方生产支持 |
| Windows 托管 | 官方 WinSW 2.12.0 x64，直接托管 RealYu、Sub2API 和预投影 worker 三个 Go exe | LocalService 实际权限、Ctrl+C 排空、崩溃重启、系统重启后恢复须在候选机验证 |

生产并非必须购买 Memurai。按用户免费优先偏好，社区 `redis-windows` 作为待严格验收候选；其 README 明确建议本地开发使用、生产遵循 Redis 官方 Linux 部署指引，也说明不隶属 Redis。通过本项目验收只能证明所测版本/环境表现，不能获得上游生产支持承诺。[社区项目说明](https://github.com/redis-windows/redis-windows)。

若以后选择商业支持，Redis 官方列出 Memurai Enterprise Windows 方案。Memurai Developer 禁止生产使用并在 10 天后自动停止，不能把其默认开发许可用于商业长期运行；它不是免费社区构建的许可规则。本交接不自行申请付费产品或试用。[Redis 官方说明](https://redis.io/blog/use-redis-natively-on-windows-with-memurai/)、[Memurai 版本限制](https://www.memurai.com/get-memurai)、[许可文件要求](https://docs.memurai.com/en/config-license)。

免费 Windows 候选固定为已用于隔离测试的 [redis-windows 8.10.2 MSYS2 ZIP](https://github.com/redis-windows/redis-windows/releases/tag/8.10.2)，SHA-256 `7c8cebd50347eaa1d9e784da842ed47a4f33394637835531d6614777b950ee85`。实际服务应显式设定私有数据目录、认证、回环绑定、AOF/RDB 策略并验证停止/重启后的数据；不能沿用 E2E 中关闭持久化的配置。必须测试异常终止后的恢复、AOF rewrite、RDB 导入、TTL/租约语义、磁盘满和备份恢复；尚未完成这些项目不能切正式流量。包中的 Redis 本体、MSYS2 组件、服务 wrapper 应分别保留实际随附许可证，仓库 wrapper 的许可证不自动覆盖全部组件。

PostgreSQL 的正式服务可由其受支持安装程序注册，数据目录和专用账号由目标管理员明确配置；不要在管理员交互账号下长期挂着 `postgres.exe`，也不要给 PostgreSQL 数据目录 Everyone 写入权限。[官方 Windows 安装入口及平台表](https://www.postgresql.org/download/windows/)。

固定工件参考：

- [Sub2API Windows ZIP](https://github.com/Wei-Shaw/sub2api/releases/download/v0.2.15/sub2api_0.2.15_windows_amd64.zip)，SHA-256 `1a95286596a10c268b17e67d508ade717b4ca1dfde2b4f4c295ab2d31f1dc5d1`。
- [WinSW 2.12.0 x64](https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe)，本次官方 HTTPS 下载计算的 SHA-256 `05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da`；此散列是本地工件记录，不冒称上游签名。
- [WinSW 配置](https://github.com/winsw/winsw/blob/v2.12.0/doc/xmlConfigFile.md)与[日志规则](https://github.com/winsw/winsw/blob/v2.12.0/doc/loggingAndErrorReporting.md)。

## 可执行的最小打包流程

`New-NativeCandidate.ps1` 仅校验并写入一个新候选目录，不下载工件、不注册/启动服务、不操作 SCM、不改数据库/DNS/防火墙。输入是私有 JSON；输出 XML 也含秘密，不得上传 Git、贴到对话或输出日志。空白示例不会启动成功。

1. 在 Git 外建立受限私有配置，参考 `native-windows.example.json`。RealYu、Sub2API、预热 CLI 和 WinSW 四个程序路径必须是已审核工件，填写各自 SHA-256。还必须在 `dependencies.zoneinfo` 和 `dependencies.go_license` 中填写经过核验的 Go `zoneinfo.zip`、对应 Go `LICENSE` 的路径及 SHA-256；它们是显式打包输入，不依赖接手机器的 `GOROOT`。预热 CLI 从同一提交的 `./cmd/sub2api-prewarm` 构建。为本次构建设置唯一 `candidate_version`，与 `/api/status` 和路由 bootstrap 一致。
2. 准备独立候选 PostgreSQL 数据库和 Redis 端点。数据库名必须包含 `candidate`、`rehearsal` 或 `e2e`；名字检查不是数据库隔离证明，仍须确认真正创建的是独立库和专用账号。远程 PostgreSQL 要求 `verify-full`，远程 Redis 要求 TLS；证书链、ACL 用户及后台账号实际连接仍须验证。不要使用生产 Redis 的同一 DB 来测试租约和调度。
3. 填写稳定 session/crypto/JWT 密钥；`TOTP_ENCRYPTION_KEY` 必须是 32 字节的 64 位十六进制编码。新合成环境使用新随机值，迁移环境须保留原值，不能为适配模板随意重置。WinSW 会展开 `%NAME%`，生成器因此拒绝含 `%` 或控制字符的值；现有密钥若触发此限制，应调整经过审核的秘密加载方式，不能悄悄更换密钥。
4. 指定 `redis_supply`：默认免费候选 `community-windows-candidate`，也可选 `external-supported`、`memurai-enterprise-licensed` 或 `isolated-test-only`。该声明只记录选择，并不证明验收、许可或兼容性。
5. 先仅校验，再生成。输出路径须为新的 `...\candidates\<名称>`，拒绝 `C:\srv`、现存目录、Git 内目录或经 junction/symlink 重定向的目录。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/sub2api/New-NativeCandidate.ps1 -PrivateConfig 'C:\ProgramData\RealYu\private\candidate.json' -CandidateRoot 'C:\RealYu\candidates\rehearsal01' -ValidateOnly
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/sub2api/New-NativeCandidate.ps1 -PrivateConfig 'C:\ProgramData\RealYu\private\candidate.json' -CandidateRoot 'C:\RealYu\candidates\rehearsal01'
```

`ExecutionPolicy Bypass` 仅作用于这次 PowerShell 进程，不修改系统策略；受管机器仍应遵守其执行政策。

生成结构：

```text
rehearsal01/
  candidate.json                         脱敏候选标记，不能替代服务实测
  bin/realyu.exe, sub2api.exe             固定候选二进制
  bin/sub2api-prewarm.exe                 预投影任务工具，支持 --watch
  bin/deps/zoneinfo.zip                   随包固定的 IANA 时区数据
  bin/deps/GO-LICENSE.txt                 对应 Go 发行包许可证
  services/*-service.exe                 固定 WinSW wrapper
  services/sub2api-service.xml           私有标准模式配置
  services/prewarm-service.xml            私有 --watch 常驻 worker 配置
  services/realyu-service.xml            私有 legacy bootstrap 配置
  services/realyu-service.sub2api.xml.pending  完成路由后待激活配置
  state/realyu/new-api.db                 首次启动才创建的合成库
  state/sub2api/                          Sub2API 本地状态
  state/integration/sub2api.json          操作员后续放入的私有映射
  state/enrollment-queue/                Web 写入、worker 消费的持久作业
  state/prewarm/                         每配置代次的私有快照、进度与心跳
  logs/realyu/, logs/sub2api/, logs/prewarm/  私有日志
```

根目录禁用 ACL 继承，只向 SYSTEM、Administrators、打包账号和 LocalService 授权。程序和服务 XML 对 LocalService 只读，应用数据、队列、进度和日志可写；映射目录需要写入锁/退避文件，应用代码不改映射 JSON。三个候选服务共用 LocalService，因此不能声称按进程强制实现 Web 对进度只读、worker 对队列只读或秘密文件的写入隔离。正式部署若使用独立虚拟服务账号，须配置并实测 Web 的 queue RW/state RO、worker 的 queue RO/state RW，以及双方 bindings JSON RO、worker 仅锁/退避目录 RW；还须验证网络代理和备份权限。

wrapper 配置为 `Manual`，避免打包后意外自启；配置了失败后 10/30/60 秒重启及 150 秒停止等待，不会重启操作系统。stdout/stderr 每份约 20 MiB、保留 5 份；应用自身文件日志/数据库日志另需保留期和磁盘告警，不能只检查 wrapper 日志。尚未通过真实 SCM 崩溃和排空验收。

### 必须随包携带 Windows 时区依赖

本机真实准备曾遇到官方 Sub2API v0.2.15 Windows 程序无法解析 `Asia/Shanghai` 而退出；该二进制没有内嵌 IANA 时区数据。打包器现在要求显式提供已审核 Go 发行包中的 `lib/time/zoneinfo.zip` 及同一发行包的 `LICENSE`，校验两个文件的 SHA-256、ZIP 中的 `Asia/Shanghai` 条目及 TZif 头，并将其固定复制到上述 `bin/deps` 路径。Sub2API 服务 XML 设置绝对 `ZONEINFO` 路径。文件缺失、无法读取、哈希不符或归档缺少所需时区时，校验失败且不创建候选目录。打包收据记录两个依赖哈希；不得只复制 exe 或在新机器上临时依赖开发者 Go 安装。

合成打包测试仅检验文件、XML 和 ACL；实际启动仍须使用真正的时区归档验证服务能读取 `Asia/Shanghai`。保留 Go 许可证，不将测试用的最小 TZif 载荷当作发行数据。时区工件和二进制一样通过受控工件传输携带，不上传运行配置或数据库。

Sub2API 模板同时显式设置 `DATABASE_MAX_OPEN_CONNS=50`、`DATABASE_MAX_IDLE_CONNS=10`、`REDIS_POOL_SIZE=128`、`REDIS_MIN_IDLE_CONNS=16`。这与本机 PostgreSQL `max_connections=100` 的预算相容，避免 stock 默认数据库池超过实例上限；仍需为管理员连接、其他客户端及多副本网关预留总连接预算。固定池限制不代表负载容量已验收。

## 候选启动、路由与确认

以下是接手机操作员未来的隔离验收步骤，本轮未执行。先确认管理员管理的是新候选、数据库/缓存依赖健康，再显式安装和启动候选 WinSW 服务。使用生成的绝对 exe 路径和其 `install`、`start`、`status` 命令；不要调用旧服务名或自动枚举并停止未知进程。`sub2api_windows_service_dependencies` 可以填写已核对的本机数据库/缓存服务名；远程依赖没有 SCM 名称，应用健康验证仍不可省略。

保持当前 legacy bootstrap 状态，完成合成管理员初始化，用既有管理员 API 的 `bootstrap_route.py` 创建 type 59 路由（见主 README）。首次 Sub2API 管理操作若返回 423，应按 [v0.2.15 管理声明](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/docs/legal/admin-compliance.zh.md) 走正常确认流程。本会话运营者已明确授权，主执行者已完成本次隔离实例正常确认：HTTP 200、后续不再要求确认、管理分组读取 HTTP 200；私有收据未放 Git。这不是对所有未来新实例的预先同意。bootstrap/worker 不内置静默同意，不写 DB 绕过；尚未完成确认的实例相关 E2E 标记 BLOCKED。

完成路由后，停止并排空候选 RealYu，确认其专用进程已退出，将私有映射放入 `state/integration/sub2api.json`，保持 `provision.mode="prewarmed"`。保留 bootstrap XML 的私有副本，核对后以 `.pending` 内容替换 `realyu-service.xml`；启动候选和下节 worker，让后台扫描已提交的合成用户与成员并完成预投影，再开放候选客户测试。服务 ID、路径和 session/crypto 秘密保持不变。验证 driver 状态、旧渠道管理接口 410、客户与团队隔离、HTTP/WS 和计费。不要以 XML 切换成功代替完整验收。

模板 `TOKEN_REFRESH_ENABLED=false` 只禁用后台刷新，**不能阻止请求路径刷新**。演练使用独立测试账号/供给，不导入现网 refresh token。正式切换须先获用户明确命令并通过验收，届时先排空旧 owner，再转交刷新所有权；当前不操作旧 owner。

### OpenAI OAuth 账号的 WebSocket 和压缩验收

stock Sub2API v0.2.15 的 OpenAI OAuth 账号需通过官方账号编辑功能显式开启 `extra.openai_oauth_responses_websockets_v2_enabled=true`，并将对应 WebSocket 连接模式设为 `ctx_pool`。首次真实 WS 测试在该能力默认未开启时收到关闭码 `1013`、`no available account`；不能将 HTTP 可用或升级握手成功视为 WS 账号已经可调度。完成官方账号配置后，同一 socket 的两轮真实上游续聊已通过。只调整获授权的隔离测试账号，不直接写数据库绕过能力探测，也不修改现网账号。

当前原生 Codex compaction v2 及压缩结果继续调用已经通过真实链路验证；旧 `/responses/compact` 接口实测上游返回 404，属于旧接口能力限制，不能笼统记为全部上下文压缩失败。完整机器交接仍分别验证当前原生协议、旧接口兼容要求与客户端实际使用版本。参见 `lab/sub2api_e2e/real-chain-features-summary.json` 的准确候选二进制 SHA 和逐项结果；这些样本不覆盖后续改版、目标主机或桌面客户端验收。

## RealYu 远程文件下载与 Sub2API 出口

RealYu 下载客户 PDF/图片 URL 的网络出口，与 Sub2API 账号调用模型时选择的上游代理是两项独立配置。前者通过私有 `native-windows.example.json` 副本里的 `realyu.HTTP_PROXY`、`realyu.HTTPS_PROXY`、`realyu.NO_PROXY` 提供；生成器校验 http/https 代理 URI，并要求 NO_PROXY 保留 localhost、127.0.0.1、::1。留空代理表示直连，不硬编码旧主机代理端口，也不允许任意额外 env 覆盖数据库或 driver。

Linux Compose 使用私有 env 中 `REALYU_HTTP_PROXY`、`REALYU_HTTPS_PROXY` 和可选 `REALYU_NO_PROXY_EXTRA`，内部服务名与回环自动保留直连。代理若有认证，相关配置和生成 XML 都是私密材料。Sub2API 的供给代理仍由其官方账号/代理管理单独配置。

本机直连公共 PDF 源曾因证书链不受信失败；保留 TLS 校验并显式配置 RealYu 下载代理后，真实链路完整读取公共 PDF 已通过（见 `lab/sub2api_e2e/pdf-url-egress-summary.json`，仅一次请求证据）。目标机必须用实际后台账号分别验证下载出口、DNS、TLS 信任链、PDF 完整内容和模型出口。不要关闭证书验证，不复制本机回环代理地址到不存在该服务的目标机；企业证书需求应使用受认可的信任链配置。

## 持久预投影与新用户准备状态

### 生产前必须解决内部授信水位

`bindings.example.json` 中 `initial_balance=100` 是合成验收预算，**不能直接当作长期生产配置**。本次只读核实的真实候选为 standard group、`rate_multiplier=1`；每次 Sub2API 调用按 `actual_cost` 消耗内部余额。余额耗尽或低于其最小 reserve 时，RealYu 已付费用户也会遭上游余额门槛；预投影任务 `DONE` 本身不保证这项余量。提高初始余额也只是延长时间，并未消除门槛。

stock v0.2.15 官方分组与用户分组倍率要求大于 0，不支持通过官方管理把通用计费倍率设成 0。供应账号自身倍率允许 0，但只影响供应账号统计口径，不等于免除 projected user 的余额扣减。不要直接改数据库倍率、改成 simple 模式或重置客户钱包来规避此问题。原始 `total_cost` 与倍率后的 `actual_cost` 可同时保留作供给核对。

候选现在把独立的**内部运营授信**接入同一个 `--watch` worker，配置在私有 bindings JSON 的 `credit`：

```json
"credit": {
  "enabled": true,
  "low_watermark": 20,
  "topup_amount": 100,
  "check_interval_seconds": 60,
  "idempotency_window_seconds": 3600
}
```

数值只是示例，单位与 Sub2API 内部余额一致。只有显式 `enabled=true` 才补额；省略为关闭，关闭时仍须自行保障供给余量。初始余额、低水位和单次补額需按单身份峰值成本、扫描全体身份所需时间、监控间隔、最长处理时间和单次最大请求成本确定，初始余额应高于低水位且覆盖处理窗口。修改 `initial_balance` 不会重置既有身份。

worker 仅检查当前 installation、当前配置代次队列中已 `DONE` 的投影身份，复验派生身份、现有 key 和组授权；按上游用户去重。每页最多 50 次身份 inspection（每次可能包含多个官方读取请求），分页间隔 5 秒，完成整轮后按配置间隔检查。后台不打开 RealYu 客户数据库。低于水位时，先把固定 operation ID、原配置 SHA、目标身份、固定金额和有效窗口原子写到私有 `state/prewarm/credit.json`，再通过官方 `POST /api/v1/admin/users/:id/balance` 使用 `operation=add`、同一 `Idempotency-Key` 和内部用途 notes；成功保留 `CONFIRMED` 回执。账户余额读取、充值审计和缓存失效均使用官方路径，不使用 `set` 或直接写 DB。

读/写 429 都持久等待至少 60 秒和服务返回的更长 Retry-After；读取 5xx/网络错误等待 60 秒，401/403/423 等需运营处理。充值网络丢响应、5xx、异常成功响应或进程中断后可能已入账，因此默认 `BLOCKED`，重启不会重发或更换操作 key。先核对官方调整记录与原 operation ID，只有确认可安全重放且仍在原窗口内，才停止该候选 worker 后显式运行 `--watch --resume-credit`；许可只消费一次，不是永久服务参数，仍复用原 key、金额和配置。过窗或配置代次不一致拒绝补额，必须人工核账；不得删除 intent 来恢复。stock 的原子 add 与幂等成功记录并非单一事务，本实现不承诺绝对 exactly-once。

原生与 Compose 模板显式固定 Sub2API `IDEMPOTENCY_DEFAULT_TTL_SECONDS=86400`，worker 示例仅允许 3600 秒恢复窗口。接入另一个现存 Sub 实例时须核实实际服务端 TTL 不短于本窗口并留出安全余量，不能假设默认永远不变。

`heartbeat.json.credit_status` 记录 `disabled/ready/checking/blocked/error`。异常在所有投影 `DONE` 时也显示运营告警，不能以准备完成掩盖余额补充失败；该状态不修改既有请求的鉴权或客户扣费。需落实此告警的运营处理与目标主机验收，`ready` 不是未来余量保证。

授信不读取、不镜像、不扣减 RealYu 客户钱包，也不是客户再次充值。stock admin add 可能触发邀请返佣规则；专用后端须核实该返佣关闭、内部身份无邀请关系，避免把供给补额算作销售充值。保留官方调整记录、原始 usage 与 RealYu 实际扣费的各自审计。

本地 credit 审计上限为 10,000 个操作，达到上限后拒绝新补额并告警。归档时先停止该候选 worker，确认全部操作为 `CONFIRMED` 并与官方记录核对，在私有加密备份中保存原文件和散列，再创建新的空审计（version 1、operations 空数组）并恢复；任何未决/过窗 intent 都不能通过归档绕过。跨配置、跨机器或备份恢复必须原样保留未决状态及原 operation ID。内部 key/user-platform 其他金额额度仍须明确管理，不能暗中成为客户已购套餐的第二份额度；并发与安全限流另行保留。此功能仅操作经过确认的隔离/迁移后内部身份，本轮未触及真实生产余额。

真实 stock Sub2API 进程授信验收已通过 10 项：同一内部用户的两个 pool keys 只产生一次 add，余额 1 + 100 = 101，官方调整记录恰好一条且匹配 operation ID，worker 重启后不重复充值或审计，未读取 RealYu 数据库或发起模型调用。准确 worker SHA 与范围见 `lab/sub2api_e2e/credit-watch-summary.json`；这不是目标 SCM、长期容量或未知结果人工核账流程的实机验收。

源码依据为固定 v0.2.15 的[分组倍率校验](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/admin_group.go)、[余额准入](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/billing_cache_service.go)、[usage 与扣款](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/gateway_usage_billing.go)、[官方补额及幂等入口](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/handler/admin/user_handler.go)和[原子调整/缓存失效/返佣处理](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/admin_user.go)。

标准默认 `prewarmed` 不在客户首个推理请求里创建 Sub2API 用户/密钥或登录；它只恢复已预备身份。未准备的实际成员/团队组合失败关闭，不能回退共享 key。只有全部 pool 为 loopback 的隔离 fixture 可以使用 `isolated-lazy`。

RealYu 启动后每 30 秒用只读查询扫描已提交的启用用户、有效团队成员、token 和获授权渠道，将不可变作业原子发布到 `state/enrollment-queue/<配置SHA>/<成员>-<团队>-<渠道>.json`。不会把网络调用或行锁加入原开户/团队/资金事务。状态接口也会为当前获授权 scope 幂等入队；终端用户不能提交任意成员 ID。`workspace_team_id=0` 是个人，团队资金所有者不能替代实际成员身份。

`prewarm-service.xml` 用 `--watch` 常驻，5 秒扫描一次队列，长批次每 15 秒写心跳；进度在 `state/prewarm/<配置SHA>.json`，批次快照为同名 `.queue.json`。新配置代次不继承旧 ready；namespace/identity_secret 稳定时已存在的远端身份可幂等恢复。客户端 `/api/workspace/sub2api/status?scope=personal|team|current` 只返回自己的准备状态，不泄漏密钥或他人 ID；`ready` 仅表示所需身份已准备，不表示上游供给健康或真实推理已成功。

生成器仅写服务文件，没有执行 `install/start`。在独立候选手动启动的等价命令如下，正常长期托管仍使用生成的 WinSW 定义：

```powershell
$env:REALYU_SUB2API_BINDINGS_FILE = 'C:\RealYu\candidates\rehearsal01\state\integration\sub2api.json'
$env:REALYU_SUB2API_QUEUE_DIR = 'C:\RealYu\candidates\rehearsal01\state\enrollment-queue'
$env:REALYU_SUB2API_STATE_DIR = 'C:\RealYu\candidates\rehearsal01\state\prewarm'
& 'C:\RealYu\candidates\rehearsal01\bin\sub2api-prewarm.exe' --watch
```

worker 不打开 RealYu 数据库，不写客户账本，不把内部预算复制成客户钱包。配置、清单与进度都在私有目录；日志只打印状态分类，不打印原始 HTTP 错误或秘密。配置文件旁 `.prewarm.lock` 由 OS 锁保护整个 worker 生命周期；`.prewarm-rate.json` 跨队列/状态文件持久共享冷却。正常停止发生在投影中间时保留 RUNNING 和冷却，重启走同样幂等恢复；真实永久错误仍需人工处理。默认每项至少 4 秒；429 按至少 60 秒和服务返回的更长退避等待。每个 Sub 实例只保留一个调度入口；不同主机或配置副本没有全局锁，共用 upstream IP 的其他登录仍需协调。

`BLOCKED`（423/凭据/权限/MFA 等）或 `FAILED` 不会因服务重启自动重试。解决原因后，停止仅属于该候选的 worker，用当前配置代次的 `.queue.json` 与 `.json` 运行单批 `--queue <快照> --state <进度> --resume-blocked`；成功后再启动常驻 worker。不要删除进度/退避文件或在长期服务参数里默认启用恢复开关，不把部分完成算作完成。控制台的 worker 离线/错误和准备中应有运营处理流程。

自动扫描/入队已接入候选，准确集成与目标机测试证据另列。作业保留用于幂等和审计：成员失效会阻断 RealYu 调用，但已经排队的内部任务不会自动删除；过时内部用户回收/取消仍需运营流程。旧失败作业会停止后续入队处理，须人工核查。若确认主体已退组/停用且 RealYu 权限已撤销，先停该候选 worker，再将对应作业文件归档到扫描目录之外，保留进度中的审计项，重启后处理其他任务；有效主体的权限故障应修复供给设置并显式恢复，不能靠归档伪造 ready。Sub2API 用户 allowed_groups 使用全列表更新，不同配置代次或外部管理员同时改权限没有 stock CAS，变更应串行协调。worker 存在不等于全部商业运行验收通过。

## 备份、恢复和重启演练

正式建议把版本程序放在 `C:\RealYu\releases`，持久状态放在 `C:\ProgramData\RealYu`，备份暂存在 `C:\Backups\RealYu`；当前生成器只创建隔离候选，不会提升到这些正式目录。正式安装和定时任务须待明确切换命令后按最终路径生成。

RealYu SQLite 使用 `snapshot_sqlite.py` 的在线备份 API，保留已提交 WAL；不要只复制 `.db`。PostgreSQL 使用同主版本的 `pg_dump.exe --format=custom --file=<私有新文件>`，通过 ACL 受限 `PGPASSFILE` 提供认证，**不要把二进制 dump 经 Windows PowerShell 5.1 的 `>` 管道传送**。保留数据库角色/权限定义以及确切主版本。恢复使用新空库，`pg_restore.exe --exit-on-error --no-owner --dbname=<候选连接>`，不加会覆盖现存数据的 `--clean`。

Sub2API 的本地 data、数据库、JWT/TOTP、RealYu session/crypto、身份 namespace/secret、服务管理凭据、服务 XML、入队作业、预投影进度与共享退避文件均属私有备份。Redis 持久化或完全排空后丢弃临时缓存/租约的决策要记录；不能把旧活动租约混入仍服务中的集群。

跨组件一致的最终备份必须在获准切换窗口排空所有入口并停止写入/刷新后进行。演练至少验证：SQLite integrity 和用户/团队/套餐/余额/审计计数；PostgreSQL 空库恢复；映射保持；两个客户及同团队不同成员隔离；进程崩溃恢复；长 SSE/WS 排空；系统重启后的依赖和网络；离线备份解密恢复。通过后才考虑把正式服务设为 Automatic delayed start。当前所有目标 SCM、系统重启和真实上游/恢复 E2E 均为 **NOT_RUN**。

私有文件用获授权的加密传输渠道转移；代码 Git 与数据/凭据分离，密钥不放聊天、issue 或 Git。按免费优先方案完成 Redis 长期运行验收，补齐备份目的地、正式域名入口和私有管理方式；当前实例声明按已有授权处理，新实例保留自己的确认记录。原主机下线必须等新主机验收、最终数据同步和回滚窗口完成。

本机独立持久化验收已通过 12 项：PostgreSQL 18.6 提交数据正常重启与 custom dump 空库恢复；免费社区 Redis 8.10.2 MSYS2 的认证、Lua 租约、TTL、AOF 正常/崩溃恢复及 rewrite、数据类型和独立 RDB 恢复。见 `native-persistence-validation.json`；所有自建测试进程已停止，没有操作现网或其他测试实例。可用 `verify_native_persistence.py` 在新私有目录和专用回环端口复测；它只管理本次创建的进程。磁盘满、目标 SCM 账号/系统重启、生产数据恢复与长期负载仍未运行；这些结果不形成可用性 SLA。
