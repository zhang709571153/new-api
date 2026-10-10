# RealYu 单核心候选：开发、演练与 Windows 交接

更新：2026-10-10。**已开发隔离候选，未发布、未切流，不能把当前桥接版当成已经全迁。**
此文档与 `singlecore-source.json`、源码补丁及测试脚本一起交付。当前生产部署仍使用
[HANDOFF-SUB2API.md](../../HANDOFF-SUB2API.md) 与 [LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md)。

## 1. 源码与边界

- 上游 Sub2API v0.2.15，固定提交 `f2669c8cf62555cd92389b3f55920e9e6e7c6ff2`。
  官方 tag 是 annotated tag，tag object 与 peeled commit 不同；以提交和文件哈希为准。
- `singlecore-candidate.patch` 保存全部 Go/Vue/SQL/品牌资源变更，包括测试；无需从开发机复制
  未提交目录。`singlecore-source.json` 固定补丁及每个修改文件的 SHA256。
- 原生 Sub2API 继续负责上游协议、账号池、调度、支付与 Vue 界面。RealYu 扩展客户身份、
  套餐/团队资金语义与迁移工具。个人钱包只有 `users.balance`，不再创建一份影子钱包。
- `realyu_funding_enabled` 默认 `false`。已映射客户在功能关闭或协议未接入时拒绝调用，
  不自动退回另一套扣费规则。此开关不是生产切换工具。
- 未改生产服务、数据库、域名、代理或节点；未调用付费模型、未创建真实支付订单。

## 2. 已实现的用户合同

| 项目 | 候选行为 |
|---|---|
| 登录 | 旧用户名/密码继续作为受控登录身份；保留原生已验证邮箱登录。旧 Argon2id 与 bcrypt 可验证，不重设密码、不赠送默认余额 |
| 注册 | 原生注册接口新增 `login_name`、`display_name`；启用 `realyu_username_registration_enabled` 后邮箱可不填；公众请求不能提权 |
| 昵称 | `login_name` 独立且稳定；`users.username` 保存昵称，API 另提供 `display_name`。改昵称不改变登录名或 Key 所有者 |
| 邮箱安全 | 无邮箱占位不显示为已验证邮箱；旧邮箱无验证证明不用于自动登录、OAuth 自动合并或找回。原生验证流程继续使用；旧密码账号绑定邮箱须重新验证密码 |
| 权限 | 原 role100 可映射原生 admin；role10 的原限制尚无等价映射，演练暂映射 user 并列阻断，保存原 role 审计，绝不默认扩权 |
| 客户 Key | 保持带 `sk-` 的完整现用 Key；禁用/删除、过期、IP、组、模型限制迁入。未带前缀的历史别名尚未兼容，不承诺该非安装器形态 |
| 新 Key | 受管用户通过原生接口创建 Key 时，同事务登记个人资金归属；不通过分组猜测团队，已有团队绑定不覆盖；登记失败则 Key 一起回滚 |
| 客户端检查 | `/api/client/key-check` 保留整数 quota JSON，禁止缓存；有效零余额/额度耗尽 Key 仍可只读检查；团队额度动态读取，不返回静态开户额度 |
| 模型限制 | 在组/合成路由重写前检查实际客户端模型，覆盖默认图片模型、重复字段、multipart、大小写/嵌套/query 变体；不能用省略 `model` 绕过 |
| 账务 | quota/USD 比例固定 500000；金额以整数/NUMERIC 核对。套餐优先、允许时个人钱包溢出、团队不扣成员钱包、成员周限额、尾周额度和跨周迟到结算 |
| 结算 | 数据库持久预占，原生用量去重、Key/上游计数、最终资金扣费同事务；超额不丢弃已完成用量；已结算消费不会被迟到失败退款 |
| 禁用与异常用量 | 团队出资人禁用/删除后拒绝新消费，已经准入的消费仍结算；cyber 拒绝有用量时保留同一预占，未知价格不能当作免费结算 |
| 工作区续聊 | 同一用户个人/团队 A/团队 B 分别授权；同工作区跨 Key 可续聊。缺绑定、数据库错误、资金关闭均拒绝受管续聊 |
| 品牌 | 通过原生站点设置启用现有 RealYu Logo、独立 favicon 和浅/深蓝色；保留上游版权、项目归属与其他站点主题 |
| 历史前端体验 | 复用模型推荐顺序/精确图片别名，保留原模型 ID 和原生定价；订阅请求失败显示错误/重试，成功空集才显示无订阅。来源和后续顺序见 [前端复用报告](FRONTEND-REUSE-20261010.md) |
| ZPay | 原生 EasyPay 明确 `compatibilityMode=zpay` 才启用 GET 查单与无 query/fragment 返回 URL；保留验签、阻止带凭据查单跨站重定向，通用 EasyPay 不变 |

`realYu` 资金支持的 HTTP 入口以候选 `middleware/realyu_funding.go` 为准。未接入的 WS、
Google/异步任务等路径明确拒绝；不能因为 Sub 上游原生支持这些协议，就声称扩展账务已经验收。

## 3. 另一台 Windows 机器重建

先 clone 交接分支并核对本次交付提交。准备 Git、Go（模块自动选 Go 1.27.2）、Node 24 与
Corepack。本次实测 Node 24.18.0、pnpm 9.15.9、Python 3.12.14、PostgreSQL 18.6。
前端属于 Sub 原生 Vue 工程，按上游 pnpm 锁文件构建，不改用 New API 的 Bun 工程。

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\sub2api\Prepare-SingleCore.ps1 `
  -Destination C:\srv\realyu-singlecore-candidate -Build -Test
```

需要显式代理时加 `-Proxy http://127.0.0.1:7890`，端口按接手机器实际配置。脚本只在不存在的
目标目录 checkout 固定上游、验补丁/文件哈希、构建和本地测试；不注册服务、不监听端口、不读取
生产配置、不改数据库、不切流。失败目录保留原状，下一次换新目录，不能直接 reset 现有部署。
候选输出包含 `sub2api.exe`、`resources/model-pricing` 和源码清单。定价 fallback 资源不能漏传。
本次已从全新目录执行上述 `-Build -Test` 成功，逐一核对 117 个修改文件；通过 265 项前端
回归、3 项 i18n、Go 定向回归与 embed 构建。新程序启动后另跑 23 项 HTTP 检查通过。
Windows 替换候选 exe 前必须等待原进程完全退出，复制后核对二进制哈希，再等待真实监听/健康
检查完成才启动功能测试；`Start-Process` 返回 PID 不等于程序已经可接收请求。

原生前端分步构建，避免 PATH 上另一个 pnpm 接管内层脚本：

```powershell
corepack pnpm@9.15.9 install --frozen-lockfile
corepack pnpm@9.15.9 exec vitest run src/i18n/__tests__/localeKeyCompleteness.spec.ts
corepack pnpm@9.15.9 exec vue-tsc -b
corepack pnpm@9.15.9 exec vite build
```

## 4. 只在独立 PostgreSQL/Redis 运行候选

本机演练目录 `C:\srv\realyu-singlecore-dev`；数据库/缓存/HTTP 分别为 loopback
29490/29479/29480，独立数据目录和随机凭据，未注册常驻服务。生产端口及生产数据库不可复用。
正式 Windows 安装的依赖来源与免费 Redis 说明见 [NATIVE-WINDOWS.md](NATIVE-WINDOWS.md)。
本次 Redis Windows MSYS2 二进制接受 `/cygdrive/c/...` 配置路径，`C:/...` 参数曾启动失败；
必须以进程身份、真实监听、认证 PING 核验，不能只相信 Start-Process 成功。

用私有配置提供原生 `DATA_DIR`、`DATABASE_*`、`REDIS_*`、`JWT_SECRET`、`TOTP_ENCRYPTION_KEY`、
`SERVER_HOST=127.0.0.1`、`SERVER_PORT`。初次 `AUTO_SETUP=true` 仅指向全新候选数据库；
验证 SQL migrations 300/301/302 均成功后停止候选进程，再执行客户导入。
不要复制当前 Sub 供应侧的内部大额账户作为客户资产。

候选品牌配置在重建源码的 `deploy/realyu/branding-settings.json`。用户名注册由原生 settings
键 `realyu_username_registration_enabled=true` 打开；`registration_enabled` 仍控制是否开放注册。
这些设置已在本地 HTTP 验收中启用，未写入生产。邮箱可选不意味着未验证邮箱可用于找回。

## 5. S0/S1 演练导入

先用现有 `snapshot_sqlite.py` 创建一致性备份。原库只读；不能直接把在线 SQLite 文件冒充最终
静态快照。导入器检查输入 SHA256、SQLite integrity、残留 WAL/journal、两次读取哈希与目标身份。
若发现活跃旧 MFA、Passkey 或未移植 OAuth，明确阻止，不能降级为纯密码；本次快照这些记录为零。

私有 target JSON 示例（密钥值不要写入 Git）：

```json
{
  "mode": "rehearsal", "host": "127.0.0.1", "port": 29490,
  "database": "realyu_singlecore_e2e", "user": "your_isolated_db_user",
  "password": "PRIVATE_VALUE", "installation_id": "realyu-singlecore-rehearsal-001",
  "group_mapping": {"default": 2}
}
```

映射 ID 必须是目标中已审核的 active standard 组；平台、模型、倍率和价格不能只由组名猜测。
活跃用户自身组是独立权限，没有 Key 的用户仍可创建 Key；只有可用旧 Key 才增加额外组权限。
每次快照重建导入用户的授权集合，撤销不保留旧授权。

```powershell
python -m venv C:\private\realyu-migration-venv
C:\private\realyu-migration-venv\Scripts\python.exe -m pip install "psycopg[binary]==3.3.6"
python .\deploy\sub2api\singlecore_migrate.py inspect --snapshot C:\private\customer-s0.db --sha256 SNAPSHOT_SHA256
C:\private\realyu-migration-venv\Scripts\python.exe .\deploy\sub2api\singlecore_migrate.py import-rehearsal `
  --snapshot C:\private\customer-s0.db --sha256 SNAPSHOT_SHA256 `
  --private-target C:\private\target.json --operation-id rehearsal-s0-unique-001 `
  --report C:\private\receipt-s0-new.json
```

单事务导入并逐账号、Key、团队、订阅实例和成员周用量核对。保留原实例的购买价格、期限与
历史订单，不从当前套餐目录“重买”。同 operation/相同输入返回原收据；相同 operation/不同输入
拒绝。S1 使用新 operation，在 staging 且没有业务写入时覆盖绝对状态，不能重复加钱。
已有客户交易、未知客户/新注册账号、未知 Key、错误安装标识、active 阶段均拒绝覆盖。
历史孤儿禁用 Key 只归档摘要与 Key 哈希，不创造新所有者或复活权限。

**此工具没有 activate 命令。** 历史支付记录的归档不是已接管支付履约；迁移收据永远
`activated=false`，同时列出所有当前门槛。真实副本内的客户资料、散列、Key 和私有配置留在
受限运行目录，Git 只收脱敏证据。

## 6. 验证命令与证据

- [本轮脱敏结果](../../lab/sub2api_e2e/singlecore-candidate-20261010.json)：区分合成测试、真实
  PostgreSQL、完整本地应用 HTTP 与未执行的公网/真实商户/真实上游验收。
- Python：`python -m unittest discover -s deploy/sub2api -p "test_singlecore_migrate.py" -v`。
- 原生 schema PG：先停止候选应用；只为合成/副本数据库设置私有
  `REALYU_MIGRATION_TEST_TARGET`、`REALYU_MIGRATION_TEST_SNAPSHOT`、`REALYU_MIGRATION_TEST_SHA256`，
  再运行 `test_singlecore_migration_pg.py`。测试创建临时触发器注入故障并验证回滚，禁止对生产运行。
- 资金 PG：候选源码 `go test -p 2 -tags realyu_funding_pg ./internal/repository -run '^TestRealYu'`，
  只接受显式 `REALYU_FUNDING_TEST_DSN` 和指定 `realyu_funding_e2e` 数据库，每例独立 schema。
- 身份 PG：`go test -p 2 -tags unit ./internal/service -run '^TestRealyu'`，显式
  `REALYU_AUTH_TEST_DSN` 仅指向 `realyu_auth_e2e`。未提供 DSN 的 skip 不计为 PG 验收通过。
- 本地应用：`singlecore_http_smoke.py --private-target PRIVATE_JSON --report NEW_REPORT`，
  只接受 127.0.0.1:29480；创建合成用户并检查旧个人/团队 Key，不调用模型、不触发真实支付。
  运行后新注册客户属于业务变化，不能再对该数据库执行覆盖快照导入。

保留首个失败后再记录重跑：依赖证书/直连下载、Redis 路径、SQL 测试夹具、构建 include 边界、
HTTP 测试对 PNG 的 JSON 解码均有独立记录。最终通过不能覆盖首错。
浏览器首次因 Codex IAB 页面/标签崩溃与初始化超时失败，后续重新打开候选成功，已实测用户名
登录、修改昵称、退出并以原用户名再次登录，保留合成账号截图。此结果不代替完整后台、全部
浏览器/设备、支付和真实上游验收；组件测试与 HTTP HTML/资产检查仍单独记录。
最终重建版本另通过原生订阅页的真实浏览器故障/重试/恢复检查，详见前端复用报告。

## 7. 全迁前仍需完成

1. WS 每轮独立授权、持久预占和结算；Google/异步任务等未接入入口必须保持拒绝，验收后才开放。
2. 崩溃后未决预占与未完成计费任务的可恢复队列/对账。当前保留预占防止误退款，不能称已恢复交付。
3. 旧 `resp_ry1_`、Redis owner/account affinity、图片/文件保留引用的受控迁移；不能靠放宽所有权判定兼容。
4. 旧支付未决订单及回调唯一消费方、确认前落盘 inbox、幂等履约/退款；ZPay 真实商户全链路仍需验证。
5. 团队/成员管理、资金来源/周限额/历史实例展示的最小原生 UI 与 API；role10 的范围管理权限映射。
6. 真实业务模型/分组/计价配置、上游供应状态导入、全部安装器下载/diagnostics 路由、桌面 Codex
   （新会话/恢复/PDF/联网/图片/WS）及实际浏览器验收。
7. 跨库最终写栅栏、在途请求排空、旧投影/补额 worker 停止、最终 S1/逐主体对账、唯一写权威
   切换和故障恢复演练；上线后已出现 PG 新交易时，不允许直接路由回旧 SQLite。

旧 `host_cutover.py` 只适用于现有桥接系统的同库升级，不能拿来完成这里的跨库全迁。
这些是上线前的明确门槛；已完成的候选代码可独立继续开发和测试，无需先改动线上。
