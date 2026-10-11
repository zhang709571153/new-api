# UX3 已完成协议请求的资金只读验收

2026-10-11 08:33:58（Asia/Shanghai）对已完成的 SSE 一轮、同连接 WebSocket 两轮执行 PostgreSQL `REPEATABLE READ READ ONLY` 核对，三轮均通过。无新增 HTTP、模型调用、生产配置或账务写入。

| 已完成请求 | usage 条数 | settled 条数 | billing dedup 条数 | quota | USD 实际费用 |
|---|---:|---:|---:|---:|---:|
| SSE | 1 | 1 | 1 | 172 | 0.000344 |
| WebSocket 第 1 轮 | 1 | 1 | 1 | 14 | 0.000028 |
| WebSocket 第 2 轮 | 1 | 1 | 1 | 41 | 0.000082 |
| 合计 | 3 | 3 | 3 | 227 | 0.000454 |

`USD × 500000 = quota` 以 Decimal 精确相等核对，没有采用浮点容差或强制舍入。三轮 actor/payer/team 与 Key 的资金 scope 一致；subscription/wallet 拆分等于最终 quota。固定验收 Key 和时间窗内共三条资金记录，没有残留 reserved 或额外记录。SSE 原始输入 4408 Token 包含缓存读 4224；使用日志保存未缓存输入 184，二者按原生明确合同核对。

证据文件：`ux3-production-protocol-funding-20261011.json`。原协议结果摘要 SHA256 为 `aec70bcb811f655fdededb0cb17d86352c32a73cf2bfde89c99dc70aa8759a4c`；运行程序 SHA256 为 `353428ccb6a09a3f512bc8bced5c23364c0d4f7cb46f5cecccaea24782446569`。

## 关联方法和限制

上游 `response_id`、HTTP `request_id` 与 managed 账务的服务端逐轮随机资金 ID 不同，数据库没有三者的直接外键。本次使用原验收 Key、固定 08:27:11.537–08:27:18.871 时间窗、三组各自唯一的输入/输出/缓存 Token 数和原生 HTTP/WS 日志绑定建立唯一对应，然后以资金 ID 连接使用记录与去重记录。结果只保留 ID 摘要，不导出 Key、客户身份或请求/回复内容。

这是已完成请求的结算一致性核对，并非独立的消费前后钱包快照；reserved 为该 Key 与验收时间窗的计数，不表示所有客户流量均无处理中请求。原协议工具首次依赖检查失败发生在零网络/零模型调用阶段，原首错由主验收目录保留，本次未覆盖。

## 复用边界

`audit_ux3_protocol_funding.py` 是此 Windows 主机的固定验收工具，依赖既有私有 plan、manifest、凭据、服务日志及协议摘要路径，并严格限制数据库、已发布程序 SHA 和三轮验收集合。它不是可直接用于其他主机或未来版本的通用脚本；修改目标前须重新审查护栏。原始私有输入不进入 Git。每次运行产生唯一目录和不可覆盖的结果，失败单独保留；该脚本没有发送请求、补扣、退款或重放功能。
