# UX4 独立复核

状态：已完成源码与收到的候选截图复核。后续暗色价格页截图实际发现了说明文字对比度回归，已修复并通过实际编译 CSS 回归测试；修复后浏览器复验由 root 的 final-r2 构建继续完成，不能用组件测试替代。其他复核范围未发现新增 P0/P1；这不是全站、生产支付或最终全部视觉验收结论。

范围：对比 `source` 的 HEAD `3f13f202765f2d9473664f3ec239f71d187adecf`，只读复核 DashboardView、RealYuWorkspaceSetup、RealYuUsageOverview、SubscriptionPlanCard、PaymentView，以及新增团队历史统计后端；另查看 root 提供的团队页面截图。模型导航由本 reviewer 实现，因此不将该部分称为独立实现审查；对应最终 40 项专属测试已通过（原 39 项加实际编译 CSS 回归 1 项）。未修改生产、未提交、未使用共享浏览器。按 root 授权只读查看生产 public settings 的单一开关值，并修改严格隔离的候选 fixture 开关，详见文末独立记录。

## 源码与数据语义

- Dashboard、WorkspaceSetup、PaymentView 的完整 `<script setup>` 与 HEAD 精确一致（仅统一换行后比较）。周额度、PAYGO 余额继续分开显示；过期/未来套餐不计入当前周额度，冲突套餐或未知结果显示未知，不相加、不替代成零。
- Dashboard 新增原生 `details/summary` 只折叠视觉呈现；PersonalEntitlements 仍挂载，immediate watcher 仍取数并向顶部周额度 emit loaded。代码没有以 open/v-if 阻断请求，因此折叠本身不影响额度来源。最终组件测试仍需包含折叠状态取数验证。
- Setup 只改模板组织和按钮视觉优先级。canonical key 查询、身份与 scope 校验、获取明文前重新校验、取消迟到请求、切换配置后清除旧命令、组件卸载清理，以及四种客户端/平台命令生成均未改变。初次显示命令是已有行为，复制事件仍显式触发。
- UsageOverview 只把默认周期由今日改为累计；API 参数、费用转换、服务器 token 总计、历史覆盖说明不变。累计与趋势指定日期是不同时间范围，应在最终截图中仍能明确分辨。
- PaymentView 只调整宽度、纵向间距及空目录高度。订单 payload、支付恢复、支付关闭门控、报价和购买流程未变。SubscriptionPlanCard 的 managed 商户价格仍直接取 price_cents/100，以 CNY 呈现；额度继续按 quota 换算；新的品牌色分支不改变 select payload 或按钮禁用条件。
- 品牌初始化没有发现 TDZ 问题：`computed(() => isRealYuLogo(appStore.siteLogo))` 延迟求值，render 前 appStore 已初始化。主入口先应用 `__APP_CONFIG__`，`/purchase` 守卫对未加载设置先执行 fetchPublicSettings；computed 对后到的 logo 更新仍响应。不以无配置/网络失败时的默认配色推断生产品牌失效。

## 团队统计后端

- `scope=team` 必须绑定唯一正整数 team_id；重复 scope/team_id、非团队范围带 team_id、team 加 user_id、actor_user_id/member_id 均拒绝。repository 二次验证相同边界。
- 每次读操作在同一个只读 RepeatableRead 事务内读取当前用户状态、角色与精确团队权限；普通成员不获得全队视图，必须 canManage 且团队未关闭。旧 role 10 的层级管理规则沿用现有 teamAccess，没有扩张规则。
- `historyArgs` 对团队使用 actor=0 以汇总所有成员，但前面已经授权，且共享 CTE 必须同时满足 scope=team 和精确 team_id。个人、self、platform 的原有选择条件保持。
- 分页 LIMIT/OFFSET、今日边界、趋势 bucket 时区/粒度的 SQL 位置参数均随 team_id 新参数同步后移；未发现遗漏调用。历史去重、原生 settled receipt 费用来源、token/cache 合计算法未修改。
- 已阅读新 PG 测试与 `team-scope-go-20261011T035346Z.log`：该日志中新增三项团队测试以及现有历史/HTTP 测试通过，包括跨团队并发隔离、权限撤销、关闭团队、成员拒绝、聚合与分页一致、无账务写入。此为审阅保存的执行证据，非本 reviewer 重跑。
- 原建议补跑现有 `TestRealYuTrendPG*` 三项已关闭：已读 `trend-pg-existing-20261011T040008Z.log`，day/hour exact sums、DST、scope/bounds/cancellation 三项均通过；`backend-team-scope-result-r2.json` 保留原始失败、补测日志与合计 15 项根测试通过记录。该项是审阅其他 agent 的执行证据。

## 待最终截图确认的短项

1. 普通用户工作台的 compact setup 在顶部后，1280×720 首屏是否仍同时提供配置、累计指标和有效趋势信息，手机是否有根页面溢出。
2. 六套餐与长标题情况下的价格对齐、字号层级、蓝色统一及禁用按钮可辨识性。
3. 可加 siteLogo 空值 → RealYu → 非 RealYu 的 plan card 响应式用例，覆盖初始化与后到配置分支；不是已发现的功能故障。

已实看 before 证据：01-before-workbench.jpg、02-before-plans.jpg、05-before-plan-catalog.jpg。前两张为合成 admin 空数据/配置未就绪基线，不能据此判定真实用户数据丢失。05 的原 fixture feature 是 JSON 字符串，被按后端换行文本约定呈现；root 已确认在隔离 fixture 中改为换行字符串，不需要修改真实渲染逻辑。

## 已实看的候选截图

- `10-after-member-workbench-en-dark.jpg`、`11b-after-member-workbench-zh-light-settled.jpg`：普通用户桌面中，配置区约 150px，高度集中；之后是周额度/钱包余额与累计费用、次数、token，首屏仍可看到趋势主体与两条 recent。周额度 ¥21、钱包 ¥0 分开，累计 5 次/¥19.25 与最近 7 天两条记录有明确范围控件，不误读为相同统计范围。蓝色与中性灰统一；黄色仅用于历史覆盖警示。没有看到新的视觉 P0/P1。
- `12-after-workbench-mobile-zh.jpg`：中文手机首屏，客户端/平台按钮按组自然换行，输入区和动作不出卡片边界，资金与统计两列可读。完整趋势位于折下；只有此首屏证据时不宣称整张移动页面已验收，也不以截图推断精确 DOM 宽度。
- `13-after-plans-zh-light.jpg`：三列套餐标题 → 价格 → 每周额度 → 功能 → 购买动作顺序清晰，关键金额明显优于旧版绿色价格挤在标题旁的结构，第一排全部动作可见。品牌蓝一致，功能列表已是两行正常文本。此截图包含管理者录入的英文 fixture 描述，不当作翻译漏项。

可选 P2：购买页顶部 PAYGO 仍为约 96px 的全宽单值条；可以在保持余额字号的前提下，把标题、余额、按钮合成约 64px 的一行。此为继续提高空间利用率的建议，不是支付功能故障。卡片完整英文/手机与模型导航截图仍待最终证据。

- `15-after-owner-team-calls-zh.jpg`：团队调用范围、团队选择、时间/模型过滤、三项总计与五条明细同屏，ID 和昵称有独立列。全队历史 5 条/¥17.50 与团队页本周 3 条/¥12.25 并非相同时间窗口；日期说明可见，不能只比较数字判定不一致。
- `16-after-team-hierarchy-zh.jpg`：队名、每周可用额度、重置/到期时间明确；成员按一人一行，ID 与昵称独立，金额优先级突出，操作集中在省略号。列表继续说明“限额不转入个人余额”，符合已有额度语义。
- `17c-after-team-mobile-en-settled.jpg`、`18-after-team-mobile-en-horizontal.jpg`：采用稳定重载截图，不使用 17/17b 过渡图。昵称在横滑后仍可见，费用数值可读；root 的 DOM 记录为根宽 382≤390，内部表横滑。仍有 P2：英文状态与昵称同一行时，“Former member”把昵称挤成 `E2E f...`；初始 ID+昵称+固定动作占了主要可视宽度，周限额只露部分。此结论不是“手机整表所有列完整可见”。建议手机昵称列稍窄、状态放下一行或提供明确横滑提示；不靠全面缩小字号解决。
- `19-after-plans-mobile-en.jpg`：英文手机套餐名称、价格/周期、每周/总额度、功能列表、禁用动作都可读，无截图可见的溢出；文本为正常两行功能，不再出现 JSON 字符串。该图仍是余额条压缩前的候选。
- `14-after-model-navigation-zh.jpg`：确认登录价格页 sidebar/header 保留。该图尚是 24px 标题与单 h1 修正前的候选，不能用来验证最终标题层级；模型导航实现仍需 parent 的最终构建/浏览器验收。

## 导航实现与隔离开关记录（非独立实现审查）

最终冻结 6 个源/测试文件：RealYuHomeView、ModelPlazaView、RealYuHomeModels，以及三个专属测试。登录 `/pricing` 用既有 AppLayout，home 保留公共壳；登录 direct `/model-plaza` 与 embedded query 都保留导航，匿名保留公共导航。AppLayout 提供唯一 h1，登录价格区降为 h2 且只在该范围收紧标题间距。

发现完整目录 link 原先无条件显示。只读 GET 实际结果：生产 `model_plaza_enabled=true`，候选 `false`；候选点击跳工作台是原有关闭守卫生效。现 link 只在明确 true 时出现，未知/false 不展示；没有绕过 router 或后端开关。`navigation-fix-result-r2.json` 记录指定 pnpm 9.15.9 执行的 3 文件 39/39 通过与冻结文件哈希。

为验收与生产相同开关状态，经 root 明确授权后，只对候选 `127.0.0.1:29481` 的 `/admin/settings` PUT `{model_plaza_enabled:true}`。脚本固定 PG 29490/realyu_singlecore_e2e、fixture prefix/DB身份、禁用且不可调度的上游账号，登录后验证返回 API admin 与 fixture 身份一致；数据库连接为只读。修改前后 API settings 仅该字段变化，余额、API key 用量、funding、订单、usage 指纹一致。`model-plaza-fixture-setting-receipt.json` 为成功回执；默认 Python 缺 psycopg 的首次工具失败保存在 `model-plaza-fixture-first-failure.json`，该失败发生在 import 阶段，零网络、零改写。使用既有 venv 后成功，没有安装依赖或修改生产。

## 最终候选截图与暗色样式修复（保留首次失败）

- 已实看 `20-final-model-catalog-mobile-en-dark.jpg`：登录模型目录保留应用 header/hamburger，开关启用后的原生目录正常渲染。原生目录内部宽表需要横滑，不把首屏仅见部分价格列写成整表全部可见。
- 已实看 `22-final-plans-desktop-zh-dark.jpg`：PAYGO 已压缩为约 64px 同行标签、余额和按钮，仍可清楚读数；桌面首屏能完整看到三张套餐的动作和下一排标题/价格。此前 PAYGO 留白的 P2 建议在桌面截图中关闭。
- 已实看 `21b-final-pricing-desktop-zh-dark.jpg`：sidebar/header、单一 h1 和 24px 模型标题均正确，但说明文字仍呈深灰。root 的实际 computed style 为 `rgb(107,114,128)`；不能把整体导航通过误写为全部视觉通过。
- 根因：Vue scoped 样式里的 `:global(.dark) .realyu-pricing-in-app` 编译后没有按预期定位价格容器。已改为 `:global(.dark .realyu-pricing-in-app)`，仅影响暗色登录价格区域，不改首页样式或模型价格数据。
- 回归测试调用 Vue 的 `compileStyle` 编译真实 SFC 的 scoped CSS，把产物加入 DOM，再验证暗色规则匹配价格容器、不匹配公共首页，以及明暗切换的 `--home-muted` 实际计算值。旧选择器在 12:26:10 明确失败（1 failed / 12 passed），保留 `pricing-dark-css-red.log`；修复后 12:28:35 三文件共 40/40 通过，保留 `pricing-dark-css-green.log`。不是只检查源码字符串是否存在。
- `navigation-fix-result-r3.json` 记录修复后的文件哈希和执行结果；r2 与首次候选产物保留。新的浏览器截图尚待 root final-r2 验证，此处不提前判定通过。
- 团队手机宽表 P2 继续保留：固定身份和操作列保证操作上下文，但首屏不足以同时完整呈现所有财务列，需要横滑说明。按 root 本轮范围决定不继续调整列宽或字号。

## 2026-10-11 12:35 北京时间：final-r2 浏览器复验追加

已实际查看 `24-final-r2-pricing-en-dark.jpg`，并读取 `pricing-dark-final-r2-dom.json`。英文暗色登录价格页的说明、模型描述与输入标签恢复为可读灰色；DOM 三项均为 `rgb(172, 172, 172)`，已不再是首次失败的 `rgb(107, 114, 128)`。左侧应用导航与顶部 header 保留，页面路径为 `/pricing`；DOM 仅有一个 h1 `Model pricing`，模型区标题为 H2，navigation=true、theme=dark。图中模型价格仍按美元/百万 token 展示。本次实际截图与 DOM 证据共同关闭上文的暗色选择器回归，以及该页面 final-r2 浏览器待验项；不覆盖未提供的其他屏幕、语言、宽表列或真实支付交易。

root 另报告已用 CUA 核验普通成员直达团队调用深链接被拒绝、无团队数据及团队调用 tab；这是 root 的执行结果，本 reviewer 本次没有操作浏览器，也未将该项记作自行执行的验收。此前团队手机横滑 P2 继续保留。

本文结论仅针对隔离候选及收到的证据。发布 UAC 仍在等待，本次没有修改生产、没有确认生产上线。最终 source、preparer 和部署 plan pins 均未改动；只将本文件同步至交接目录的 `lab/sub2api_e2e/ux4-independent-review-20261011.md`。
