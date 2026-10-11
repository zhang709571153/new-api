# UX3 独立合同审查

审查对象：`C:\srv\realyu-singlecore-dev\source`，基线 `f98d4b6c56f0f56a5d08dbd351c8a4af6ab6a3b7` 之后的工作区改动。审查日期：2026-10-11。

这是独立代码/合同审查，未操作生产、数据库、支付或模型调用，未使用 root 正在操作的浏览器。初次审查只读；后续 root 明确授权后，仅对 PaymentView 的发单中守卫与对应 deferred 测试作了修复，未操作 Git。需求依据为本目录 `requirements-matrix.redacted.md` 和 `usage-contract-audit.md`，实现结论来自直接检查 diff、新增文件及调用边界，而不是把作者的测试 PASS 当作代码正确性的证明。

## 结论

在所查改动中，**未发现未解决 P0/P1**，未发现新增跨用户/跨团队数据泄露、充值守卫绕过、重复入账或历史 Token 双计问题。发现两项具体 P2，调用记录 URL 往返与充值深链现均已修复并复核；深链追加的发单中竞态也已修复。此结论不是发布/生产验收，也不表示原先 35 项 UX 需求已全部恢复。

## 具体发现

### F1 — P2，已修复：浏览器前进回到新筛选时仍保留旧记录

原位置：`frontend/src/views/user/RealYuUsageView.vue`，`syncURL` 与 route watch（现第 114–124 行）。

触发：在页面本地依次应用筛选 A、B，再浏览器后退到 A、前进到 B。原 `writtenRoute` 永久保留 B，前进 B 时 watcher 误判为本次本地写路由而 return，页面实际仍显示 A 的筛选与调用记录，URL 已为 B。管理员用户筛选也会出现该错位。

root 已修复为一次消费的标记：同地址不保留标记；一次 watcher 命中后清空；其他路由变更也清空。已独立重新读取修复代码和新增 `reloads both backward and forward history after two locally applied filters` 测试。该测试确实覆盖 A→B→back A→forward B，而非仅模拟一次外部 query 赋值。本 reviewer 未单独执行该套测试；root 报告 targeted 19/19 PASS，执行证据由主流程保存。

### F2 — P2，已修复：工作台充值深链与发单中状态

位置：`frontend/src/views/user/DashboardView.vue:9`；`frontend/src/views/user/PaymentView.vue:590–596,1285`。

新增链接为 `/purchase?tab=balance`，但购买页只读取 `tab=subscription`。当用户具有充值权限且返回真实可用 CNY 方式时，RealYu 首次加载的 methods 为空，使 immediate tabs watcher 先选择 subscription；等 checkout 返回后 subscription 仍为有效 tab，watcher 保持原选择。由工作台点击“充值”会显示套餐 tab，用户还要再点余额卡上的充值。

root 已统一链接为 `tab=recharge`，由 `applyRechargeQuery` 在 checkout 完成、选择阶段、RealYu且充值真实可用时切换。初次进入与已挂载页面 query 变化均可消费；关闭时缺货，已进入 paying 状态不会被重置。独立检查了对应初次深链、挂载后变化、managed gate关闭、已恢复付款状态四类测试。

复核时另发现：创建订单 POST 尚未返回的阶段仍为 select，仅 submitting=true，原修复会因 query 变化清空 selectedPlan 并跳到充值。经 root 明确分配，此 reviewer 在 PaymentView 添加 submitting 守卫，并在 PaymentView.spec 增加真实 deferred createOrder 的回归。先在旧代码得到 AmountInput 意外出现的稳定断言失败；修复后全部42个PaymentView测试通过。该补丁只限制视图切换，不增加订单/账务写路径，不改变金额或付款请求。

证据均保留在本目录：`f2-pending-order-regression-before-fix.log`（真实代码回归失败）、`f2-payment-targeted-after-fix.log`（42/42 PASS）。最先一次命令误中 PATH 内 Codex fallback pnpm，其自动依赖检查因 no TTY 中止，尚未执行测试；原始输出保留为 `f2-pending-order-first-failure.log`，没有允许 purge 或修改依赖。随后全部使用显式 corepack pnpm@9.15.9 运行现有依赖。

## 追加复核：工作台个人额度与首屏顺序

- `DashboardView.vue:12–13,45–53` 独立显示本周个人套餐可用与按量余额，不显示二者相加值，更不混入团队资金。钱包为0但本周权益有效时仍显示真实周可用；多份同时有效的冲突结果或非法quota显示破折号，不自行求和。
- `RealYuPersonalEntitlements.vue:49–57` 每次开始读取立即发 loaded=null；仅在 actor、quota_per_usd=500000、每行user_id和team_id=0验证后发真实summary。过期请求/换账号被AbortSignal及actor检查阻断；错误路径不留下旧summary供顶卡使用。
- 顶卡与compact权益列表都排除未来尚未开始和已经过期的active套餐；无有效个人套餐且请求成功时显示0，未知或unmanaged时显示破折号。这是读取时的快照，组件仍未增加跨时间边界自动刷新，见下方新鲜度边界。
- DOM顺序现为顶部额度/用量卡、统一历史趋势＋最近调用、个人权益详情＋接入配置。由代码可确认高频用量内容前置；具体首屏密度、留白和手机端美观度仍以独立截图/浏览器验收为准，本 reviewer 未占用浏览器。
- 独立运行 `RealYuWorkbench.spec.ts` 与 `RealYuPersonalEntitlements.spec.ts` 共11/11通过，涵盖钱包0/周额度非0、禁止溢出时不相加、未来/过期、冲突/读取失败、actor/team/scale异常和迟到响应。证据 `workbench-contract-independent-tests.log`。新日期边界测试使用真实时钟的now+1秒，建议主流程后续改固定时钟或较长future距离以减少慢CI偶发风险；本次执行无失败。

## 核心合同检查

| 检查项 | 独立检查结果与边界 |
| --- | --- |
| 历史列表/累计/趋势的数据源 | 新 `realyu_usage_trend_repo.go` 复用 `realyuSelfUsageCTE` 与同一个 `realyuHistoryAggregate`，没有把旧记录插入 usage_logs，没有写入钱包或资金表。过滤后再聚合，不是求当前页之和。 |
| 事务/时间 | 趋势在只读 REPEATABLE READ 中取 stats、coverage、series 和 transaction_timestamp。日/小时桶显式传 tz，start 包含、end 排除；按日最多366天、按小时最多31天。Go 校验与 SQL 参数化存在。只返回有数据的桶；当前前端固定北京时间补零，未把任意 DST 的 24 小时补点算法用于其他时区。 |
| 角色 | 新趋势复用 `historyActor`，从数据库重新读 active/未删除用户角色，platform 只允许 native admin。self/personal 不接受他人 user_id。没有从历史 payer、前端角色或 team owner 推导平台权限。 |
| 团队汇总 | `member_summary` 由完整 filtered 集合按 member_id 分组，再由前端按费用/请求/Token等排序；不依赖当前详情分页。每人主行按稳定 ID，昵称相同不会合并。模型维度仍在展开层。成员查询被收敛到本人，owner/admin 权限延续现有 teamAccess。 |
| 昵称归属 | history item 的昵称 JOIN 同时使用该条事实的 team_id 和 actor_user_id；个人/未知记录回退 users.username（展示昵称，不是 immutable login_name），deleted actor 返回 NULL。成员表主键(team_id,user_id)保证 join 不放大记录；历史详情主键 source_log_id 同样不放大旧行。 |
| 缓存与 Token | 团队旧记录改读308详情，未知 cache 保留 NULL；聚合提供 known_requests。total 使用 input_total 优先、Anthropic exclusive 加缓存、其他reported不再加缓存，与 history 公式一致。native保持独立缓存相加。新增字段在前端已以服务端 total_tokens 显示，不再自行通用求和。 |
| 金额 | 新趋势/列表返回 USD decimal string，展示仅调用 customerMoney 转 CNY一次；团队金额仍 cost_quota/500000。表格排序将成本转 Number 只用于次序，不参与记账。没有新资金写路径。 |
| PAYGO | 路由只特赦 RealYu `/purchase` 的读取；其它支付路由守卫保留。`BalanceRechargeAllowed`、服务端 CreateOrder 和 managed gate 未改。前端 availability 要求 payment_enabled、balance_disabled=false、实际可用CNY方式；createOrder/resume再次检查。关态不生成付款请求。 |
| 异步/错误 | 趋势与近期请求共享范围且两请求携带 AbortSignal，scope/范围变化清空旧结果并阻断过期写回；history和team同样取消前次请求。余额缺失显示破折号；checkout读取失败与确定缺货分开。 |
| 管理操作 | “…”仍使用原 nickname/cap/status API。成员 cap 仍仅限制共享资金，不创造额度；新弹窗转换仍是 customerAmountToQuota。移除仍确认，普通member无操作菜单，后端再次鉴权。 |
| 中英文 | 本次新增真实产品文案在 en/zh 的 realyuUx/realyuTeams 中对应；协议/币种字段保持不变。浏览器截图和手机端视觉效果属于另一独立审查/主流程 E2E，本审查不代替。 |

源代码差异检查 `git diff --check` 本 reviewer 独立执行，exit 0。检查过新增 PG/HTTP 测试的断言：跨旧新日界、UTC/北京、DST 23/25小时、范围限额、权限、nullable缓存、精确金额求和和 member_summary 跨详情分页。没有独立重跑生产或付费测试，也没有将源码单测当作实际线上已发布证明。

## 需求未完整恢复，非本轮新增回归

| 原需求 | 当前边界 / 后续交付不能误称已完成 |
| --- | --- |
| U01 工作台统一个人/团队资金视图 | 顶部已独立标出个人本周套餐可用与按量余额，接入组件可选Key资金scope；工作台仍没有由同一个个人/团队选择同步全部资金与统计的全局视图，不能称旧工作台完整等价。默认统计 self 含本人在团队的调用，资金卡明确仅个人。 |
| U26 团队用量深链 | 个人/平台记录页已有日期、模型、scope、页码URL往返；`RealYuTeamsView.vue` 仍用组件本地 selectedID/filter，团队/成员/日期/模型筛选与展开状态未完整URL化。 |
| U27 费用/请求/Token × 三图型 | 新统一历史趋势是费用/请求/Token × 折线/柱状，日/小时两种粒度。尚无旧完整第三图型/模型占比分布，不应宣称完整三图矩阵。 |
| 汇总/图表刷新一致 | 趋势区刷新同时刷新趋势和近期记录；上方 UsageOverview 和个人余额/权益仍各自读取、各自快照，未统一刷新，也没有跨午夜自动同步。需要用户全页刷新才能使所有卡片同次读取。当前没有修改交易数据，只是页面新鲜度边界。 |
| U12 外部PAYGO收款 | 余额/缺货入口已恢复，不等于商户支付、CNY收款→USD入账、回调幂等与退款真实验收。managed gate仍关闭；此处是刻意保留的既有边界。 |
| U14/U15/U17/U18 | managed 管理员授予/订阅编辑、团队新买/续费/升级/退款及原子建队仍是既有业务缺口。本轮 UI 不应以 native group subscription 冒充周资金。 |
| U23 管理员用户行真实订阅摘要 | `UsersView.vue:410` 仍展示 native subscriptions，而非managed周资金摘要；本次diff未完成这个旧要求。 |
| U33 日志IP隐私 | 未完整审计持久化 usage/error IP；新 read projection不增加这类字段，页面没有IP也不能证明存储未记录。 |
| 实机与外部商户 | Windows/macOS客户端、真实移动操作系统、真实支付收款不属于本次源码审查证据；仍需各自实测。 |

建议主流程在最终“需求完成度”中区分：已完成并本轮验收、已实现待候选/线上验收、保留且明确后续的业务缺口。尤其不要把“无 P0/P1 代码发现”翻译成“所有旧需求已100%完成”。
