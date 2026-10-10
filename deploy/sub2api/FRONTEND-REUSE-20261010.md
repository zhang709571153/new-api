# RealYu 前端历史需求复用审查 — 2026-10-10

范围：读取历史聊天、已交付 New API 源码与 Sub2API v0.2.15 候选；在隔离候选实现两项不依赖资金迁移接口的前端改动。本文不表示生产部署或真实浏览器验收完成。

## 来源与可复现基准

- 旧系统可交付源码：`https://github.com/zhang709571153/new-api`，分支 `codex/sub2api-handoff-20261009`，基准提交 **93357b11481e7dd7c1811870c455968151d23474**。
- 该提交整体收录 `realyu-provider-v3.9.2.22-20261008` 冻结源码；`SOURCE-MANIFEST.json` 明确 `git_ancestry_included=false`。很多 10 月需求在旧开发树中没有独立提交，不能编造一组可直接 cherry-pick 的历史提交。
- 新系统基准：`Wei-Shaw/sub2api` **f2669c8cf62555cd92389b3f55920e9e6e7c6ff2 / v0.2.15**。
- New API 前端是 React，Sub2API 是 Vue。页面组件不可直接 cherry-pick；纯 TypeScript 展示规则、图片资源、文案及验收合同可复用，页面/API 绑定需薄移植。
- 本次只读以下真实聊天，不向其他聊天发送消息；历史用户消息仅提取需求，不复制其中凭据或运行配置。

## 历史需求 → 旧实现 → 新系统现状

| 历史来源（thread ID） | 用户明确要求 | 已交付源码/记录 | Sub2API 当前情况与处理 |
| --- | --- | --- | --- |
| `01a11af2-4ad0-7a92-939f-9ee6e85b86f8` | 客户用量纯展示；操作放用户页；ID 在昵称前，费用/Token/余额可排序；成员跳请求和趋势；月订阅只显示本周剩余；团队负责人不得自行增总额/延长到期；3 指标 × 3 图表；桌面顶栏一行 | `lab/RELEASE-20261008-USAGE-ADMIN-REDESIGN.md`；`web/src/features/dashboard/lib/usage-charts.ts`、workspace/usage 与 admin-users | 原生 UsersView 已有 ID/昵称/余额排序、订阅列及管理入口，不能再造一套通用用户 CRUD。团队和迁入周账本尚无完整只读/管理 API，需等后端合同，禁止把原生分组当成团队，或把月总额当本周剩余。 |
| `01a11aa6-1c5e-7172-b8b2-9fcbfb8fecf8` | 团队 owner 能切“仅自己/我的团队”，按实际成员/模型/日期看明细；保留离队历史、排除个人请求 | `web/src/features/usage-logs`，`web/src/features/usage-logs/lib/query-params.ts`；旧团队 usage 路由 | 原生 `/admin/usage?user_id=...` 已支持精确用户筛选，原生订阅表用户链接已接入。但普通团队 owner 的授权过滤、历史成员归属不是普通前端筛选能保证，必须按 actor/payer/team 的新资金合同接接口后移植。 |
| `01a1172b-4697-7423-a9a6-8e96e16a31f9`（初始前端需求 turn `01a1172f-be78-7b43-9ce1-a255c44b9332`） | 删除“选中命令”，保留复制；图片展示为 GPT Image 2.5；模型中文界面；顺序 6.1 Sol → 6 Astra → 6 Sol → 6 Luna → 5.6 Sol/Terra/Luna → 5.5 | `web/src/lib/model-catalog.ts`（blob `7c73aec3d04fded28ee49a21a172e58969aec7a5`）；home-models、pricing；`lab/maintenance/frontend-client.md` 的 v3.9.2.15 记录 | 原生模型广场已有完整中英文、原生价格与分组逻辑，但按官方输出价排序，且直接显示模型 ID。本轮复用展示名/推荐顺序，保持模型 ID、对象、价格公式和非 RealYu 站点行为。当前原生首页没有旧模型卡片；不增加一组硬编码价格。 |
| `01a111fa-57f6-71d3-9c36-b880ce16e609` | WorkBuddy 配置简洁、与 Codex 一致；Windows/macOS 命令；不要冗余解释 | `web/src/features/dashboard/components/overview/setup-command.ts`（blob `0ce7be074b278d9de9a3820834aca9924b77f546`）；codex-guide；`lab/RELEASE-20261007-WORKBUDDY.md` | 纯命令模板、资源与文案可复用，但命令仍引用旧下载/诊断/密钥合同。需等新单核提供并验证全部下载/身份/原生客户端依赖；本轮不挂一个看似可用却转到旧账本的入口。桌面与 Mac 历史未完成项继续保留。 |
| `01a109f3-54c1-7280-b6a6-34d62ca1dab0` | 管理员用户/客户用量显示月订阅，无则“无订阅”；调整订阅收进“…” | `web/src/features/workspace/subscription-summary.tsx`（blob `b609d02d44966539a48b430b721119c2cdfe29f2`）；`lab/REVIEW-20261005-SUBSCRIPTION-INVITES.md` | 原生管理用户列表已通过一次列表请求带 `include_subscriptions=true`，显示无订阅与分组标签；无需搬旧逐行订阅请求。原生 `/subscriptions` 首次请求失败却落入“无有效订阅”分支，本轮修复错误/空结果混淆。 |
| `01a11515-49ab-7c63-b625-bec158fa534a` | 订阅加载成功/失败交错要稳定修复，不能把失败隐藏 | 旧 subscription API 四路队列、15 秒超时、有限瞬时重试；`lab/RELEASE-20261007-SUBSCRIPTION-LOADING.md` | 旧故障由旧桥接队列和 N+1 查询共同触发；Sub 原生用户列表已批量加载，不复制旧 4 并发设施。保留“未知不是无订阅”的合同：当前订阅页加持续错误态、用户重试、请求单飞、卸载取消/迟到结果忽略。 |

## 本轮实际移植

1. **RealYu 模型展示规则**
   - `frontend/src/utils/realyuModelCatalog.ts` 直接复制旧纯 TS 实现，保留 New API/QuantumNous 版权与许可文字。
   - 仅现有 `site_logo=/brand/realyu-wordmark.png`（允许资源版本 query）启用；其他站点保留原生模型名称和输出价排序。
   - 已知模型按历史推荐顺序，未知模型按原先纯函数的数值自然名称顺序回退；token/按图/按次不同计量类别的原生分区顺序继续保留。
   - 只将精确 `gpt-image-2` 的展示文本变为 `GPT Image 2.5`；原始 ID 保留在对象和 title，检索兼容原 ID/展示名，不对新 ID 或带前后缀模型猜测别名。
   - 价格、倍率、官方价缺失语义、上游 ID 和后台模型配置完全沿用原生实现。
   - 文件：`components/modelPlaza/PlazaModelPricingTable.vue`、`PlazaGroupSection.vue`、`ModelPlazaContent.vue`，及聚焦组件测试。

2. **原生订阅页错误与空状态分离**
   - 首次未完成请求显示加载；成功空数组才显示“无有效订阅”；请求失败持续显示错误和重试按钮。
   - 连点重试只允许一个进行中的请求；离页取消原生 Axios 请求并忽略迟到结果，不弹离页错误。
   - `api/subscriptions.ts` 的现有 `getMySubscriptions` 增加可选 AbortSignal，路径与结果类型不变，旧调用无须变更。
   - 读取的是原生订阅端点；这不表示已接入迁入的 RealYu 团队资金或每周套餐。没有新增账户、余额和支付写入操作。
   - 文件：`views/user/SubscriptionsView.vue`、`api/subscriptions.ts`、`views/user/__tests__/SubscriptionsView.loading.spec.ts`。

## 后续价值排序

| 优先级 | 项目 | 可复用内容 | 前置条件 |
| --- | --- | --- | --- |
| P1 | 迁入用户/团队/套餐真实摘要与本周剩余 | 旧表格字段、SubscriptionSummary 的状态合同、精确 ID 深链；保留原生 UsersView 排序/批量加载 | 只读 API 明确 actor/payer/team、实例周窗口与失效/未来期；统一新资金权限 |
| P1 | 团队 owner 请求/趋势联动 | query-params、时间范围和成员 ID 合同、旧同名/离队/混合消费用例 | 服务端团队授权过滤、历史资金归属、分页与统计口径一致；不能仅前端过滤 |
| P1 | Codex/WorkBuddy 双平台简洁配置 | setup-command、命令显示/复制、平台 tabs、已交付安装资产 | 新单核下载/诊断端点、持久 Key、零额度只读鉴权、旧客户端真实 E2E 都可用 |
| P2 | 费用/请求/Token × 3 视图 | `usage-charts.ts` 的 top15+其他、真实时间补零、不平滑、总额对账用例（blob `4d88f68d7036f07c07fd46436e0643f942ed45de`） | 对接原生 USD/Token 统计字段；无数据不补假费用，不能只改货币符号 |
| P2 | 模型广场显式重试与管理界面布局小修 | 当前原生 loading/error/empty 基础与统一按钮 | 未在本轮扩大范围；逐页处理，不复制旧桥接 4 路队列 |

## 验证与边界

本次合成组件测试首次执行 **4 文件、58/58 通过，其中新增 14 项**。验证模型顺序、精确别名、原 ID 搜索、其他品牌不变、计价输入不变，以及原生订阅首次失败、重试后有数据/空集、重复失败、连点单飞、卸载取消与迟到结果。`vue-tsc -b`、10 个改动文件的 ESLint、`git diff --check` 通过。命令和逐文件摘要见[脱敏结果](../../lab/sub2api_e2e/frontend-reuse-20261010.json)。

最终已用全新目录从固定上游加补丁完成 Windows 重建，26 个前端测试文件共 265 项通过，
另有 3 项 i18n 检查及 Go 定向回归、嵌入式程序构建通过。

真实浏览器已验证原生订阅成功空集、隔离候选停止后的“加载订阅失败”、恢复后手动重试回到
正确空状态；[错误画面](../../lab/sub2api_e2e/singlecore-subscription-error-20261010.jpg)与
[恢复画面](../../lab/sub2api_e2e/singlecore-subscription-recovered-20261010.jpg)只含合成账号。
模型目录排序/搜索仍为组件验收；全部浏览器/设备、真实上游模型、真实商户和生产发布均未验收。
