# UX4：工作台与团队用量恢复（2026-10-11）

## 当前状态

**状态：LIVE_SCOPED_ACCEPTED，已部署且本轮限定范围通过。** binary r3 / operation r2于北京时间12:44:28.612开门，维护约37.591秒；同PG/300migration/代理不变，未恢复数据库或启动旧服务。公网47/47只读检查（82请求）、真实SSE一次+同连接WS两轮及3笔独立账务核对通过。线上既有管理员会话的9项CUA检查通过，包括工作台、模型导航、六套餐，以及390×844英文暗色价格页和菜单；普通用户线上登录/403、全部角色/设备矩阵未测。英文手机团队表P2、首错和采样边界保留，不称全站或长期稳定保证。详见 [LIVE-DEPLOYMENT](../../LIVE-DEPLOYMENT.md)。

既有10秒采样覆盖开门后至12:55:09.609，共640.997秒：六路径各65/65健康，最大间隔10.014秒，读取时样本年龄5.951秒，两Tunnel均4连接；ACTIVE后样本零失败。维护期10个失败样本（9个503、1个backend连接失败）另行保留，不将计划维护混为上线后故障，也不从采样推出精确用户中断时长或长期稳定。隔离29481候选随后按PID、启动时间、exe SHA和端口匹配停止，生产未动。

保留的r2 clean source commit为 `2309268ca569eac91361f55ecad25a03dbbf25a4`，完整patch SHA为 `e4fc4c9002eca03f96eb663ef09da6b459ab947af678e8c5bb0c79429e5bd933`，404个改动文件在固定上游重建一致。r2二进制SHA为 `7467958c9ef4ba8441ee2541c4d04693c873f91934bfef2b717a05e31c387c7b`，Go内嵌revision相同且 `vcs.modified=false`。r2本机source metadata SHA为 `b8a53c553e6a6a52cacbe3dcbbce01d501156359c850b832404548cae25a35f2`；这是本机文件字节hash，Git换行规范化后不得冒用。**它们不是新增文字语言入口r3的最终pins。** r1/r2完整结果保留于 [UX4 E2E记录](../../lab/sub2api_e2e/UX4-E2E-20261011.md) 与 [机器可读汇总](../../lab/sub2api_e2e/ux4-candidate-validation-20261011.json)。

**已部署r3身份：** source `7e0215f62539a52a3c7884560152a11599f7fe1e`，binary SHA `1918fcac7e0e47b64cacd369be4d0b3b58e0e86699658d86b67db6063ffc2821`，patch SHA `b0688d7dea49955fbf0deee0eb2534da1947f625df86493aaa0acc87cbd1058c`，本机metadata SHA `4a66dacf2b8b83dc1531f02356ad828bd0774275172fefcfedad2caa8b5ccbaf`。405文件含新增的LocaleSwitcher相对上游差异；Go VCS与clean source一致，modified=false。

最终可交付证据：[r3构建](../../lab/sub2api_e2e/ux4-final-r3-build.json)、[405文件重建](../../lab/sub2api_e2e/ux4-source-reconstruction-r3.json)、[85项候选HTTP](../../lab/sub2api_e2e/ux4-final-r3-http-20261011.json)、[发布预检](../../lab/sub2api_e2e/ux4-production-preflight-20261011.json)、[ACTIVE回执](../../lab/sub2api_e2e/ux4-production-activation-20261011.json)。

## 本次8项需求矩阵

路径相对于重建后的 Sub2API 源码根；“源码完成”与“生产验收完成”分开记录。本矩阵覆盖本次要求，不代表 [UX3 的35项历史业务需求](UX3-RECOVERY-20261011.md) 全部补齐。

| 项目 | 本轮实现与保留的语义 | 当前证据 / 尚待验收 |
|---|---|---|
| 1. 一键配置放工作台首部 | `frontend/src/views/user/DashboardView.vue` 首先展示 `RealYuWorkspaceSetup`，随后是额度与累计、趋势和最近调用。套餐详情默认折叠；其组件继续挂载和取数。配置继续使用明确绑定的个人/团队 Key，不按最新 Key 猜身份。 | 定向测试通过；真实候选普通用户桌面中英/明暗和中文手机首屏已复核。命令逻辑保持，不把截图当真实复制或四套客户端安装验收。 |
| 2. 默认显示累计 | `RealYuUsageOverview.vue` 默认周期改为 `all_time`，保留今日切换。默认统计范围仍是自己的调用；金额、Token及历史覆盖度来自同一服务端合同。 | 定向测试通过。已只读核对受测管理员 self 今日0、累计1343次 / 112,446,830 Token，`legacy_archive_complete=true`；同期平台今日716次。个人今日为0不等于全站零调用或历史丢失。 |
| 3. 套餐少色、取消装饰性绿色 | `SubscriptionPlanCard.vue` 对 RealYu 品牌使用单一蓝色重点和中性色边框、文字，`PaymentView.vue` 收紧宽度/间距。商品价格、周额度、发单 payload 与关闭条件不变。 | 候选中文桌面/英文手机可读，PAYGO条约66.7px；生产已看到98/198/398/698/1398/2598六套餐的蓝色中性样式。不是购买/商户支付验收，也未启用充值商户。 |
| 4. 使用记录分自己的 / 团队的，并显示昵称 | `RealYuUsageView.vue` 增加“我的调用 / 团队调用”。自己的原 self / personal 筛选保留；团队选择只含当前可管理且未关闭的团队，真正请求服务端全队范围。团队明细显示本队成员昵称，不用前端筛选假装全队数据。 | 使用记录组件、真实PG、r3候选与生产真实admin的team history/trend/summary通过；普通成员拒绝由候选/PG覆盖，未伪造线上成员凭据重测。 |
| 5. ID与昵称独立两列 | `RealYuTeamsView.vue` 管理、按成员汇总和请求明细均拆分ID/昵称；团队模式使用记录同样拆分。重名不合并，ID继续作明确身份；手机横滑保留昵称固定列。 | 团队28项及使用记录相关测试通过；中文桌面/英文手机横滑已有实图，昵称保持可见。保留P2：英文离队状态挤压长昵称，初始周限额仅部分可见，不宣称全部列同屏完整。 |
| 6. 字号层级清楚、保持紧凑 | 团队名称约20px，当前周剩余额度约24px并用主色；日期、状态、ID降低视觉权重。同一卡片内组织，不新增大块留白。每人一行总和、排序、“…”管理菜单和模型展开保持。 | 源码及组件合同通过；已复核队名/余额/日期层级和手机内部表格横滑，主流程记录根宽382≤390。英文昵称截短属于上述P2。 |
| 7. 登录后的模型页保留网站框架 | `ModelPlazaView.vue` 对已登录用户使用原 `AppLayout`，保留侧栏/顶部并避免双头部；未登录仍为独立公开模型页。单靠 `embedded=1` 不产生登录身份。追加要求：中央语言入口纯文字“中文 / English”，所有使用者共用，不依赖旗帜emoji。 | 导航39项与后续40项定向复验重叠，r2暗色说明已修；r3候选中英文双向切换/中文手机通过。生产中文桌面价格页保留导航；390×844英文暗色价格页宽382≤390，实际菜单展开通过，语言入口纯文字。 |
| 8. 严格E2E与Git交付 | 本轮增加真实隔离PG权限/聚合验证，保留首错；候选与生产分层。r3的405文件重建、clean source/binary、完整85项HTTP已通过。 | 生产47项、3次真实SSE/WS及独立账务通过；3usage/3settled/3dedup，无本测试窗口reserved，226quota=USD0.000452。生产页面实看与监控采样分别记录；Git交付收尾不重用已完成计划。 |

## 查询与金额边界

- **self / personal：** self 为当前调用者自己的个人及团队调用；personal 只取个人范围。团队成员仍可在自己的调用中看自己的记录，不能获得全队数据。
- **team：** `scope=team&team_id=<正整数>`。每次请求由服务端在同一个只读事务内检查有效用户、既有 `teamAccess.CanManage` 和团队未关闭。不能靠前端隐藏选项授权；撤权、转移owner、停用用户或关队后重新检查。旧role10层级规则沿用现有合同，不扩大为全局管理员。
- **platform：** 仍遵原平台级权限。团队请求不接受平台 `user_id` 筛选，其他scope不能夹带 `team_id`；有歧义的选择参数拒绝。
- **切换与失败：** 切scope或团队时立即取消旧请求、清空记录/合计/展开项；迟到成功或失败都不能覆盖新页面。团队响应须回显相同scope和team_id。无权限、已关队或错误深链明确失败，不静默回到self伪装成功。团队列表加载失败保留自己的调用并提供重试。
- **合计与分页：** 汇总对完整过滤集合计算，不能按当前页重新相加。日期、模型、scope、team_id、页码同步URL，前进/后退恢复相同语义。未知缓存仍为NULL，不伪造0；服务端 `total_tokens` 不重复加缓存。
- **资金不变：** 本周个人套餐可用与个人按量余额分别显示、不相加，团队池不混入个人余额。API费用为USD十进制，客户展示按既有配置换算CNY一次；模型参考价格仍USD。本轮没有扣费、套餐履约或充值结算改动。

## 同机发布准备与交接

本轮是现有原生单核心的 **schema-free 同库更新**：现有300项migration截至 `308_realyu_usage_history.sql`，不增加DDL、不重导旧usage、不恢复数据库、不启动旧New API或影子供给服务，也不改变SG代理/商户。

原私有准备入口 `runtime/production-ux4-update-20261011-r1/` 曾为binary r2封计划SHA `c5760fa4fedf05911edfafa3984f418aefe62389e42208e32d7ed0d81e7654a6`，UAC取消、未执行且被r3需求取代；保留证据不得执行。实际已完成入口为 `runtime/production-ux4-update-20261011-r2/`，plan SHA `9ddb8cb60adad4bf9793dedba632051260fb7bed99a3d0c951ad9f7e39271d61`、wrapper SHA `81030fa924675d0be93464896300a119228213baf1d423b194e508b0e37fe645`，721pins/300migration预检通过后取得ACTIVE。回执SHA `3ac8d29af192999ec614095ced3b172996d807445a1ab4c54b45f7906f3e3f13`；**已完成计划也不能再执行。** operation r2与binary r3是不同编号维度，均依赖本站权威，不能直接搬到新机器执行。

流程来源为交付的 [singlecore_native_update.py](singlecore_native_update.py) 与既有release control，具体顺序为：

1. 取得最终候选clean commit、完整patch、逐文件重建证据、二进制SHA及Go VCS信息；私有准备器要求显式提供候选路径、预期二进制SHA和source commit，禁止把预览产物自动当成最终版。
2. 读取当前真实manifest、env、前次ACTIVE权威回执及300项迁移；验证本轮新备份和隔离恢复证据。读取运行中的请求/预扣/批处理只说明当时状态，不预留维护窗口。
3. 在新的唯一operation中固定所有文件哈希：发布器、release control、准备器、wrapper、当前manifest/env、权威回执、源metadata/patch、候选与迁移文件、备份回执及dump。计划只写一次，首错另存，不覆盖旧计划来掩盖失败。
4. PowerShell 5.1的 `Activate-Ux4.ps1 -ExpectedPlanSha256 <本站新计划SHA>` 默认只读预检；仅显式 `-Execute` 才交给既有发布器执行，wrapper不会自行提权。执行前仍再验pins和权威，不靠旧预检结果跳过。
5. 发布器关维护门、排空30秒，再停/更换原生API。新exe置于当前exe父目录下新的唯一版本子目录，新env使用对应新operation名；具体路径以本轮新plan为准，不使用已取消r1的目的地。允许的env增量仍只有已配置的CNY汇率7，未引入支付或网络开关。
6. 新版本健康与版本/引擎合同检查后才开门并记录ACTIVE。失败只能按既有更新器边界恢复同一当前PG权威上的兼容旧程序；不恢复旧快照、SQLite或旧供给服务，不丢弃开门后的业务写入。
7. ACTIVE后执行分层公网、权限、协议及既有监控检查，记录首错和维护区间；最后更新实际版本与Git交付状态。当前47项只读、3次真实协议、后验账务、9项限定浏览器与六路径65轮采样分别通过。可交付的 [公网只读验收器](../../lab/sub2api_e2e/verify_ux4_public_20261011.py) 默认离线，需要显式released和已审binary SHA才能联网；它不代替真实SSE/WS或浏览器验收。

本轮备份：11:55:31（北京时间）生产只读一致快照，在独立29490的新建验证库恢复通过；127表、92,838行、300项migration与逐表hash一致。schema摘要SHA `4b27ba25c1ab6cc6d44107715fa33733fa2ebe9f7055ff91b7be24b8983f4662`，dump SHA `bf53fd56e4bb2ab6340e71bae7259357d0815e91d7a7bba5b153958724cf95d9`。schema摘要不是schema dump文件哈希；逻辑快照不是PITR，也不包含快照后的写入。详见E2E记录。

迁移到另一台Windows Server应另按 [整站Windows交接](SINGLECORE-WINDOWS-HANDOFF.md) 制定计划：验证固定上游和完整patch内容，在目标机形成自己的clean commit/metadata并重新build、封本机pins；保留原交付commit作为来源，不能冒称恢复了相同Git历史。通过私有渠道移交数据库一致快照、env、OAuth及服务资料，先在隔离环境验收，再安排最终短写冻结/流量转移。不得直接复制本机私有plan或重放UX2历史导入计划。

## 当前不属于本轮已完成能力

外部PAYGO充值商户仍未开放，缺货入口继续可见；没有进行真实商户支付/回调/钱包入账验收。管理员managed订阅原子授予、实例编辑、团队新购/续费/升级/退款等既有差距不因这次布局和查询改善而消失。参照UX3矩阵逐项处理，不能宣称全历史UX已经恢复。

Git仅提交源码patch、可复现说明和脱敏测试结论。账号、密码、API Key、OAuth、签名密钥、支付和SG凭据、PG/Redis数据、私有plan/env/receipt及客户页面原图均不进入Git。此文件后续只依据实际候选/生产回执追加结果，保留首次失败与范围限制。

候选首轮HTTP为81/85，4个参数校验探测先被现有查询限流返回429；未关闭限流，测试器改为每角色61秒内最多40个认证请求、失败不重试。预览第二轮只重跑36项扩展并复用49项基线；clean候选r1及CSS修复后的r2各在新目录完整跑85项全过，资金指纹均前后一致。该事实不能删成“从未失败”。英文手机表的昵称截短/首屏横滑提示仍为P2；r1暗色价格说明首图保留，r2已通过实际修复确认。

生产账务结果仅为请求后只读一致性核对，不是独立前后钱包快照；请求关联须唯一匹配，reserved=0只限本测试Key/窗口。线上工作台截图包含真实客户数据，留私有不入Git；复用已登录管理员session，不冒称本轮重新提交登录表单或验证普通用户。PDF、搜索、图片、真实商户和四套安装器本轮没有重复测试。

生产证据分别见 [47项HTTP](../../lab/sub2api_e2e/ux4-production-readonly-20261011.json)、[真实SSE/WS](../../lab/sub2api_e2e/ux4-production-protocol-20261011.json)、[3笔后验账务](../../lab/sub2api_e2e/ux4-production-protocol-funding-20261011.json)、[浏览器范围](../../lab/sub2api_e2e/ux4-production-browser-20261011.json)、[既有健康采样](../../lab/sub2api_e2e/ux4-production-observation-20261011.json)。[后验账务审计源码](../../lab/sub2api_e2e/audit_ux4_protocol_funding.py) 仅供审查本机流程，含本站路径/权威约束，不能作为跨机器直接执行命令。采样确认代理连接17897，但本轮不重新认证全部出站节点路径或带宽。
