# Sub2API 单核心迁移：独立审查与最小交付方案

日期：2026-10-10。审查基线：RealYu `c9146b18dc1b01cadce696e894078319b299cc6b`；
Sub2API `v0.2.15` / `f2669c8cf62555cd92389b3f55920e9e6e7c6ff2`。
本文件是下一阶段开发与验收方案，不是可执行切流计划，也不是迁移完成报告。

## 用户确定的交付范围

- 使用 Sub2API 单核心，复用其供给管理、协议适配、原生管理与用户前端。
- 前端首版复用 RealYu 现有 Logo 和配色；优先原生配置及主题变量，不重做整站布局。
- 优先保证现有客户正确使用：账号、旧 Key、团队权限、客户权益、支付和旧会话必须承接。
- 尽量保持同域名、同 API 地址、同 Key；将浏览器重新登录、WS 重连和切换窗口明确列为边界。
- 开发日志、更新日志、部署教程与可交付代码保持同一 Git 交接。凭据、生产数据库和客户原始记录不进 Git。

## 审查结论与现网基线

选型仍成立。需要完成的主要工作是客户业务移植、数据转换和切换控制；原生 Sub2API
加品牌调整尚不能直接替代当前客户系统。下文 P0 表示全迁上线前必须关闭的缺口，
不是已经证实现网发生丢账或越权，不据此回滚当前服务。

三个独立审查方向为：客户/账务合同、运行/切换/回退、原生前端/品牌与客户端契约。
主审交叉核对了密码校验、默认订阅副作用、旧响应签名、支付未知订单处理、
type59 计费分支及现有切换器的适用边界。原始审查稿留在本机私有研究目录，
本文件保留开发所需的脱敏结论及源码入口。

2026-10-10 09:52 CST 只读复核：公网、backend、bridge 均返回
`realyu-sub2api-v3.10.0.1-20261010`，RealYu 与 Sub2API/PG/Redis/worker 等服务 Running。
当前仍是 RealYu/SQLite 客户账本加 Sub2API/PG 供给内核；v3.10.0.2 门户是未发布候选。
这是版本与健康检查，不是本轮重新完成客户端 E2E。历史试点/维护辅助脚本的 Git HEAD
不作为本次修改的源码基线。部署事实详见 [LIVE](../../LIVE-DEPLOYMENT.md)。

## P0：迁移前必须关闭的五组缺口

### P0-1：保留用户名登录和既有密码，不通过普通创建用户 API 导入

匿名只读核查发现：51 个启用且未删用户均未填写 email；密码格式为 49 个 Argon2id、
1 个 bcrypt、1 个空值。空密码记录不因此获得密码登录能力。RealYu 支持 username/email
查找及双格式验证，Sub 原生登录要求 email，密码校验使用 bcrypt。
因此“复制用户然后让客户直接登录”目前没有实现基础。

最小实现是稳定的旧用户名别名、原始合法哈希受控导入与双格式校验，保留禁用、软删除、
角色和管理员边界；旧密码不做 trim/截断，不批量降级 Argon2id。空邮箱不能自动视为已验证，
不能向影子邮箱发恢复邮件，后续绑定真实邮箱应验证。使用目标会话重新认证，旧 cookie、
管理员 PAT 和供给投影随机密码不能直接转换成客户登录凭据。

Sub `CreateUser` 会把 `Password` 当明文再哈希，并可能按配置授予默认余额/订阅。
迁移器必须有独立导入语义、显式期初值、幂等记录和事务，不调用普通创建接口重复赠权益。

依据：[旧密码规则](../../common/account_password.go#L42)、
[旧登录查找](../../model/user.go#L1118)、
[Sub 登录输入](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/handler/auth_handler.go#L77)、
[Sub 密码验证](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/user.go#L99)、
[Sub 创建用户](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/admin_user.go#L120)。

### P0-2：客户开账、团队和计费要迁业务语义，不能直接复制数字

保留个人钱包、团队独立资金、成员周 cap、个人套餐溢出钱包、团队禁止溢出，以及 Key 限额。
Sub group 是路由/订阅分组，不能替代团队资金主体；成员 cap 不产生新资金。
当前 Sub 投影身份的内部初始预算与自动补额不属于客户余额。目标成为客户权威前须退出
旧投影/credit worker，核清未决 intent，不能让旧补额逻辑继续修改真实客户余额。

每份在用订阅应保留实例的开始/到期、总额、已用、周窗口与下一重置时间。当前 7 个有效
实例中有 1 个 30 天历史团队实例；不能按最新 28 天套餐目录重建全部权益。这些实例均为
管理员授予，不能伪造为已付款订单；历史充值、赠金、权益来源分别记录。

旧 quota、CNY 收款/展示与 Sub 金额字段必须采用明确的单位转换及舍入。优先配置 Sub 原生
价卡，再用当前 type59 真实收费基线离线重放；有差异时才添加兼容。native Images 继续
用户已选的 token 计费，Responses 图片工具沿用其单独合同，避免两种费用叠加或漏计。

**Fast 不能仅凭“上游实际 tier”几个字判等价。** RealYu `TryTieredSettle` 的实际返回 tier
覆盖分支限定 type57，当前渠道是 type59；Sub 对 ChatGPT OAuth 的 observed=default
也有特殊处理。必须记录客户端请求 tier、最终出站 tier、上游声明、最终收费 tier，
用 priority + default/empty/unknown/flex、组强制 Fast 等例子比较实际路径。
本次未认定现网多扣，也未修改价格。

依据：[团队资金](../../model/workspace_funding.go)、[成员额度](../../model/workspace_member_usage.go)、
[资金结算](../../model/subscription_funding.go)、[内部补额](../../service/sub2api_credit.go)、
[现有 tier 结算](../../service/tiered_settle.go#L221)、
[Sub tier 规则](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/service_tier_billing.go#L64)。

### P0-3：旧 Key、成员授权域和续聊同时承接

旧 Key 按客户端实际发送的完整字节迁移，保留前缀行为、归属、启停/过期、IP/模型/额度限制；
不能把数据库中的无前缀字符串直接当作全部客户端输入。个人、同人不同团队、同团队不同成员
的授权域均要保留，不能用一个 Sub user 的多个 Key 替代所有成员。

旧 `resp_ry1_` 是带主体签名的响应 ID。目标应在认证后以稳定的旧 user/team 别名验证签名，
检查当前权限，再解包并映射原供给 owner/account/group。保留必要 namespace/secret 与
PG/Redis 归属/账号绑定；仅删除前缀不能完成兼容。HTTP、SSE、WS 每轮和 session/cache
元数据采用一致的主体规则。退组、禁用和过期后不得借有效旧签名继续使用。

客户端 `/v1`、当前模型名、`/api/client/key-check` 的 JSON、diagnostics、安装器及图片工具
下载 URL 属于兼容合同。原生 Sub 管理路由为 `/api/v1`，同一个域名不会自动保留旧端点。
已有客户端无需重发 Key 是验收目标，不是本轮已实现事实。

依据：[签名与解包](../../service/sub2api_response_scope.go#L13)、
[身份作用域](../../service/sub2api_driver.go#L305)、[旧客户端入口](../../web/public/downloads/realyu/setup.py#L30)、
[Sub retained owner](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/openai_gateway_response_handling.go#L1438)、
[Sub 状态绑定](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/openai_ws_state_store.go#L132)。

### P0-4：旧支付订单和迟到回调只能由一个账本履约

旧 `/api/billing/epay/notify`、`/api/billing/epay/return` 与 Sub 原生 webhook/result 路径不同。
旧订单号、商户实例、币种/金额、权益快照、已履约/退款状态都需要稳定映射。
Sub 对查不到的订单通知会返回成功以停止重试；没有迁旧订单就改回调，存在支付被确认
但没有履约的风险。这是迁移风险，本轮未发现现网已经发生这类漏单。

首版保留有界的旧 URL/订单适配，进入唯一目标事务。最终冻结期间采用验签后可靠落盘的
回调收件记录再接管履约，或以已验证的提供方重试/主动补单覆盖窗口；不可向未保存的通知
返回成功。停止创建订单不代表旧支付已全部结束。回调、主动查单、人工重试和退款并发时
只改变一次权益，不能靠两个系统各自幂等来防止跨系统重复入账。

ZPay 原生 EasyPay 接入存在，但查单 GET/POST 与返回 URL 参数两项兼容差异仍待验证；
真实商户充值、订阅、漏回调补单、重复通知、整单退款及渠道支持时的部分退款尚未 E2E。
详见 [ZPay 专项](PLATFORM-COMPARISON-20261010.md)。

依据：[旧支付回调](../../controller/billing_checkout.go#L258)、[旧订单事务](../../model/billing_order.go#L357)、
[Sub 未知订单处理](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/service/payment_fulfillment.go#L23)、
[Sub 回执](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/handler/payment_webhook_handler.go#L130)。

### P0-5：跨库切换与回退必须另有实现，现有桥接升级脚本不可直接使用

`host_cutover.py` 仅支持 initial-cutover / portal-upgrade，强制候选和原客户 SQLite
结构一致，其回退保留同一份实时客户数据库。这不能证明 SQLite → PostgreSQL 客户全迁
安全。当前缺少全迁状态机、最终快照/差异导入、单写权威记录与跨库回退验收。

现有 300 秒排空及账表连续 15 秒不变，不是所有写入停止的证明。要覆盖已接收生成、
WS 下一轮、异步退款、套餐重置、管理调账、注册/Key/团队变更、支付和所有后台写者。
最终快照必须在旧写者退出后生成，包含已提交 WAL；不能直接复制活动 .db。
对当前规模优先冻结后的全量按键/行指纹比较，处理新增、原地更新和软删除；仅用 max(id)
或不完整的 updated_at 会漏掉旧行余额变更。候选未开放时按绝对期末状态幂等导入。

**新账本接受第一笔业务写入之后，不能直接切回旧 SQLite。** 此前可撤销目标并恢复旧侧；
此后默认保留 PG 权威，关闭新写、修复或回退兼容同一 PG 的目标程序。只有覆盖全部交易
类型且演练通过的反向迁移，才能允许跨回旧核心；首版不承诺尚不存在的自动反向回放。
供给账号 refresh token 也必须只有一个刷新 owner，不能恢复成旧快照中的过期状态。

切换前撤销也要处理已确认接收的支付通知：先停止目标消费者，将已回复成功的持久回调
收件记录交回旧侧并幂等履约，确认唯一消费者后才恢复旧入口。这些记录不能随候选 PG
清空/重建而丢弃；“目标还没有客户交易”不代表已经确认接收的支付事件可以忽略。

依据：[排空](host_cutover.py#L102)、[适用模式和同库结构](host_cutover.py#L210)、
[回退边界](host_cutover.py#L330)、[异步退款](../../service/billing_session.go#L89)。

## 前端最小范围与 P1

采用 Sub 原生 Vue 页面，不移植完整 React 外壳。复用
[RealYu wordmark](../../web/public/brand/realyu-wordmark.png)、现有 favicon/图标及
[浅深色主题](../../web/src/styles/theme.css)。Sub `site_name`、`site_logo` 原生设置优先使用；
主色在 `frontend/tailwind.config.js` 的 primary 色阶及 gradient-primary 统一映射。
横向 wordmark 与原生方形品牌槽需做最小尺寸/contain 调整；favicon 使用适合小尺寸的现有
图标，不把长 wordmark 强行压成方形。保留应有的上游许可和作者署名。

首版必需页面是登录、个人/团队切换、Key、余额/当期套餐、充值/支付返回、用量与必要历史、
团队成员/额度管理、下载/安装引导。原生支持的直接复用；团队与 CNY 单位适配属于正确性，
不能因“只改配色”而省略。布局重做、动效、营销首页改版和非必要报表后置。

门户还需核对 `backend_mode_enabled`、支付/订阅功能开关及公开 API 地址，不能直接沿用
当前仅作内核的配置。个人/团队切换时按作用域隔离缓存并取消旧请求；付款、移除成员后
刷新对应主体数据。旧标签页、旧 JS 和既有 Sub 管理登录状态不能导致身份混用。
新用户注册、团队邀请和首次权益也纳入业务验收；新用户若改为要求验证邮箱，应作为
明确的产品行为变化记录，不能用它阻断已有用户名账号。

Sub 品牌入口：[站点设置](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/frontend/src/views/admin/SettingsView.vue)、
[主题](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/frontend/tailwind.config.js)、
[标题与 favicon](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/frontend/src/App.vue)、
[用户门户开关](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/backend/internal/server/middleware/backend_mode_guard.go)。

P1 运维包括目标 Windows 服务安装/自动启动/恢复、备份异机恢复、告警送达、代理/TLS和
完整客户端验收。两个 Tunnel 共享代理的共因仍单列，不因迁核心而关闭；网络异常不能
自动触发跨账本回退。站点品牌缓存、首屏 HTML、登录后页面、浅深色/移动端、支付返回及
错误页面需做浏览器实际验收。本轮没有执行这些候选 UI 测试。

## 最小开发、演练和切换顺序

| 阶段 | 唯一客户账本 | 交付/出口条件 |
| --- | --- | --- |
| 1. 固定合同与候选 | 当前 SQLite | 固定真实使用的功能/价格/Key/实例权益；准备 Sub fork 与隔离端口/PG，完成最小身份、资金、团队、旧客户端及支付适配，品牌只做上述范围 |
| 2. 初次导入与验收 | 当前 SQLite | 一致快照 S0 → 隔离 PG；实体映射、幂等导入、单位转换及 usage 重放通过；候选不接生产回调、不双刷新真实账号、不产生重复客户消费 |
| 3. 全量演练 | 当前 SQLite | 从代表性快照演练冻结/排空/S1 差异导入/逐项对账/重启恢复；记录每步上界、超时退出、旧订单接管和目标写入后恢复办法 |
| 4. 最终一致窗口 | 旧侧仅完成已接受工作，然后两侧暂停新写 | 暂停新推理与新 WS turn、交易/身份/权限变更；收齐在途结算并停止旧写者，停止投影/credit worker 并禁止自动重启，核清未决 intent；支付可靠保存；S1 最终导入、源不再变化证明与逐主体对账 |
| 5. 目标接管 | 仅目标 PG | 固化权威状态后切同一入口，旧 Key 恢复；只有目标履约回调和客户消费；真实客户端/支付验证与逐笔对账，目标首笔写入后按前向恢复边界处理 |
| 6. 退休旧核心 | 仅目标 PG | 观察与恢复演练通过，卸载/归档第 4 阶段已停用且禁止自动重启的旧服务及补额 worker；旧库只读归档，保留必要旧 URL/响应签名兼容。兼容层移除按实际使用证据决定 |

最终一致窗口中，首页、下载和维护说明可以继续提供；余额展示明确冻结时刻。
排空失败且尚未转换权威时应放弃本次切换、恢复旧入口。不能为压缩窗口强杀结算状态
不明的请求。窗口时长根据演练给出，本轮没有可承诺的零停机或固定分钟数。

## 验收清单与完成定义

| 验收组 | 必须观察的结果 |
| --- | --- |
| 身份 | 旧用户名及两种哈希合成样本可登录，Unicode/空白/超过72字节旧密码不被截断；错误密码、空密码、禁用/删除不可越权；无新赠金/默认套餐；管理员权限、注册邀请和密码恢复/邮箱绑定正确 |
| Key/安装器 | 既有完整 Key 不改配置可调用；撤销/过期/IP/模型/额度限制一致；Windows/macOS/WorkBuddy 的 key-check JSON、diagnostics 和所有既有下载入口实际可用 |
| 团队 | 个人/多团队/多成员隔离；共享资金只扣一次；并发耗尽和成员 cap 正确；退组/解散时在途费用仍落原资金主体 |
| 续聊 | 切换前后的 HTTP/SSE/WS、Codex 新聊/恢复；旧签名有效且有上游状态时可续接；跨主体、篡改、失效状态正确拒绝且无误扣 |
| 期初权益 | 逐主体余额、赠金、历史权益来源、7 个在用实例的原到期/重置/已用及所有 Key 限额一致；重复导入和失败后重跑不二次入账 |
| 计费 | 同一 usage 下核对单位、缓存、type59 Fast 规则、图像 token、工具费、失败/取消；保留差异明细，不能只比总金额或用浮点容差掩盖差异 |
| 支付 | 新旧订单通知/主动查单并发、错金额/错商户/错误签名、重复回调、晚到付款、退款；客户权益只变一次且可追溯 |
| 核心 API | 复用现有 SDK 冒烟规范，在单核心候选重新验证 PDF/真实搜索引用/函数工具/最终响应/图像生成编辑与计费；CLI与桌面分别记录结果 |
| 运行恢复 | 长流自然结束与 WS 重连、窗口超时退出、控制器/服务崩溃、PG/Redis恢复、单写和单刷新owner、Windows重启/异机恢复及告警 |
| 品牌/前端 | 原生配置与主题一致、Logo无裁切、favicon清晰；CNY收款/权益单位准确；核心流程桌面/移动端可实际完成，首屏无旧品牌缓存闪回 |

上述全迁验收项本轮均未执行。已有桥接的 13 类功能、20 项财务核对或健康检查不能标成
新候选通过。复用 [既有冒烟规范](../../lab/sub2api_e2e/README.md) 的协议/终态检查，
为单核心改接认证和数据断言；保留首次失败，真实支付/模型测试使用专用身份和明确证据。

认证设计依据为 [ASVS 5.0.0](https://owasp.org/projects/asvs)、
[Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)、
[Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、
[Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。
本轮为设计审查，没有实施认证变更或宣称完整 ASVS 合规。

本次交付仅审查、修订方案和文档校验；没有改现网配置、数据库、支付、代理节点或程序。
