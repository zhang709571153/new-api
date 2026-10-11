# UX3 发布后只读统计验收

2026-10-11 08:26:28–08:26:55（北京时间），最终 UX3 二进制
`353428ccb6a09a3f512bc8bced5c23364c0d4f7cb46f5cecccaea24782446569`
通过 32 项检查、55 次真实 HTTP 请求。

- 脱敏结果：[ux3-production-readonly-20261011.json](ux3-production-readonly-20261011.json)。
- 首轮工具错误：[ux3-production-readonly-first-tool-failure-20261011.json](ux3-production-readonly-first-tool-failure-20261011.json)。当时没有发出 HTTP 请求。原始证据保留，未覆盖或重试隐藏。
- 脚本：[verify_ux3_public_20261011.py](verify_ux3_public_20261011.py)；[6 项离线测试](test_verify_ux3_public_20261011.py)。默认不访问网络或读取私有文件。

固定统计范围为上海时间 2026-09-11 00:00 至 2026-10-11 00:00，结束时间不包含在内。该范围的平台 31,422 次调用中，旧归档 31,291 次、新核心 131 次。日／小时趋势、列表统计、汇总统计，以及本地／公网代理返回的整数 Token 与精确 Decimal USD 金额一致。管理员本人的 self 与 personal 范围也通过相同比较。

完整旧归档仍为 31,291 条；当时全时段统计为 31,704 条（原生 413 条，正常流量会继续变化）。三个活跃团队均验证了 `member_summary` 一人一行、合计等于全范围 `stats`、分页不改变汇总、昵称与团队成员视图一致。API 金额单位 USD、客户展示 CNY，配置汇率为 7；模型参考单价继续为 USD。

公网 direct 与显式本机代理两条路径的中文／英文首页、文档、价格页均实际读取完整 HTML；匿名历史／汇总／趋势均为 401。普通用户线上角色检查为 **NOT_RUN**：没有现成的已授权登录凭据，未伪造 JWT、启用账号或重置密码。普通用户平台 403 等权限证据来自独立候选 49 项 HTTP 测试，不冒称本轮线上角色验收。

本轮没有模型调用、商户调用、数据库直连、资金或配置修改。正常管理员登录可能更新原生认证时间与会话缓存。完整业务行、昵称、用户标识、Key、登录令牌和 HTML 均未导出；公开结果进一步去掉了 self/personal 的活动计数。

## 本机约束与重跑

这是 **当前 Windows 主机的操作脚本，不是可直接迁机运行的通用工具**。固定依赖包括：

- `C:\ProgramData\RealYuServices\manifest.json` 的 `singlecore_api`；
- 18300 监听进程与运行中 `RealYuApi` SCM 服务的父子关系；
- 旧发行目录 `.lab\credentials.json` 中已授权管理员凭据，仅在内存读取；
- `https://api.realyu.fun`、本地 18300、显式代理 `http://127.0.0.1:17897`；
- Python 标准库及系统可用的 `zoneinfo` 时区数据。

迁机必须审查并重新设置这些值，不能复制私有凭据到 Git。运行所在账号的 Medium integrity 无权读取 LocalService 的完整 `ExecutablePath`；因此核对真实监听进程的文件名和到 `RealYuApi` 的父子链，另核验 manifest 指向的文件 SHA，以及本地／直连／代理三路径的精确 UX3 版本。报告明确不声称直接读取了进程内存映像哈希。

本机在明确发布完成后执行：

```powershell
& C:\srv\realyu-singlecore-dev\runtime\venv\Scripts\python.exe .\verify_ux3_public_20261011.py --released --expected-sha256 353428ccb6a09a3f512bc8bced5c23364c0d4f7cb46f5cecccaea24782446569 --out-root C:\srv\realyu-singlecore-dev\runtime\ux3-production-readonly
```

每次生成唯一操作目录；`first-failure.json` 只写一次，没有自动请求重试。默认采用结束于本地当天零点的过去 30 天窗口，避免与当下新调用竞争。历史迟到结算仍可能改变旧时间窗口，若跨请求出现差异必须保留并核查，不能通过循环重试消除证据。

本工具不覆盖浏览器视觉、Safari、安装器、SSE、WebSocket 或真实商户。真实 SSE 与同 socket 两轮 WS 应由独立 `runtime/production-singlecore-e2e.py` 在受控预算内运行，并单独记录实际消费证据。
