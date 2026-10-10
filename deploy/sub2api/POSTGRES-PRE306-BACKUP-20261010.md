# PostgreSQL 306/307 迁移前快照与恢复证明

本次只读备份完成于 2026-10-10 22:07（北京时间），没有部署候选、变更生产配置或启停服务。生产现有迁移截至 `305_realyu_teams.sql`，共 297 条迁移记录；306/307 尚未应用于本次快照。

工具：[singlecore_pg_backup.py](singlecore_pg_backup.py)，测试：[test_singlecore_pg_backup.py](test_singlecore_pg_backup.py)。脱敏结果：[备份恢复汇总](../../lab/sub2api_e2e/postgres-pre306-backup-20261010.json)。

## 已执行结果

- 生产 PostgreSQL、隔离 PostgreSQL、`pg_dump`、`pg_restore` 均为 18.6，源库约 72 MB；执行前 C 盘剩余约 395 GiB。
- 从当前 manifest 的 `singlecore_api.env_file` 发现实际连接，严格校验 loopback、28490、独立客户数据库和专用 owner；同时校验当前二进制 SHA，执行结束再次核对 manifest/env 未变化。
- 一个 `REPEATABLE READ READ ONLY` 导出快照覆盖迁移记录、结构、所有应用表内容摘要、完整 custom-format dump 和 schema-only dump。生产没有 SQL 写操作；期间正常请求可以继续。
- 恢复到 29490 的全新唯一数据库。没有覆盖、清空或删除任何原有隔离数据库，也没有连接恢复库运行应用。
- **120 张表、51,028 行**逐表计数与完整行哈希一致，297 条 migration 的名称、校验和、时间一致，规范化结构哈希一致。没有输出客户、团队、Key 或余额明细。
- 完整 dump 大小 **3,066,701 bytes**，SHA256：`6b22d02bca90cd92aecf885d43eaa81354eb38f7618a21ee48d6e8a0bc90b8fd`。
- schema-only dump SHA256：`f19a039bd565935f4d54bd60cfe1f2685df0c90c8d9e1479dd8fab5b17b13b5c`。
- 结构摘要 SHA256：`33dcee6d73b2ca5ffa6407d04c8e12ad99e0491149fea7d5804dedd1d9be7534`。

私有 receipt、dump、schema、stderr/stdout 和逐表摘要保存在执行机 runtime 的本次唯一备份目录。该目录仅当前操作员、SYSTEM 和本机管理员可访问；原始文件和恢复库内容不得上传 Git。交接给发布器的是私有 `receipt.private.json` 路径；公开仓仅含脚本、测试和脱敏汇总。

## 复现入口

在已有 psycopg 3 的 Python 环境运行，参数使用本机已审核的私有路径：

```powershell
python deploy/sub2api/singlecore_pg_backup.py `
  --manifest <当前manifest.json> `
  --isolated-config <隔离29490连接的private.json> `
  --pg-bin <PostgreSQL18.6-bin目录> `
  --migrations <候选backend/migrations目录> `
  --output-root <私有runtime备份父目录>

python -m unittest -v test_singlecore_pg_backup
```

脚本为本次 305→306/307 发布证据而设计，固定生产源身份、恢复 endpoint 和数据库前缀。它拒绝生产端口恢复、任何已存在的目标库、源身份漂移、306+ 已应用、源迁移 checksum 不一致或恢复内容不一致；不会为了重跑删除证据或旧恢复库。重新运行会建立独立目录和独立目标库。密码仅来自私有 JSON、进程环境和内存，不放命令行；异常不回显原始连接或 SQL 数据。

## 首错与结构比较口径

三次未通过记录完整保留在独立目录：第一轮服务器地址的 `inet::text` 返回 `/32`，改为 `host(inet_server_addr())` 后仍严格检查 127.0.0.1；第二轮原生 migration 含 `120a` 等合法编号后缀，改为解析数字前缀；第三轮真实恢复暴露 PostgreSQL 合法结构重新表示，未忽略行数据或移除约束检查。

结构比较保留实际列顺序、类型、默认值、空值限制、约束及其 validation 状态、索引、视图、函数、触发器、扩展和序列定义。只规范化两种恢复等价表示：被删除列留下的物理 attnum 空洞，以及字符串常量 varchar 数组整体转 text[] 与逐元素转 text 的等价表达；每个常量值和约束操作符仍精确比较。所有表行用同一时区、`row_to_json`、C 排序、长度前缀流式哈希。序列当前值不在结构摘要中，dump/restore 的 `setval` 由 PostgreSQL 执行；序列定义纳入摘要。

## 不能由本次结果推出的承诺

这是**一致性逻辑快照备份**。记录的 WAL LSN 只是观测值，没有建立 WAL 归档/PITR。`--no-owner --no-acl` 使隔离恢复不依赖生产角色；实际灾备的权限和 owner 仍需独立审核配置。没有验证主机灾备、存储损坏恢复、停机恢复时长或真实商户收款。

**新权威收到任何后续资金/订单/使用量写入后，不能直接把本次快照恢复覆盖当前库。** 306/307 的增量 DDL 与旧查询兼容，不等于新 managed 订单产生后旧二进制仍能正确履约；发生此类业务写入后应前向修复，或执行另行设计、逐笔对账并演练过的反向迁移。发布器只能使用本证明作为备份和迁移前结构证据，不能把它作为自动账本回滚许可。
