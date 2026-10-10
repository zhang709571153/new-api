# RealYu UX 恢复与验收交接（2026-10-11 候选）

**最终候选补充：** r5 已冻结在 `f98d4b6`，完整前端892项/82文件和最终HTTP51/51通过；真实浏览器已闭环复验双向快速语言导航、disabled中英翻译和窄屏菜单。下文的r4待验描述属于此前冻结点，完整最终证据见[UX2验收](UX2-RELEASE-20261011.md)。本补充不表示生产已更新或搜索已经收录。

本轮恢复原有操作规则、历史调用连续性和公开站点体验，继续使用 Sub2API 单核心。审查基线为原生源码 `93022254b98d9cdfbc36db8d7ef6007365a89b53` 之后的候选改动；最终源码提交、补丁及二进制 SHA 由发布记录补齐。本文件不是上线回执。

本次更新已纳入 r1–r3 的隔离 HTTP 与真实 Codex 内置浏览器记录，以及两个浏览器缺陷的候选修复。r4 构建及修复后的真实浏览器复验由主流程补记；**本轮仍未部署，308 历史归档未上生产**。

前一版 `realyu-singlecore-v0.2.15-20261010-ux` 的生产状态和既有验收见 [UX-NATIVE-RELEASE-20261010.md](UX-NATIVE-RELEASE-20261010.md)。本轮代码、单元测试、隔离 PostgreSQL 测试与前一版的公网结果不能合并宣称为“新版生产全部通过”。

## 需求依据与修订优先级

基础清单见 [UX-PARITY-RECOVERY-20261010.md](UX-PARITY-RECOVERY-20261010.md)。本轮补充直接核对 S01、S05 原始用户消息、旧冻结源码和当前候选。后来的明确修订优先；不恢复已取消的欢迎赠款、不恢复随意建队或跨资金范围回退。

只保留下列安全摘录和定位，不附会话全文：

| 依据 | 原始要求及本轮采用的含义 |
| --- | --- |
| S01，`01a0cde7-1581-7d81-bfe5-8f5ae06440ed`，09-23 分段第 2279 行 | “一把key访问所有gpt的模型”；普通用户不应面对多把 Key 的管理和技术入口。指每个使用范围一把明确的默认身份，不是删除已经在用的其他凭据。 |
| S01 同分段第 2340 行；S05 第 679 行 | Key 不只展示一次；保留可显示、隐藏、复制及手动选择命令的路径。本轮按最新恢复要求默认显示当前绑定命令，凭据仅留组件内存；截图和日志必须遮蔽。 |
| S01 第 5634 行；S05 `01a0e5c5-f940-7b92-87fc-f2186e1282d4` 第 5140 行 | 首页恢复模型介绍、价格和接入文档；Windows/macOS，Codex/WorkBuddy 按实际操作分步说明。 |
| S05 第 57 行及后续 rc8 修订 | 个人和团队独立 Key，owner 也分开；使用现有有效 Key，不静默跨池消费。 |
| S05 第 1951 行 | 团队成员累计费用、Token、可用额度可排序，ID 与昵称帮助辨认实际调用者。 |
| S01 09-26 续段第 10、169 行；S05 后续生命周期修订 | 团队邀请长期有效并手工轮换；个人资金不随入队合并；owner 不进入别的队，购买/管理员授予后建队。 |
| S01 术语分支 `01a0db76…` 第 2553 行；本轮重申 | 中文思考程度“中”、首次响应时间、默认分组、标准、百万 Token、每分钟调用数（RPM）、每分钟 Token 数（TPM）、生成速度（Token/秒）、逐步输出。只改显示，不改协议值。 |
| 当前根会话 `01a11fe1-9ac2-7080-ab0c-2a8e91c2a528` 后续明确要求 | 团队 owner 不应看到加入邀请输入；非管理员隐藏 API Key 管理；管理员导航剪枝；移动端与明暗主题可用；中英文公开内容和真实计价；迁移前后调用累计连续。 |

旧实现主要依据是 `web/src/features/workspace/my-team.tsx`、`personal-key.tsx`、`member-key.tsx`、`team-members-table.tsx`，以及 `model/workspace.go` 的 `WorkspacePersonalKey`/成员 `token_id` 绑定。旧菜单隐藏不是权限校验的替代。

## 本轮实现与仍需验收的对应关系

下表源码路径相对于恢复出的原生源码目录；开发机为 `C:\srv\realyu-singlecore-dev\source`。Git 交付为固定上游、完整补丁及部署脚本，不包含开发机私有状态。

| 项目 | 当前候选实现 | 验证状态与边界 |
| --- | --- | --- |
| owner/member 的加入入口 | `frontend/src/views/user/RealYuTeamsView.vue` 仅成功加载且没有未关闭团队时显示加入表单；加载、失败和已入队不是“无团队”。服务端同时拒绝 owner 加入本人或其他团队。 | 组件 21 项及真实 PG 通过；候选 HTTP 验证 owner 两种拒绝，CUA 已验 owner 无加入入口、成员调用筛选及中英桌面/390px 团队页。不是全部角色的整站交叉矩阵通过。 |
| 每范围一个 canonical Key | `realyu_setup_key_repo.go` 优先原 `workspace_personal_keys`/成员 source token 明确映射；新注册仅接受唯一受控 scope。拒绝歧义、错 actor/payer/team，不按最新 ID、名称或渠道组猜测。 | 真实 PG 覆盖旧绑定优先、无余额仍可配置、坏映射、真实离队再入队和审计约束。既有有效 Key 不删除、不轮换。 |
| 安全配置读取 | `GET /api/v1/keys/realyu-setup?scope=personal\|team` 只返回元数据且 `no-store`；明文仍走本人 `/:id` 校验。无绑定 404 有固定 reason，409/网络失败不能伪装空集。 | service/handler、PG 通过；候选 HTTP 已验 owner/member 的 personal/team canonical 与 owner 不能读取成员 Key。CUA 已验个人/团队使用不同绑定及团队命令复制；四个桌面安装器执行不在此证明范围。 |
| 普通用户不再造额外 Key | 托管普通用户通用 `POST /keys` 在服务端 403；管理员和 stock 模式保留原行为。注册与团队入队内部事务继续按受控流程创建默认 Key。菜单与路由同步隐藏普通用户 Key 管理。 | 单测覆盖 managed/admin/stock；候选 HTTP 验 owner/member/former/role10 额外建 Key 被拒绝；CUA 普通用户 Key 路由重定向通过。隐藏入口不是仅有的安全控制。 |
| 一键配置简化 | `RealYuWorkspaceSetup.vue` 使用 canonical 元数据，个人/团队及客户端/系统分段选择；没有 Key 下拉选择。切换、退出和失败清除旧命令，复制/显示前重新核对所有权、scope、状态和有效期。 | 组件通过；CUA 已记录个人掩码复制、团队绑定区别、团队复制及 WorkBuddy/macOS 命令，未把凭据写入报告。文档四种客户端/系统页签通过；这些不等于四个桌面安装器安装和真实应用接入完成。 |
| 全历史调用及累计 | 308 的四张独立事实/receipt 表，`realyu_usage_history_repo.go` 合并原团队、补充个人/未知 actor 事实与 native settled 记录；`RealYuUsageOverview` 和 `RealYuUsageView` 使用同一查询口径。 | 隔离 PG/导入器通过；308 及正式归档尚需本轮发布执行。未归档时明确显示累计尚不完整。原账本及 native usage 不被补写。 |
| 归属与缓存口径 | self 只按实际 actor；platform 要求实时原生 admin，旧 role10 不提权；旧已删除 actor 仅平台可见。缓存缺失为 null，已知零仍为 0，旧输入包含缓存时不再重复相加。 | PG 覆盖 self/platform、离队历史、分页、日期、1 quota、未结算排除和缓存语义；handler 拒绝伪造主体过滤。 |
| 客户费用与模型价格 | 客户金额继续 `customerMoney` 按配置汇率一次显示 CNY；历史 API 返回精确 USD decimal。公开模型价格直接取配置的个人标准组及原生定价计划，乘组倍率和百万单位一次。 | 候选 HTTP 已验 USD 公开字段白名单、合成客户 CNY 精确换算及财务指纹不变；CUA 已验模型选择、滑杆和 USD 估算。缺价格显示未知。不是逐个生产价格对账或商户成交证明。 |
| 首页与文档 | `RealYuHomeView`、`RealYuDocsView` 和 public 组件恢复原品牌气氛、四步教程、四种平台命令指引、手动 WorkBuddy 参数；保留 Sub2API/new-api 归属和版权。 | public 六个前端测试文件共 70 项通过；CUA 已有中英首页/价格/文档桌面与 390px 记录、文档四页签及英文刷新保持。官方 Codex 下载/说明地址已核对；真实下载、安装与客户端启动另验。 |
| 搜索可读内容 | `realyu_public_seo.go` 为首页/价格/文档输出可抓取正文；价格与 JSON 同一来源。canonical、hreflang、OpenGraph、JSON-LD、sitemap、robots、llms.txt；私有 SPA 页 noindex。 | Go service/web、客户端 metadata 测试通过；CUA 已记录公开 canonical/lang 和私有 noindex/无 canonical。快速切语言后导航缺陷已修并有 RED/GREEN，r4 真浏览器复验待补；不代表收录，也不代替全部前进/后退组合。 |
| 管理员导航 | `AppSidebar.vue` 把平台管理与个人区分组折叠；常用客户、团队、供应账号、价格、商品/订单优先，高级配置和诊断保留；未删除后端功能或放宽管理员权限。 | CUA 已记录管理员中英桌面/390px、明暗主题、供应账号、主体筛选及原生诊断入口；HTTP 拒绝匿名/member 管理员 API。disabled 原始翻译键已补中英，r4 复验待补；不宣称每个折叠状态和高级配置都已点击。 |
| 简洁工作台和主题 | `AppLayout`/品牌 CSS 使用中性背景、紧凑内容、圆角容器和右上主题按钮；累计统计与当前系统图表分开。个人 `DashboardView.vue` 已恢复独立 CNY 可用余额卡，冻结金额单列，不合并团队资金。 | CUA 已有成员中英桌面/390px、管理员与调用记录的明暗/窄屏记录；已验 viewport 无横向溢出。余额零值/缺值的专门浏览器断言未单列，不能由页面快照推定全部边界通过。 |
| 个人原生诊断 | `/usage/diagnostics` 复用原生用户 `UsageView.vue`，合并历史页按 self/platform 分别链接个人/管理员诊断；个人路由要求登录，平台路由另要求管理员。 | 主流程新增 r3 CUA 已实际从 member `/usage` 点击到诊断页，看到本人两条记录及首次响应时间/生成速度；实际下载并读取 CSV，只有两个合成本人模型、没有 owner 模型，通过。与管理员诊断记录分开，结果待主流程并入浏览器汇总。 |
| 可理解的中文术语 | `realyuUsageDisplay.ts` 复用共享 display helper；原生详情、近期请求、统计、排行榜、筛选与 CSV 文字保持一致；Token 缩写可查看完整值。 | 真正 createI18n 的 12 个文件 / 111 项通过，`vue-tsc -b` 成功；不改变 model、service_tier、stream、reasoning_effort 等请求值。 |

## 历史归档边界

只读预检报告记录封存源 31,295 条消费事实：原有团队 24,520；可归属个人 6,703；保留 actor 的未知范围 10；已删除 actor 58；旧已隔离的已删除团队 4。用户查询按本人 actor 过滤，这些数不是任何普通用户的可见全量。最后 4 条继续运维隔离，不重新分配给付款人。

`deploy/realyu/import_usage_history.py` 默认只读，验证封存源 SHA 和 SQLite `mode=ro&immutable=1`；只提取计数、模型、归属 ID、时间及白名单缓存字段。正式应用要求独立核过的 manifest、相同 installation/source SHA 和明确目标；同一事务只写四张新增表，统一 advisory lock，重复导入校验原事实，不静默覆盖。旧团队事实和隔离表仅核对；钱包、权益、成员周使用、Key 计数、native usage 和结算表不写入。

详细合同在候选 `deploy/realyu/USAGE-HISTORY.md`。正式归档是否完成只看正确目标库的 receipt 和本轮实际回执。不得重新跑客户迁移器、重放扣费、恢复旧 SQLite，或用发布前备份覆盖上线后的交易。

## 本轮已确认测试与首错

私有原始日志位于开发机 runtime，仅列文件名供接手者核对；日志正文、浏览器 profile、客户逐行历史和凭据不入 Git。下列计数有重叠，禁止相加当作去重后的总覆盖数。

| 验证 | 已确认结果 | 原始证据文件名 |
| --- | --- | --- |
| 团队加入 UI | 21 项通过 | `team-join-ui-first-20261010.log` |
| canonical/Create service + handler | 12 个根测试，含子场景 20 项通过 | `canonical-key-unit-first-20261011.log` |
| canonical/团队/key presentation 真 PG | 14 个根测试，含子场景 20 项通过；loopback 29490 隔离库 | `canonical-key-pg-r2-20261011.log` |
| 真实 i18n 用量术语回归 | 12 个文件、111 项通过；后续 typecheck exit 0 | `usage-terminology-real-i18n-20261011.log`、`usage-terminology-typecheck-r2-20261011.log` |
| 首页/文档/价格与配置组件 | 六个文件、70 项通过 | `public-faithful-20261011-tests-r3.log` |
| 服务端公开价格/SEO | service/web 包通过 | `ux-public-backend-r1.log` |
| 历史归档并发与回滚 | Python 12 项通过 | `history-import-final-concurrency.log` |
| 历史查询真实 PG + handler | 最新日志结果通过；精确根数由主验证汇总记录 | `history-pg-final.log` |
| 隔离候选 HTTP / 权限 / 财务不变 | 51/51 通过；首次 50/51 的公开价格 fixture 缺失单独保留，补明确合成定价后通过 | [usage-history-http-20261011.json](../../lab/sub2api_e2e/usage-history-http-20261011.json) |
| 选定前端完整回归 | 81 个文件、887 项通过；包括真实中英 JIT，不是翻译条目数或浏览器流程数 | `ux2-20261011T004522-frontend-tests.log` |
| 真实 CUA r1–r3 | 45 条记录（34 条页面记录、11 条流程记录），包含首错，不能写成 45/45 PASS；另有 3 条早期复制检查 | `ux2-browser-20261011/browser-results-before-r4.json`，截图同目录 |
| disabled 中英文缺键修复 | 首次两项失败；补缺失词条后状态/locale 相关 5 个文件、19 项通过，不改状态业务逻辑 | `account-disabled-locale-20261011-red.log`、`account-disabled-locale-20261011-green.log` |
| 切语言后的导航竞态 | 首次 4 FAIL / 1 PASS；真实 Vue Router 两个切换方向等 5/5 GREEN；相关 16 文件、104 项通过，typecheck exit 0 | `locale-navigation-20261011-red.log`、`locale-navigation-20261011-green.log`、`locale-navigation-20261011-related.log`、`locale-navigation-20261011-typecheck.log` |

保留的首错包括：canonical PG 测试把两个 SQL 命令放进一个带参 Exec（测试 fixture 已拆开）；Vitest 缺少生产 Vite 已有的 `__INTLIFY_JIT_COMPILATION__` 定义（主流程补齐后恢复真正 i18n 测试通过）；工作台有一个未使用变量导致 typecheck 失败（已修复后 exit 0）。首错文件不被重跑覆盖；没有用 mock 结果冒充最终真实 locale 验证。

浏览器记录对应隔离 `http://127.0.0.1:29481` 的 `realyu-singlecore-v0.2.15-20261011-ux2` r1–r3，使用 Codex 内置浏览器，覆盖 1440×1000、390×844，并补测 360×800 调用页。r3 SHA 为 `2e9021c288c2294f34a14f038b40c5efa3652e6f2ca7dc2f689e4fdf63b22f5b`，**不是最终 r4 或生产 SHA**。记录覆盖匿名公开内容、成员、owner 与管理员的具体流程，不代表六类角色全部语言/尺寸组合都完成。

主流程在该 45 条快照之后新增 r3 CUA 验收：member 从 `/usage` 点击 Detailed diagnostics 到 `/usage/diagnostics`，实际导出并读取 CSV 两行，仅含合成本人模型 `e2e-member-model` 与 `E2E_PRIVATE_PERSONAL`，无 owner 模型；首次响应时间与生成速度等原生字段可见。此项已通过，原始下载和浏览器证据由主流程合并，暂不改写上述 45 条基线计数。

真实 UI 首错保留三项：r2 主按钮透明导致白底白字，r3 已恢复蓝底白字并保留实际色值；r3 快速切语言后点击文档仍留价格页；r3 上游账号 disabled 显示原始翻译键。后两项候选源码已修：`setLocale` 在新语言暴露前发起 URL 更新，被后续导航取消时不补发 replace；en/zh 账户 locale 补 `disabled`。两者单测 RED/GREEN 已通过，r4 CUA 结果仍待主流程填入，不能用单测覆盖首次浏览器失败。

## 独立审查发现与待验项

截至本轮源码审查，未发现新的确定越权或资金写入 P0。以下是候选功能问题，不能描述成已经发生的现网事故：

1. **钱包卡已恢复，专项边界仍区分**：工作台已重新显示本人可用余额及冻结金额，使用统一 CNY helper；缺失值显示 `—`，不合并团队余额。浏览器已有成员工作台中英桌面/窄屏记录，但未单列零值、缺值和请求失败全链路的专门断言。
2. **个人原生诊断入口与 CSV 已验**：`/usage/diagnostics` 与 self 历史页链接已在源码接通；`/admin/usage/diagnostics` 仍限管理员。主流程新增 r3 member 实际点击、诊断字段与 CSV 两行仅本人数据均通过；它是独立新增证据，不能借此声称所有诊断筛选/导出组合已验。
3. **英文刷新已验证，快速语言切换另行修复**：CUA `english-public-reload-preserved` 通过，公开导航保留 `lang`。另一个首错是切语言后立即点击文档被迟发 replace 覆盖，候选已按上述 RED/GREEN 修复，等待 r4 真浏览器复验；全部前进/后退及重定向组合没有因此自动验完。
4. **移动端已有真实窄屏覆盖**：现有 CUA 已验中英公开页、成员工作台、owner 团队与管理员/调用页的具体桌面及 390px/360px 组合、主题和无视口溢出。剩余边界是完整折叠/高级菜单组合与 Safari、iOS/Android 实机，不能笼统写成“手机全待验”，也不能把内置浏览器缩放当作实机兼容证明。
5. **明细/趋势深链不是已完整恢复**：当前新历史页只从 URL 读取 `user_id`，日期、模型、scope 和页码没有完整深链往返；团队趋势和“费用/请求/Token × 多图形”矩阵未在本轮全部恢复。

完整发布验收按候选 `deploy/realyu/UX-E2E-MATRIX.md`：匿名、普通用户、成员、owner、旧 role10、真正 admin；中文/英文各 1440×1000 与 390×844；登录、scope、复制/显隐、错误/空态、历史分页过滤、金额和菜单/主题。已执行的 r1–r3 记录保留各自版本和结果，未执行的脚本或剩余组合仍标 `PREPARED_NOT_RUN`；不把所有组合统一改成通过。

本轮尚未完成最终 r4 CUA 复验/新版生产发布、真实商户支付、托管充值、团队首购/续费/升级/退款、管理员周权益授予编辑、完整日志 IP 隐私核验或四种客户端实际安装。已有 r1–r3 CUA 不能替代这些边界，也不能借菜单剪枝或丰富原生界面宣称业务能力已实现。

## 交付与发布补记

- root 统一提交原生完整补丁、对应测试、部署文档和脱敏验收结果。私有封存源、数据库备份、原始会话、历史逐行数据、Key、OAuth、商户信息、签名密钥和 SG 配置不得进入 Git。
- 308 只做新增事实 schema；本轮发布入口必须明确迁移计划、固定校验和、同库恢复边界，不标成“无数据库迁移”。兼容回退保留四张新表和 receipt，不删除档案。
- 新版全站验收完成后才填写下面实际结果。旧 UX 版既有 19/19 公网和 12/12 注册结果不是下面的新版结果。

| 发布项目 | 本文编写时状态 |
| --- | --- |
| 最终源码提交 / patch SHA / binary SHA | 待主流程冻结填写 |
| 308 生产迁移与校验 | 未在本轮审查中执行 |
| 封存历史正式导入 receipt | 未在本轮审查中执行 |
| 新版隔离 HTTP | 已有 51/51 的独立候选记录；首次 fixture 失败保留，非生产结果 |
| 新版真实浏览器 | r1–r3 已有 45 条记录及截图，含首错；最终 r4 的语言/disabled 修复复验待补 |
| 新版生产 ACTIVE 与最小公网复核 | 待发布记录 |
