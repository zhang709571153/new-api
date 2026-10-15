# 资金错误提示修复交接

整理时间：2026-10-15，北京时间。**状态：候选已验证，未上线生产。**

本轮补齐个人余额、个人套餐、团队总额度、团队成员额度、个人/团队套餐不可用和 Key 限额的错误展示。
API、错误记录和前台筛选保持原因一致，并显示处理办法。没有修改定价、扣费条件、数据库结构或公网隧道配置。

## Git 与候选身份

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/zhang709571153/new-api` |
| 交接分支 | `codex/funding-error-reasons-20261015` |
| 交接分支基线 | `4687ac7a794ce9eaa0e29a4a484213963ef36f41` |
| 本次原生源码提交 | `0b0fbc0b4397bf0344ef38c71972e7843175a154` |
| 依托的 UX9 源码 | `998f501f5d598b9a9362600f1100e0cdc88b0dfa` |
| 候选版本 | `realyu-singlecore-ux9-funding-reasons-20261015` |
| 完整补丁 SHA256 | `7b5968de7bf592e733017532a64eb0d21907a3a30832c29a49bc2f1f6b240d22` |
| 本机候选 EXE SHA256 | `8afe630d37d8de51099223ec711dd76d547255ac45ff525d95f1a878cf8c03a2` |

本仓库沿用“固定上游 + 完整补丁”的交付方式。原生源码提交号不属于交接仓库的 Git 历史；
接手按本目录 [source.json](source.json) 重建。[candidate.patch](candidate.patch) 已复原为相同 Git tree，
覆盖 455 个变更文件；相对 UX9 的本次修改仅 33 个文件。现有默认 `singlecore-source.json` 仍指向 UX9，
重建本候选必须显式指定下面的 `-SourceManifest`。

## 取得与构建

```powershell
git clone --single-branch --branch codex/funding-error-reasons-20261015 https://github.com/zhang709571153/new-api.git realyu-funding-errors
Set-Location realyu-funding-errors
git rev-parse HEAD

# Destination 必须是不存在的新目录；先准备 Go 1.25.12、Node.js 和 Corepack。
.\deploy\sub2api\Prepare-SingleCore.ps1 `
  -Destination C:\srv\realyu-funding-candidate `
  -SourceManifest .\deploy\sub2api\funding-error-reasons-20261015\source.json `
  -Build
```

重建器校验固定上游、完整补丁和逐文件哈希。程序输出在新目录的 `candidate-output/sub2api.exe`；
换机重建应重新记录程序哈希。本机已验证程序位于
`C:\srv\realyu-error-reasons-20261015\results\realyu-singlecore-funding-reasons.exe`，约 127 MB，未作为 Git 文件上传。
它的 `--version` 显示上述候选版本，内嵌 commit/built 字段为 unknown；源码身份通过 manifest 和 EXE 哈希对应。

## 已验证与复核

详见 [validation.json](validation.json)。后端分类/视图/筛选、HTTP/SSE 原因码、原生 WebSocket 首轮及后续轮次、
Key 耗尽的原协议兼容测试均通过。真实隔离 PostgreSQL 的 14 项根测试通过，包含并发、结算、退款、团队隔离及
拒绝后的余额/额度不变。前端 6 项文案/组件测试、Vue 类型检查、Vite 和 Go 构建通过。

在重建后的源码目录运行：

```powershell
Set-Location backend
go test -p 2 -tags unit ./internal/handler ./internal/server/middleware ./internal/service -run 'Test(RealYuFundingReason|RealYuFundingErrorView|RealYuFundingQuotaAuthClassification|RealYuWebSocketTwoTurnsAndFreshPermissions|APIKeyAuthOpenAIQuotaErrorFormat|APIKeyAuthQuotaErrorKeepsLegacyFormatOutsideResponses|BillingErrorDetails|MapUserErrorCategory|CategoryToFilter|ToUserErrorRequest|LogOpsStreamError|OpsErrorLoggerMiddleware_RequestScoped)' -count=1
# 仅在已配置隔离测试库时运行；不能使用生产 DSN。
# REALYU_FUNDING_TEST_DSN 必须指向 loopback 的 realyu_funding_e2e。
go test -p 2 -tags realyu_funding_pg ./internal/repository -run '^TestRealYuFundingPG' -count=1
Set-Location ..\frontend
node node_modules/vitest/vitest.mjs run src/i18n/__tests__/localeKeyCompleteness.spec.ts src/utils/__tests__/fundingErrorPresentation.spec.ts --reporter=dot
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit
```

团队总额度与成员额度分开：提高成员额度不会补充团队资金，个人充值也不会增加团队额度。
旧记录缺少细分原因码时显示“原因未细分”，不能按现有 Key 归属倒推历史原因。
Key 在认证入口已耗尽时保留既有 HTTP 状态和协议码，记录进入 Key 分类。资金拒绝按业务限制记录。

## 剩余工作

1. 读取接手机器实际运行 manifest/程序哈希。UX9 是本候选开发基线，不能替代当时的现场核查。
2. 如要发布，使用现有[原生更新器](../SINGLECORE-NATIVE-UPDATE.md)生成本机新计划；先等待请求、资金预占和批量作业排空。排空失败则撤销维护，保留现有连接。
3. 本次无 schema 变更，继续使用当前权威 PG。保留旧程序供同库回退，不用旧数据库覆盖新增交易，也不重放历史发布计划。
4. 发布后使用隔离测试身份验收个人、团队、成员和 Key 拒绝：核对客户端原因、用户/管理员错误列表、筛选、请求 ID，以及拒绝前后的余额/额度。
5. 对实际 Codex 客户端补一次提示验收。本轮协议与组件测试没有替代生产前台或原生桌面客户端验收。

本交接不包含生产凭据、客户数据或原始故障日志。10 月 15 日公网故障取证独立保留；
本修复提升以后资金拒绝的可解释性，不补造历史故障原因。
