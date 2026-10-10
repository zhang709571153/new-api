# UX 原生发布接续（2026-10-10 22:31 CST）

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
