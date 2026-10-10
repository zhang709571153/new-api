# 2026-10-10 单核心生产切换记录

截至 20:40 CST，原生 Sub2API 已于 20:30:20 正式 ACTIVE，公网正在承接真实调用。
新加坡双 Tunnel、原生客户/团队/资金核心和旧 Key 均已切换，New API 不再作为客户账本权威。
在此之后不可恢复旧 SQLite。前端确认拦截移除、人民币金额和遗漏品牌配置正在合并更新，
后续更新只替换原生程序，保留同一 PostgreSQL / Redis / 身份数据。

## 原生公网验收

- r3 维护门 20:29:21.226–20:30:20.639，共 59.413 秒；两次先前尝试另列，不能把全部用户影响仅写成 59 秒。
- 实际原生二进制：`realyu-singlecore-v0.2.15-20261010-ws-owner`，Session 0 / LocalService。
- 模型目录、流式/非流式、随机 PDF 内容读取、真实联网搜索及引用、实际图片生成、函数工具往返均通过。
- 同一 WebSocket 两轮续聊通过；8 个文本模型逐一真实调用通过，保留完成事件、增量完整性和 usage。
- 图片为 1536×1024 PNG，2,600,168 字节，已查看像素确认“小比熊打丘丘人”，并非占位图。
- 切换前创建的真实 CLI 会话在切换后原 URL / Key / CLI home 直接 resume 通过，无需重跑安装器。
- 20:30:22–20:40:20 的 26 笔资金请求对账通过：25 settled、1 refunded，包含另 6 笔其他 Key 并发交易；
  钱包、订阅、成员周期、Key 实际用量、usage cost 无差额、无 pending、无归属/重复扣费违规。
- 旧用户名/密码的公网浏览器登录通过，应用 JS 错误为零；原生首次管理确认和缺失品牌导致的团队导航隐藏已定位，
  用户已明确要求移除确认，并补齐人民币展示，见后续更新记录。

首错不覆盖：搜索第一次携带 `max_tool_calls` 得到真实上游 400，移除测试器的该可选字段后新测试通过；
该参数仍为兼容性边界。首次 WS 测试误以 HTTP 规则要求 terminal output，实际 ACK、completed item、
usage 已完整到达；保留原失败，依据原始帧单独分类并修复测试解析，后续完整两轮重新通过。
没有将重试结果改写成首次全通过。

脱敏证据：[公网协议与账务验收](../../lab/sub2api_e2e/singlecore-production-protocol-20261010.json)。

## 已完成的网络切换

- 19:22：独立 `RealYuSgHy2` / `RealYuSgHy2EdgeProxy` 已作为 LocalService
  自动启动服务安装，运行于 Session 0。
- 20:03：副 Tunnel 切至新路径；20:13:43 原地复核通过。
- 20:14:41：主 Tunnel 切至新路径并通过复核，副连接器未被重启。
- 20:15:26：既有 Evidence / TunnelGuard 监控已跟随新路径，6 条健康路径通过；
  两 Tunnel 合计 8 个相同 flow ID 均证明双向收发增长，随后释放本轮 guard pause。
- 真实路径：cloudflared → GOST `19464–19467` → 独立代理 `17897`
  → 固定 Singapore HY2；两 Tunnel 各 4 条连接。生产不再依赖桌面 Clash 节点。
  原桌面路径保留用于审阅后的网络恢复，不代表有自动双出口容灾。

Windows PowerShell 5.1 将 curl stdout 中的中文按控制台代码页解码，曾使有效的
`/api/status` JSON 解析失败。修复后 curl 原始响应和 headers 写入唯一私有文件，
显式 UTF-8 读取；使用同一检查代码真实测试公网直连和 SG 代理均通过。
新增 `VerifyOnly` 可复核已切的连接器，不重复重启，也不覆盖首错。

## 两次核心尝试与恢复

| 尝试 | 维护开始 | 结果 | 实际观察 |
| --- | --- | --- | --- |
| r1 | 20:15:29 | 停旧服务阶段退出，20:16:04 旧权威恢复 | 公网首个 503 为 20:15:34，首个恢复 200 为 20:16:04；10 秒采样界限约 20–40 秒 |
| r2 | 20:21:44 | S1 导入后复核退出，20:22:38 旧权威恢复 | 公网首个 503 为 20:21:44，首个恢复 200 为 20:22:44；采样界限约 50–70 秒 |

两次均在 OPENING 之前，`opened=false`；没有新核心客户交易，也没有用旧 SQLite
覆盖已产生的新交易。两次维护期间 Tunnel 均保持 4 连接，无采样 1033，不能归因为 SG 断网。
两个 authority 回执和所有首次失败、CF-Ray/探针 ID 均保留在各自私有 operation 目录。

r1 原始异常未完整保留；同一 `WindowsHost.ps` 路径随后只读复现：中文服务提示导致
`UnicodeDecodeError`。子 PowerShell 现显式设置控制台与管道为 UTF-8，Python 保留严格解码；
真实 Windows PowerShell 5.1 中文 warning 回归通过。新增脱敏首错阶段/类型/固定代码及阶段时间。

r2 的客户导入与历史导入已完成：65 个用户、111 个源 Key（109 可导入）、3 个团队，
客户/Key/团队权益精确对账。后续读取已完成的 WAL 模式快照采用普通 `mode=ro`，
创建空 WAL，随后的严格快照检查因此拒绝。修复仅将已校验哈希的私有快照改为
`immutable=1`，线上源仍使用普通只读连接；没有删除 WAL 或原始证据。
同一真实快照经哈希验证复制后，完整剩余检查在 PG READ ONLY 下通过：
2 个 role10 用户、6 个团队权限关系、旧零额记录原样归档均确认，资金未作调整。
最终新增回归合计 30 项运行，24 通过、6 项外部 PG 集成测试本次跳过；之前真实 PG 结果另存。

## 第三轮执行边界

新 operation 为 `singlecore-sg-20261010-r3`，私有目录为：

```text
C:\srv\realyu-singlecore-dev\runtime\production-cutover-20261010-r3
```

保持同一 installation、目标库和身份密钥；重试前已确认目标仍为 staging、资金请求与
原生 usage 均为零、旧迁移 PG 会话为零、旧三个 writer Running、维护门已释放。
只复用逐件哈希核验的 S0 与已完成网络证明，不复用旧 authority、activate、manifest 备份。
仅执行 `Activate`，不重复滚动 Tunnel。默认 90 秒尝试、30 秒恢复预留不变。

- r3 计划 SHA256：`16f8c039513ecf21cbc64c6f1bffa4977426bac1851aade01541f77a608b1fe3`
- 切换器 SHA256：`5367a4ce47e15fc091c85b8a9848a2f974fcc99af2fa881525802680f9e41bf9`
- 原生二进制 SHA256：`0124bffafc6232702bb43b723f21d9999e88f89e2d37b8a8261925ee7981fad9`

正常管理员入口：`Activate-ReviewedRetry.ps1`。不要重复运行已产生 authority 的 operation；
先判断 ACTIVE / OPENING / 恢复状态。OPENING 之后只能向前恢复，不能恢复旧账本权威。
公网验收与 CLI resume 的 authority 检查已指向 r3；只读账务审计需显式传入 r3 `plan.json`。

## 后续更新仍须保留的验收边界

1. 确认 ACTIVE/opened、实际原生二进制与公网 version/engine/single_core，一致后才读取验收 Key。
2. 记录 PG 只读资金基线，运行原 Key 的文本、PDF、真实搜索/图片、工具往返、WS 两轮和模型覆盖。
3. 同一公网 URL、Key、CLI home 和会话执行 resume；不重新安装客户端。
4. 公网浏览器用旧用户名/密码登录，检查原生管理员页面、团队授权和客户金额。
   20:38 用户明确要求关闭首次确认；应移除前后端阻断，不代写已同意记录，不削弱正常认证/管理员权限。
5. 对账资金扣减、原生 Key 用量、usage/dedup、actor/payer/team 归属；原始精度保留，差异不舍入隐藏。
6. 复核旧 Sub2API / prewarm 已停止并禁用，六路径新鲜、两 Tunnel 各 4、8 条 SG 流持续双向增长。

隔离候选通过不等于公网验收完成。原生支付实商户、异常 WS 耐久结算、旧工具附加费、
少量 1 quota 差异及旧 role10 的全局权限缺口继续记录为后续事项。
既有路径样本的最大间隔应按实际统计披露；网络采样不能作为无人登录重启或长期稳定保证。

迁机入口：[Windows Server 交接](SINGLECORE-WINDOWS-HANDOFF.md)；
源码与行为边界：[切换教程](SINGLECORE-HOST-CUTOVER.md)、
[公网验收](../../lab/sub2api_e2e/SINGLECORE-PUBLIC-ACCEPTANCE.md)。
