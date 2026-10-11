# Local Sub2API deployment record

## 2026-10-11 21:35 CST — UX7 prepared; live remains UX5

Final candidate source `f9941441e79062ae860b1cc0f429656c0e9c4ed3`, EXE SHA256 `0734fee22619df881f7a014b4383df17e1a9310e5a467627a691741aa21e02c4`; full433-file reconstruction and25 final-binary synthetic HTTP checks passed. Read-only plan `native-ux7-20261011-r2`, SHA256 `b81eeaffe583ca0dd256f9053559bf69a4fd61f037a8d56be12b00815a78a9bc`, is prepared, not activated. Both samples observed3 in-flight requests; execution must still drain within30s or abort and reopen. Current authority has300 migrations, UX7 requires302 after reviewed309/310. Native recharge remains disabled and no real new payment has been completed. [Delivery, first failures and limits](deploy/sub2api/UX7-PAYGO-ADMIN-20261011.md).

## 2026-10-11 17:30 — UX6 candidate ready, UX5 remains the last verified live version

The icon-complete r4 elevation attempt returned `UAC_NOT_STARTED`; no r4 activation receipt exists. R2 gate duration was30.162s, with9 preserved maintenance failure samples. After reopening, the existing collector observed171/171 healthy samples on each of six paths through17:34:21 CST; both Tunnels had4 connections. This is bounded sampling, not a continuous availability guarantee. [Observation](lab/sub2api_e2e/ux6-abort-observation-20261011.json).

UX6 now includes provider marks and nickname avatars. The r2 attempt aborted before stopping any service after a30s drain timeout; the gate reopened. r3 was superseded without activation for the icon correction. Final r4 preflight saw zero active/reserved/batch work twice, but this is not deployment proof. The current candidate binary is `26aaecdbab17baeb86e9f984cf66d3b8c99a51542845b0b73603827f26298cc7`; source `a631266bde77db86b5b530cc4fccb7b6aba1013f`. Only an r4 ACTIVE receipt plus public acceptance may replace UX5 as the verified live version. [Delivery and first failures](deploy/sub2api/UX6-USAGE-20261011.md).

## 2026-10-11 15:04 — Network recovery, application remains UX5

Defender quarantined the new SG GOST binary at14:26:57 CST. The original digest was restored with an effective exact-file exclusion; dependent Tunnels and SG forwarding were recovered. Native API/bridge/Mihomo processes and the application/database version were not changed. Six paths27/27 through15:08:20, both connectors4 connections, and one real public SSE passed. See [incident evidence and remaining recovery gap](deploy/sub2api/NETWORK-INCIDENT-20261011.md). Do not replay the completed incident script on healthy production.

## 2026-10-11 — UX5工作台布局与对齐

UX5：UX5已上线，公网及浏览器验收通过；监测保留一次本机代理样本超时。新operation native-ux5-20261011-r2，binary SHA ed4a0f9524693c8e3047a5f637dfae6c9176099a116207a138e896f43b174ab3。r1已过期，不执行。生产身份以实际manifest与ACTIVE回执为准；同PG/300migration/原Key与代理。 见[UX5交付与部署](deploy/sub2api/UX5-WORKBENCH-20261011.md)。

## 2026-10-11 12:44:28 — UX4已ACTIVE，限定范围验收通过

**当前实际版本：`realyu-singlecore-v0.2.15-20261011-ux4`。** operation r2已在12:44:28.612 ACTIVE；排空12:43:51.021至开门37.591秒。同一原生PG、300项migration和代理保持，未恢复数据库或启动旧服务。部署source `7e0215f62539a52a3c7884560152a11599f7fe1e`、binary SHA `1918fcac7e0e47b64cacd369be4d0b3b58e0e86699658d86b67db6063ffc2821`，与405文件重建、完整85项候选HTTP身份一致。

已完成operation `production-ux4-update-20261011-r2` 的plan SHA `9ddb8cb60adad4bf9793dedba632051260fb7bed99a3d0c951ad9f7e39271d61`，ACTIVE回执SHA `3ac8d29af192999ec614095ced3b172996d807445a1ab4c54b45f7906f3e3f13`。此前r1 UAC取消无执行，两个旧/已完成计划均不能重放。公网47/47检查（82请求）和SSE一次/同连接WS两轮通过；3usage/3settled/3dedup、窗口reserved0、226quota=USD0.000452。账务为后验只读一致性，不是前后钱包快照。

线上浏览器复用既有管理员session确认中文工作台、文字语言入口、配置首部及默认累计；实际模型页保留导航，六套餐样式确认。390×844英文暗色价格页及菜单实际展开通过，页面宽382≤390，合计9项限定浏览器检查。无新表单登录，客户截图不入Git，不外推普通成员、全部角色或实体设备。见[浏览器范围](lab/sub2api_e2e/ux4-production-browser-20261011.json)、[10秒采样](lab/sub2api_e2e/ux4-production-observation-20261011.json)、[流程与首错](deploy/sub2api/UX4-RECOVERY-20261011.md)，不据短样本推导长期稳定。下方UX3及更早版本是历史。

既有10秒采样从ACTIVE至12:55:09.609，共640.997秒，六路径各65/65健康；最大间隔10.014秒，读取时新鲜度5.951秒，两Tunnel均4连接。维护期10个失败样本（9个503、1个backend连接失败）保留，ACTIVE后此窗口0失败；采样不等于连续可用或长期稳定，也不用于推算精确用户中断时长。

## 2026-10-11 08:24:45 — UX3 已ACTIVE，本轮限定范围验收通过

正常Windows提权首次取消，未启动更新；第二次08:24批准后，`realyu-singlecore-v0.2.15-20261011-ux3` 在同一PG权威上ACTIVE。08:24:12.930排空至08:24:45.448开门，维护 **32.518秒**，没有恢复数据库、启动旧服务、新增migration、重导历史、启用商户或切换网络。实际SCM worker为Session0且程序SHA与final2一致。

最终源码 `3f13f202765f2d9473664f3ec239f71d187adecf`；binary SHA `353428ccb6a09a3f512bc8bced5c23364c0d4f7cb46f5cecccaea24782446569`；patch SHA `0840cba9656fbea1aeba3d47da24732cebebe9bf2cfd682c4be8f79ff8b54f45`。399文件完整重建一致。958/87完整前端阶段、最终36/3定向复验、16个真实隔离PG根、final2 HTTP49/49分开记录；不将重叠测试相加。独立合同/重点页面视觉审查在所查范围无未决P0/P1，原始充值竞态和手机表格首错保留。

工作台个人周权益与按量余额分项，趋势与最近调用前置；团队一人一行、昵称/排序/模型展开和“…”菜单；PAYGO入口恢复但缺货，未开放真实商户。管理员managed周订阅授予/编辑、团队商业生命周期及其它旧需求缺口仍见[35项矩阵](deploy/sub2api/UX3-RECOVERY-20261011.md)。

**公网只读32/32检查（55请求）、真实SSE一次与同连接WS两轮、三次调用的独立账务核对均通过。** 每次各有一条用量、一条settled及一条dedup，quota为172/14/41，合计227（USD 0.000454）；测试Key的固定窗口无reserved、重复或额外记录。这是请求后只读一致性核对，不是独立前后钱包快照，也不代表全部客户流量。

线上CUA已完成既有管理员登录后的**英文浅色桌面1280×720和手机390×844**工作台复验：桌面首屏趋势/最近调用完整，手机根宽与内容宽均382，无整页横向溢出。三次30秒工具绑定/DOM超时保留，后来实际登录与截图通过；不能将工具超时推成站点故障。私有截图不入Git，见[生产浏览器范围](lab/sub2api_e2e/ux3-production-browser-20261011.json)。普通用户线上登录/403、真实商户、全部语言/主题与四套客户端本轮未测，不宣称长期稳定。

既有10秒观测另有一次已恢复的网络样本：ACTIVE后至08:40:18，六路径各94个样本，五路径94/94；经SG17897的公网代理路径93/94（98.936%）。08:33:18.578923发生TLS握手4110ms超时，CONNECT已200、无HTTP/CF-Ray、curl28；前08:33:08及后08:33:28样本健康，同轮其余五路径健康。最大采样间隔10.014秒，读取时最新样本3.713秒；首错与probe保留在[生产观测](lab/sub2api_e2e/ux3-production-observation-20261011.json)。因此不能称全路径零失败，不能用采样推出精确中断时长或归因为新UX；这不是已证实的本轮应用P0。未为此更改生产。

已完成的r1发布计划不能重放；当前库保持300迁移。新机走[Windows交接](deploy/sub2api/UX3-WINDOWS-UPDATE-20261011.md)。[候选及部署证据](lab/sub2api_e2e/ux3-candidate-validation-20261011.json)保留阶段边界，下面UX2与早期“未上线”均为历史。

## 2026-10-11 06:51:29 — UX2 已上线并完成历史归档

用户再次发起后，正常 Windows 提权成功。`realyu-singlecore-v0.2.15-20261011-ux2` 已 ACTIVE，维护 **33.942 秒**；源码 f98d4b6、r5 binary 与已审计划一致，SCM 自动服务 / LocalService / Session 0 已核验。沿用同一 PostgreSQL，迁移由299增至300（仅308），没有恢复数据库、启用旧服务或更改网络配置。

封存 **31,295** 条历史来源已在生产归档，manifest 与隔离审核完全一致；重复执行确认幂等。平台可见31,291条，另4条旧隔离记录保留；归档仅追加事实，不重放扣费。公网API已确认 `legacy_archive_complete=true`。

公网HTTP **22/22**、真实模型目录/SSE/同连接WebSocket续聊 **3/3**通过。账务窗口5条变更（本次模型3条、并发其他2条）均结算，违规/差额/待结算均0。正式浏览器复验了中文首页、真实USD模型价、英文390px手机首页和文档导航；先前控制连接超时及恢复证据保留。

本机 `Activate-Ux2.ps1` 已执行完毕，**不要再次运行已完成计划**。下方01:23“待提权”和旧UX状态均为历史。新机须接手当前完整PG/Redis与私有材料；300迁移的现库不能直接套307专用备份入口。实体移动设备、真商户和四套安装器仍保持未验收边界。

[正式发布证据](lab/sub2api_e2e/ux2-production-20261011.json) · [上线后HTTP](lab/sub2api_e2e/ux2-production-http-20261011.json) · [观测摘要](lab/sub2api_e2e/ux2-production-observation-20261011.json)

**提权结果补充（2026-10-11 01:23 CST）：** Windows 提权未成功启动发布，未产生 UX2 ACTIVE 回执；重新只读预检通过，现网 schema 仍299、历史归档为空。线上没有被这次候选更新。请使用已给出的管理员终端命令完成系统提权，之后才能核对实际发布、正式归档和公网验收。

**2026-10-11 UX2：候选已验收，尚待 Windows UAC 执行。** 最终源码 `f98d4b6`、r5 SHA `aef7b287ba4fdea3dca6590cc10bedf1e5ed8b90be639bde4d01cbc60001453c`；源码383文件、HTTP51/51、中英桌面/窄屏实测已通过。生产仍为下述20261010-ux、schema299，不能将本轮候选视作上线。当前发布入口是[308增量更新](deploy/sub2api/UX2-RELEASE-20261011.md)，旧 r1/r2 计划已因候选原路径移存而失效。

**2026-10-10 23:13:51 CST：完整 UX 已 ACTIVE。** 当前实际版本为 `realyu-singlecore-v0.2.15-20261010-ux`，
源码 `93022254b98d9cdfbc36db8d7ef6007365a89b53`、binary SHA256 `88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081`。
维护 32.596 秒，306/307 已应用到原 PG（297→299），未恢复数据库或启动旧服务，SG-HY2 路径保持。
公网协议 7/7、严格浏览器 19/19、Cow 原 Key 实际回复及单次结算、14 请求账务零差额已通过。
工作台、四种配置命令、团队/用量与管理员详细页、CNY×7、隐藏版本/兑换及取消确认门均有实际公网页面证据。
品牌、注册/目录配置及仍未完成的经营能力见[本次发布](deploy/sub2api/UX-NATIVE-RELEASE-20261010.md)。
下方 UAC 取消与候选记录均为历史；不要再次运行已经完成的 Activate-Ux 或旧 CNY-only 计划。

**2026-10-10 22:31 CST：完整 UX 的 UAC 返回取消，发布未执行。** 新 operation 没有执行日志或 ACTIVE 回执，schema 保持 297 项；取消后再次只读预检通过。线上仍为 ws-owner、维护门开放，既有六路径最近各 30/30。正常管理员命令及后续真实验收见[接续说明](deploy/sub2api/UX-NATIVE-RELEASE-20261010.md)。

**2026-10-10 22:24 CST：UX 候选准备完成，未执行生产更新。** 当前实际服务仍为 `realyu-singlecore-v0.2.15-20261010-ws-owner`，SCM 正常，入口 maintenance=false。r3 UAC 返回取消，无 update receipt；r2 已恢复的事实不变。默认源码交接已推进到 UX 候选 `93022254b98d9cdfbc36db8d7ef6007365a89b53`，新二进制 SHA `88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081`，前端 737 项及隔离浏览器 15/15 通过；这些不是生产验收。部署须使用[306/307 增量更新](deploy/sub2api/SINGLECORE-ADDITIVE-UPDATE.md)，两个旧热修计划不得套用。生产备份在独立数据库恢复核对 120 表/51,028 行/297 迁移通过，完整备份保留私有。

**2026-10-10 21:32 CST：第一次原生更新已恢复旧版，修正后的 r3 等待 UAC。** r2 在 `STOPPING_NATIVE` 触发失败，维护门 21:30:23–21:30:59，约 36.314 秒，自动恢复同一 PG 权威上的 `ws-owner`，公网复核恢复。已只读复现 PowerShell 在确认旧进程不存在时隐式返回 1，发布器增加完成校验后的显式成功码；13 项测试通过，真实所有权异常仍失败。首次错误与恢复阶段见[回执摘要](lab/sub2api_e2e/singlecore-cny-first-update-20261010.json)。没有恢复 SQLite 或更改网络。r3 使用同一已通过[28 项隔离浏览器检查](lab/sub2api_e2e/singlecore-cny-isolated-browser-20261010.json)的冻结二进制；下列源码与运行版本区分仍有效。

**2026-10-10 21:24 CST：CNY/管理员直接进入/CowAgent 修复候选已验收，等待 Windows 服务提权。** 当前生产仍为下述 `ws-owner`，本轮 UAC 返回取消，未关闭入口、未停止服务。待发版本 `realyu-singlecore-v0.2.15-20261010-cny-admin`，二进制 SHA256 `4cb44fe13cbf48793ae28b71175cb6f919454499688092d3516a7accf5686c0a`，原生源码 `a184b4a63385e71347b80c2b59f34bce4605acfd`。隔离真实浏览器 28/28；CowAgent 同类 15 把无限 Key 的历史负额度读取修复通过 3 根 PG 和 8 项权限测试，尚未完成生产恢复验证。新增 UX 全面恢复属于下一阶段，详见 [35 项矩阵](deploy/sub2api/UX-PARITY-RECOVERY-20261010.md)。

**2026-10-10 20:40 CST：原生单核心已 ACTIVE，SG 双 Tunnel 已承接生产。** 当前版本 `realyu-singlecore-v0.2.15-20261010-ws-owner`，20:30:20 开门；旧 New API 客户权威和旧 Sub2API/prewarm 已退役。公网 PDF、搜索、生图、WS 两轮、工具往返、8 模型、原 CLI 续聊通过；26 笔资金请求对账零差额。确认页移除与人民币展示为下一次原生程序更新，尚未发布。详情见[生产记录](deploy/sub2api/SINGLECORE-PRODUCTION-20261010.md)；下方均为历史时间点，不能重新执行旧库切换。

**Single-core preparation, 2026-10-10 19:42 CST:** the separate customer PG
database/S0 and Singapore SCM services are installed. SG exit and a real
upstream request passed. The application authority and production Tunnel paths
are still the bridge baseline below: the second Windows elevation was cancelled,
and no native ACTIVE receipt exists. The operator has a single reviewed command
for the remaining rolling network change and core activation. This is not a
single-core release declaration. See the [Windows migration handoff](deploy/sub2api/SINGLECORE-WINDOWS-HANDOFF.md).

**Latest network check, 2026-10-10 08:47 CST:** after the user's TW switch,
both connectors automatically regained 4 connections. The observed path uses
tw1 through the fallback selector; public pages, strict edge TLS and one real
Codex stream passed. No extra restart or application deployment was performed.

**Network incident, 2026-10-10 morning CST:** public access failed from 08:10
to 08:23 while origin services stayed healthy. The observed tunnel traffic still
used the us2 proxy; after the route changed to jp3, public access recovered.
Homepage assets and one real Codex stream passed. This diagnostic session did
not deploy code or perform the node switches. Shared proxy-path reliability
remains an open issue; see the [incident and handoff note](deploy/sub2api/NETWORK-INCIDENT-20261010.md).

**Portal follow-up, 2026-10-10 03:15 CST:** the v3.10.0.2 candidate passed
management HTTP E2E against isolated RealYu state and real Sub2API reads. Its
publication has not run: Windows elevation was cancelled. The new native-console
DNS exists, but its ingress is not loaded and returns 404. Production remains
v3.10.0.1 below. See [UPDATELOG](UPDATELOG.md) and the
[portal deployment guide](deploy/sub2api/ADMIN-PORTAL.md); candidate success does
not replace production acceptance.

**2026-10-10 status: PUBLISHED AND PUBLICLY ACCEPTED.** Production runs
`realyu-sub2api-v3.10.0.1-20261010`, verified through `api.realyu.fun`. The second
cutover completed with customer ledger preserved and no schema change. The four
new dependency services run automatically under LocalService. This records an
actual local-host publication; another machine remains separately undeployed.

## Historical bridge topology (superseded by native single-core at 20:30 CST)

| Component | Host-local endpoint | Responsibility |
| --- | --- | --- |
| Existing public tunnel | `api.realyu.fun` to `127.0.0.1:18301` | Existing public ingress |
| Transparent edge | `127.0.0.1:18301` | HTTP/SSE, Responses WS and native Images |
| Maintenance observation | `127.0.0.1:18302` | Admission and active requests |
| RealYu / New API | `127.0.0.1:18300` | Users, teams, permissions, plans and customer ledger |
| Sub2API 0.2.15 standard | `127.0.0.1:28090` | Upstream accounts, scheduling and projected identities |
| PostgreSQL 18.6 | `127.0.0.1:28490` | Separate Sub2API state |
| Redis 8.10.2 community Windows build | `127.0.0.1:28391` | Cache, leases and coordination; no paid Redis dependency |
| Identity and internal-credit worker | No listener | Persisted preparation and credit intents |

The isolated candidate used 28600/28601/28602 and its own RealYu database copy
and namespace. Neither its balances nor its response ownership are production
acceptance. Preserve the production namespace and identity secret on migration.
The member/team identity is distinct from the customer who pays the charge.
The owned candidate gateway and edge were stopped after public acceptance.

## Accepted production operation

Operation `sub2api-scope-20261009T172526Z-3ee7e037` reached `published` after
matching public acceptance. Source build revision was `05a732e7e24acc129e9bfc8808d9ffe718608b83`;
later handoff changes only improve deployment helpers and evidence. Dependency
verification passed 24 checks, including exact running executable paths and
pinned application hashes under normal administrator elevation.

- 13 public SDK categories passed, with 23 bounded request attempts, including
  all eight text models, PDFs, real search/citations, tool round trip, WS and Images.
- Eight HTTP/WS ownership checks passed, including cross-person, personal/team,
  unsigned/tampered ID rejection, original-owner continuation and zero denied charges.
- Codex CLI 0.162.0 completed a fresh dialogue and a separate-process resume.
- Twenty financial invariants matched across 47 dedicated-operator usage records
  after the refresh follow-up: no duplicate settlement, exact wallet/token usage,
  team subscription/member allowance attribution and native image token pricing.
- The requested bichon/hilichurl scene was generated through the public Images
  API and visually reviewed. Input 172 + output 601 tokens settled to 97 quota
  at model ratio 1 and group ratio 0.125, without an extra per-image tool charge.
- Both renewable accounts transferred and successfully refreshed through the
  official Sub2API API. SSE, WS continuation and three identities passed afterward.
- Two dedicated operator users and personal keys were disabled. All four keys,
  including workspace keys revoked by disabled-user status, were denied. Financial
  records were preserved. The fixture is no longer available for inference.

See [the sanitized public report](lab/sub2api_e2e/release-public-20261010.json).
Cold backup SHA-256 was
`e3fabff39d55cb8cf161a7dfc84db11b5c5cb106d66cf76ee5f149ebd78376cf`;
it is retained privately and was not restored over newer customer transactions.

## Actual first attempt

The cancelled elevation attempt was followed by a successful manual elevated
installation at 2026-10-09 16:51 UTC. All four new services were installed. The
first gateway cutover opened for public testing at 16:52 UTC. It took an integrity
checked cold backup, preserved the customer ledger and changed no database schema.

Public PDF inline/link reading, web search with citations, HTTP/SSE and chat
passed. Account 1 returned upstream WS 1011 and was quarantined through the
normal Sub2API admin API. A separate run using the remaining supply passed two
WS turns, three identities, validation rejection and native image generation.

The real cross-subject WS check then selected a retained upstream connection
and timed out with a busy error. No foreign content or charge was observed, but
there was no authoritative ownership denial. This was treated as P0. The gateway
program and routing were rolled back against the current customer database;
the backup database was never restored over new transactions. Refresh tokens
had not moved at that rollback. All first-failure evidence remains private and
unchanged; the later accepted operation transferred refresh ownership separately.

## Corrected candidate

Version `realyu-sub2api-v3.10.0.1-20261010`, binary SHA-256
`91a9dc35306ae1fe29f3326d91af92ec855e25df09f6e56c9678d359a7864b8a`.
The worker remains at SHA-256
`8ac607bf72faee338238b5393555d326d910662b76503d4cc4854d0dda75710c`.

Authenticated opaque response IDs now bind retained continuation to the member
and team. Foreign, unsigned and tampered references are rejected locally before
upstream lookup. See [ownership behavior](lab/sub2api_e2e/RESPONSE-OWNERSHIP.md).
The exact candidate passed 13 functional categories, all eight text models,
eight real HTTP/WS ownership checks, exact denied-request no-charge checks and
Codex 0.162.0 initial dialogue plus separate-process resume. See the
[sanitized candidate report](lab/sub2api_e2e/release-scope-candidate-20261010.json).
The separate production run is recorded above; historical candidate reports keep
their original candidate-only scope.

Native `/v1/images/*` uses the new interface's reported-token pricing by the
user's explicit decision. The former Responses image-tool tariff is unchanged.
Successful image bytes alone are insufficient; reconcile usage, token quota,
wallet/subscription payer and team allowance after settlement.

## Retry and migration

Use a fresh operation directory and pinned inputs, including the exact gateway,
edge, management helper and service verifier. A plan with `dependency_mode:
"reuse"` verifies the existing installed dependency services under normal Windows
administrator elevation; it does not reinstall them. `production_route.py
restage` validates the pinned previous restored receipt and unchanged channel
baseline without modifying routing. Never reuse a completed operation directory.

The reviewed `Install-HostRelease.ps1 -Plan ... -ExpectedPlanSha256 ...` entry
checks every pinned input. `-ValidateOnly` performs no service mutation.
`host_cutover.py` closes admission, drains active work and async settlement,
activates the route through the official API, stops the old gateway/edge,
takes a cold backup, installs verified inputs and compares closed-gate ledger
and schema. It provisionally opens for a 20-minute public acceptance window.
Only a matching operation/version/binary PASS verdict publishes the release.
Failure restores the old program/routing while preserving the latest ledger.
Unexpected schema changes or an unverified drain retain maintenance for recovery.

After public acceptance, the old executable was confirmed absent, the legacy
auth-sync flag was false, the Sub2API driver was active and channels 1/2/3 were
disabled. Credentials for accounts 2/3 then transferred and actually refreshed.
Account 1 remains inactive and has no refresh token. The first readback incorrectly
expected secrets in the redacted ordinary GET. A single-account official export
verified the existing write without replay; the helper now uses that verification
and has regression tests. No export credentials are printed or written to Git.
Rollback now requires the newest rotated credentials from Sub2API; an old legacy
secret backup is no longer a safe refresh-owner rollback.

Another Windows Server uses [NATIVE-WINDOWS.md](deploy/sub2api/NATIVE-WINDOWS.md)
and [the migration runbook](lab/maintenance/sub2api-migration.md). Copy current
private databases, bindings, queue/credit state, credentials and tunnel material
through a protected channel, separately from Git. Do not allow two machines to
write independent customer ledgers or refresh the same OAuth credentials.

## Remaining boundaries

The 2026-10-10 post-publication audit confirms the live routing and refresh-owner
switch, and compares actual non-fixture traffic with Sub2API usage. It records
seven remaining P1 items, including interrupted-stream settlement, backups,
delivered alerts and the remote management URL. See the
[current P0/P1 checklist](lab/maintenance/sub2api-p0-p1-20261010.md) and
[sanitized audit](lab/sub2api_e2e/production-audit-20261010.json).

- Files upload/file IDs and old search/compact interfaces retain compatibility
  limits. Inline and URL PDFs and current `web_search` are separately tested.
- Images may return 1254 by 1254 or 1536 by 1024 when 1024 by 1024 was requested.
- Old unsigned retained response IDs and unauthenticated retained item/conversation
  references are rejected; full inline history remains supported.
- Browser UI acceptance was blocked by browser runtime failures. CLI dialogue
  acceptance does not imply file/tool execution or desktop UI acceptance.
- Automatic startup is configured; no actual whole-machine reboot was performed.
  Finite smoke observations are not a long-term stability or availability SLA.
- Non-P0 compatibility issues are tracked for subsequent fixes, per user direction.
  A real isolation or accounting P0 still requires stopping the affected release.

Raw transcripts, customer identities, secrets and private receipts are excluded
from Git. Historical reports are preserved with their original binary hashes.
