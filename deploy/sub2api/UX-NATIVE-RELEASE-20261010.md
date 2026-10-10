# RealYu 原生 UX 已发布（2026-10-10）

北京时间 **23:13:51**，`realyu-singlecore-v0.2.15-20261010-ux` 已真实 ACTIVE，维护窗口
**32.596 秒**。306/307 已应用，迁移历史 297→299；继续使用同一 PostgreSQL/Redis 和
新加坡 SG-HY2，没有恢复数据库或启动旧 New API 服务。见[发布回执](../../lab/sub2api_e2e/ux-production-activation-20261010.json)。

## 当前版本与迁机边界

| 项目 | 已核验值 |
| --- | --- |
| 原生源码 | `93022254b98d9cdfbc36db8d7ef6007365a89b53` |
| 上游 | Sub2API v0.2.15，固定 `f2669c8cf62555cd92389b3f55920e9e6e7c6ff2` |
| binary SHA256 | `88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081` |
| 完整补丁 SHA256 | `12e80b9900afd22b15fc37d3e26b06567426c7e0578747bb2d978acf83b24678` |
| binary | `C:\ProgramData\RealYu\singlecore-production-20261010\bin\20261010-ux-r1\sub2api.exe` |
| env | `C:\ProgramData\RealYu\singlecore-production-20261010\config\api-env-ux-20261010-r1.json` |

**下方历史 Activate-Ux 命令已经执行完成，不要再次运行。** 原计划绑定发布前 manifest、
旧版本和数据库状态。后续更新需准备新计划；另一台 Windows Server 按[迁机交接](SINGLECORE-WINDOWS-HANDOFF.md)
迁移当前 PG/Redis、身份与受保护凭据。禁止重新导入旧 SQLite/S0/S1，或用发布前备份覆盖后续业务交易。

## 生产验收

- [公网协议 7/7](../../lab/sub2api_e2e/ux-production-public-protocol-20261010.json)：模型目录、SSE、
  非流式、PDF 真实内容、实际联网搜索及引用、同 socket 两轮续聊、函数工具往返。8 个模型请求，
  保留请求 ID 和 CF-Ray，无自动重试。
- [严格浏览器 19/19](../../lab/sub2api_e2e/public-browser-strict-20261010T152452Z.json)：旧用户名密码、
  登录直接进入、管理员工作台/账号/用量/用户、真实团队与成员用量、CNY×7、工作台名称、版本与兑换入口隐藏、
  Codex/WorkBuddy Windows/macOS 四种掩码命令预览。同源 HTTP 与脚本错误为 0，预期第三方拦截单列。
- [CowAgent 原 Key](../../lab/sub2api_e2e/cowagent-ux-recovery-20261010.json)：原配置真实响应 `COWAGENT-OK`，
  HTTP 200、一次资金结算、一次用量及去重记录，13 项检查通过。[同类 Key](../../lab/sub2api_e2e/cowagent-cohort-ux-production-20261010T151429Z.json)
  中 12 把有效 Key 的身份与模型 24 次检查全部通过，3 个已禁用/删除身份仍正确拒绝。
- [资金对账](../../lab/sub2api_e2e/ux-production-funding-reconciliation-20261010.json)：23:13:37–23:26:08，
  14 个变化请求全部结算，差额、违规、未决预留为 0；首次快照中的一个并发预留自然完成后才报告 PASS。
- [真实注册与商品预览 12/12](PUBLIC-SIGNUP-BROWSER-VERIFICATION.md)：23:36:51–23:37:33，仅注册一次，
  用户名/昵称、无邮箱、自动一把个人 Key、零余额配置、团队 scope 不回退、六档商品及钱包方式、
  九模型 USD 价目通过。测试用户及 Key 经正常 API 停用并由 PG/管理员接口复核，不删行、不充值、不下单、不调用模型。

本次未再次调用图片模型；代码未变，实际比熊图片证据来自[此前单核心公网验收](SINGLECORE-PRODUCTION-20261010.md)。
浏览器不代表四种桌面安装器已重新运行，也不代表 35 项旧 UX 全部完成。真实钱包扣款/履约仍以
[隔离浏览器与 PG 15/15](UX-CANDIDATE-VALIDATION-20261010.md)为证，不能混称生产付款。

## 品牌和首错

[注册与六档商品配置](../../lab/sub2api_e2e/ux-production-catalog-settings-20261010.json)已读回：
用户名注册、自动个人 Key、默认个人 group 2、个人钱包购买开启；Lite/Starter/Pro/Max/Ultra/Scale
分别为 ¥98/198/398/698/1398/2598，28 天四周。商户实例为 0，余额充值关闭，不发注册欢迎资金。
模型页启用，9 个既有模型保持 USD 报价及原价格，group 2 仍为专属，只向有权限的登录用户展示。
首轮配置因原生空支付类型返回 `null` 而中止上架，保留首错；仅按明确合同归一化存在的 null/[]，
17 项回归通过后，第二轮只执行六个商品上架 PUT，未重复改设置、未调用商户或创建订单。

RealYu 名称、Logo、公网 base URL/frontend URL 已通过正常管理员 API 恢复，公共设置及实际 HTML 缓存读回通过，
未写虚假确认记录或改客户余额。[品牌首错](../../lab/sub2api_e2e/ux-production-brand-20261010.json)保留：
首次默认 Python UA 遭 Cloudflare 403（CF-Ray `a486939538fccd1d-LHR`），固定维护 UA 的受控对比成功，未改 WAF/网络。
第二次写入触发原生禁用 OIDC 的 GET/保存缺省值不一致，两个 PKCE/ID-token 标志 true→false，严格检查阻止误报成功。
正常 API 尝试恢复 true 被禁用分支忽略，未伪称恢复成功。OIDC 始终关闭；最终品牌通过且 false/false 被后续工具保留。
将来启用 OIDC 前须显式配置和验证这两项。

Cow 初次验证器把成功响应 `error:null` 当对象解析，误报 AttributeError。修复空值处理与证据保存顺序后，
直接核对原响应和账本，没有重发付费请求。HTTP 跟踪 UUID 与资金流水 UUID、两种指纹分别有不同语义，
通过授权身份、模型、完整 token 数和时间窗唯一关联；原始误报与复核错误保留。

## 健康和后续缺口

[完整采样窗口](../../lab/sub2api_e2e/ux-production-health-20261010.json)为 23:13:56–23:37:46：
直接公网 142/144（98.61%），经代理公网、backend、bridge、两个 Tunnel 均 144/144。
最大采样间隔 10.014 秒，复核时最新健康样本距今 6.84 秒；43 个路径样本全部为固定 SG 的八条连接，
最大路径采样间隔 47.33 秒、最新距今 16.34 秒，最后相同连接的收发计数均有增量。

现有采样在 23:15:36、23:21:36 记录两次直接公网无响应超时，见[首次失败及相邻采样](../../lab/sub2api_e2e/ux-production-direct-path-first-failures-20261010.json)。
相邻采样与同期代理、源站、双 Tunnel 正常；不能认定为网站整体中断、未走代理或 20 分钟周期故障。
继续复用现有观测，没有新增常驻探针；采样不构成长期 SLA。

真实商户/ZPay 付款及退款、托管充值、团队首购/续费/升级/退款、管理员周订阅授予编辑、部分客户总览/趋势联动
和日志 IP 隐私仍按[UX 矩阵](UX-PARITY-RECOVERY-20261010.md)保留。六档商品和用户名注册的配置流程见
[目录恢复](RESTORE-CATALOG.md)与[设置工具](SINGLECORE-UX-SETTINGS.md)，不以钱包测试替代商户验收。

## 以下为 22:31 的历史接续记录（已经执行，禁止重放）

当前生产仍为 `realyu-singlecore-v0.2.15-20261010-ws-owner`，实际二进制 SHA256 为
`0124bffafc6232702bb43b723f21d9999e88f89e2d37b8a8261925ee7981fad9`。完整 UX 候选已构建、
审查、隔离验收并推送；22:30 Windows UAC 返回“操作已被用户取消”。私有 operation 没有
activate 日志或 update receipt，维护门未关闭，306/307 未应用，未启动新版。

## 本机唯一接续入口

已固定计划 SHA256：`acaabad7f18893cdf6aa1b1921b7cf8a993a7cd2d4465050274b51e88db9064a`。
生产只读预检在取消后再次通过。不要再运行旧 r2/r3 CNY-only 入口；它们仍保留用于首错追踪。
新计划包含所有 CNY/管理入口/CowAgent 修复和已验收 UX，两项迁移复用当前 PG 权威。

在本机 PowerShell 执行以下命令，并完成正常 Windows UAC。该动作已获用户发布授权，
需要交互只是系统管理权限；不借用其他服务或绕过权限。命令本身的退出与服务实际版本分别核对。

```powershell
Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "C:\srv\realyu-singlecore-dev\runtime\production-ux-update-20261010-r1\Activate-Ux.ps1"' | Select-Object ExitCode
```

私有执行目录：`C:\srv\realyu-singlecore-dev\runtime\production-ux-update-20261010-r1`。
每次终端日志使用新的时间戳，首次错误不覆盖。新服务目标版本为
`realyu-singlecore-v0.2.15-20261010-ux`，二进制 SHA256
`88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081`。
切换器先排空在途调用，再停原生服务、应用两项增量、启动验证后开门。30 秒只是排空预算，
实际维护窗由回执计时；不会强杀仍在进行的客户请求。

## ACTIVE 后仍需完成

1. 核对 `update-receipt.json` 的 ACTIVE、真实 SCM→launcher→worker、Session0、二进制 SHA、
   `/api/status` 的 engine/single_core/version、维护门已打开和 306/307 checksum。
2. 使用 [品牌修复](Apply-NativeBrand.py) 经正常管理员 API 更新五个品牌字段，核验实际 HTML 缓存。
   管理员凭据继续私有；不写虚假首次确认记录。
3. 用 [CowAgent 验证器](../../lab/sub2api_e2e/probe_cowagent_native_recovery.py) 检查原配置/原 Key，
   再执行一次明确限额的真实请求；同类 15 把 Key 仅做身份/模型只读检查。
4. 使用正常管理员 API 核对 group 2 的当前个人路由与权限，再配置用户名注册/个人默认 Key。
   注册与支付开关不会由程序更新器自动打开；按已授权 UX 恢复范围单独记录设置前后与实际页面验收。
5. [恢复六档旧套餐](RESTORE-CATALOG.md) 默认下架，正常管理员 API 核对售价/周期/周额度/来源后，
   才上架已支持的个人钱包购买。真实商户未验收，不能仅因为钱包测试通过就开放商户；团队首购、续费、
   升级和退款仍按 capability 关闭。既有客户权益不能通过重建商品模板改写。
6. 严格公网页面检查管理详细页、工作台、团队、USD 模型价与 CNY 用量；做小额文本/SSE/PDF/WS
   及当前 PG 资金对账，保留首错。图片转发代码未改，本轮可以明确引用此前实际比熊图片证据，
   不假称新二进制又执行了付费生图。继续读取现有采样，不能用 ready=200 代替完整响应。
7. 以实际结果追加 LIVE、DEVLOG、UPDATELOG 和此入口，再 commit/push。源码已交付不等于上线。

当前[隔离验收](UX-CANDIDATE-VALIDATION-20261010.md)为前端 737 项、真实浏览器/HTTP/PG 15/15，
包括钱包实际单次扣款/履约。取消后的 22:26–22:31 既有六路径采样各 30/30；8 条路径样本保持
八个 SG 流量。只读账务快照间无新请求、无差额；这不构成新增模型或商户成功证据。

## 另一台 Windows Server

上述命令是本机固定计划，不可原样在其他机器运行。新机器按[Windows 交接](SINGLECORE-WINDOWS-HANDOFF.md)
重建源码、受保护迁移当前 PG/Redis/凭据并准备其服务计划。已 ACTIVE 后禁止再重放原 SQLite S0/S1。
当前 PG 验证备份虽可恢复，也不能覆盖后来新增的资金交易。
