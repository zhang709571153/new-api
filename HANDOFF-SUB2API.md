# RealYu / Sub2API 候选交接入口

## 本轮单核心交付（2026-10-10，文档冻结时未切生产）

用户已授权本机发布；授权不等于完成。新候选固定 Sub2API v0.2.15 加 RealYu 移植，
目标是单核心承接客户与供给，保留原域名、旧 Key、用户名/昵称及团队权益，首版复用原生前端。
已有生产桥接系统和本轮候选必须分别验收；下面旧版公网结果不能用于证明新单核心已上线。

按以下顺序接手，不能直接运行旧同 SQLite 的 `host_cutover.py` 做全迁：

1. [单核心重建](deploy/sub2api/SINGLECORE-CANDIDATE.md)：使用冻结上游、补丁和逐文件哈希；
   该文档早期未完成项的本轮进展以本入口与 [DEVLOG.md](DEVLOG.md)最新条目为准。
2. [供给与价格迁移](deploy/sub2api/SINGLECORE-SUPPLY-PRICING.md)：客户资金来自 RealYu 源账本，
   原 Sub2API 影子用户与内部授信不是客户余额；供给账号、组和代理 ID 保留，实际价卡单独对账。
3. [跨数据库切换](deploy/sub2api/SINGLECORE-HOST-CUTOVER.md)：S0 演练、最终 S1、冻结旧写者、
   最终凭据与选择性 affinity、开门和向前恢复。默认 120 秒总预算分为 90 秒尝试和 30 秒恢复预留；
   目标已有业务写或已进入 OPENING 后，不能自动回到旧 SQLite。
4. [原生 SCM 服务入口](deploy/sub2api/service_entry_singlecore.py)：只替换 RealYuApi 的原生分支，
   透明 bridge、入口地址、PG/Redis 与监控契约继续保留。原生环境/数据库权威不合要求时拒绝启动。
5. [SG 独立服务与滚动切换](deploy/sub2api/SG-HY2-SERVICE-HANDOFF.md)：独立 headless Mihomo、
   GOST、逐连接器切换和已有监控跟随；不依赖登录后的 Clash Verge，不新增常驻探针。
6. [Windows Server 迁机交接](deploy/sub2api/SINGLECORE-WINDOWS-HANDOFF.md)：源机 ACTIVE 后
   使用原生 PG 备份恢复，保留身份、团队资金、Redis 有效续聊状态和单一 OAuth 刷新者；
   [launcher/bridge 运行包](deploy/sub2api/host-runtime/README.md)给出全部 Python 源码、安装映射和哈希。
7. [生产公网验收](lab/sub2api_e2e/SINGLECORE-PUBLIC-ACCEPTANCE.md)：固定 ACTIVE/版本校验、
   PDF/搜索/图片/WS/模型测试及独立账务、浏览器和路径验收边界；默认离线，保留首错。

本轮候选已取得的证据：

- owner/member 团队管理、限额、邀请、加入/离开、用量与历史已经接入原生 API/UI；
  60 项运行 HTTP 检查中 57 PASS、3 项为原生管理员首次使用确认 gate。保留该原生流程，
  未伪造确认；随后用独立 headless Edge 完成真实页面登录、团队筛选和权限渲染，9/9通过、JS错误0。
- 真实 SG 上游文本、PDF、搜索、图片工具完整通过；含 SCM 复核的9条独立资金结算及1条零额拒绝退款样本归属有效。
  同一 CLI URL/home 跨旧、新隔离 origin 的 Codex resume 精确通过，未运行安装器；
  这不是生产域名、所有旧会话或已安装桌面的验收承诺。
- 独立 headless SG 首次两条 120 秒 SSE 完整通过，25 次采样保持四连接及收发增长。
  [脱敏网络记录](deploy/sub2api/SG-HY2-VALIDATION-20261010.json)同时保留旧桌面 7897 的首次失败；
  尚未证明无人登录启动、崩溃恢复或长期稳定。
- WS `ctx_pool`/`http_bridge` 新归属 ID 的两处漏接已修复；最终构建真实两轮续聊通过，
  两模式10个回归及两轮资金结算通过，保留首次1008失败。源码提交 `5c506e193ee6668cea0bae751ec3f9cccda138fd`。

切换前及后续明确保留的 P1：旧订单回调只保证验签后的持久 inbox，`pending_review` 需人工对账，
不等于自动履约；异常终止 WS 的耐久结算需加固；旧图片/搜索工具附加费未由 Token 价卡覆盖，
4950 个合成价向量中 14 个各差 1 quota；旧 role10 只保留受限团队管理，其全局用户/个人资金
管理未等价恢复。真实商户支付/退款尚待验收；浏览器独立结果已记录。最终 S1 保留 SG 代理、长导入取消并
确认子进程/PG 会话退出、目标 Redis 拒绝不明状态三个部署 review 项已在冻结代码核对，65 项
定向测试通过（6项真实PG）；该结果不代替真实 SCM 切换或恢复验收。

私有证据仅取匿名结论写入 Git：`team-final-handoff.md`、`team-live-http-acceptance.json`、
`singlecore-real-{text,pdf,search,image}-result.json`、`codex-resume-cutover-result.json`、
`real-upstream-ledger-result.json`。不上传原始请求、客户记录、凭据、数据库、完整 Redis 或私有快照。

### 本轮生产发布结果（由主发布流程补录）

19:42 CST：独立客户 PG/S0 与 SG SCM 已准备；生产核心和 Tunnel 路径尚未切换。第二次
Windows UAC 取消，等待给出的合并管理员命令执行。必须取得 ACTIVE/S1 对账以及公网功能、
账务和 SG 真实路径验收，才能更新为已发布。后续同步 [LIVE-DEPLOYMENT.md](LIVE-DEPLOYMENT.md)、
[DEVLOG.md](DEVLOG.md)及 [UPDATELOG.md](UPDATELOG.md)，保留首错和向前恢复边界。

## 既有桥接生产基线与历史交接

2026-10-10 当前主机既有桥接版本为 `realyu-sub2api-v3.10.0.1-20261010`，该版本已获得公网确认。13 类功能、8 个文本模型、8 项 HTTP/WS 身份隔离检查、Codex 0.162.0 对话/恢复及 20 项账务核对通过；两套上游续期凭据已迁入 Sub2API，并实际刷新成功。Sub2API、PostgreSQL、免费 Redis 和 worker 已作为 Windows 服务运行。首次切换的回退与修复证据保留，完整状态见 [LIVE-DEPLOYMENT.md](LIVE-DEPLOYMENT.md) 和 [公网验收报告](lab/sub2api_e2e/release-public-20261010.json)。用户选择的新 token 计费适用于 native Images，既有 Responses 图片工具口径不变。以下是该桥接系统的历史交接信息；另一台机器仍按独立 Windows 部署和验收流程接手。

**首次交接时的冻结说明（历史记录，后续本机授权和状态以上方 LIVE 文档为准）：** 当时授权覆盖隔离开发、测试、候选打包及迁移准备。
不要替换线上程序、路由、数据库或凭据，不要重启线上服务，不要切换域名、
隧道或用户流量，不要提前下线旧机器。生产切换须等待用户后续明确命令。

接手机器为 Windows Server。先阅读：

- [单核心开发候选与重建教程](deploy/sub2api/SINGLECORE-CANDIDATE.md)：已完成用户名/昵称兼容、
  资金与隔离迁移的第一阶段开发；固定上游提交加完整补丁可从本分支重建。默认关闭、未切流；
  这份候选不是下方当前生产桥接程序，剩余全迁门槛和真实验收边界见该文档。
- [历史前端需求复用审查](deploy/sub2api/FRONTEND-REUSE-20261010.md)：六个会话的真实需求、
  可追溯源码、已移植两项和依赖资金/团队接口的后续顺序。
- [开发日志](DEVLOG.md)记录决策、验证、首个失败和未完成项；
  [更新日志](UPDATELOG.md)区分源码、候选和实际发布状态。
- [原版平台对照与单核心迁移研究](deploy/sub2api/PLATFORM-COMPARISON-20261010.md)
  区分 New API/Sub2API 原生能力和 RealYu 自研业务；建议以 Sub2API 单核心为目标，
  单核心现已进入隔离开发，但尚未生产全迁。下方部署教程仍对应当前桥接系统。
- [单核心迁移的三组独立审查](deploy/sub2api/MIGRATION-REVIEW-20261010.md)
  固定首版仅最小品牌适配、客户功能优先的范围，并列出身份/权益/Key续聊/支付/跨库切换
  五组阻断与验收顺序。旧 host_cutover.py 仅处理同客户SQLite的桥接升级，不能直接
  执行客户全迁；目标账本产生新交易后不可直接回切旧SQLite。
- [管理入口部署教程](deploy/sub2api/ADMIN-PORTAL.md)说明渠道账号分布、真实用量
  归属、独立 HTTPS 原生后台、现有系统升级与验收命令。
- [10 月 10 日公网中断记录](deploy/sub2api/NETWORK-INCIDENT-20261010.md)说明
  共享代理出口、已确认的恢复范围和迁移时必须单独验证的 Tunnel 网络路径。

当前线上复核与后续修复顺序见 [2026-10-10 P0/P1 清单](lab/maintenance/sub2api-p0-p1-20261010.md)。数据面切换完成不代表异地运营入口、备份告警与完整客户端验收均已完成。

使用交接分支 `codex/sub2api-handoff-20261009`，不要从默认分支部署：

```powershell
git clone --single-branch --branch codex/sub2api-handoff-20261009 https://github.com/zhang709571153/new-api.git realyu-sub2api
Set-Location realyu-sub2api
git rev-parse HEAD
```

将提交号与本次交付消息核对，再按以下文档准备隔离环境。

1. [完整迁移交接](lab/maintenance/sub2api-migration.md)：架构、数据范围、备份、
   恢复、隔离验证及未来获准切流后的回退步骤。
2. [Windows 原生部署](deploy/sub2api/NATIVE-WINDOWS.md)：无需付费 Redis，
   PostgreSQL、Redis、Sub2API、RealYu 及身份准备 worker 的候选配置。
3. [端到端验收](lab/sub2api_e2e/README.md)及
   [完整冒烟清单](lab/sub2api_e2e/portable/checklist.json)：每项分别记录
   PASS、FAIL、BLOCKED、NOT_RUN；源码支持、健康检查和模拟上游不替代真实功能验收。

历史桥接候选基于已发布的 `realyu-provider-v3.9.2.22-20261008` 冻结源码，
采用 Sub2API `v0.2.15`。原有 New API / QuantumNous 版权、许可和项目标识保留。
RealYu 保留客户、团队、权限、套餐和账务；Sub2API 管理上游账号与池。

Git 交接只包含审查后的源码、构建输入、公开下载资源、合成测试材料及脱敏报告。
数据库、登录凭据、生产 Key、原始日志、私有测试身份、隧道证书与历史客户证据
不进入 Git。迁移这些私有数据必须使用另外的受保护传输和校验过程。
代码分支推送不代表已部署，也不代表允许切流。

历史桥接构建使用仓库锁文件和同一提交。先构建 `web/`，再构建根目录 RealYu 和
`cmd/sub2api-prewarm`；精确步骤见部署文档。任何公开验收结论都须对应报告中的
二进制 SHA256，不能把旧二进制通过的结果直接标成后续改版已通过。

本机隔离环境已验证 PDF 内嵌读取、经可信代理出口的 PDF 链接读取、联网搜索、
函数工具往返、原生压缩续接、WebSocket 续接，以及图片生成和两种图片编辑。
团队账务、独立上游身份、后台身份准备及内部额度维护分别有验收记录。

目前尚未完成全部上线验收：Files API、旧版 `web_search_preview` 与旧压缩接口
存在兼容边界；图片接口未遵守请求尺寸。Codex CLI 的对话/恢复通过，但文件和
终端操作被执行策略阻断。官方最新 CLI 0.162.0 补测还记录了一次上游 HTTP/2
断流，失败请求未计费并返还预扣；独立无工具对话及恢复通过，不能覆盖原失败。
浏览器运行时崩溃导致页面验收阻断。目标 Windows
Server 的服务安装、重启恢复、入口网络、备份恢复及完整客户端验收仍须在目标
机完成。不要把这些限制或未执行项改写成通过，也不要据此承诺原生 Codex 完全等价。

原仓库的 workflow 内容与署名原样保存在 `deploy/sub2api/workflow-templates/`，
本交接快照不启用自动 CI/发布工作流；这也避免扩大当前 Git 登录的 workflow
权限。接手期间按文档手工构建验证，不要激活发布模板或执行历史 `lab/` 发布脚本。
