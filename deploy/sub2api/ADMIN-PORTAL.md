# 渠道、用量归属与异地管理入口

本次桥接复用 Sub2API 0.2.15 的管理 API 与原生后台。RealYu 负责客户、
权限、套餐、账本和门户；Sub2API 负责账号、分组和调度。没有新增账号编辑器、
自研调度或 SSO。实际发布状态以根目录 [UPDATELOG](../../UPDATELOG.md) 为准。

## 管理员使用

1. 在任意电脑打开 RealYu，使用有渠道读取权限的管理员登录，进入「渠道」。
2. 每个配置的供应分组显示账号、启用状态、并发、5 小时/7 天使用比例、
   今日请求和 Token，以及当前展示池内的请求占比。每分钟自动更新，可手动刷新。
3. 点击「打开 Sub2API 管理后台」，新标签进入独立 HTTPS 域名的原生后台。
   使用 Sub2API 自己的管理员账号登录。RealYu 登录不会自动赋予 Sub2API 权限。
4. 全部用量列表及移动卡片显示精确关联的账号，详情显示供应分组、模型和
   Sub2API 用量 ID。开启隐私模式后，列表中的账号名称同样隐藏。

今日按 Sub2API 服务器时区计算，当前主机是 Asia/Shanghai。原生账号今日统计
包含该账号在所有分组的调用，不是 RealYu 客户账单，也不是本次切换后的净增量。
未知用量显示横线；记录缺失、查询失败和旧记录不会被猜测分配给账号。
5 小时/7 天比例来自原生缓存元数据，本页面不主动刷新凭据或探测模型。

## HTTPS 入口部署（Windows Server）

当前目标域名是 `supply.realyu.fun`，入口指向 `/admin/accounts`。
Sub2API HTTP 服务仍绑定 `127.0.0.1:28090`。数据库、Redis、metrics、Kuma
均不开放公网端口。创建独立管理主机名，让浏览器 Cookie 与 RealYu 分开。

1. 保留现有隧道名称、凭据、协议、代理出口及 `api.realyu.fun → 18301` 路由。
   将 [管理入口规则](admin-ingress.example.yml) 合并到现有配置最后的兜底规则前，
   把示例域名替换为实际域名。只放行原生管理 API、静态资源和管理页面，其他路径
   返回 404，避免从新域名直接调用原生推理并绕过 RealYu 账本。
2. 使用已有 Cloudflare 账号证书创建新主机名的 DNS 路由，不使用覆盖选项：

   ```powershell
   cloudflared tunnel route dns <existing-tunnel-id> supply.realyu.fun
   cloudflared tunnel --config '<candidate-config.yml>' ingress validate
   cloudflared tunnel --config '<candidate-config.yml>' ingress rule 'https://supply.realyu.fun/admin/accounts'
   cloudflared tunnel --config '<candidate-config.yml>' ingress rule 'https://supply.realyu.fun/v1/responses'
   ```

3. 在正常管理员提权后备份并替换隧道配置。双连接器依次重启，等待各自的
   loopback `/ready` 为 200，再处理下一台连接器。同步检查原 API 公网可用。
   本机服务为 `RealYuTunnelPrimary` 和 `RealYuTunnelReplica`，ready 端口
   分别为 20242、18432；接手机器须核对实际名称，不能照搬本机路径。
4. 在 RealYu 的服务环境中设：

   ```text
   REALYU_SUB2API_ADMIN_URL=https://supply.realyu.fun/admin/accounts
   ```

   `bindings.json` 的供应 `base_url` 继续使用 loopback。不要修改 namespace、
   identity_secret、内部用户映射、余额、上游 OAuth 凭据或刷新所有权。
5. 保留原生注册关闭、强管理员密码和原生鉴权/限流。管理员凭据通过私有受保护
   渠道交接，不能放在 URL、前端代码、教程或 Git。可在原生后台管理独立运维身份。

本机 cloudflared DNS 管理命令曾因直接网络路径的 TLS 证书校验失败而退出；
其该版本管理客户端不使用系统代理变量。使用显式可信代理调用同一官方管理 API
后创建成功，始终保持 TLS 校验；没有安装根证书或使用 `--insecure`。
接手机器优先运行标准 CLI，网络失败应先诊断，不能关闭证书验证。

## 升级既有 RealYu 程序

按 [Windows 构建教程](NATIVE-WINDOWS.md) 同一提交先构建 `web/`，再构建 Go。
本功能没有更换 Sub2API、PostgreSQL、Redis、worker，也没有数据结构迁移。

`Install-HostRelease.ps1` 与 `host_cutover.py` 支持私有计划中的
`operation_kind: "portal-upgrade"`、`dependency_mode: "reuse"`，另设
`sub2api_admin_url`。必须固定当前 bindings、构建产物及所有执行脚本的 SHA256，
并先用独立 SQLite 快照启动相同二进制验证。原配置中的 `sub2api_base_url`
继续为 loopback，不得用管理域名覆盖供应地址。

该模式校验当前服务确为 Sub2API，保留原有准备队列/内部额度机制；不重新执行
首次迁移的供应导入、路由激活或刷新权交接。现有流程继续负责停止接收新请求、
排空、冷备份、核对客户账本、启动新程序及有限的公开验收窗口。
失败时回退程序和配置，保留当前客户数据库；禁止恢复旧库覆盖新增交易。

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned `
  -File .\deploy\sub2api\Install-HostRelease.ps1 `
  -Plan '<private-operation>\host-plan.json' `
  -ExpectedPlanSha256 '<reviewed-plan-sha256>' -ValidateOnly
```

检查通过后，在正常 Windows 管理员终端中去掉 `-ValidateOnly` 执行。
每次操作使用新目录和新计划，不能重跑已完成的旧切换计划。验证结果文件必须
绑定该操作 ID、版本和二进制 SHA256，依据实际公网结果填写；不能预填 PASS。

## 验证与边界

```powershell
python lab/sub2api_e2e/admin_portal.py `
  --base-url https://api.realyu.fun `
  --admin-config '<private>\credentials.json' `
  --expected-version '<deployed-version>' `
  --expected-console https://supply.realyu.fun/admin/accounts `
  --report '<new-private-report>.json'

python lab/sub2api_e2e/native_admin.py `
  --base-url https://supply.realyu.fun `
  --runtime-private '<private-sub2api>\runtime-private.json' `
  --report '<new-native-report>.json'
```

第一个脚本创建一个零余额 RealYu 测试登录，以验证普通用户无法访问渠道概览
和全部日志，结束时禁用并保留审计记录；不调用模型或修改客户余额。
第二个脚本验证真实原生登录、管理账号读取、静态资源及推理路径隔离，结束时
注销测试 refresh token。需要代理时使用其 `--proxy` 参数，报告保留该路径信息。
任一失败均保留首次报告；修正后使用新报告文件，不能覆盖失败。

还需浏览器实测：管理员渠道、长账号名/横向滚动、手机视图、隐私开关、原生
登录及账号页。HTTP E2E 和组件测试不能代替实际浏览器。另机测试应明确记录
机器和网络路径；本机经公网 HTTPS 成功不等于所有外部网络已经验证。

读取接口通过既有 `AdminAuth + ChannelRead` 和日志权限保护。关联仅接受
`client:<upstream_request_id>` 的唯一精确匹配，并核对配置的分组。
日志增强最长 5 秒、并发最多 4、每页最多 100 条，正缓存 5 分钟、负缓存 8 秒，
缓存最多 4096 项。失败不阻止客户历史日志读取，也不进入推理/结算链路。
单池概览最多 2000 个账号，达到上限或中途失败会明确显示不完整状态。

鉴权审查参考 [OWASP ASVS](https://owasp.org/projects/asvs)、
[Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
与 [Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)：
服务端权限、HTTPS、凭据不出现在日志/URL、独立原生会话及注销。本次未改写原生
认证，也不宣称通过完整 ASVS 认证。接口依据固定版本的
[账号处理器](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/handler/admin/account_handler.go)
及 [用量处理器](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/handler/admin/usage_handler.go)。
