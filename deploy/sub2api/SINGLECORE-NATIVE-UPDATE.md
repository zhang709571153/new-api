# 已 ACTIVE 原生核心的程序更新

首次 New API → Sub2API 迁移完成后，不再运行旧迁移 Activate，也不恢复 SQLite。
没有数据库迁移的原生程序更新使用 `singlecore_native_update.py`，保持当前 PostgreSQL、Redis、身份密钥、客户和团队账本。2026-10-10 UX 版本增加 306/307，须使用[专用增量入口](SINGLECORE-ADDITIVE-UPDATE.md)，不能沿用下述 schema-free 计划。
它只替换原生可执行文件引用，可附带一个已核实的客户展示汇率；不更改 Tunnel、代理、旧服务或数据库 schema。

## 发布前

1. 固定原生源码 commit、上游 commit 和完整 patch，完成页面/权限/金额输入测试，构建带唯一版本号的新二进制。
2. 在独立测试数据库和 loopback 端口运行候选，确认旧用户名登录、无确认拦截、管理员/成员权限、美元单价与人民币金额。
3. 保存当前 ACTIVE authority、manifest、env 和旧/新二进制哈希。源代码 diff 必须无数据库 migration 变化。
4. 准备私有 plan，使用新 operation 目录、新版本二进制子目录、新 env 文件名；不复用失败 operation。
5. 运行默认 preflight，确认文件未漂移。实际执行必须用普通 Windows 管理员终端或 UAC；不绕过系统权限。

plan 的 `verified_files` 必须含脚本、`release_control.py`、ACTIVE authority、当前 manifest、当前 env 和新二进制。
`env_additions` 仅允许 `REALYU_CUSTOMER_USD_TO_CNY`，当前已核实值为字符串 `7`。
新 env 从现有私有 env 原样复制后增加这一键，保存在同一受保护 config 目录；不得上传 env/plan 私密内容。
模型参考价格保持 USD；汇率只用于客户金额展示和金额输入转换，不能再次乘 group ratio 或改历史账单。

```powershell
python.exe -B .\singlecore_native_update.py --plan C:\private\native-update\plan.json --expected-plan-sha256 <审核后的SHA256>
# 管理员终端，preflight 和隔离测试均已通过后：
python.exe -B .\singlecore_native_update.py --plan C:\private\native-update\plan.json --expected-plan-sha256 <审核后的SHA256> --execute
```

## 执行与失败边界

- 先检查实际 SCM → launcher → worker 关系、Session 0、监听 PID、二进制哈希与当前版本，再关闭已有 bridge admission。
- 使用完整写入并 fsync 的临时文件，通过 Windows 原子独占 rename 创建有 owner 的维护门。
- 现有请求、未结算 funding 或批量图片任务未清空时，最多等待指定 30–60 秒，超时即开门退出；不强杀仍在工作的请求。
- 只停止 `RealYuApi`，确认旧监听和同一出生时间的进程均退出，再原子切换 manifest；旧原生二进制和 env 保留。
- 新 worker 的真实身份、hash、engine/version/single_core 验证通过才开门。日志写失败不能跳过入口恢复。
- 新程序启动失败时只恢复上一版 **原生** manifest，仍使用同一 PostgreSQL 账本；禁止启动退役 New API / 旧 Sub2API writer。
- 原生恢复也失败时保留门和首次错误，人工检查回执；不要靠恢复旧 SQLite 来开放服务。
- 记录阶段时间、实际 worker 与第一次异常；不要覆盖 failed operation 或用重试回执假装首次通过。

Windows PowerShell `-Command` 会将最后一次预期的 `Get-Process -ErrorAction SilentlyContinue` 未找到进程映射为退出码 1，
即使此前服务停止和全部身份断言已通过。发布器必须在所有断言完成后显式 `exit 0`；实际 `throw` 仍然终止并返回失败。
该主机 2026-10-10 r2 的首次更新因此自动恢复旧版，原回执保留，修复已通过真实 Windows 只读复现及失败保护测试。

批量任务 `output_deleted` 也是终态，不应被算成在途。SCM stop 最多 20 秒的进程树等待是本机现有 WinSW 设置，
因此“没有请求”不代表发布耗时为零。具体维护时长以本轮 receipt 和公网采样为准。

## 发布后

新版移除管理确认门后，使用正常管理员设置 API 提交显式品牌配置，触发既有 HTML cache invalidate；
不要仅 SQL 写 logo 后假设浏览器已刷新。先保存五个品牌字段的 before，保留其他设置，再检查全新浏览器收到的 HTML 注入。

用既有 Key 做小额真实文本/WS 验收、旧 CLI 续聊和只读资金对账；浏览器验证登录、团队成员用量、管理员详细页面、
模型 USD 单价、CNY 用量/余额、无确认弹层。模型转发未变的功能可以引用同版本来源的已完成专项证据，
但必须明确哪些在本次二进制上复核、哪些属于前一次发布。真实商户支付与异常断流耐久结算不能被健康检查替代。

迁机仍从 [Windows Server 交接](SINGLECORE-WINDOWS-HANDOFF.md) 开始；应导出当前 PG 权威和当前 env/manifest，
不能从初始 S0/S1 快照重建已上线后的客户余额。
