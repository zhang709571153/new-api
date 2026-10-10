# 306/307 原生增量发布入口（2026-10-10 已用于生产）

23:13:51 CST，UX 更新已真实 ACTIVE，维护窗口 32.596 秒。306/307 事务应用完成，
历史迁移 297→299；同一 PostgreSQL 权威保持，未恢复数据库、未启动旧业务服务。
真实 SCM 发布通过，见[回执](../../lab/sub2api_e2e/ux-production-activation-20261010.json)
和[最新发布说明](UX-NATIVE-RELEASE-20261010.md)。以下预检记录有明确时间；已完成的计划
不得再次执行，新的更新必须以当前版本、manifest、数据库和交易状态重新准备。

`singlecore_additive_update.py` 是已 ACTIVE 单核心的专用增量发布器，复用
`singlecore_native_update.Host/run_update` 和 `release_control`。不修改既有热修入口，
不能把含数据库迁移的计划交给 `singlecore_native_update.py` 或标成 `database_migrations=false`。

此次唯一许可的迁移为以下顺序，校验值采用原生 Go runner 的 UTF-8 原始内容
`TrimSpace` 后 SHA256，保留内部 CRLF/LF；不是文件原始 SHA256：

| 文件 | runner checksum |
| --- | --- |
| `306_realyu_managed_purchases.sql` | `0399b87f731f3511f42ca973f44014cf96ae9a28336a99889ffff3f92ab30bd5` |
| `307_realyu_team_member_nicknames.sql` | `deb1deaaccae541d7b5cf5dda6cb098eeaf3598fe3227700b3d4c955ebe37c16` |

## 计划与只读预检

沿用旧入口的 manifest、旧/新 binary SHA、版本、ACTIVE authority、维护门、运行目录、
30–60 秒 drain budget 和环境文件字段。增加：

- `database_migrations: true`。
- `migrations: [{filename, path, checksum}, ...]`：完整 306/307 顺序，使用本轮已审核源码文件。
- `candidate_migrations: [{filename, checksum}, ...]`：新 binary 构建源码内**全部**迁移清单，
  必须精确等于备份历史清单加 306/307。迁移原生文件名包括历史 `006b_...` 等字母后缀。
- `backup_evidence: {path, sha256}`：私有 `singlecore-pg-snapshot-restore` PASS 回执。
  必须验证 custom dump 的文件 SHA/大小、schema dump SHA、规范化 schema 摘要、
  24 小时内同一 dump 在隔离 29490 `realyu_backup_verify_*` 数据库的恢复、所有表行哈希、
  schema 与迁移清单一致。结构摘要和 schema dump 文件 SHA 是不同概念。
- `verified_files` 必须包含本入口、原 updater、`release_control.py`、authority、manifest、
  当前 env、旧/新 binary、备份回执、两个 SQL 文件的**文件原始 SHA256**。

真实连接严格保持 `127.0.0.1:28490/realyu_singlecore_20261010_candidate`，原生 owner、
Redis DB1、funding=true、18300 均核对。数据库端再验证 database/user/address/port/schema、
主库状态及备份时 PG 版本。普通预检只读，不建目录、不写维护门、不启动服务。

新 binary 只可放在固定 `C:\ProgramData\RealYu\singlecore-production-20261010\bin`
下新的版本子目录，与旧版本目录相邻；不能套在旧版本目录内部。旧 binary 在 bin 根目录也兼容。
新 env 仍在原受保护 config 目录，以新文件名复制，仅允许既有人民币展示汇率键。
本入口不改注册、支付开关、账户、供给凭据或网络设置。

```powershell
python.exe -B .\singlecore_additive_update.py --plan C:\private\additive\plan.json --expected-plan-sha256 <审核的计划SHA256>
# 仅独立review通过、隔离新binary验收完成后，在正常管理员终端执行：
python.exe -B .\singlecore_additive_update.py --plan C:\private\additive\plan.json --expected-plan-sha256 <审核的计划SHA256> --execute
```

## 应用与恢复边界

先验证旧 SCM/Session0/worker，再持有原全局 release lock，关门，等待完整 drain。
只在 `RealYuApi` 停止并确认旧 worker 退出后应用迁移。使用原生 advisory lock
`694208311321144027`，获取预算约 2 秒、`lock_timeout=2s`、每条语句 `statement_timeout=8s`。
各迁移 SQL 和 `schema_migrations` 记录在同一事务提交。后续失败不撤销已经提交的合法前缀；
重新运行只会跳过校验和一致的前缀。所有连接退出时释放锁。

新 binary 启动前两条迁移均须已完成；启动后再确认无未知迁移、表/昵称约束/订阅序列有效，
并验证实际版本、hash、engine、single_core 和 SCM 归属后才开门。

失败只允许恢复同一 PG 上的旧原生 manifest，保留已增加 schema；**不执行 down 或数据备份恢复**。
预检、停服务后的迁移、恢复旧 manifest 前、恢复后开门前均检查
`realyu_purchase_snapshots` / `realyu_purchase_grants`。任何托管订单或发放记录存在，
或历史 schema 漂移、结构不完整，均拒绝降级并保留维护门等待前向修复。
开门后的新业务写不能靠旧数据库备份撤回。旧 New API/供给影子服务始终不启动。

## 发布前验证与限制

测试使用 `test_singlecore_additive_update.py` 的 fakes 和独立
`127.0.0.1:29490/realyu_auth_e2e` 临时随机 schema；没有迁移生产。
真实 PG 覆盖新建、二次幂等、原数据/约束保留、307 被锁时保留 306、锁释放、
advisory 超时无写、错误数据库与 checksum 拒绝、已有托管订单拒绝降级。
fakes 覆盖关门/排空/停止/迁移顺序、启动失败同库恢复、新订单阻止恢复、目的版本目录和必需 pins。

首轮隔离 PG 校验因 `inet_server_addr()::text` 带 `/32` 拒绝正确连接，现用
`host(inet_server_addr())` 比较实际地址；首错日志保留。备份回执接入首次也明确拒绝了
历史字母后缀迁移名，修复为原生已有命名规则；实际 297 历史项加 2 新项合同已通过。

2026-10-10 最终测试共 **27 PASS、0 FAIL、0 SKIP**：旧 updater 的 13 项原测试保持原样，
新增 14 项中 5 项连接上述独立 PG 18.6。设置专用 `REALYU_ADDITIVE_TEST_DSN` 和
`REALYU_ADDITIVE_TEST_MIGRATIONS` 后执行：

```powershell
python.exe -B -m unittest test_singlecore_native_update test_singlecore_additive_update -v
```

私有运行日志为 `runtime/additive-update-final-20261010.log`，首轮失败为
`runtime/additive-update-first-20261010.log`，没有覆盖。备份契约另已验证 297→299 迁移清单。
主流程及独立 agent 已完成只读审查，未发现新的 P0/P1 阻断。22:24 的 UX r1 私有计划已将
实际当前 manifest/env/worker binary、候选、入口及依赖、备份回执和 SQL 全部固定；
真实生产只读预检通过，迁移仍为 297 项，306/307 尚未应用，订单/发放表尚不存在。
计划 SHA256 为 `acaabad7f18893cdf6aa1b1921b7cf8a993a7cd2d4465050274b51e88db9064a`。

本入口于 23:13:51 完成真实 SCM 发布，实际维护 32.596 秒；增量失败后的真实生产恢复未演练，
其恢复分支仍以隔离测试为证。30–60 秒是排空预算，不是总维护窗硬上限。
该计划仅适用于当次核验的本机状态，接手时重新固定私有计划，
不能复用旧热修的 `database_migrations=false` 计划，也不能把测试通过记作生产上线。
