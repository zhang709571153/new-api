# 客户人民币与管理员入口交付记录（2026-10-10）

## 状态与来源

本记录描述原生 Sub2API 候选源码及本地验证，**写入时人民币版本尚未部署**。现网已切换原生核心，不代表这里的人民币、入口及品牌修改已经公开生效。最终发布必须另记录二进制 SHA、配置、公开页面与业务验收结果。

原生源码由本目录 `singlecore-source.json` 所固定的上游和补丁重建；以下 `backend/`、`frontend/`、`deploy/realyu/` 路径均指**补丁应用后的源码树**，不是本交接仓库的相对文件链接。详细契约在补丁内 `deploy/realyu/CUSTOMER-CURRENCY.md` 与 `deploy/realyu/ADMIN-ENTRY-AND-BRANDING.md`。本记录不包含真实用户资料、Key、商户密钥或私有数据库。

## 历史要求与单位契约

2026-09-26 10:05:58（北京时间），用户要求：“网站上的余额计成人民币……按照7元/美元……首页的gPT相关的api计价还是按照美元展示……费用，我的用量，工作台我的团队等等，都用人民币计价。”

原始 session `01a0cde7-1581-7d81-bfe5-8f5ae06440ed`，分支 `01a0db76-10af-7ae3-ac28-17b9bc5f361c`；会话文件 `rollout-2026-09-26T10-05-42-01a0cde7-1581-7d81-bfe5-8f5ae06440ed_01a0db76-10af-7ae3-ac28-17b9bc5f361c.jsonl` 第 9 行。同日 16:27:27，第 2553 行在列举输入/输出/cache 的美元单价后再次明确：“另外这个详情页面中 总费用应该是￥不是美元”。续分支 `01a0dcd4-ebe8-7f61-9808-bd8ac0c73127` 第 10 行保留该澄清。这里只保留相关短摘录，不复制完整会话。

2026-10-10 本轮只读核实原配置 `USDExchangeRate=7`、`GroupRatio.default=0.125`。汇率与客户计费倍率不同：实际费用已含 group 倍率，展示不得再乘。用户后续选择新 Images 接口 token 计费，本次不恢复旧按张图片附加费。

- 账本和原生 API 金额保持 USD；托管资金仍是整数 quota，`500000 quota = 1 USD`。
- 客户费用金额、余额与额度展示为 `原 USD × 汇率`；团队 cap 展示为 `整数 quota / 500000 × 汇率`。
- 模型参考价格、输入/输出/cache 等单位价格保持 USD/1M 等原单位；上游账户成本保持明确的 USD。
- UI 输入仅在提交 API 时转换一次。USD 金额沿用 8 位小数边界，团队 cap 沿用整数 quota；BigInt 十进制计算避免浮点中间误差。打开表单不修改原值，未编辑时原金额保留。
- 本次不修改结算公式、倍率、原始 usage、历史账单、预占、退款或钱包。不追补、不重扣。汇率变更只改变历史 USD 金额的显示等值；历史成交汇率快照尚未实现。

## 配置与覆盖范围

部署必须显式提供核实过的 `REALYU_CUSTOMER_USD_TO_CNY`；当前配置应为 `7`。管理资金模式不猜默认汇率：缺失/无效时发布 CNY + 空汇率，界面显示 `—` 并拒绝金额编辑。未启用 RealYu funding 的原生部署保持 USD/1。

后端通过 `/api/v1/settings/public` 及 HTML 初始配置发布同一 `customer_currency`、`customer_usd_to_cny`。对应实现为 `backend/internal/service/realyu_customer_currency.go`；前端统一边界为 `frontend/src/utils/customerMoney.ts`，由 app settings 加载配置。

| 类别 | 原始 API / 账本 | 客户展示或输入 | 保留边界 |
|---|---|---|---|
| 钱包、header、个人资料、工作台、冻结余额 | USD | CNY | 不变更资金 |
| 用量实际/标准总费用、input/output/cache 费用分项、详情、图表 | USD | CNY | 模型单价仍 USD；图表原数据保留 USD，仅轴/tooltip 格式化 |
| 用量 CSV/Excel | USD 原始 API | CNY 费用列，标题显式币种 | 上游成本另列 USD |
| 模型目录、单位参考价格 | USD/1M 等 | USD | 不乘汇率或再次套 group 倍率 |
| 上游账户成本、供应商标准成本 | USD | USD | 客户 user_cost 另显示 CNY；共轴时明确 USD/CNY 等值 |
| API Key 消费、额度、5h/日/周限制及批量修改 | USD | CNY 输入/展示，提交 USD | 0 无限额、未编辑值保留；微小正数不能变成无限额 0 |
| 团队/成员 cap、已用、可用、用量 | integer quota / USD usage | CNY，提交 integer quota | 无个人钱包溢出；权限与结算不变 |
| 个人订阅已用/限额、权益额度 | USD | CNY | 商户 subscription.price 单独处理 |
| 兑换结果/历史、管理钱包、平台额度、提醒阈值 | USD | CNY，提交 USD | 次数、并发、天数不转换；全额扣减用原余额 |
| 默认初始余额、来源初始余额、平台默认额度 | USD | CNY，提交 USD | 加载/保存未编辑金额不漂移 |
| 商户实付、手续费、subscription.price、商户退款金额 | 声明的支付币种 | 保留原 currency | 已是 CNY 不再 ×7 |
| 充值入账/赠送、批量生图预留/费用、佣金展示 | USD | CNY | 不因此启用支付、生图或佣金功能 |

管理端兑换码机器导出仍是原生 `value`：balance 类型 USD，其他类型次数/天数。本次没有把该原始接口改为人民币。API/SDK 调用方仍遵循原字段单位，不能依据 UI 符号推断 API 变币种。

输入回归包括 `7142857 quota → 99.999998 CNY → 7142857 quota`、14 CNY → 1000000 quota、21 CNY → 1500000 quota，及原生 USD 8 位小数、无效/空输入、极小负数、缺汇率拒绝。

## 支付能力尚未验收的边界

2026-10-10 20:59:37（北京时间）只读生产检查：`payment_enabled` 未配置（原生解析 false），启用的 payment provider instances 为 0；充值倍率与订阅支付汇率未配置。私有聚合证据为 `customer-currency-payment-readonly.json`。**真实商户支付与退款 NOT_RUN**；本轮未启用商户、未调整生产充值规则。旧支付回调持久 inbox 不等于新支付履约完成。

原生 `balance_recharge_multiplier` 是每单位支付币种到账多少 USD，和显示汇率不同。若实付 CNY、无赠送，必须使到账 USD × 汇率按既有精度对账于实付 CNY；不能默认 1 CNY → 1 USD，再显示为 ¥7。启用商户前必须校准支付币种、该倍率、手续费及赠送，并做实际收款、唯一入账和退款闭环。充值预览已显示“1 支付币种 = 客户到账金额”，但这不是商户联调证据。

## 管理员入口与品牌

按用户 2026-10-10 的明确要求，候选移除首次进入确认弹窗及对应前端路由/API 拦截、后端确认门。**不自动同意条款，不创建/修改确认记录**；原法律文档、许可与归属声明保留。

管理员 JWT/API Key 验证、数据库角色/状态、吊销检查、审计、限流和敏感操作 step-up 均保留。普通用户没有获得管理员能力。回归实际注册的 settings、payment config、pages 三组管理路由，不用仅 mock 页面代替鉴权验收。

补丁内 `deploy/realyu/brand-settings.json` 只包含五个显式设置：`site_name`、`site_logo`、`site_subtitle`、`api_base_url`、`frontend_url`。它不是每次启动覆盖的默认值。发布后先保存原状态，再通过正常管理员 `PUT /api/v1/admin/settings` 部分更新，触发原生 HTML 缓存失效；不能仅 SQL 写表，也不能调用确认接口伪造接受记录。

`/brand/realyu-wordmark.png` 是已嵌入资源，触发现有 RealYu 品牌及团队导航。公开页面需重新加载配置并实际检查登录、品牌、`/teams`。注册开关与新用户赠送是独立决策，品牌更新不自动开放注册。安装器仍使用 `/downloads/realyu/`；不增加失效的旧 New API/setup 路径。

## 验证证据与重建

本地最终合并验证：2026-10-10 21:02，changed + untracked 前端 **31 suites / 426 tests PASS**；包括币种、钱包/额度、usage/account、团队和管理员入口。TypeScript 检查 exit 0。后端两个 currency 根测试 PASS，验证显式汇率与公开/HTML 配置一致。

管理员实际路由回归 `TestRealYuAdminEntryUsesAuthenticationWithoutFirstUseAcknowledgement` 覆盖三个管理端点 × 六种认证情况，共 18 个边界：有效管理员无确认可访问，匿名/坏 token/禁用/吊销仍拒绝，普通成员仍 403；确认记录不读写。前端实际 App mount、已注册路由守卫和 API interceptor 保持相同权限边界。

私有运行日志文件名（不随公开 Git 发布）：

- `customer-currency-final-all-tests.log`
- `customer-currency-final-typecheck2.log`
- `customer-currency-backend-tests-final.log`

首次失败保留；其中前端新增测试的 stub/DOM 引用问题及后端测试专用 stub 的编译问题已修正。最终通过不替代真实浏览器或真实商户验收。

后续 21:11 的隔离真实浏览器验证为 17 PASS / 2 FAIL：团队全体费用正确，但成员费用的 PostgreSQL `cost_usd` 返回 20 位小数字符串，触发显示解析器 18 位限制而显示 `—`。已修复显示解析，使用有界 BigInt 精确换算；输入精度限制保持，缺失/非法金额仍明确未知，合法零显示零。旧代码定向回归确实得到 4 FAIL / 19 PASS，修复后 23/23 PASS，TypeScript 检查通过。私有证据 `customer-currency-pgscale-red.log`、`customer-currency-pgscale-green.log`；首次浏览器失败证据保留。该修复仍需重建后的真实浏览器复验，不能用单测替代或放宽断言。它不修改后端、账本、团队权限或额度提交逻辑。

交接 `Prepare-SingleCore.ps1 -Test` 保留已有登录、profile、品牌、模型目录与付款结果回归，新增统一 currency helper、团队/Key/批量额度、钱包/默认设置/兑换/充值、usage/dashboard/account 与 App/router/client 入口套件。Profile 金额用例已被原 profile 目录覆盖，不重复列入。现有 Go `TestRealYu|TestRealyu|…` 过滤已覆盖新 currency 与 admin-entry 根测试，无需重复运行。同一脚本还做类型检查、前端构建与固定上游补丁校验；它不安装服务或操作生产数据库。

Cow Key 的历史 unlimited 负 remaining 导致 policy 503 属于另一项兼容性热修复。修复只规范化非团队 unlimited Key 的可用额度展示：历史负值、已用、钱包保持原样；有限 Key 仍用原生限额，团队仍用实时成员/套餐可用额，模型限制仍执行。独立只读复核未发现该变更放宽财务或模型权限。

该专项私有日志 `cowagent-key-pg-red-20261010T130606Z.log` 在旧代码上复现负 remaining 失败；`cowagent-key-pg-green-20261010T130704Z.log` 为三个真实 PG 根测试通过；`cowagent-key-policy-middleware-green.log` 为八个策略用例通过。由专项任务另提供脱敏报告 `lab/sub2api_e2e/cowagent-key-fix-20261010.json`。不能把币种通过数作为这个热修复的证据；本记录写入时尚未发布修复、尚待真实客户端路径复测。发布前以最终补丁/manifest 与各专项报告为准。
