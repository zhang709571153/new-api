# 资金提示、账户页面与调用记录交接

2026-10-15，北京时间。**状态：已验证候选，未部署生产。**

本分支汇总用户本轮要求，接续资金错误原因修复。交付为固定上游源码加完整补丁；生产
配置、进程、隧道及数据库均未修改。邮件按用户选择使用隔离收件箱，不代表 QQ 已收信。

## 本轮行为

- 资金错误细分个人余额/套餐、团队总额度、团队成员额度、套餐不可用和 API Key 限额，
  API、错误记录与前台筛选显示对应原因和处理办法。保留原定价与扣费条件。
- 管理员可直接修正团队本周已用额度，正常新增消耗和运行中请求不会再使保存失败。
  实际值至少保留本周期在途预扣，随后正常结算/退款；配置和周周期变化仍需要刷新。
  例如目标 0、当前预扣 0.01 元，先保留 0.01 元，完成后按实际用量结算。
- 使用记录与全部调用记录展示 API Key 名称，包括桌面、手机、详情；缺失显示“—”。
- 全部调用记录增加**上游账号、渠道的名称与 ID**。只向平台管理员返回；个人及团队
  查询不返回上游信息。ID 来自请求记录，名称是当前标签；标签删除后仍显示记录中的 ID。
- 包月订阅去掉“按量余额支付”，后台拒绝新的钱包购买订单。外部支付、按量充值和调用
  已有的超额补扣策略保留；已接受的历史订单仍可按旧快照完成，避免中断交易。
- 密码最小长度改为 6 个字符，无数字/大小写/符号组合要求；注册、改密及提示一致。
  原哈希、当前密码核验、改密后的旧令牌失效和已有 MFA 登录校验保留。
- 个人资料去掉 2FA、Passkey 卡片，绑定区域只保留邮箱。“API 密钥”对普通用户恢复可见，
  紧接“工作台”下方，管理员个人侧栏也保持同样顺序。

旧版 Realyu 管理员日志有渠道名称/ID，部分请求有重试链和多 Key 索引。原生 usage_logs
本来已记录 account_id/channel_id，精简接口此前未返回。已迁移的旧消费归档没有保留
这两项路由字段，所以旧记录显示“未记录”；不使用当前路由反推，也不混用新旧渠道 ID。
完整诊断入口保留，本次不补造历史重试链。

## Git 与构建身份

| 项目 | 值 |
| --- | --- |
| 仓库 | https://github.com/zhang709571153/new-api |
| 分支 | `codex/funding-error-reasons-20261015` |
| UX9 开发基线 | `998f501f5d598b9a9362600f1100e0cdc88b0dfa` |
| 资金原因提交 | `0b0fbc0b4397bf0344ef38c71972e7843175a154` |
| 团队用量、Key 名称、支付提交 | `0de507e2004e84264ff8061a29cff1bc6511bb72` |
| 账号页面、密码、邮件验收、上游来源提交 | `3261f676e9e9ece3edf976cf7cc1895bbee96296` |
| 候选版本 | `realyu-singlecore-ux9-account-usage-20261015` |
| 完整补丁与源码 tree | 见 [source.json](source.json) |
| 本机候选 EXE 哈希、验证结果 | 见 [validation.json](validation.json) |

原生提交属于本机 Sub2API 工作树，**不属于公开交接仓库历史**。本仓库仍采用固定上游
`f2669c8cf62555cd92389b3f55920e9e6e7c6ff2` + [candidate.patch](candidate.patch) 重建，
逐文件哈希及 Git tree 均校验。默认 `singlecore-source.json` 仍是 UX9，必须显式选择本目录：

```powershell
git clone --single-branch --branch codex/funding-error-reasons-20261015 https://github.com/zhang709571153/new-api.git realyu-account-usage
Set-Location realyu-account-usage
.\deploy\sub2api\Prepare-SingleCore.ps1 `
  -Destination C:\srv\realyu-account-usage-candidate `
  -SourceManifest .\deploy\sub2api\funding-error-reasons-20261015\source.json `
  -Build
```

Destination 必须是新目录。准备 Go 1.25.12、Node.js 与 Corepack；输出为新目录下
`candidate-output/sub2api.exe`。本机验收程序在
`C:\srv\realyu-error-reasons-20261015\results\realyu-singlecore-account-usage.exe`。
本机 --version 还注入了源码提交和构建时间，换机重建字节可能不同，应重新记录哈希。
EXE、私有配置、客户数据和原始日志没有上传 Git。

## 验证与邮件验收

当前源码：42 项隔离 PostgreSQL 根测试通过，覆盖用量修正期间的预扣、结算、退款、
幂等及审计；Key 和上游名称、平台权限、个人/团队隔离、历史缺失、改名/删除；新钱包
订单拒绝、历史订单完成以及资金不变。认证套件 10 项根测试通过，包括实际 PostgreSQL。

邮件 E2E 使用真实 JWT HTTP 接口、实际 SMTP TCP 连接、原生通知模板和 MIME 编码：
发送验证码 → 隔离收件端收到邮件 → 从正文提取验证码 → 验证并绑定入库 → 邮箱登录。
另外确认未登录 401、重复发送冷却 429、错码拒绝、验证码重用拒绝，密码与余额不变。
测试只使用合成账号，临时 schema 自动清理。未发送到用户 QQ；公网投递还需 SMTP 配置
及收件确认，不能把隔离 E2E 当作生产送达证明。

前端账户/导航现有回归 70 项通过，最后邮箱文案修改后重跑相关 41 项通过；上游展示及
翻译 51 项通过。上一源码 0de507e 的管理员/支付回归 116 项通过，其中最新使用记录
用上述新回归覆盖。Vue 类型检查、Vite、内嵌前端的 Go 构建通过。资金 HTTP/SSE/WS 和
错误视图验证沿用 0b0fbc0 的已通过记录，后续未修改这些协议路径；不声称本次重新执行。

保留首错与修复：原钱包购买 HTTP 测试曾断言旧成功行为，现改成能力关闭、400、资金和
订单不变；邮件 fixture 首轮缺少头像表，补上上游迁移后又发现 Ent schema 无 settings
时间戳默认值，补齐隔离 schema 后通过。没有因此修改生产迁移。详细日志摘要哈希见 manifest。

可在重建后的源码目录复核：

```powershell
Set-Location backend
# 两个 DSN 必须使用隔离数据库，不能指向生产。
# REALYU_FUNDING_TEST_DSN: 127.0.0.1:29490/realyu_funding_e2e
# REALYU_AUTH_TEST_DSN: 127.0.0.1:29490/realyu_auth_e2e
go test -p 2 -tags realyu_funding_pg ./internal/repository -run '^TestRealYu(Funding|AdminSubscription|History|Usage|Purchase)PG' -count=1
go test -p 2 -tags unit ./internal/service -run '^Test(Realyu|ChangePassword_OnlyDeclaresPasswordHash)' -count=1
Set-Location ..\frontend
node node_modules/vitest/vitest.mjs run src/views/user/__tests__/RealYuUsageView.spec.ts src/views/user/__tests__/ProfileView.spec.ts src/components/user/profile/__tests__/ProfileIdentityBindingsSection.spec.ts src/components/user/profile/__tests__/ProfileInfoCard.spec.ts src/components/user/profile/__tests__/ProfilePasswordForm.spec.ts src/views/auth/__tests__/RegisterView.spec.ts src/i18n/__tests__/localeKeyCompleteness.spec.ts --reporter=dot
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit
```

6 位密码为用户明确要求，低于 [ASVS 5.0.0 V6.2.1](https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x15-V6-Authentication.md)
的长度要求，不宣称该项符合。详细实现说明在重建源码
`deploy/realyu/ADMIN-USAGE-PURCHASE-20261015.md`。

## 后续部署

本轮只完成 Git 交付。接手先读实际运行 manifest、程序哈希、监听端口和当前权威数据库，
不能把本地 Git HEAD 或旧运维说明当成线上状态。若部署，按现有
[原生更新器](../SINGLECORE-NATIVE-UPDATE.md)生成新计划并等待请求、预占与批量作业排空；
排空失败保持原服务。未新增业务 schema 迁移，不恢复旧数据库覆盖新交易。

上线后还需使用测试团队验收额度调整及后续结算、普通用户与管理员页面、实际客户端资金
提示；真实邮件投递需补 SMTP 配置后另验收。网络故障取证独立保留，本候选不证明或修复
10 月 15 日公网故障根因。
