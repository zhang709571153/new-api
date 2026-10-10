# RealYu UX 严格验收矩阵（2026-10-11，候选）

状态规则：`PREPARED_NOT_RUN` 为脚本/用例已准备，不能当作通过；`PASS` 必须有对应版本、二进制 SHA、环境与原始证据。生产发布、隔离 HTTP、真实浏览器、真实付费请求分别记录，不能相互替代。此矩阵当前未表示新版已部署。

## 环境与保护

- 自动脚本 `ux_history_http_e2e.py` 默认离线；显式 candidate-ready + version + SHA 后才能访问。
- 仅 HTTP 127.0.0.1:29481、PG 127.0.0.1:29490/realyu_singlecore_e2e；复用私有 team-http-fixture，核对原合成前缀、账号和禁调度合成上游。凭据在内存中，不进入日志/Git。
- 先核对候选进程 PID/可执行文件/SHA/端口与 `/api/status`。不启动、重启或替换进程，不调用 `/v1/responses` 等模型接口。
- `seed` 只新增专属 SHA/source ID 的合成 history fixture；独立私有 receipt，旧记录不覆盖。归档库导入器的12项验证另行保留，不用 HTTP fixture 冒充真实31,295条生产归档。
- `run` 只读与预期拒绝的 POST；邀请测试使用 fixture 自有的临时 invite，必须恢复原行。任何意外成功或财政指纹变化都算失败并保留现场，不通过补数据掩盖。
- 种子/运行前后核对 wallet、key额度、funding、订阅、成员周用量、付款的计数/内容摘要。正常登录的last-used/session变化不属于资金变化，不误判为账务写入。

## HTTP 自动验收

| ID | 角色与操作 | 必须满足 | 当前执行状态 |
|---|---|---|---|
| A01 | 匿名 health/status | reviewed version、engine=Sub2API、single_core；完整JSON | r1 HTTP PASS |
| A02 | admin/owner/member/普通账号登录 | 原生登录成功，主体ID与受保护fixture一致；不输出token | r1 HTTP PASS |
| A03 | 匿名读取 history/list/summary/setup | 401；不能回任何客户数据 | r1 HTTP PASS |
| H01 | 每角色默认self list+summary | native+legacy；只本人personal及本人在各队调用；费用/Token精确匹配独立PG计算 | r1 HTTP PASS |
| H02 | 个人筛选 | 排除team与legacy_unknown；owner付款身份不能带出其他actor | r1 HTTP PASS |
| H03 | 普通/member/owner/旧role10访问platform | 403；列表和summary均校验 | r1 HTTP PASS |
| H04 | 真正admin platform及user_id过滤 | 全量已归属+deleted actor；指定用户仅该actor，旧role10不扩大 | r1 HTTP PASS |
| H05 | self/personal附带user_id/member_id/team_id | 400；不能伪造subject；本轮HTTP实测user_id，其余由handler合同回归覆盖 | r1 HTTP PASS |
| H06 | 日期/model/page/page_size | 原始日期不变，end exclusive，页间无重复；stats不随页变化 | r1 HTTP PASS |
| H07 | 原始未知归属 | unknown本人/admin可见；deleted actor仅platform、actor null；原4deleted-team不入用户接口 | r1 HTTP PASS |
| H08 | cache/source/token总数 | cache unknown=null；显式0仍0；旧cache包含在input时不双加；native独立cache计一次 | r1 HTTP PASS |
| H09 | 小额与人民币 | API费用decimal string USD；1quota=0.000002USD；页面按配置汇率换一次，模型价格不换CNY | HTTP金额自动；人民币页面由浏览器验收 |
| H10 | native settled/未决 | 已结算按funding实际整数charge；reserved不伪装完成；不因查询结算/退款 | PG自动回归PASS；r1 HTTP既有样本累计PASS |
| K01 | owner/member personal+team setup | 重复读取同canonical ID；scope/user/payer/team匹配；禁止按新旧ID/路由组猜Key。既有owner合成fixture故意保留7把personal且无旧绑定，必须409，不允许挑最新；member单一personal及双方team必须200 | r1 HTTP PASS |
| K02 | setup响应安全 | 不含key/secret；实际显隐/复制只能走本人owned detail | r1 HTTP PASS |
| K03 | managed非管理员POST /keys | 403、固定管理错误码；key总数和额度不变。全局管理员保留维护权，本测试不发送admin创建Key | r1 HTTP PASS |
| K04 | 读取他人key/detail | 403或404；不回密钥 | r1 HTTP PASS |
| T01 | owner用本人团队invite join | 409；不创建新成员/Key，不改旧邀请归属 | r1 HTTP PASS |
| T02 | owner加入另一已有team | 409；不切team/资金主体 | r1 HTTP PASS |
| T03 | member越权改cap | 403；其他成员管理由既有权限回归与浏览器分别验收 | r1 HTTP PASS |
| P01 | 匿名公开目录 | currency=USD，严格字段白名单，仅配置公开模型；无group/account/provider/代理/凭据 | r1 HTTP PASS |
| P02 | 中文/英文公开页面 | 中文/英文正文、品牌/导航、完整响应；无客户信息或后台seed | r3 四组合屏幕已观察；交互及待复验项见下文。本脚本不验证SSR正文 |
| P03 | 匿名后台/登录用户无权后台 | 401/403，不以SPA返回200当API成功 | r1 HTTP PASS |
| F01 | 所有用例后账务校验 | 原wallet/funding/sub/member/key金额/付款摘要不变，唯一专属fixture按receipt管理 | r1 HTTP PASS |


本轮真实HTTP：`realyu-singlecore-v0.2.15-20261011-ux2`，二进制SHA `5a458c4cbd61148fd1ff3bb3cb25aac40fe88effa9e494f92c9de75e3628fd3b`。首轮50/51，公开目录503因隔离fixture完全没有渠道（signup group正确）；保留首错。显式 `catalog-seed` 只增加2个公开合成模型的价格，不创建/启用上游账号；原合成账号仍disabled/unschedulable。重跑51/51 PASS，资金指纹完全相同。脱敏证据见交接仓 `lab/sub2api_e2e/usage-history-http-20261011.json`；不可据此宣称生产已归档或CUA通过。

## 真实浏览器证据：屏幕覆盖与交互断言分开

截至源码冻结，主流程通过 Codex in-app browser/CUA 在隔离 29481 留下 `runtime/ux2-browser-20261011/browser-results-before-r4.json`。其中45条记录混合了页面观察和交互结果，且包含一次已明确失败的导航；**不能写成45/45通过**。r3二进制SHA为 `2e9021c288c2294f34a14f038b40c5efa3652e6f2ca7dc2f689e4fdf63b22f5b`。最终构建改名r5以保留已哈希的r4产物；本节记录r1–r3事实，最终结果另存交接仓，不能由本节推定上线。

四组合为中文/英文×桌面/窄屏。请求视口为1440×1000、390×844；调用记录另实测360×800。滚动条使实际内容宽度为1432/1440、382、352；不把截图像素冒充请求视口。下表名称均省略`.png`，文件在同一私有证据目录。`已观察`表示该URL、语言、布局已由CUA检查且记录无横向溢出；不表示页面每个控件均执行成功。仅凭文件名不认定通过。

| 页面/实际路由 | 中文桌面 | 英文桌面 | 中文窄屏 | 英文窄屏 | 范围 |
|---|---|---|---|---|---|
| 匿名首页 `/home` | r3-home-zh-desktop-light | r3-home-en-desktop-light | r3-home-zh-mobile-dark | r3-home-en-mobile-light | 四组合已观察；公开canonical/语言/robots记录齐全 |
| 公开价格 `/pricing` | r3-pricing-zh-desktop-light | r3-pricing-en-desktop-estimate | r3-pricing-zh-mobile-light | r3-pricing-en-mobile-light | 四组合已观察；模型单价USD |
| 公开文档 `/docs` | r3-docs-zh-desktop | r3-docs-en-desktop-workbuddy-mac | r3-docs-zh-mobile-actual | r3-docs-en-mobile-reloaded | 四组合已观察；快速切语言后导航缺陷另列，不能被随后直达成功覆盖 |
| 管理员平台概览 `/admin/dashboard` | r3-admin-overview-zh-desktop-light | r3-admin-en-desktop-details | r3-admin-zh-mobile-dark | r3-admin-en-mobile-details | 四组合已观察；含独立详细诊断入口 |
| 成员个人工作台 `/dashboard` | r3-member-zh-desktop-light | r3-member-en-desktop-light | r3-member-zh-mobile-light | r3-member-en-mobile-light | 四组合已观察；self累计及Setup |
| 调用记录 `/usage` | r3-usage-zh-desktop-detail | r3-usage-en-desktop-light | r3-usage-zh-mobile-dark | r3-usage-en-mobile-light | 四组合已观察；窄屏含360×800 |
| 队长团队 `/teams` | r2-owner-zh-desktop-light | r2-owner-en-desktop-light | r2-owner-zh-mobile-light | r2-owner-en-mobile-light | r2四组合已观察；队长筛选与成员权限由独立断言补证 |

`r3-admin-zh-desktop-light`实际URL是`/dashboard`，只是管理员自己的个人工作台，**不计入平台概览**。`r3-docs-zh-mobile-light`实际仍停在`/pricing?lang=zh`，是首次导航失败证据，**不计入文档页成功覆盖**。`r3-admin-accounts-zh-mobile-dark`只证明页面被观察，不能盖过disabled状态裸翻译键缺陷。

| ID | 可重复交互与预期 | 截至r3的证据/状态 |
|---|---|---|
| B01 | 价格模型选择、估算滑块；参考单价始终USD | `public-model-selector-slider-usd` PASS |
| B02 | 平台历史按actor筛选，原生详细诊断仍可进入 | `admin-actor-filter`、`admin-native-diagnostics-available` PASS；筛选主体为合成fixture |
| B03 | 队长没有join自身动作、可按成员筛选；成员仅看本人记录 | `owner-no-join-and-member-usage-filter`、`member-team-own-records-only` PASS；HTTP越权独立PASS |
| B04 | 调用记录空态/筛选按钮可用，主操作对比度可辨认 | `usage-filter-empty-and-primary-button` r3 PASS；保留r2白底白字首错 |
| B05 | 文档四种应用/系统页签可切换，英文公开页刷新保持英文 | `docs-four-app-os-tabs`、`english-public-reload-preserved` PASS；不等价于执行安装器 |
| B06 | Setup默认完整命令可见、眼睛隐藏；切scope先清旧内容，再读canonical并复制 | 早期隐藏状态个人复制、个人/团队Key不同、WorkBuddy macOS团队命令PASS；r3 `member-team-copy-uses-existing-key` PASS。不记录密钥。默认策略按9/28合同；最终构建需抽查显隐/快速切scope |
| B07 | 普通用户整条`/keys`入口隐藏，直接访问redirect；Setup保留canonical；logo返回公开首页 | `non-admin-key-route-redirect`、`signed-in-logo-returns-public-home` PASS；管理员维护入口保留 |
| B08 | 中文/英文、深浅色及窄屏可读，无横向溢出 | 上述页面观察PASS；这不是每个页面×每种主题的穷举，也不证明实体Safari可用 |
| B09 | 快速切语言后立即跳文档，URL/正文应到目标页 | **r3 FAIL，待最终构建真实CUA复验**；不能以reload或第二次点击代替第一次导航成功 |
| B10 | 上游账号disabled状态中文/英文都显示文案 | **r3 FAIL，待最终构建真实CUA复验**；粗略页面rawTranslationKeys=false不能覆盖具体组件首错 |
| B11 | 浏览器告警/错误与请求失败分别留证 | 主流程本次读取warn/error为0；仅该读取窗口，不是整个会话或所有网络请求零错误 |
| B12 | 钱包买套餐/注册 | 本轮没有再收款。复用此前隔离真实UI/PG证据；如改路径必须重验。真实商户未验收 |

首错必须保留：r1公开目录503（fixture无渠道，添加纯合成模型配置后51/51）；r2主按钮白底白字（r3修复）；r3快速语言导航与disabled裸翻译键（最终构建待复验）。修复后使用新结果/截图，不覆盖旧文件。

## 最终候选与发布后的验收边界

最终r5构建由主流程提供冻结SHA及进程receipt后，仅复用已存在fixture执行51项HTTP，不重复`seed`或`catalog-seed`：

```powershell
python deploy/realyu/ux_history_http_e2e.py
# PREPARED_NOT_RUN，无网络/数据库访问
python deploy/realyu/ux_history_http_e2e.py --candidate-ready --mode run --candidate-receipt <final-private-process-receipt.json> --expected-version realyu-singlecore-v0.2.15-20261011-ux2 --expected-sha256 <final-reviewed-binary-sha> --fixture-receipt <runtime>/history-ux-http-e2e/20261010T163124Z-5967fa9e7a/fixture.private.json
```

脚本校验PID、端口、exe内容SHA、进程创建时间、HTTP版本和固定隔离数据库；结果写入唯一`runtime/history-ux-http-e2e/<UTC>-<random>/`。7条合成种子不写native usage/资金，不迁入生产。脚本不启动/重启服务；fixture正在被其他验收修改时先协调窗口，不能把并行测试变化误判成产品资金变动。

最终结果只追加交接仓`lab/sub2api_e2e/`的脱敏记录，关联版本/SHA/私有原始证据。该源码矩阵冻结后不再以候选PASS预填发布结果。具体待办见[交接冒烟清单](UX-HANDOFF-SMOKE-CHECKLIST.md)。

- **待跑**：最终构建51项HTTP及B09/B10真实CUA回归；已通过的关键导航/Setup/权限抽查。
- **待发布后跑**：公开域名SSR/目录、self/platform累计与归属、一次性308归档与幂等、只读资金指纹验收。308发布与历史归档分别记receipt；不重放付费调用补历史。
- **本轮未覆盖**：实体iOS Safari/Android浏览器、真实商户交易与退款、客户机器安装器执行、持续稳定性SLA。IAB窄屏、文档页签、health均不能代替这些验收。
