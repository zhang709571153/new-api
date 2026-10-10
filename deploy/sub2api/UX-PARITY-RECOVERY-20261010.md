# RealYu 单核心前端 UX 恢复清单

审查日期：2026-10-10。用途：把用户历史需求、旧发布实现和当前 Sub2API 页面逐项对齐，作为后续开发与验收依据。本轮为只读研究；没有为此报告改生产、客户数据或前端源码。当前正在发布的 CowAgent/CNY/管理员直接访问热修复不等待本清单全部完成。

## 结论与“之前编译的前端去哪了”

旧前端没有丢失。**旧 React 工作台、购买、套餐管理、团队管理等 UX 确实曾经发布，并且冻结源码、构建和发布证据仍在。现在的缺口主要是切换到原生 Vue 前端时没有完成同等功能移植；另有品牌设置漏迁造成已实现的团队菜单被条件隐藏。** 这两件事要分开处理。

可核验的三份前端并不是同一份产物：

| 产物 | 已证实内容 | 证据 / 限制 |
| --- | --- | --- |
| 旧 New API 正式前端 | `realyu-provider-v3.9.2.22-20261008` 已正式发布；包含完整旧工作台与 10 月 8 日用户/团队/用量重构 | 旧 `lab/RELEASE-20261008-USAGE-ADMIN-REDESIGN.md`：程序 SHA256 `f729e2b8f2c631e1b804b7e6ef56397efbe76560c2e8fc6528f845c2eff4de20`，163 项前端、24 组三库、9 条隔离浏览器及正式域名只读验收。冻结目录 `C:/srv/realyu-team-funding-lab/.lab/usage-admin-redesign-20261008/source/web/dist/index.html` 本轮确认存在。 |
| 10 月 9 日 RealYu 前端 + Sub2API 驱动桥接候选 | 仍是 React/RSBuild，改渠道入口和成员接入准备状态，同时保留旧业务 UI | 本仓库 `deploy/sub2api/frontend-validation.json`：29 文件/346 测试、生产构建通过；`frontend-enrollment-validation.json`：10 文件/187 测试。该报告当时标明候选和浏览器边界，不能把编译成功理解成后来原生 UI 全面移植完成。 |
| 10 月 10 日原生 Sub2API 候选/单核心 | Vue/Vite，先移植品牌、用户名/昵称、模型展示规则、订阅错误状态，后补团队 API/UI 与安装资源兼容 | `lab/sub2api_e2e/frontend-reuse-20261010.json` 明确最初只做两项展示移植，且 `includes_migrated_team_funding_display=false`。其完整重建 265 项测试证明这份限定范围的候选可构建，不代表旧全部 UX 已恢复。 |

构建链也确实不同：旧 `web/package.json` 使用 React/RSBuild；原生 `frontend/package.json` 使用 Vue/Vite。原生 `frontend/vite.config.ts` 输出到 `backend/internal/web/dist`，`backend/internal/web/embed_on.go` 将其嵌入新原生程序。把旧 `web/dist` 原样放进去会继续请求旧 `/api/workspace`、`/api/subscription` 等合同，无法获得单核心目标。正确恢复方式是复用旧交互与纯工具函数，接原生 API 或明确的 RealYu 业务接口；不是重新启用旧后端，也不是挂一个空按钮。

基准：本仓库初始冻结提交 `93357b11481e7dd7c1811870c455968151d23474`，当前只读审查 HEAD `6a516bc2ddf1543974832d4d5fb356238bcf59da`。`SOURCE-MANIFEST.json` 收录 2,973 文件，声明旧基准 v3.9.2.22、`git_ancestry_included=false`；不能为每项历史需求编造独立可 cherry-pick 的提交。原生上游基准 `f2669c8cf62555cd92389b3f55920e9e6e7c6ff2 / v0.2.15`，本轮审查原生本地 HEAD `a184b4a63385e71347b80c2b59f34bce4605acfd`，另有同轮热修复工作区改动。

## 证据与口径

本报告读取了原始用户消息、历史完成回复、发布报告和当前源码。历史用户名、客户金额、Key、商户信息与会话全文不复制入报告。日期使用北京时间；同一线程内后来的修订优先。下表 `O:` 指本交付仓库旧 `web/src` / Go 源码；`N:` 指按 `singlecore-source.json` 重建后的原生源码。现状是**源码审查状态**，生产状态必须由新版本实际页面验收补充，不能用报告代替。

| 来源代号 | 日期、真实 session | 主要原始要求 |
| --- | --- | --- |
| S01 | 09-23～26，`01a0cde7-1581-7d81-bfe5-8f5ae06440ed` | 面向普通用户，关注额度和一把 Key；精简技术入口、保留注册；一键配置、RealYu 品牌；模型 USD、消费 CNY；团队生命周期和持久邀请。 |
| S02 | 09-26，`01a0dc2a-cbd1-7ee3-8c29-c4ba86eda048` | 独立可选昵称、注册/账号页编辑、团队管理员可改昵称；“选填”放 placeholder；移除记录 IP 功能；统一大写 Y。 |
| S03 | 09-26～27，`01a0de63-b426-7470-9916-a91061ee307d` | PAYGO/包月、管理员可调预设；后修订为“按量余额”、简洁购买按钮、销售开关关闭显示缺货；导航“套餐”。 |
| S04 | 09-27，`01a0e34c-5253-7951-9f33-208e3ade0fe6` | 取消充值最低额/倍数；个人套餐先扣、不足再扣个人 PAYGO；余额显示要与真正可调用额度一致。 |
| S05 | 09-28～30，`01a0e5c5-f940-7b92-87fc-f2186e1282d4` | owner 也分个人/团队 Key；按费用/Token/余额排序；团队只有周订阅；个人和团队可各有一份订阅；购买建队、只升不降、owner 到期前不解散/不入别队；紧凑工作台/折叠菜单。 |
| S06 | 10-05，`01a109f3-54c1-7280-b6a6-34d62ca1dab0` | 用户/客户用量显示订阅、无则明确无订阅；取消新欢迎额度发放、保留已发权益，复用邀请机制。 |
| S07 | 10-06～07，`01a111fa-57f6-71d3-9c36-b880ce16e609` | WorkBuddy 与 Codex 一样简洁，Windows/macOS 命令；不放冗余说明。 |
| S08 | 10-07，`01a11515-49ab-7c63-b625-bec158fa534a` | 订阅加载失败要修复，不可混为无订阅；不能用错误时的零值蒙混。 |
| S09 | 10-07，`01a1172b-4697-7423-a9a6-8e96e16a31f9`，初始 turn `01a1172f-be78-7b43-9ce1-a255c44b9332` | 删除选中命令，保留复制；GPT Image 2.5 展示名、中文模型目录与固定推荐排序。 |
| S10 | 10-08，`01a11aa6-1c5e-7172-b8b2-9fcbfb8fecf8` | owner 查团队实际成员调用；仅自己/我的团队；离队历史保留、个人消费排除；趋势与明细联动。 |
| S11 | 10-08，`01a11af2-4ad0-7a92-939f-9ee6e85b86f8` | 客户用量只读；管理放用户页；ID/排序/周剩余/订阅到期；管理员管理全队，owner 仅本队；3 指标×3 图表与顶栏布局。 |
| S12 | 10-09～10，当前根 session `01a11fe1-9ac2-7080-ab0c-2a8e91c2a528` | 单核心迁移、用户名登录和昵称、保留原生计费统计；移除确认门；最新追加隐藏版本/兑换/优惠码、工作台/购买/套餐/团队 UX 恢复、注册即 Key。最新 UX 请求在 turn `01a125b1-b789-7f43-b768-7e5da74c3799`。 |

## 功能要求矩阵

优先级是恢复需求及不可退化验收的等级，不表示每一行都是现存缺陷：P0 涉及正常接入或资金隔离；P1 为用户已明确要求的关键经营/管理流程；P2 为不阻断调用的展示与效率。`已实现` 不等于本轮重新完成公网验收。历史 superseded 项在下一节单列，不按旧要求恢复错误行为。

| ID / 优先级 | 日期 / 来源、原始需求摘要 | 旧状态与可复用实现 | 当前原生状态 / 需要恢复的差距 |
| --- | --- | --- | --- |
| U01 P1 | S01/S12：面向普通用户，仪表盘改工作台，突出可用额度与接入 | 已发布；O:`features/dashboard/components/overview/workspace-overview.tsx` | 原生 Dashboard 有余额/用量/图表；不是旧工作台。名称修改正由主任务处理；应保留原生统计，加接入卡与范围选择，避免再造统计内核。 |
| U02 P1 | S01/S07/S09/S12：Codex/WorkBuddy 一键配置；Windows/macOS；只留复制 | 已发布；O:`overview/codex-guide.tsx`、`setup-command.ts`、安装资源 | `/downloads/realyu/*`、诊断和 Key 检查已移植，当前 Dashboard 无配置卡。复用原命令模板/资产/平台 tabs；选定 scope 后取该 Key，不错误回退另一资金池。 |
| U03 P1 | S01/S12：新用户无需手工理解复杂 Key 设置，注册即一把可用身份 Key | 已有后端实现：O:`model/user.go:725,798` 在创建事务中调用 `workspacePersonalToken(..., true)`；O:`model/workspace.go:126` 幂等取/建默认 Key | N:`auth_service.go` 注册目前只建用户/身份，未接默认 Key provision。应注册事务内或可靠幂等补偿生成个人 Key；并发/重试仅一把，失败不能返回伪成功。不自动送新资金。 |
| U04 P0 | S05：个人/团队两把 Key；owner 同样分离；团队每人独立身份 | rc8 已发布分池；O:`WorkspaceScope`、`personal-key`、`member-key`、`use-api-credential` | 迁入 `realyu_key_scopes` 已供鉴权/计费；N:`KeysView.vue` 无 team/personal 范围标签或切换。需补自有 Key 的 scope DTO/筛选/复制；不得把“分组 group”伪装成团队。 |
| U05 P0 | S05：配置只验身份，零额度也能配置；实际推理才验资金 | 1.4.3 起已修复；O:安装器 key-check 合同 | 已有 native key-check；此次 CowAgent unlimited 负历史字段热修复已通过 PG/中间件，等待发布后真实恢复验证。复用同一合同，不在新配置卡检查余额后阻止复制。 |
| U06 P1 | S01/S02/S12：用户名登录、邮箱不强制、昵称独立选填 | 已发布；O:注册/profile/团队昵称 | 原生用户名注册与 legacy 登录、昵称保存/显示已移植。需继续回归原用户名与昵称互不改登录标识；表单保持简短。 |
| U07 P1 | S02：团队管理员可改本团队成员昵称 | 已发布；O:`workspace/member-settings.tsx`、`model/workspace.go` | N:`RealYuTeamsView.vue` 能显示 nickname，`UpdateMember` 仅 cap/status；没有昵称修改。需按 actor/team 授权补编辑，不能放宽到改他队用户。 |
| U08 P1 | S01/S02：RealYu / RealYu API，统一 logo/颜色，不改上游版权 | 已发布旧品牌；资源保留 | 原生已嵌入 `/brand/realyu-wordmark.png`，但 r3 初次设置为空使品牌和团队菜单未触发。主任务在本轮新版后以原生 settings API 修复并清 HTML 缓存；不强制启动覆盖。 |
| U09 P2 | S12：截图版本 badge/update popover 隐藏 | 最新新增要求；不是丢失旧代码 | N:`AppSidebar.vue` / `VersionBadge.vue`，主任务同轮修改中。隐藏用户入口即可，后台版本与运维 status 保留。 |
| U10 P2 | S01/S12：兑换码/优惠码先隐藏 | 旧普通用户流程曾精简；最新明确隐藏 | 原生用户 `/redeem`、管理员兑换/优惠菜单、注册优惠输入均需一并查。仅隐藏界面，不删历史订单或兑换审计；不要顺手取消团队邀请码。 |
| U11 P1 | S03/S12：恢复套餐/购买页与按钮，关闭销售显示缺货 | 已发布；O:`features/wallet`、`subscription-plans-card.tsx` | 原生 `/purchase`=`PaymentView.vue` 已存在，但受 payment flag；还有 `/admin/orders/plans`。这不是只重命名就完成：原生分组订阅与 RealYu 个人/团队周资金不相同，必须核对履约写入。未完成履约的产品不可开放真支付。 |
| U12 P1 | S03/S04：按量余额充值人民币，支持分到元输入；金额边界清晰 | 已发布修订为 ¥0.01～¥10,000；O:wallet/计费 handlers | 原生支付支持充值；CNY 客户显示与输入转换同轮处理中。支付金额币种与账户 USD 入账必须明确，一次换算；不能仅把美元符号换成人民币。 |
| U13 P1 | S03/S11/S12：管理员套餐预设可调价/周额度/上下架，已购快照不变 | 已发布；O:`features/subscriptions`、`components/cny-plan-fields.tsx`、`lib/plan-form.ts` | 原生已有 PlanEditDialog/AdminPaymentPlansView，优先扩展/适配现成页；尚无等价 RealYu team/personal 商品与周资金履约关联。不能把修改商品预设追溯更新已购订阅。 |
| U14 P1 | S03/S11：管理员手工开通个人/团队订阅、只需团队名称 | v3.9.2.22 已发布；O:`features/users`、`subscriptions/components/dialogs/user-subscriptions-dialog.tsx` | native 原生分组订阅分配存在；迁入 RealYu `realyu_funding_subscriptions` 未接对应管理员写 API/表单。需明确独立管理员权限、原子建队/Key/资金，复用原生 UsersView 对话框位置。 |
| U15 P1 | S05/S11：管理员改周额度及到期，不重置用量/周窗口/购买快照 | 已发布；O:`workspace/weekly-usage-form.tsx`、`subscription-expiry-form.tsx` | 当前团队 API 只改成员 cap；没有修改团队订阅额度/到期。需精确实例 ID、原值并发校验、审计与在途保护，owner 不能改总资金或延长到期。 |
| U16 P0 | S04/S05：个人先套餐后 PAYGO；团队只套餐、不动个人钱包 | rc8 以后已发布；旧 funding 测试可作为合同 | 原生 RealYu funding middleware/repository 已接迁入资金并有账务验收。页面恢复必须读同一权威余额，不把全部 native subscriptions 简单相加，也不把团队剩余并进个人余额。 |
| U17 P1 | S05：同一人可有个人与团队各一份订阅；各维度只升不降 | 已发布 `WEEKLY-SUBSCRIPTIONS-20260928.md` | 当前消费读取保留 scope；新销售/升级没有完整同等桥接。购物页需先选个人/团队，冲突明确显示；不能以 UI 单选代替服务端唯一性。 |
| U18 P1 | S05：付款/管理员授予后建队；owner 不可入别队；活跃期不可解散 | 已发布；O:`CreateWorkspaceTeamTx` 与周订阅业务规则 | 当前 Join 拒 owner；没有公开任意建队，符合约束。当前 owner Leave **一律拒绝**，未恢复订阅到期后的允许解散；普通成员离队已有。恢复时应检查时间而非额度是否耗尽。 |
| U19 P1 | S05/S11：成员管理 cap、暂停/恢复/移除，站点管理员全部队、owner 本队 | 已发布；O:`my-team.tsx`、`supplier-teams.tsx`、team-management-dialog | 当前 native 团队 API/UI 已实现这些基础操作；普通成员只能自己的明细，role10保留限定团队权限，不提升为nativeadmin。此项保留已有实现，补原生管理员入口/回归。 |
| U20 P1 | S01/S05：永久邀请、手动轮换；成员独立 Key；退出保留历史 | 已发布；O:workspace invite/reveal/rotate | 当前 native 邀请/join/leave/历史隔离已实现；邀请原文不保存，只可新生成后取回。Key 展示/轮换入口尚未达到旧 UX；不能每次刷新自动新邀请。 |
| U21 P1 | S05/S11：团队成员费用、Token、可用余额等双向排序，ID 在昵称前 | 已发布；O:`team-members-table.tsx`、`__tests__/team-sorting.test.tsx` | 当前 RealYuTeamsView 成员行按服务端原序，无交互排序，且 nickname 后才 ID。可薄移植数字排序/未知值/稳定 tie-break；当前接口只有本周字段，累计费用/Token需明确定义并接聚合数据。 |
| U22 P1 | S05/S11：管理操作进“…”或对话框，客户用量纯展示 | v3.9.1.1 与 v3.9.2.22 已发布；O:`workspace/usage.tsx`、`supplier-teams.tsx`、`team-management-dialog.tsx` | 当前团队页把 cap 输入与状态按钮直接铺每行，基础能用但不等价；暂无旧客户用量总览。复用原生表格/弹窗/UsersView 操作，不重新造通用用户后台。 |
| U23 P1 | S06/S08/S11：用户/客户用量显示订阅；有错、无订阅、加载三态分开 | 已发布；O:`subscription-summary.tsx`、`user-subscription-cell.tsx` | native用户列表能批量带原生订阅，SubscriptionsView错误重试已修；迁入 RealYu 周订阅不是原生 group subscription，当前用户列表仍可能显示无订阅。需要准确摘要 DTO，不复制旧逐行 N+1 查询。 |
| U24 P1 | S05/S11：用户只看本周剩余、刷新、到期，不展示月总量/累计费用 | 已发布；O:`subscription-balance.tsx`、workspace-overview | 当前团队页周剩余/刷新/到期已有；个人工作台仍仅原生余额/原生订阅。需接个人周资金，概览合计只涵盖可实际消费部分。商品页可保留购买权益总量。 |
| U25 P1 | S10/S11：owner 看团队实际调用成员、模型、时间、Token、费用；离队历史不丢 | 旧团队明细已发布；O:`features/usage-logs`、query-params | native `/realyu/teams/:id/usage` 已实现真实历史+新消费、分页/统计一致、排除个人和他队；成员只能自己。不要绕过这个端点直接按 payer 查原生全局日志。 |
| U26 P1 | S10/S11：仅自己/我的团队，成员查看明细/趋势携带筛选可往返刷新 | 已发布；O:`usage-logs/lib/query-params.ts`、dashboard/models | native团队页有 member/model/date 筛选但本地组件状态，无旧主明细 scope switch、深链、趋势联动。可复用参数解析和边界测试；直接复用原生图表与请求详情结构。 |
| U27 P2 | S11：费用/请求/Token × 柱状/分布/趋势，合计一致 | 已发布且真实浏览器9流程；O:`dashboard/lib/usage-charts.ts` | 原生已有丰富统计与图表，应优先满足可用口径，不照搬旧图表栈。缺团队维度趋势时接同一过滤数据；时间补零、top15+其他、不平滑这些纯函数可移植。 |
| U28 P2 | S05/S11：客户用量按团队/个人分组可折叠；一行顶栏；窄屏换行 | 已发布；O:workspace usage/supplier-teams、dashboard布局 | 当前原生通用用户/日志表功能丰富，但客户分组总览与旧桌面工具栏细节未恢复；先补经营需要的摘要/入口，再做折叠/排序布局。 |
| U29 P2 | S01/S05：接入文档、模型目录、状态复用左栏；常用导航不过载 | v3.9.1.1 已发布；旧sidebar config与路由可作验收合同 | native AppLayout 已统一多数页面；须将新增接入/购买/团队入口放同一层级；公开页面保持访客可读，不复制独立导航壳。 |
| U30 P2 | S09：中文模型名、固定推荐顺序、Image 2.5仅显示名 | v3.9.2.15 已发布；O:`lib/model-catalog.ts` | 已薄移植到 N:`utils/realyuModelCatalog.ts` +modelPlaza，仅RealYu品牌生效、未知模型稳定回退；不改上游model ID/计价。生产brand值修正后需补浏览器目录验收。 |
| U31 P1 | S01/S12：模型单价USD，余额/用量/消费/团队额度CNY，固定7 | 旧已实现；O:currency/usage detail | 本轮 native customerMoney 与实际CNY表单测试中；不能混入旧销售倍率或旧图价。新模型真实计费按当前原生供给配置，不从历史静态文案恢复。 |
| U32 P2 | S01：推理强度、默认分组、标准档、首字/Token单位合适中文 | 旧已发布；O:usage detail/i18n | native已有中文大部分字段，但团队新明细直接model ID、部分技术文案仍原生。逐字段核对，保留源值在详情/tooltip；不要隐藏排障必需请求ID。 |
| U33 P1 | S02：不记录使用/错误日志IP，移除开关 | 旧 session 明确已完成 | native系统存在独立的请求/审计/风控日志机制，尚未在本次 UX 审查完成隐私等价验证。需单独核对持久化字段与代理访问日志，不可因旧页面开关消失就声称不记录。 |
| U34 P2 | S06：取消欢迎额度新发放，保留已送；邀请采用成熟原生方案 | 10-05历史要求已发布 | native有邀请码/返利基础设施，但旧邀请历史与奖励语义需独立核验。恢复注册Key不能顺手恢复9月的“新注册送¥5”旧流程；不要重复发奖励。 |
| U35 P1 | S12：管理员登录直接用，取消确认阻断；仍严格登录/角色权限 | 本次明确授权取消；旧 gate非客户业务需求 | frontend/backend gate移除已完成定向权限回归，待新版公网验收；没有伪造接受记录。匿名/普通成员必须仍被401/403/跳转拦截。 |

## 关键历史修订：不要恢复过期行为

- 09-26 曾允许直接建队、owner 退出自动解散；09-28 已明确改为购买/管理员授予建队、有效订阅期不解散。仅恢复前者会放开错误业务流程。
- 09-27 曾讨论“月总额＋周上限”；09-28 已确定用户界面隐藏月额度、按周且不结转；10-08进一步明确只显示本周剩余。管理员日期/周额度编辑与购买权益快照仍需保留。
- 09-27充值曾限制 ¥50 起/倍数；同日后续明确取消。旧套餐价格/倍率也经过多次迭代，本清单不把历史报价重新设为现价。
- 09-27的新用户欢迎赠额在10-05已明确取消；旧已发额度保留。自动 Key 是身份便捷性，不是自动送资金。
- 09-28成员无团队额度时曾讨论个人 Key 回退，用户明确要求仍用团队 Key。新工作台可以让用户主动切 scope，但不得静默跨池消费。
- 旧说明中所有者最早复用个人 Key 的特例已在 rc8 取消，两个独立 Key 是最终合同。
- 原生数据统计、计次、计Token及模型供应管理优先复用；用户本次允许原生能力，不要求复刻旧全部图表视觉和技术后台。

## 推荐实施顺序与复用边界

1. **接入闭环**：默认个人 Key 幂等 provision → personal/team scope DTO → 工作台Key与Codex/WorkBuddy命令卡 → 安装只读鉴权。保留旧复制、隐藏、重试、轮换确认、身份切换取消迟到结果合同。已有安装资产无需重做。
2. **成员管理效率**：复用原生表格和弹窗，补排序、ID/昵称、昵称编辑、成员操作菜单；已有team权限/消费API保留。增加显式查看明细/趋势链接，参数可刷新返回。
3. **经营流程**：管理员准确显示迁入个人/团队周订阅，补手动授予/周额度/到期修改；再接套餐商品与付费履约。先复用原生 Users/Plan/Order 页面，只有 scope、团队生成与周资金必要合同做适配。
4. **展示补齐**：图表维度、中文技术值、客户折叠总览等按原生能力简化。旧 React组件不整包塞进Vue；纯 TS、资产、文案、失败测试可直接复用并保留版权。

当前旧支付通知兼容层 `realyu_legacy_payment.go` 明确只是持久接收 `pending_review`，并不等于履约成功。不能据此开启旧购买按钮或向用户显示“到账”；原生新订单成功与迁入资金的对应关系需真正确认。

可直接复用的旧 Git blobs（均可从本仓库读取）：

| 文件 | blob |
| --- | --- |
| `web/src/features/dashboard/components/overview/workspace-overview.tsx` | `80aa806e1931b0baf4dc175362d8867889e8088f` |
| `web/src/features/dashboard/components/overview/setup-command.ts` | `0ce7be074b278d9de9a3820834aca9924b77f546` |
| `web/src/features/workspace/personal-key.tsx` | `e4089cf32620c2e367ef831cfa5eecd01e63fc2a` |
| `web/src/features/workspace/team-members-table.tsx` | `4297ab8cffeff4ddf88bad2d45eeddb1d5e91755` |
| `web/src/features/workspace/my-team.tsx` | `9abd34d099e3ad1814b403f1b9b55c95ab5464c4` |
| `web/src/features/wallet/components/subscription-plans-card.tsx` | `91282fd9fd66f004c08174e0afc6df90a2940c75` |
| `web/src/features/subscriptions/index.tsx` | `67312719e64ea0f9b1dc359f20fc0819b6595644` |

## 恢复完成时必须实际验收的路径

- 注册新的隔离用户名/可选昵称 → 自动获得且只获得一把个人 Key → 零余额仍可获取配置命令 → 一次正常身份检查；失败/重试不增多把默认Key。
- 同一 synthetic owner 的个人与团队 Key 分别可见、复制/轮换不交叉；个人扣费不动团队，团队扣费不动钱包；成员仍无跨团队/管理员权限。
- Codex/WorkBuddy、Windows/macOS 命令与现有下载签名/路径一致；复制不含另一scope Key，日志不保存凭据。页面验证不能替代真实桌面安装验收。
- 团队有同名成员、退出成员、个人/团队混合请求；各排序数值而非字符串；分页总计与过滤明细一致；明细/趋势往返保留ID与日期。
- 管理员周额度/到期编辑使用精确实例，旧弹窗冲突明确报错；用量、已付权益、历史账本及在途结算不重置；owner不能自己扩资金。
- 购买关闭时显示明确不可购买状态，接口同样拒绝；开放后的支付必须验证真实回调一次履约、重复通知不重复入账、订单金额/币种与目标scope一致。此报告未执行真实支付。
- admin/user入口、兑换/优惠隐藏范围、移动端/桌面导航、主题、键盘焦点、错误/空数据状态；匿名与普通用户不能直接访问管理员API。
- 模型参考单价仍为USD；客户金额按配置换算CNY。输入14元只提交2美元/1,000,000 quota，回填不能漂移；长decimal字符串不会显示“—”。

## 审查范围与未完成证据

已定位并阅读全文或相关原始需求的核心 UX 线程见S01～S12，另读取旧周订阅、工作台、10月8日团队请求/管理发布文档及当前源码。这是本次可定位历史的系统清单，不声称所有未归档/不可访问聊天、所有历史截图内容均已穷尽。用户最新要求可直接作为恢复标准，不需要重复确认。

没有把旧报告的通过数量充当新版通过数量；没有把编译产物存在等同已发布；没有把native管理员原生订阅冒充迁入RealYu周订阅；没有改生产设置或新发消费请求。新版 CowAgent/15 Key、公网品牌、直接管理入口和人民币浏览器验收由对应独立结果文件记录，完成后在 DEVLOG/UPDATELOG 引用，而不覆盖本报告的首次差距证据。
