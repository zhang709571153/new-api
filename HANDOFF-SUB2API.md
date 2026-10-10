# RealYu / Sub2API 候选交接入口

2026-10-10 当前主机已发布 `realyu-sub2api-v3.10.0.1-20261010`，公网确认新版本。13 类功能、8 个文本模型、8 项 HTTP/WS 身份隔离检查、Codex 0.162.0 对话/恢复及 20 项账务核对通过；两套上游续期凭据已迁入 Sub2API，并实际刷新成功。Sub2API、PostgreSQL、免费 Redis 和 worker 已作为 Windows 服务运行。首次切换的回退与修复证据保留，完整状态见 [LIVE-DEPLOYMENT.md](LIVE-DEPLOYMENT.md) 和 [公网验收报告](lab/sub2api_e2e/release-public-20261010.json)。用户选择的新 token 计费适用于 native Images，既有 Responses 图片工具口径不变。另一台机器仍按独立 Windows 部署和验收流程接手。

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

本候选基于已发布的 `realyu-provider-v3.9.2.22-20261008` 冻结源码，
采用 Sub2API `v0.2.15`。原有 New API / QuantumNous 版权、许可和项目标识保留。
RealYu 保留客户、团队、权限、套餐和账务；Sub2API 管理上游账号与池。

Git 交接只包含审查后的源码、构建输入、公开下载资源、合成测试材料及脱敏报告。
数据库、登录凭据、生产 Key、原始日志、私有测试身份、隧道证书与历史客户证据
不进入 Git。迁移这些私有数据必须使用另外的受保护传输和校验过程。
代码分支推送不代表已部署，也不代表允许切流。

构建应使用仓库锁文件和同一提交。先构建 `web/`，再构建根目录 RealYu 和
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
