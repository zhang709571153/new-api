# 原版 New API 与 Sub2API：选型与完整迁移调研

检查时间：2026-10-10 上午（北京时间）。本轮为官方源码、发布记录、相关 fork 与生产匿名聚合的只读研究；没有迁移客户、发布程序或新增模型调用。当前生产状态仍以 [LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md) 为准。

## 结论与范围

**建议把长期目标调整为：Sub2API 单核心，加上必要的 RealYu 定制业务移植。** 当前桥接可以作为过渡，不必成为永久架构。

理由是我们的主要供给来自 ChatGPT 订阅账号，订阅反代兼容、刷新、调度是核心技术要求；Sub2API 在这一层有专门实现和持续修复。其原生用户、Key、支付、订阅、统计、管理后台也已足够丰富。在本轮核查的已用功能中，未发现必须永久保留 New API 核心的硬依赖；价卡、工具附加费和权限语义仍需迁移验证。

这是一项基于当前业务的技术选型判断，不是两个项目的全面质量排名，也不是全迁已经完成的声明。必须分开看三类内容：

1. 原版 New API 与原版 Sub2API 的原生能力差异；
2. RealYu 自研的团队、资金与套餐规则：属于移植工作，不属于原版 New API 的独占优势；
3. 客户数据、旧 Key 和历史会话兼容：属于一次性迁移与验收工作。

后续三组独立复核及具体开发/切换合同见 [迁移审查](MIGRATION-REVIEW-20261010.md)。
该复核新增了旧用户名/Argon2id、默认权益副作用、历史订阅实例与迟到支付等具体缺口；
选型成立不代表现在已具备直接切流条件。

## 固定版本与证据等级

| 对象 | 本轮检查的准确版本 |
| --- | --- |
| New API 最新发布 | `v1.0.0-rc.42`，`6370b29424168039e94d40d610191e7d2e65dbf4`，发布于 2026-10-08 10:10:46 UTC。GitHub 标记 Latest，但名称仍是 RC，不称已经证明稳定的正式版。 |
| New API 主线 | `1d4328e97417a043a161a0dd30a5b129be3ace49`，2026-10-09。 |
| Sub2API 最新发布 | `v0.2.15`，`f2669c8cf62555cd92389b3f55920e9e6e7c6ff2`，2026-10-09；也是当前部署固定的版本。 |
| Sub2API 主线 | `3a6fd1c9db07203ca308aaba69e502bc1f35b307`，相对发布 tag 仅同步 VERSION，本轮比较没有依赖未发布功能。 |
| RealYu 检查基线 | 交接分支源码 `9ed6bd9a1587730dc4b809a678b5244b81db369c`；运行版本按既有发布凭据为 v3.10.0.1。本轮没有重新核验二进制哈希。 |

证据分为“固定版本源码存在”“发布记录已发布”“当前匿名数据确有使用”“真实迁移/E2E 已通过”。本轮前三类均有，但没有新增第四类证据。相关源文件、提交哈希比较与聚合口径见同目录 [研究证据索引](research-20261010.json)。

## 原版能力对照

| 能力 | 原版 New API | 原版 Sub2API v0.2.15 | 对本业务的判断 |
| --- | --- | --- | --- |
| ChatGPT 订阅供给 | 有 Codex type57、OAuth 刷新、用量读取、Responses/WS；不承诺处理 Codex 订阅反代成通用 API 后的全部兼容差异 | 专门维护订阅接入、账号池、刷新、粘性调度及 Codex 协议兼容 | Sub2API 更符合本业务的主要维护需求；不是 New API 完全无 Codex 功能 |
| 通用协议与模型 | Chat、Responses、Messages、Gemini 等转换；大量厂商专用适配 | 同样有主流协议，平台已扩展至 OpenAI、Anthropic、Gemini、Grok、Kimi、DeepSeek、Cline、Command Code 等 | 当前单一 Sub2API 供给不依赖 New API 的全部厂商广度 |
| 多模态与任务 | Realtime、音频、embedding、rerank；JavaScript 任务插件承接多家图片、视频和音乐接口 | 有图片/编辑/批量、embedding、Grok 视频/语音/Realtime、Seedance 任务等；未找到同等通用 rerank/音乐与全部厂商任务适配 | New API 扩展广度是实质差异，但这些额外能力没有本轮确认的当前使用证据；不能说 Sub 只支持文本 |
| 用户、Key、余额、套餐 | 原生具备 | 原生具备，Key 有有效期、IP/额度窗口等控制 | 不构成保留两套系统的理由；具体套餐扣减语义仍须映射 |
| 支付和商业运营 | 易支付、Stripe 及多种其他支付接入，套餐与充值 | 内置易支付、官方支付宝/微信、Stripe 等，订单/退款、邀请返佣及提现记录 | 当前易支付充值有目标原生接入；不用另造支付系统。各家支付渠道并非完全相同 |
| Token/缓存/Fast/图片费用 | 通用表达式及其他定价方式 | 原生 token/缓存/图片价卡，Fast/Flex、推理、区间/分时倍率、按次及搜索费 | 当前价格先尝试原生配置并逐笔比对，不先搬整个表达式引擎 |
| 任意计费表达式 | `expr` 引擎，支持请求字段/头/时间条件及任务用量表达式 | 未找到可直接导入 New API 表达式的通用引擎 | 原版 New API 的真实优势；当前 8 个文本模型没有分时表达式，不等于每个能力都必须移植 |
| 登录安全与第三方登录 | Passkey、TOTP、OAuth/OIDC、自定义 OAuth 等 | Passkey、TOTP、OIDC 及多种 OAuth 登录已有实现 | 不能把这些误列为 Sub 缺失。本轮没有进行完整安全合规审计 |
| 细粒度管理授权 | Casbin：渠道 read/operate/write/sensitive_write/secret_view、task_plugin.bind、audit.read 等资源权限 | 主要为 admin/user；未找到等价资源级授权矩阵 | New API 有明确优势，但不能夸成所有领域的完整企业 RBAC；如后续需分权运营，要列为扩展要求 |
| 统计、账号可见性 | 用户/Key/渠道/模型日志、配额与费用统计 | 用户/Key/实际供给账号/分组/价格/usage，导出、账号用量窗口、ops 监控与告警 | 对订阅供给管理，原生 Sub 界面能减少当前两边统计联结和信息损失 |
| 数据库选择 | 主库 SQLite/MySQL/PostgreSQL，分离日志库并支持 ClickHouse；Redis 可选 | 生产存储使用 PostgreSQL，依赖 Redis；已有备份/恢复及多实例协调代码 | New API 更灵活；我们已经运行 PG/Redis，全迁并不新增此依赖，也没有付费 Redis 的必然要求 |
| 插件扩展 | JavaScript 任务插件及宿主协议，已带多种厂商插件 | 已有独立进程 gRPC 插件框架，但当前公开能力点集中在 OpenAI OAuth 出站传输 | 不能说 Sub 没插件；两种扩展面不等价，当前未使用的插件生态不是迁移硬阻挡 |
| 前端 | React；管理 API/数据模型为 New API 契约 | Vue；拥有自己的完整用户与管理前端 | 建议使用 Sub 原生前端，迁入 RealYu 必要页面和静态引导；改一个 base URL 无法复用全部 React 页面 |

源码依据：[New API 固定版本功能与部署说明](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.42/README.md)、[渠道清单](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.42/constant/channel.go)、[协议路由](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.42/router/relay-router.go)、[表达式引擎](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.42/pkg/billingexpr/expr.md)、[授权资源](https://github.com/QuantumNous/new-api/tree/v1.0.0-rc.42/service/authz)、[任务插件](https://github.com/QuantumNous/new-api/tree/v1.0.0-rc.42/plugins/tasks)。

Sub2API 依据：[平台及角色](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/domain/constants.go)、[网关路由](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/server/routes/gateway.go)、[支付](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/docs/PAYMENT_CN.md)、[价卡](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/channel.go)、[管理路由与监控](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/server/routes/admin.go)、[备份实现](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/backup_service.go)、[插件能力边界](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/docs/PLUGIN_DEVELOPMENT.md)。

两处容易误判：New API `/files` 虽有路由，但绑定 `RelayNotImplemented`，不能算作它已经实现而 Sub 没有的优势；两边有某种 API 路由，也不代表 ChatGPT 订阅账号具备该 API 的全部原生行为。

## ZPay 支付专项核查（2026-10-10 09:45 CST）

Sub2API v0.2.15 的官方支付指南明确列出 ZPay，接入使用内置 EasyPay 服务商，
不是必须另装的支付服务。配置项为 PID、PKey、API 基础地址及可选的支付宝/微信通道 ID；
前台支付方式还需路由到对应 EasyPay 实例。使用商户后台提供的实际 API 地址，
不能把 ZPay 营销首页域名自动当成 API 域名。本轮没有读取或迁移真实商户密钥。

源码已有支付宝/微信、扫码/跳转、回调验签、查单与退款适配；订单履约服务同时处理
余额充值与订阅购买。当前 RealYu 的团队资金及套餐业务仍须按上文移植，不能因为
目标有支付按钮就把影子用户预算当作客户余额，或现在直接开启双边收款。

但本轮发现两项需要验证的协议差异，不能把官方列名当作完整商户验收：

1. **查单请求方式不一致**：ZPay 文档规定 GET `/api.php?act=order`，Sub 的
   `EasyPay.QueryOrder` 向 `/api.php` POST 表单，`act` 也在表单中。对文档所列
   `https://zpayz.cn` 使用无效测试身份、虚构订单号做只读探测，Sub 形态返回
   HTTP 200 / 0 字节；GET 返回 JSON 错误。追加把 `act` 放到查询串的 POST
   返回必填参数错误。没有有效商户认证，不能由这些结果宣称真实订单查单成功或
   已复现真实漏单；但现有空响应无法被适配器 JSON 解码，主动补单路径需要验证/适配。
2. **同步返回地址约束不一致**：ZPay 跳转接口文档称 `return_url` 不支持带参数；
   Sub 的 `buildPaymentReturnURL` 会添加订单 ID、状态及恢复令牌。异步通知地址本身
   是无查询串的固定路径。同步返回的实际接受、参数保留和页面恢复尚未实测。

ZPay 文档还说明多数通道退款额需等于原订单额，因此不能承诺所有通道支持部分退款。
迁移前的商户验收应包含充值与订阅支付、异步通知、重复回调只入账一次、主动查单补单、
支付后返回页面、整单退款，以及渠道支持时的部分退款。本轮没有创建支付订单、付款、
退款、修改支付配置或进行真实商户 E2E；这些仍是待验收项。

结论：ZPay 有原生接入基础，不构成必须长期保留 New API 的理由；原版 v0.2.15
的上述兼容点应进入迁移验收，发现问题时优先做小范围上游适配，不重造支付系统。

依据：[Sub 官方支付指南](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/docs/PAYMENT_CN.md)、
[EasyPay 适配器](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/payment/provider/easypay.go)、
[订单履约](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/payment_fulfillment.go)、
[返回地址生成](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/payment_resume_service.go)、
[ZPay 官方协议](https://api.z-pay.cn/doc.html)。只读探测摘要见同目录研究证据索引的 `zpay_followup`。

## 为什么选择 Sub2API，而不是继续扩写 New API 的 Codex 渠道

New API 官方 issue 范围明确排除逆向渠道、Codex 反代为通用 API 后的兼容问题；这是维护范围，不是代码完全不支持。当前官方已支持 Codex WS，rc.38 已有流中断估算与图片 token 修复，rc.42 还有搜索计费改进。

Sub2API v0.2.15 则直接修复了 OAuth 历史回放 `web_search_call`、加密 reasoning 被上游拒绝后的恢复、WS 后续轮次的价格更新及图片输入用量、Codex 模型目录等问题。这些正是本业务反复遇到的变化。它提供的是专门的实现与维护方向，不是保证所有场景永久正常的服务承诺。

我们已经通过 Sub2API 替换了旧自研供给内核；本轮没有找到回到 New API 自写反代、或换一个泛用 fork 更合适的证据。完整迁移后仍应跟随 Sub2API 上游，RealYu 的差异尽量只落在业务领域，不重新复制其 OAuth、协议转换与调度。

来源：[New API 官方支持范围](https://github.com/QuantumNous/new-api/blob/1d4328e97417a043a161a0dd30a5b129be3ace49/.agents/github/ISSUE.md)、[rc.38](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.38)、[rc.42](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.42)、[Sub2API v0.2.15](https://github.com/Wei-Shaw/sub2api/releases/tag/v0.2.15)。

## 上游与 fork 排查结果

| 对象 | 查证结果 | 本次用途 |
| --- | --- | --- |
| `QuantumNous/new-api` / `Calcium-Ion/new-api` | 后者重定向到前者，API 返回同一 repository ID | 同一个官方项目，不能当作两个不同候选 |
| `songquanpeng/one-api` | New API 文档声明的历史基础；检查提交 `8df4a2670b98266bd287c698243fff327d9748cf` 的 relay/router/controller，未找到现代 Codex/Responses 实现 | 更早的上游不是当前 Codex 兼容修复来源 |
| `Calcium-Ion/new-api-horizon` | README 说明增强版本不开源 | 没有可复用源码证据，不能拿宣传证明解决问题 |
| `Mree9527/new-api-pool` | 检查 `d30ad87e70218c4561a6e78ada13c92e40bd7125`；有账号池扩展，README 说明不计划合并上游；Codex adaptor 仍拒绝 native Images | 可作账号池设计参考，未证明可替代当前 Sub2API |
| 官方 PR #5062 的贡献 fork | WS 功能已经合并到官方并发布 | 直接复用官方即可，没有搬整个贡献 fork 的必要 |
| 官方 PR #6254 的贡献 fork | SSE 缺 Content-Type 的处理仍是未合并提案 | 若复现对应错误可评估补丁，不能称已发布稳定方案 |

来源：[One API 检查提交](https://github.com/songquanpeng/one-api/commit/8df4a2670b98266bd287c698243fff327d9748cf)、[Horizon 说明](https://github.com/Calcium-Ion/new-api-horizon/blob/main/README.md)、[pool fork](https://github.com/Mree9527/new-api-pool/blob/d30ad87e70218c4561a6e78ada13c92e40bd7125/README.md)、[WS PR](https://github.com/QuantumNous/new-api/pull/5062)、[SSE PR](https://github.com/QuantumNous/new-api/pull/6254)。本轮是针对相关上游与 fork 的有界核查，不是穷举所有 fork。

另已比对 RealYu 与官方固定版本的 Git blob：`relay/channel/newapi/adaptor.go`、`service/codex_credential_refresh.go`、`service/codex_wham_usage.go` 与 rc.42 相同。官方原本就有 Sub2API type59，RealYu 使用它再增加身份/作用域桥接。不能把既有问题全部解释成“fork 太旧、没同步任何修复”。rc.42 的厂商搜索计费、部分失败图片收费策略仍有差异，应按实际路径评估，不能由差异数量推导生产故障。

## RealYu 定制移植清单：不算作原版 New API 优势

本轮匿名只读检查确认：唯一启用供给渠道为 Sub2API；3 个团队采用独立资金主体，存在成员周额度及实际团队流量；个人订阅也在使用。当前 8 个文本费率有缓存和 service tier 规则，分时规则为 0。Passkey/TOTP/OAuth 绑定数量为 0，不能以未使用的登录方式阻挡迁移。

| 要迁的内容 | 目标实现方向 | 不应携带的冗余 |
| --- | --- | --- |
| 团队资金、成员周 cap、个人/团队作用域、owner 管理 | 把既有业务规则接入 Sub 原生用户、Key、订阅和一次结算事务；按实际调用成员保留会话隔离 | 不搬整个 New API relay/渠道/刷新体系 |
| 既有实例权益、个人套餐溢出钱包、团队禁止溢出、既有报价 | 保留原始起止/重置/已用/总量；包含28天目录之外的30天历史实例；原生同组续期不是期初导入 | 不再保留另一套同时扣费的客户账本 |
| 当前文本、native Images 与 Responses 图片工具费率 | 优先映射原生价卡，按当前type59实际路径核对cache、请求/出站/返回及收费tier、图像token、工具费、单位和舍入 | 不因现状使用表达式而默认重写完整表达式引擎 |
| 用户、旧 Key、余额/当期已用、订单和历史审计 | 一次性迁移并逐项对账；历史记录可保留可追溯只读归档 | 不把内部 Sub 影子用户的预算当作客户钱款或赠金 |
| `resp_ry1_` 旧响应 ID、namespace 和主体映射 | 限定过渡兼容；旧续接仍验证成员/团队归属，新会话使用目标规范 | 不因一段兼容逻辑永久保留整个旧网关 |
| 品牌页面、下载地址、安装器和指南 | 使用 Sub 原生前端，迁必要页面或静态同域资源，保留客户端 URL 契约和应保留的许可署名 | 不把两套用户中心/支付/管理后台再次拼成长期双系统 |

业务源文件包括 `model/workspace*.go`、`model/subscription_funding.go`、`model/billing_catalog.go`、`model/billing_order.go`、`service/workspace.go`、`service/billing_session.go`，以及 `web/src/features/workspace` / `team`。这些是移植依赖范围，不是已经完成的新实现。

Sub 原生 group 代表路由/准入/价格/订阅分组，不是组织资金主体。不能简单把一个团队变成一个 Sub user、成员变成多个 Key：原生同一 user 的不同 Key 可共享 response continuation 归属，这与当前成员隔离合同不同。既然用户要求保留定制业务，正确工作是移植该合同，而不是丢掉它或据此否定全迁。

旧 Key 不必预设全部更换：Sub 支持受格式和长度约束的 `custom_key`，需要验证前缀、冲突和归属。后续独立复核已确认当前启用未删用户全无邮箱，绝大多数密码为 Argon2id；Sub 原生邮箱登录且仅 bcrypt，须增加用户名和哈希兼容。普通创建用户 API 不接受现成 password_hash，还可能触发默认余额/订阅，不能当迁移器。本轮没有找到 New API 用户/余额/套餐/历史的一键导入器，上游 account import 不是客户迁移。

来源：[Sub 用户/资金 schema](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/ent/schema/user.go)、[分组语义](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/docs/COMPOSITE_GROUPS.md)、[同用户 Key 的续接边界](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/openai_gateway_response_handling.go#L1438-L1454)、[Key 创建](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/api_key_service.go)、[套餐续期](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/subscription_service.go)、[订阅窗口](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/user_subscription.go)。

## 目标架构与能删掉什么

```text
当前：客户端 → RealYu / New API → 身份与额度桥接 → Sub2API → 订阅账号
目标：客户端 → RealYu 品牌的 Sub2API + 定制业务 → 订阅账号
```

目标只保留一套权威客户身份和客户账务。迁移完成后，可以退休影子身份准备、内部供给余额补额 worker、跨系统统计联结及第二个在线客户数据库。原始历史库可作为只读档案留存，不再承接新交易。

这不是“切 simple 模式”的方案。simple 会改变金额/RPM检查、group 账号池选择及 Composite 能力，不能仅为了关闭内部扣费直接切换。全迁应使用 Sub 的标准运营功能，让其余额和套餐成为真实客户权益，桥接的内部授信才自然退出。

两边前端/ORM分别为 React/GORM 与 Vue/Ent，业务语义需要适配，无法原样复制 Go/React 文件就宣称完成。当前团队与账务确实不只是前端，但它们可以作为业务模块迁入目标系统。

## 后续迁移必须证明的结果

以下是将来开发/演练的验收项，本轮状态均为未执行；不是要求再次确认已有研究权限。

1. **一个账本、一笔扣费**：同一真实 usage 在当前价卡与目标价卡重放，覆盖缓存、当前type59的Fast收费规则、图像token、工具费及失败/中断；核对单位/精度/舍入。type57按实际tier覆盖的逻辑不能直接视为现网type59合同。当前CNY展示、quota与目标USD数值不能直接等值复制。
2. **现有团队合同保留**：共享订阅只扣一次；成员 cap 不生成新资金；个人钱包/团队资金不串用；退组/解散后的在途结算仍落到原资金主体。并发耗尽、退款和重复回调要有事务与幂等验证。
3. **身份与续聊连续**：旧 Key、个人/团队切换、禁用/过期、跨用户及跨成员拒绝；HTTP/SSE/WS 的旧响应 ID 和新会话均验证。登录迁移、重新认证、凭据存储按目标安全模型处理，不迁移活动登录 cookie 充当身份绑定。
4. **客户数据可核对**：开账余额、套餐当前周期剩余、订单/退款/赠金、历史日志分别核对；停止旧侧新写入并排空在途请求后做最终增量迁移。禁止用旧备份覆盖切换后的新交易。
5. **完整客户端与恢复**：PDF 内嵌/URL、真实搜索引用、SDK 终态、工具往返、图片生成编辑、Codex 新聊/续聊/文件及终端任务、长会话；Windows 重启、PG/Redis/密钥/静态资源恢复、代理中断和账号故障。已有短请求通过不能替代迁移版本的验收。

这轮研究未修改认证实现，也未作完整 OWASP 合规声明。后续涉及认证移植时以 [ASVS 5.0.0](https://owasp.org/projects/asvs)、[Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) 和 [Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) 为检查依据。

当前共享代理/Tunnel 的网络单点、整机恢复及中断结算等 [P1](../../lab/maintenance/sub2api-p0-p1-20261010.md) 不会因为更换前端或合并平台自动消失。现有公网观察继续执行；本报告不宣称稳定性或所有原生 Codex 功能已经等价。

## 本轮交付边界

本报告和证据索引可进入 Git；生产数据库、账户凭据、完整日志及私有研究副本不进入 Git。没有因本研究切换服务、账户模式、域名或客户流量。部署教程仍描述当前桥接系统，未来单核心版本需随实际实现再更新，不能提前当作已发布教程。
