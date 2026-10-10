# RealYu 单核心 UX 公网浏览器验收

2026-10-10 23:24:52–23:26:07（北京时间），发布后使用独立临时 Edge profile 对 `https://api.realyu.fun` 验收，**19 / 19 PASS**。

- 实际版本：`realyu-singlecore-v0.2.15-20261010-ux`。
- 发布二进制 SHA256：`88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081`；版本由本轮公网 `/api/status` 重新核验，二进制摘要由发布 receipt 对应。
- 机器可读结果：[public-browser-strict-20261010T152452Z.json](../../lab/sub2api_e2e/public-browser-strict-20261010T152452Z.json)。
- 工具：[production-headless-browser-strict-check.cjs](../../lab/sub2api_e2e/production-headless-browser-strict-check.cjs)。

## 实际通过的路径

旧用户名和密码直接登录原生控制台，全程没有管理员首次确认门，也没有提交任何确认。匿名管理接口仍返回 401。

工作台、原生账号管理、全部用量、用户管理均同时检查了真实 API 数据和页面渲染。RealYu logo 成功加载，团队菜单可见；工作台名称、隐藏版本/更新入口和兑换/优惠码入口符合本轮要求。Codex / WorkBuddy 的 Windows / macOS 四种命令预览显示正确下载入口及 `sk-****` 掩码，未点击真实 Key 显示或复制。

真实团队列表返回 3 个团队；本轮选中团队显示 4 名成员和 25 条请求明细。昵称、成员行数、请求数、输入/输出 Token 与 API 一致，展示费用等于真实原生 USD 费用乘显式汇率 7。报告不包含团队名称、用户名称、明文 Key 或实际账本金额。

没有同源 HTTP 错误、未捕获 JavaScript 错误或非预期 console error。测试主动阻断了 Cloudflare beacon、Airwallex 第三方 SDK，共 18 条阻断；对应 console error 单列为测试隔离结果，不归为产品故障。没有业务写请求或强制刷新尝试。

## 验收边界

- 当时 `model_plaza_enabled=false`，因此**未宣称公网模型价格目录的美元展示已通过浏览器验收**。原隔离环境和组件结果不能替代这一生产边界。
- 本次没有注册、购买、充值、调用模型或改动任何用户/团队/额度/订单；旧用户只用于授权的登录和只读操作。
- 普通成员禁止越权来自此前独立合成 fixture 的浏览器、HTTP 和权限回归，本轮没有读取其他生产用户密码。
- 五张包含真实数据的截图以及 raw console 只存私有 runtime，不入 Git。测试未访问用户现有浏览器，未导出 storageState、HAR 或 trace。
- 这是一次有明确范围的公网抽样，不是长期稳定性或真实商户收款承诺。

随后在注册和六档套餐配置完成后，23:36:51–23:37:33 的[独立公网新用户验收](PUBLIC-SIGNUP-BROWSER-VERIFICATION.md)首轮 12 / 12 PASS，真实检查了无邮箱注册、零资金默认个人 Key、六档套餐与钱包方式预览，以及一组九个授权模型的 USD 价格表。该新增结果补齐模型目录边界，保留本轮目录关闭时的原始证据。测试新用户及 Key 已准确停用，没有订单或模型调用。

## 工具增量与复用

本轮工具新增只读批量用量及 `managed-subscriptions/query` POST 白名单；仍拒绝其他业务写入和 `force:true`。增加精确版本/品牌/CNY 7 的登录前校验、工作台四种掩码命令检查、隐藏入口检查，以及首个 preflight 请求的 CF-Ray / request_id 留存。匿名预检使用正常维护应用 User-Agent；不自动重试，不覆盖首次结果。

提交的工具默认离线：不传 `--active-confirmed` 时不联网、不读凭据、不启动浏览器。依赖现有 Node、Playwright 和 Edge；不安装或下载浏览器。实际执行必须显式传预期版本和私有 credentials 文件（字段 `admin_username`、`admin_password`）。`REALYU_TEST_RUNTIME`、`REALYU_TEST_RESULTS`、`REALYU_TEST_BROWSER`、`REALYU_TEST_PLAYWRIGHT_MODULE` 可配置本机路径；敏感 runtime 目录应置于仓库之外。默认输出也仅在 `.private-realyu-browser` 中，应保持不提交。

可移植副本仅替换依赖/输出路径的配置方式，已通过 `node --check` 和默认离线模式；没有为验证副本再登录一次生产。实际生产执行的原工具和首份结果均保留在私有 runtime，报告时间戳可用于关联。
