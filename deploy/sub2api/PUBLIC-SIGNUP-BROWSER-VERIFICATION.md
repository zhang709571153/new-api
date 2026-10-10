# 单个公网注册与购买页预览验收

2026-10-10 23:36:51–23:37:33（北京时间）已完成一次明确授权的公网新用户验收，**首轮 12 / 12 PASS**。只提交一次注册，创建一个随机合成用户，随后准确停用该用户及唯一 Key；没有注册重试。

实际版本 `realyu-singlecore-v0.2.15-20261010-ux`；发布二进制 SHA256 `88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081`。执行前重新核验公网版本及 manifest 摘要。

机器可读结果：[public-signup-browser-20261010T153651Z.json](../../lab/sub2api_e2e/public-signup-browser-20261010T153651Z.json)。本机执行工具与交付工具 SHA256 一致。

- 浏览器：[production-signup-browser-check.cjs](../../lab/sub2api_e2e/production-signup-browser-check.cjs)。
- PG 只读关联：[production-signup-readonly-db.py](../../lab/sub2api_e2e/production-signup-readonly-db.py)。

## 本次真实结果

- 正常 UI 无邮箱注册成功，保留用户名和昵称，直接进入工作台，无原管理员确认门。
- PG READ ONLY 与真实 API 确认：恰好一把个人 Key，actor/payer 均为新用户，零余额和 Key 用量；团队成员关系、创建团队、原生订阅、托管订阅、订单、购买授予、资金请求及使用日志均为零。
- 零余额仍可查看客户端配置入口；团队 scope 为空且不能使用个人 Key 作为回退。没有显示或复制明文 Key。
- 实际购买页展示六档已上架托管个人套餐，进入一档套餐预览后钱包方式可见。没有点击最终下单按钮，也没有充值、赠送或调用模型。
- 新用户真实授权可见一组九个模型，价格表单位为 USD；这补齐了 23:24 管理员只读抽样时模型目录尚未开启的边界。
- 同源 HTTP 错误和未捕获 JavaScript 错误均为零；九条第三方 beacon / 支付 SDK 请求由测试隔离策略主动阻断，其 console error 已单列，不作为产品故障。
- 清理的两次 PUT 都返回 200：唯一 Key 设为 `inactive`，准确新用户设为 `disabled`。随后 PG 和管理员 GET 均验证通过，零资金/订单/用量保持，未删除任何行。

四张截图、随机用户名/用户 ID/Key ID 与逐次 PG 快照仅留私有 runtime。Git 中的结果只含脱敏检查、状态、计数及请求编号。注册 request_id 为 `c87bfbbc-aa0a-495b-998b-bec0be530767`；没有首错可补，因为本轮所有路径首次通过。

公网套餐预览通过不等于真实商户收款验收；本轮仍未执行钱包支付或商户交易。此前隔离钱包支付/幂等通过记录保持独立。

## 执行方式

默认运行两个工具均离线，不读取凭据、不联网、不创建用户。实际执行需要同时传 `--active-confirmed`、`--settings-live`、精确 `--expected-version`、`--expected-sha`，以及私有 `--credentials`、`--manifest`、明确 `--expected-pg-port`、`--expected-database`、已有 `--python` 路径。数据库 helper 不单独扩展操作范围，始终使用 PG READ ONLY 事务和超时。

浏览器工具支持 `REALYU_TEST_RUNTIME` / `REALYU_TEST_RESULTS` / `REALYU_TEST_BROWSER` / `REALYU_TEST_PLAYWRIGHT_MODULE` 环境变量。依赖本机已有 Node、Playwright、Edge、Python 和 psycopg，不会下载安装浏览器。把 runtime 和 results 指向仓库外私有目录；仅人工检查过的脱敏结果可复制到 Git。默认路径 `.private-realyu-browser` 也必须保持私有。

## 范围

先验证公网单核心版本、已启用的用户名注册/购买/模型目录和 CNY 7，再生成一个随机 `ux-public-e2e-日期-随机串` 用户名。通过正常注册 UI 只提交一次用户名、昵称和强密码，email 留空；不添加邮箱、验证码、邀请码或优惠码，不接受管理员确认。

检查注册跳转工作台、恰好一把个人 Key、零余额、无赠送/订阅/订单/模型用量/客户团队归属。Key 值仅在内存用于脱敏，不输出、不复制、不截图显示。个人 scope 在零余额仍能提供配置入口，团队 scope 为空且不能回退到个人 Key。

购买页只读取六档个人套餐及钱包方式，点击套餐卡仅进入预览，**不提交支付、订单、充值、赠送或模型请求**。模型目录按新用户真实授权读取，检查九个模型的参考价格单位仍为 USD。

## 有界清理

清理前用 PG 只读查询和正常管理员 GET 双重确认：本轮准确新 ID、随机登录名、昵称、`signup_source=realyu_username`、非迁入身份、创建时间，以及唯一 Key 的用户/actor/payer 均为该新用户、team 为空。凭据和完整身份只保留于本机内存/私有证据，不入报告。

先由该新用户自己的 JWT 对唯一 Key `PUT /api/v1/keys/:id`，仅提交 `status: inactive`；再由管理员 `PUT /api/v1/admin/users/:id`，仅提交 `status: disabled`。这是原生最小权限路径：管理 Key 接口只支持分组和限流重置，不能将发送未知 status 字段当作成功停用。最后通过管理员 GET 和 PG 只读复核停用状态。保留所有行，不删除、不改客户账号、不写 SQL、不增加资金。

首次失败保留在新的时间戳目录，注册/购买不重试。若创建结果因网络中断未知，只允许按同一随机身份只读确认并清理，不再提交注册。若身份关联失败，停止写入并保留证据，禁止按模糊昵称或猜测 ID 清理。
