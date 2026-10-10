# 购买、套餐与注册开户衔接审计（2026-10-10）

## 核实范围与状态

本轮只读检查当前原生 PostgreSQL、已封存的最终 S1 SQLite 与实际源码，没有开启商户/注册、创建真实订单或修改生产数据。原生核心已 ACTIVE；“数据已迁入”“已有订阅可消费”不等于“原生新购已接入”。文中 `backend/` 是补丁应用后的 Sub2API 源码路径，`model/`、`controller/` 是本交接仓旧 RealYu 源码。

截至 2026-10-10 21:17（北京时间）的只读聚合：

| 项目 | 最终旧 S1 | 当前原生生产 |
|---|---:|---:|
| 套餐目录总数 / 在售数 | 7 / 6 | 0 / 0 |
| 在售个人 / 团队套餐 | 6 / 0 | 0 / 0 |
| 配置完整且按旧开关启用的支付提供商 | 1（Epay） | 0 |
| 原生 payment_orders | 不适用 | 0 |
| 原生 user_subscriptions | 原结构不能直接同表比较 | 0 |
| 托管权益实例 / 有效实例 | 已按最终状态迁入 | 10 / 7 |
| 旧 billing_orders pending | 12 | 仍归档，非已核实付款 |

旧 6 个在售模板均为个人 CNY、28 天。`controller/billing_checkout.go:20` 直接查询 `subscription_plans` 并按 `funding_scope` 分成个人/团队；只读检查未发现另一个 `TeamPlanCatalog` 或 options 中的独立团队售卖目录。团队售卖能力存在于旧代码，不能据此捏造在售团队模板。

当前原生 `payment_enabled` 未配置，服务按 false 拒绝新下单；注册两个开关也未配置。旧 storefront 开启。提供商“配置完整”仅说明旧配置可用性条件满足，**不等于本轮真实商户验收**。脱敏聚合证据保存在私有 runtime 的 `purchase-enrollment-readonly-20261010T211707.json` 和 `purchase-old-provider-readonly-20261010T211810.json`，没有输出凭据或用户身份。

## 已确认的差距

### P0：不能只打开原生订阅付款

`backend/internal/service/payment_fulfillment.go:540` 的 `doSub` 调用原生 `assignOrExtendSubscription`；`subscription_service.go:411` 创建的是 `user_subscriptions`。当前受管 Key 的资金选择器 `backend/internal/repository/realyu_funding_repo.go:438`、`:448` 只读取 `realyu_funding_subscriptions`。两者没有自动同步。

因此若直接启用 stock 订阅付款，付款成功记录不构成客户当前消费所用的权益：无钱包余额可能拒绝，有钱包余额可能继续扣钱包。当前支付关闭且原生订单为零，本次没有发现由这一路径造成的已付款损失；这是启用购买前必须补的适配，不是当前请求转发故障。

还存在准入差异：`payment_order.go:139` 要求售卖计划的 group 为 subscription 类型；当前生产两个 group 均是 standard。不能为迎合这个检查就转换全部路由分组，改变现有客户定价/授权/资金语义。

### P1：客户套餐页面的数据来源也不同

`backend/internal/handler/subscription_handler.go:46`、`:92` 和 `service/subscription_service.go:783` 使用 stock 订阅查询。托管权益已经迁入并由扣费模块消费，但没有对应 stock 实例。原生“我的订阅”需要受管权益的只读展示适配，不能用复制第二套可消费权益来填页面。

旧模板和订单保存于 `realyu_legacy_records`；`deploy/sub2api/singlecore_migrate.py:355` 导入的是现有权益期末状态，`:366` 归档目录/订单，不是把新购商品自动上线。

### P1：注册自动个人 Key 尚未恢复

旧 `model/user.go:699`、`:774` 在用户创建事务内调用 `workspacePersonalToken`；`model/workspace.go:123` 幂等复用合适个人 Key 或创建一把默认 Key。

原生 `backend/internal/service/auth_service.go:298` 创建用户，`:318` 后处理只记来源/登录与默认权益，没有自动生成 Key。现有 `repository/api_key_repo.go:44` 已能在创建 Key 时同事务登记个人资金 scope，`:88` 调用 `enrollRealYuPersonalKey`；这保证“创建了 Key 后归属正确”，不等于“注册时自动创建”。当前迁入用户没有未绑定 Key 的聚合异常，不能用这个事实替代新注册流程验收。

### 最新历史要求已取消新欢迎金

最终旧 S1：用户名/密码注册开启，EmailVerification 和 Turnstile 未配置（旧代码默认 false），`QuotaForNewUser` 未配置（默认 0）。旧站另有欢迎活动：启用、每人 ¥5、每日总预算 ¥100、总预算 ¥1000、每 IP 每日 3 次及浏览器唯一约束。

以上旧 S1 活动配置不是最新业务授权。2026-10-05 用户在 session `01a109f3` 已明确取消新的欢迎额度发放、保留已发权益；详见 [历史 UX 矩阵](UX-PARITY-RECOVERY-20261010.md) 的 S06。原 `controller/user.go:282`、`model/welcome_credit.go:105` 的活动资格与预算实现不应重新启用。注册默认不赠余额符合最新要求；已有赠送权益按迁移期末状态继续保留，不把 `default_balance` 设成 5/7 USD。

### 旧回调 inbox 不是履约

旧回调在验签、金额/商户/订单匹配后先持久化 inbox；pending_review 只表示待核对。`backend/internal/repository/realyu_legacy_payment_repo.go:31` 明确不改用户、钱包、权益或旧归档订单。未知/冲突订单拒绝，已知已履约订单不会重复入账。12 个旧 pending 不能仅按本地状态当作已支付或未支付，更不能关闭或赠送；需之后通过商户查单和旧快照独立对账。

## 可复用的原生能力与最小适配

保留原生登录、鉴权、支付提供商、下单、金额/签名校验、payment_orders、付款审计、fulfillment lease 和失败重试。钱包充值最终写入唯一 `users.balance`，可作为第一条独立商户验收路径，但必须校准 CNY→USD 到账倍率、重复通知和退款，不能默认一元到账一美元。

已获准进行候选开发的薄适配如下；本段是实施合同，不宣称已实现或已上线：

1. **计划元数据。** migration 306 为原生 plan ID 增加 managed 权益元数据：个人/团队 scope、整数 total/weekly quota、期限、钱包溢出、旧模板来源及校验摘要。先从已归档的真实模板受控导入；不要创建想象的团队价格，不复制供应侧影子用户余额。标准分组继续承担路由，不承担团队身份。
2. **不可变订单权益快照。** 在原生下单事务里固定 actor/payer/team、计划参数、价格分、期限和动作。在调用商户前验证权限、当前订阅状态、币种和能力；有效期内重复购买、续期、升降档均在本候选付款前拒绝。后续目录改价不能改变已付款权益。金额不能只依赖客户端字段。
3. **唯一履约。** 原生 `doSub` 对 managed 快照走一个权益适配，保留原 lease；同一数据库事务内唯一声明 order_id、锁定资金 scope、创建新的 `realyu_funding_subscriptions` 并写审计。不再为它写一份 stock `user_subscriptions`，也不修改消费结算公式。商户 grant receipt 提交后原生订单完成标记若失败，重试只补完成标记，不重新发权益。
4. **个人与团队隔离。** 个人 payer=actor；团队只允许实际 owner 对指定 team 购买，成员不能借 group ID 猜身份。团队权益不允许个人钱包溢出；商户回调无权重新选择主体。
5. **过期后新购。** 历史规则禁止有效期内同档重复购买；本候选不新增 active 续期。过期后可创建新一期，旧实例的 used、周锚点和历史不清空。旧“只升不降”中的升档/折抵仍未实现，应付款前拒绝并提示，不能声称已完全等价恢复。
6. **展示/能力。** 原生 plan/checkout 响应增 `managed_entitlement`，暴露 scope、整数权益与 capabilities；下单增加 `team_id`、`purchase_action`、`idempotency_key`。候选个人新购可测，`renew/upgrade/refund=false`；团队首购尚未完成 team/owner-member/Key/scope 原子创建，故 `purchase=false`。旧六个在售计划全部 `allow_balance_pay=true`，候选必须恢复真实钱包购买：`payment_type=wallet` 创建原生订单后，在同一事务中扣 USD 钱包、grant 权益和完成订单，记录 wallet source、商户实收为零，不伪造商户交易号。原生充值不是钱包购买套餐。
7. **退款。** stock 退款只减 stock 订阅天数（`payment_refund.go:257`），不能直接用于 managed 权益。未完成同 receipt 权益回收与商户退款恢复前，managed 退款能力需明确关闭；不得让已支持的客户端操作先收款、后报不支持。
8. **注册自动 Key（另任务）。** 保留正常注册策略，在已有用户创建事务中生成个人 Key、资金 scope 和明确审核过的默认 standard group 授权；失败整体回滚，不补赠余额，不覆盖旧团队绑定。重试或回调不能重复生成。默认组必须来自受控配置，不能随便取第一个组。

## 验收与上线顺序

候选阶段需真实独立 PostgreSQL 测试：计划/订单快照一致；改目录不改待付订单；个人新购与越权、团队能力付款前拒绝；并发重复 callback 与 lease 接管后只 grant 一次；提交前故障全部回滚；提交后重试不重复权益；过期新一期保留旧 used/reset；未知旧订单仍拒绝；不支持动作在商户调用前拒绝；钱包扣款与权益原子一致、商户购买不误扣钱包。注册需用户+Key+scope 原子性、并发重复、错误组拒绝、零余额 key-check、首请求正常走受管资金。

## 候选接口合同（开发中，未部署）

- 保留 `POST /api/v1/payment/orders`，使用 `order_type=subscription`、`plan_id`，新增 `purchase_action=purchase`、`team_id=0`、`idempotency_key`（16–128 位字母数字、下划线或短横线）。actor 仍由登录身份取得，客户端不能传 payer。`payment_type=wallet` 才表示钱包购买；同 key 重试返回已保存订单，不再次调用商户。
- `GET /api/v1/payment/plans` 和 `checkout-info` 的 `managed_entitlement` 返回 `funding_scope`、`total_amount_quota`、`weekly_amount_quota`、`duration_seconds`、`price_cents`、`wallet_price_quota`、`allow_wallet_overflow`、`capabilities`。权益单位是整数 quota；价格分是 CNY，钱包扣款 quota/500000 为 USD，不重复套分组倍率。
- 管理员先通过原生 plan CRUD 创建 `for_sale=false`、CNY、28 day、standard group 计划，再 `PUT /api/v1/admin/payment/plans/:id/managed-entitlement` 配置 scope/total/weekly/duration/price_cents/allow_balance_pay/allow_wallet_overflow/source_plan_id。服务器按显式客户汇率计算固定钱包扣款 quota；元数据只允许下架时配置。审核后通过原生 plan 更新上架。没有自动导入或开启生产商户。
- migration 306 和当前源代码仅在隔离候选开发/测试。不得把本文接口存在当作本轮商户、购买 UI 或生产上线验收已完成。

再用隔离完整应用验证购买页面、能力提示、管理员配置、计划读取与订单状态。最后仅在运营方启用前，对真实商户做最小支付→验签→唯一到账→真实消费→退款闭环；此项当前 **NOT_RUN**。不凭模拟支付、catalog 200 或页面币种正确宣布收款上线。

现有 paid 权益和请求服务可以继续运行。新购买/注册功能的发布单独控制，保留旧 pending 的人工核对队列，不覆盖已 ACTIVE 客户库，不借重新迁移快照补齐商品。
