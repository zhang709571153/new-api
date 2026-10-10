# 单核心供给与价格迁移（隔离演练）

本说明对应 2026-10-10 候选代码。工具不提供生产激活命令，不改变生产供给、客户账本或 Redis。授权发布由主部署流程执行；下列演练结果不代表已切流。

## 原生供给快照

`singlecore_supply.py` 的 `export` 从显式 loopback PG 28490 开启只读、可重复读事务。仅复制供给表：proxies、groups、accounts、account_groups、channels、channel_groups、channel_model_pricing、channel_pricing_intervals、三张 account_stats pricing 表、composite_model_routes，以及 8 个明确列举的 OpenAI 调度 setting。

不复制 users、API keys、usage、payment、用户订阅、user_allowed_groups。旧 Sub 的影子用户和内部授信余额不是客户的钱，必须排除；真实客户由 SQLite 快照导入器处理。

快照保持账号/proxy/group ID、状态、原 schedulable、所有 credentials/extra、限速时间、代理绑定、account_groups 优先级和价格 JSON 数字。快照包含 OAuth 与代理秘密，只能放在 Git 外的 `runtime` 或 `.private`，Windows 写入前限制 ACL。禁止上传快照、私有配置、真实测试凭据或原始客户数据到 Git。

实际源只读快照记录：3 个 OpenAI OAuth 账号（2 active、1 inactive）、1 个 proxy、2 个 group、3 条 account_groups；当时 channels/custom pricing/composite routes 均为空。2 个账号含 refresh token。原版 accounts 导出不能替代此快照：原版 payload 不包含完整 groups 绑定和账号 status/schedulable。

`import-rehearsal` 只接受 loopback、非 28490、`mode=rehearsal` 且数据库名含 e2e/rehearsal/candidate 的目标。需要输入 SHA 和 operation ID；仅允许无客户/Key/消费/支付的空目标，bootstrap group 替换另需显式开关并核对 ID/platform。所有账号被强制 `schedulable=false`，递归移除 refresh token；源快照保留原值，不被修改。事务失败整体回滚；同 operation 重跑校验输入与目标实际状态，目标被改过就拒绝。

仅设置 `TOKEN_REFRESH_ENABLED=false` 不足以避免请求路径 refresh。演练应同时禁调度并剥离 refresh。后续真实上游验收只由主部署任务明确选择 synthetic 客户及有限流量后开启指定账号。最终切换前必须退出旧 refresh owner、重新取供给最终状态，再移交刷新权，不能让两个运行实例刷新同一账号。

示例（所有路径/密码从私有配置读取）：

```powershell
python singlecore_supply.py export --source-config <private-source-config.json> --output <runtime\supply.json>
python singlecore_supply.py import-rehearsal --snapshot <runtime\supply.json> --expected-sha256 <digest> --operation-id <unique-id> --target-config <private-rehearsal-config.json>
```

## 价格保持

旧客户 default group 实际倍率为 0.125，供给 Sub 的 group 倍率为 1。不能直接继承供给倍率，也不能仅将倍率改为 0.125 后继续用模型目录价：例如 gpt-5.6-sol 旧基础 input/output/cache 为 4/20/0.4 USD/百万 Token，而原生目录为 5/30/0.5。

`singlecore_pricing.py map` 只读 SQLite options 和有限条消费日志，严格识别现有表达式语法；不会执行表达式代码。未知表达式、额外 group/group-group 比例、未覆盖的样本模型会显式列入 unmapped。输出是可审查配置，不会自动应用线上。价格用 Decimal 字符串保存，不以 float 容差判定相等。

```powershell
python singlecore_pricing.py map --legacy-sqlite <source.db> --output <runtime\pricing.json>
python singlecore_pricing.py apply-rehearsal --mapping <runtime\pricing.json> --expected-sha256 <digest> --target-config <private-rehearsal-config.json> --group-id 2
```

演练价卡写入必须在客户/Key/消费/支付导入前进行，目标账号全部禁调度。写入事务检查 PostgreSQL 存储后价格与输入 Decimal 完全相等；精度被截断则回滚。已有 group 渠道不会被覆盖。

生成配置包括：

- 8 个旧 tiered expression 模型的标准价、`len > 272000` 长上下文价、fast/priority 倍率；gpt-5.5 为 2.5，其余为 2。
- Flex 显式设为 1，避免 Sub 默认 0.5 改变旧规则。未配置 reasoning effort 额外加价。
- 客户 group multiplier=0.125、long_context_pricing_enabled=true、group.model_pricing=[]。
- 自定义阶梯放在 **channel** 价卡；Sub 会删除 group 价卡里的区间。渠道按 requested model 计费，只允许已配置模型。新增/别名模型应先扩展映射，不得落回目录价后称原价保持。
- gpt-image-2 使用已授权的 native Token 计费，旧 ratio-derived input/output/image input/image output 均 2 USD/百万，cache read 2、cache write 2.5；group 仍 0.125。长上下文保持平价。不重新引入按张人民币收费。
- 文本模型没有 img 独立项时，图片子 Token 仍属于相应输入/输出 Token。image_input=0 使用当前区间输入价，image_output=NULL 使用当前区间输出回退；需要模型目录 image_output/cache_image 价为 0。不能将图片价固定成基础价，否则长图像上下文少扣。目录变化后必须重新验证。

现有规则的明确边界：

1. 4950 个合成向量覆盖 9 个模型、标准/边界/长上下文、cache read/write、default/fast/priority/flex、18 个图像标准/长上下文组合。4936 个整数 quota 相同；14 个相差 **1 quota（USD 0.000002）**：6 个 float 半整数舍入、8 个极小 gpt-image-2 使用量的旧最小 1 quota / 新 0 quota。保留失败证据，属于已记录 P1；未重写 native 算费或使用 epsilon 隐藏差异。
2. 图片 cached-image 专用目录价若非零，目前价卡不能独立覆盖；不能声明该子类别已兼容。
3. `ultrafast`、非常规 service_tier 大小写/空白，以及上游返回 tier 与请求 tier 不一致，尚不等于旧请求表达式语义。客户端常规值与上游回传需要 E2E 核验。
4. 旧 Responses image_generation 的人民币附加费、web search/其他 tool surcharge 不由上述 Token 卡实现。只读 500 条样本中有 1 条含工具附加费；没有把它默默合并成 Token 价。实际工具定价策略由主迁移任务明确选择或补齐。
5. 套餐额度、个人混合扣费、团队周 cap 由独立资金模块保持整数 quota。供给账号的调度成本倍率不是客户价格倍率，不能以此代替。

## Redis 与旧续聊状态

供给源 Redis 只读盘点端口为 28391。一次 SCAN 样本共 2119 keys：response/account affinity 471、旧 owner 942、其他 sticky 54、旧登录 275、billing/auth 类 12、OAuth runtime 3、并发/连接 7、未列入允许名单 355。数量和 TTL 会随运行变化，这不是可直接恢复的完整快照。

候选现已实现 `realyu_legacy_response.go`：校验旧 `resp_ry1_` 的原 actor/team HMAC，然后按新用户及 Key 重新绑定 response owner。必须保留私有 namespace、identity_secret、源 actor/team 映射。不可继续按过期文档写成“旧签名完全未实现”。

| 状态 | 处理要求 |
| --- | --- |
| `sticky_session:<group>:openai:response:<response-hash>` → account ID | 延续上游路由的必要候选。保留 group/account IDs 与**剩余 TTL**；导出到导入耗时必须扣除。最终冻结旧写者后再做最终快照/选择性复制，不能延长 TTL。 |
| `sticky_session:<group>:openai:http-response-owner:user/key:*` | 旧值是影子用户/Key，不可当新客户授权整体复制。旧签名鉴权成功后候选重新绑定。原生 unsigned response 需另有可信 owner 映射，否则拒绝，不能放宽跨客户检查。 |
| 其他 sticky / `openai_responses_session_window:*` | 可能含旧投影 session 身份或窗口控制。按新旧 session HMAC/namespace 契约逐项核对；当前工具不自动迁移。 |
| `billing:*`、API key auth/rate caches | 不带入旧影子余额或旧客户标识；由新账本/权限重建。 |
| `refresh_token:*`、`user_refresh_tokens:*`、`token_family:*`、旧登录会话 | 不复制影子登录/管理登录；新登录正常建立。 |
| `oauth:token:*` / refresh lock | PG credentials 是可审计供给源；锁不可复制或与旧实例并行持有，移交后重建。 |
| concurrency/wait/live/inflight/调度缓存 | 先排空旧请求，不能把旧进程租约或未完成记账当新进程状态。未决费用按 durable outbox/receipt 单独核对。 |
| WS conn_id、session turn_state、连接池 | 原生实现是进程内状态，不能复制开放 socket。排空后需要客户端重连；不承诺毫无断连。 |

Redis 私有数据的**选择性导出/恢复尚未由本工具实现**；供给快照工具只操作 PG。不能以保留 PG account ID 代替实际续聊 E2E。若复用旧 Redis，仍必须处理 owner ID 冲突与账务/auth cache，不能全库直接共享。

## 测试与交付边界

`test_singlecore_supply.py` 7 测（4 个真实 PG）通过，`test_singlecore_pricing.py` 7 测（4 个真实 PG）通过。均仅在 `realyu_funding_e2e` 的临时 schema 写入并清理，不读取真实账号凭据作为测试数据。`realyu_pricing_migration_test.go` 用显式环境变量 `REALYU_PRICING_MAPPING` 读取脱敏规则/合成向量；无此变量就跳过。整数不相等仍失败，主部署报告必须如实记录上述 P1。

公开代码仅包含脚本、合成测试和文档。真实账号/proxy 完整快照、merchant key、数据库密码、Redis 值、旧身份 secret、支付 inbox 数据与客户记录全部留私有目录，不入 Git。生产切换、价格启用、refresh owner 移交和真实付费调用均由主部署流程另外执行。
