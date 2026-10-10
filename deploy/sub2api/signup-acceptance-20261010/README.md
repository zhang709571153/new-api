# 注册默认个人 Key：候选验收与配置交接

状态：**候选源码完成并冻结，未部署本项注册改动，未开启生产注册**。最终验证时间为北京时间 2026-10-10 21:51；不能用此前已发布的单核心/CNY 版本证明本项已上线。机器可读结果见 [acceptance.json](acceptance.json)。本文不包含凭据、真实客户记录或生产配置值。

## 本轮行为

- `REALYU_FUNDING_ENABLED=true` 是托管模式边界；必须同时开启全局注册和用户名注册，邮箱可选。托管模式的 email-only 注册返回 `LOGIN_NAME_REQUIRED`，不会创建走原生计费的用户。
- 用户、不可变登录名、默认 `Personal Key`、个人资金归属以及邀请码占用在同一个 PostgreSQL 事务完成；任一步失败全部回滚。重复用户名不增加用户或第二把默认 Key。
- 默认 Key 使用原生随机密钥格式，只绑定明确授权的一个 active standard group。用户设为 `restrict_public_groups=true`，资金归属 `actor=payer=本人`、`team=NULL`；组 ID 不是团队 ID，注册不授予团队权限。
- 初始余额为 0，不发原生默认订阅，不恢复已取消的欢迎金；非空 `promo_code` 被拒绝。Key 的原生 `quota=0` 只表示无 Key 自身上限，资金入口仍检查钱包/套餐，绝不代表免费请求。
- 托管模式暂不支持通过 OAuth 创建新客户，返回 `OAUTH_REGISTRATION_UNAVAILABLE`。已经存在且通过身份校验的 OAuth 登录、关联和 MFA 保留；复制但未验证的邮箱不能接管旧账户。OAuth 首次绑定不向托管客户发放原生默认余额/订阅。
- `REALYU_FUNDING_ENABLED=false` 时保留原生邮箱/OAuth 注册及其默认权益行为，不创建此默认个人 Key。

## 上线前显式配置

以下均是服务端配置，不能接受公众注册 DTO 自行指定 payer、team 或默认 group。此代码没有新增 schema migration；需要已有身份/资金表（300、301）。

| 配置 | 位置 | 条件 | 当前普通管理入口 |
|---|---|---|---|
| `REALYU_FUNDING_ENABLED` | 私有服务环境 | `true` | 不是网站管理设置 |
| `registration_enabled` | 原生 `settings` 表 | 审核准备完成后才开启 | 现有管理员 `PUT /api/v1/admin/settings` / 注册设置 |
| `realyu_username_registration_enabled` | 原生 `settings` 表 | 启用时为 `true` | 新增管理员 `GET/PUT /api/v1/admin/settings` 字段 |
| `realyu_signup_personal_group_id` | 原生 `settings` 表 | 明确选定的正整数组 ID | 新增管理员 `GET/PUT /api/v1/admin/settings` 字段 |

两个 `realyu_*` 设置已接入现有管理员认证和设置审计，不再依赖手工 SQL。请求字段为指针：未传或 `null` 保持现值，明确 `false` 关闭用户名注册；只有托管模式允许写入。组必须是指定的 active standard group，已删除/停用/订阅组、0、负数、缺少组读取能力均拒绝，失败不写入同一请求中的其他设置。开启全局注册还会验证用户名模式与已保存组。`false` 单独关闭可在旧组失效时使用。

管理员 GET/PUT 响应可回读这两个字段；公开设置只返回 `username_registration_enabled` 等安全模式标识，不暴露组 ID。当前尚未为它们新增专用后台表单，可由经过认证的管理 API 配置；不能把同名环境变量当作 settings 键的替代品。以下只示范请求结构，`42` 必须替换为审核后的真实组 ID，且此请求不打开全局注册：

```json
{"realyu_signup_personal_group_id":42,"realyu_username_registration_enabled":true}
```

建议发布顺序：

1. 保持全局注册关闭，审核目标客户库及新二进制版本。
2. 在目标库确认预选组 `status='active'`、`deleted_at IS NULL`、`subscription_type='standard'`，并核对其实际上游、模型、费率符合个人套餐路径。不以历史机器的组 ID 为默认值。
3. 通过管理员 `PUT /api/v1/admin/settings` 一次写入两个 `realyu_*` 字段，再 GET 回读确认；保留不含凭据的变更记录。API 复用原生 settings 批量原子写入；注册代码会再次在事务内用 `FOR SHARE` 锁定并检查组，防止与组禁用/删除并发。
4. 先在隔离候选完成真实 HTTP/浏览器注册→登录→Key 列表验收：用户名与昵称独立；邮箱为空可以注册；只出现一把 personal Key；余额和订阅均为零；重复注册、错误邀请码与未授权 scope 输入不产生额外数据。
5. 发布本项候选并核对版本后，才由操作者按发布决定开启全局注册，执行有限生产验收。本文没有执行这些生产动作。

缺少/无效组配置、组停用/删除/订阅组、缺少事务能力都会失败关闭。当前注册接口对组配置错误返回通用 `SERVICE_UNAVAILABLE`；服务端日志可区分失败原因，不向公众泄漏配置详情。关闭用户名设置不会重新开放托管 email-only 注册。

## 实际验证

运行工作目录为候选 `source/backend`，使用 Go 1.27.2，命令如下。`REALYU_AUTH_TEST_DSN` 由本机私有测试配置注入，未写入 Git；测试自身强制仅允许 `127.0.0.1:29490/realyu_auth_e2e`，建立并清理自己的随机 schema。

```powershell
go test -p 2 -tags unit ./internal/service ./internal/handler/admin -run '^(TestRealyu|TestAuthService_(Register_|SendVerifyCode|Verify|LoginOrRegisterOAuth)|TestVerifyCaptcha|TestRegister.*OAuth|TestSendPendingOAuth|TestEmailOAuthAuto|TestAuthServiceRegisterDualWritesEmailIdentity|Test.*FirstBind|TestCanBypassRegistrationDisabledForOAuth|TestUpdateSettings|TestSettingHandler|TestSettingService)' -count=1 -v
```

最终结果为 **182 个测试根、73 个子场景 PASS，0 FAIL、0 SKIP，exit 0**。其中真实 PostgreSQL 根 `TestRealyuPostgresIdentityE2E` 的 6 个子场景覆盖：托管模式与配置、Key/scope 失败回滚、组锁与重新校验、邀请码失败/成功/重放、管理员设置真实持久化/软删除组拒绝/批量写失败回滚、原生非托管模式兼容。还执行了真实 HTTP handler 的 email-only 400 检查。OAuth 边界新增 9 个拒绝新建的分支检查，已有账户登录和资金不变另有断言；同时回归了原生管理设置的部分更新、安全开关与鉴权设置行为。

最终本地日志：`runtime/signup-settings-final-20261010T215126871.log`，SHA-256 记录在 JSON。此前 Auth 第一阶段的 75 根/37 子场景结果保留在 `runtime/signup-personal-key-final-20261010T213807977.log`；本次最终结果包含了这些验证。首个回归曾撞到并行 Payment 编辑的临时未定义符号（`managedPurchaseInput`、`resumeManagedOrder`、`rejectManagedRefund`）；原始错误保留在 `runtime/signup-personal-key-final-20261010T213215717.log`，协作者接齐后完整重跑通过。没有用重试覆盖首错。

安全检查依据为 OWASP [Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)、[Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) 与 [ASVS](https://owasp.org/www-project-application-security-verification-standard/) 中适用的服务器端授权、身份绑定、随机凭据及审计要求；本轮不是全站 ASVS 认证。日志不记录可用邀请码/Key/密码。

## 验收边界

本报告是候选源码与隔离 PG/HTTP handler 回归证据，不是生产发布回执。没有改变生产注册开关，没有调用付费模型/真实支付，没有补发欢迎金，没有迁移旧活动预算。管理员 API 已可配置和回读默认组/用户名模式，专用后台表单尚未新增；完整候选浏览器注册→Key 列表与发布后有限生产验收仍由发布流程完成。
