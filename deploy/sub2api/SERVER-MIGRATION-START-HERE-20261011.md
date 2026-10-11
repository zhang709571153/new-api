# Windows Server 整站接手入口 — 2026-10-11

目标：把 RealYu API 的网页、API、客户与团队、计费与历史、上游账户、数据库、Redis、网络入口和观测完整迁到服务器；验收后开发机可以关机。域名、现有 Key、用户名/昵称、团队和余额保持。

**现在可以开始服务器安装、源码重建和隔离验收。正式切流仍需要最新私有数据、续聊状态迁移和服务器实测。** Git 同步不包含生产数据库和密钥，也不表示服务器已部署。

## 1. 已确认的交付

- 仓库：`https://github.com/zhang709571153/new-api.git`
- 部署分支：`codex/sub2api-handoff-20261009`。不要使用默认 `realyu` 分支或根目录 New API 程序作为新业务核心。
- 本入口编写前已核实 GitHub 与本地提交均为 `5c2764f570e399ebcb46b1703cde2473b521ab9e`，工作区干净；本入口随后的提交继续沿此分支。
- 默认重建输入：`realyu-singlecore-v0.2.15-20261011-ux6`，源码 `a631266bde77db86b5b530cc4fccb7b6aba1013f`。当前最后已确认线上为UX5，UX6发布状态以根目录LIVE最新记录为准。
- 以 `singlecore-source.json` 的固定 Sub2API 上游加完整补丁交付。此次重新比对 409 个改动文件全部一致，Git 内补丁 SHA256 与 manifest 一致；原生开发目录的本地 remote 不是独立 GitHub 发布仓库。
- 补丁 SHA256：`20a90f1bc034ca24d7500bc1561e6530519efb8feaca83994b1b9ae9f319e4de`。UX6本机候选 binary SHA256：`26aaecdbab17baeb86e9f984cf66d3b8c99a51542845b0b73603827f26298cc7`。不同工具链重新构建不能仅因文件名相同声称与旧 binary 一致，应记录自己的构建哈希及来源。
- 当前 PG 有 300 条 migration 记录，最新 308；历史归档已进入原生 PG。新机恢复当前完整库，不重跑旧 SQLite/S0/S1 客户导入。
- 本机 15:27 采样六路径健康，仅证明源机当次健康。服务器报告仍是 10月9日的 Windows Server 2025 Datacenter、4 vCPU/16 GiB；须在目标机重新核对实际状态。

## 2. 服务器立即可做

先阅读仓库 `AGENTS.md`。在不存在的新目录检出正确分支：

```powershell
git clone --single-branch --branch codex/sub2api-handoff-20261009 https://github.com/zhang709571153/new-api.git C:\RealYu\handoff
Set-Location C:\RealYu\handoff
git rev-parse HEAD
```

阅读本入口、[Windows 整站交接](SINGLECORE-WINDOWS-HANDOFF.md)、[UX6 交付](UX6-USAGE-20261011.md)和[最新网络事故](NETWORK-INCIDENT-20261011.md)。其余文档按需导航；有日期的旧“候选/未上线”描述不是当前版本状态。

1. 重新盘点服务器的权限、已有服务、端口、磁盘、待重启状态、实际网络和备份位置，保留云厂商/远程管理服务。
2. 安装持久目录内的构建与运行依赖，按原生 Windows 服务方案准备 PG、免费 Redis、WinSW、API、bridge 和网络组件。生产服务不依赖 Codex 缓存、用户登录或桌面 Clash。
3. 使用已审工具链重建 Sub2API + RealYu；Sub2API 前端使用该源码的 pnpm 流程。新目标目录必须不存在：

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\sub2api\Prepare-SingleCore.ps1 -Destination C:\RealYu\singlecore-build -Build -Test
```

4. 生成服务器自己的路径、服务、SID/ACL、端口、文件哈希和运行计划。不要执行开发机已经完成的 `Activate-Ux*`、首次切核或事故恢复脚本。
5. 准备独立数据库/Redis和测试入口，完成隔离部署、资源下载与无人登录服务验证。恢复生产副本前拿到私有材料，副本不可与源机争用同一 OAuth 刷新权；仅关后台刷新不足以阻止请求触发刷新。

源机已经使用 SG-HY2 17897，目标机不能指向开发机的 127.0.0.1，也不能把到开发机的远程代理当作整站迁移完成。服务器的上游出口、Tunnel直连/独立代理路径须实测；如复用SG配置，确保目标机可独立运行。不要在准备阶段加入正在接收客户流量的生产Tunnel。

## 3. Git 以外必须私下交付

| 交付项 | 必须保留/核对 |
|---|---|
| 当前完整 PostgreSQL | 用户、旧Key、用户名/昵称、团队/成员、余额/权益、结算/历史/订单、上游账户、设置、归档receipt和完整migration map |
| 必要 Redis DB1 状态 | 同一ID空间的有效response affinity与成对user/key owner、TTL、安全/撤销/幂等状态；不盲拷锁、并发占用和运行租约 |
| 私有 env/签名材料 | 稳定JWT/TOTP/旧身份secret及namespace、数据库与Redis凭据、现有启用配置 |
| 上游与网络材料 | 当前OAuth状态、SG凭据及TLS校验材料、Cloudflare Tunnel/受控域名权限 |
| 程序资源与回执 | 审核过的二进制/依赖、pricing、时区、客户端下载资源、当前服务和权威回执、快照哈希 |

通过受控私有渠道传递并限制ACL，不放Git、聊天正文或公开附件。预演快照可先行；最终数据需在切流时短暂冻结旧写者后重新取得，不能用13点的发布前快照覆盖后续交易。

当前已有 [308备份与本机隔离恢复入口](singlecore_pg_backup308.py)，旧305/307入口不适用于300项migration。308工具固定源机身份和隔离恢复端口29490，不能直接当作服务器安装器。跨机原生 `pg_dump`/`pg_restore`、角色与ACL流程见总交接第5节。

**尚未完成的迁机工作：**仓库没有已验收的通用“单核心→单核心 Redis 状态迁移器”。接手方须补齐按实际键族、TTL、身份归属导出/恢复的实现与测试，或验证独立DB1恢复及受控状态清理方案。原域名/Key不变无需用户重跑安装器，但不能在该项未验证时保证已有会话无缝续聊；在途WebSocket会需要重连。

## 4. 正式接管与开发机下线

先预演恢复、计时和对账，再确定短切流窗口，不要求人为等待20分钟/1小时空闲。正式执行：源机关闭写入并排空 → 停旧业务/OAuth写者 → 导出最终PG及必要状态 → 服务器恢复核对 → 服务器成为唯一写者 → 迁移正式入口并开门。

至少通过：原Key与账号登录、个人/团队权限及额度、历史/趋势/资金对账、完整SSE及WS续聊、PDF/联网/图片、前端及下载、两条入口真实路径、拒绝请求不扣费、重启无桌面登录仍可运行、备份恢复。逐项记录PASS/FAIL/NOT_RUN，不把源机测试当作服务器验收。

另须验证 GOST worker 异常退出的状态/告警和受限恢复：10月11日曾出现wrapper仍Running但转发端口全失效。新路径的精确Defender保护和实际worker/端口检查不可漏；不因SCM显示Running就放行。

新机开始新交易后，以新机数据库为唯一权威；不能简单开回源机旧库。保留源机只读恢复材料，完成服务器公网验收、稳定观察和独立性核查后再下线。最终标准是**开发机停止相关服务或关机后，网站、API、数据库、上游出口、续聊和监控仍由服务器独立提供**。

## 5. 可直接发给服务器 Codex

> 请从 `https://github.com/zhang709571153/new-api.git` 的 `codex/sub2api-handoff-20261009` 分支接手，先读 `deploy/sub2api/SERVER-MIGRATION-START-HERE-20261011.md`。目标是将当前单核心RealYu整站迁到本Windows Server，默认源码交付为UX6，实际源机版本先查LIVE，最后不再依赖开发机。现在先完成目标机盘点、依赖安装、固定源码重建、独立Windows服务/隔离测试和迁移预演准备，保留现有服务器服务。不要部署默认分支的旧New API，不重放旧同机切核计划，不改生产入口或与旧机并发刷新同一OAuth。列出所需私有数据/配置、续聊状态迁移实现及测试结果，与源机最终快照配合后执行短窗口切流。维护DEVLOG/UPDATELOG，输出真实回执与首次失败，不输出密钥，不将未验证项记为通过。
