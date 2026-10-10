# 308 历史归档：完整规模验收

本轮是候选验收：生产仅做一致快照的只读查询与 pg_dump，所有恢复、308 DDL、历史归档都发生在独立 `127.0.0.1:29490` 的唯一恢复库。没有启动该库的网关/账号刷新，没有修改生产数据、服务或支付。

## 已完成证据

- `lab/sub2api_e2e/usage-history-backup307-20261011.json`：307/299项迁移，123张表、51,657行的恢复内容/规范化schema/迁移记录全部相同；完整 custom dump SHA `fb789b3b13b76774ba865454df12fd7e6e013fbd47c2ba033cdfbf8adf0e6f46`。
- `lab/sub2api_e2e/usage-history-full-scale-20261011.json`：完整31,295源消费记录逐ID唯一，6,713 self、24,520原team、58 deleted actor、4原隔离；31,291查询事实详情。input、output、整数quota总额精确对齐。第一次归档与第二次幂等重放通过，122张已有业务表的全部行hash不变。
- `lab/sub2api_e2e/usage-history-http-20261011.json`：候选r1 51/51真实HTTP检查，五种合成角色、归属/日期/cache/金额/canonical/越权/公开模型字段；先前50/51首错保留，原因是隔离fixture无任何价格渠道。新增两条synthetic价格后通过，禁调度合成账号未启用，无上游调用。

独立PG恢复证明和HTTP合成fixture是两种不同证据。普通用户可归属总数31,233不意味着任一用户能查看全部；self按actor，platform仅实时全局admin，已删除actor显示null。4条旧隔离记录不进入用户接口。中英桌面/移动浏览器由主流程另行记录；本报告不宣称已发布、生产已归档或浏览器通过。

## 工具边界

`singlecore_pg_backup307.py` 是独立入口，不修改已有305 runner。它校验旧runner SHA `cbbe20c414edf90445db9c4101ad323cfdfa9a4a2a8271b5366ce9a6977b8e74`，要求299条完整迁移记录与本地307以前SQL校验和一致，306/307额外固定校验。源从active manifest/env读取并固定原生客户PG身份，read-only repeatable-read导出同一snapshot；只恢复到全新 `realyu_backup_verify_*` 数据库，不能覆盖原数据库。原始dump/日志/表哈希只存私有ACL目录。

`singlecore_history_rehearsal.py` 默认离线。显式执行必须传入新307 PASS receipt及其SHA、准确dump/schema文件SHA、隔离凭据、308文件SHA、importer SHA、封存S1 SHA、installation和已审manifest。目标只能是该receipt记载的唯一29490恢复库，先再次验证其全部表/迁移/schema与快照相同且无其他连接，才能执行308。它没有生产目标开口；原 `import_usage_history.py` 的白名单未扩大。

每次工具生成唯一目录，失败保留 `first-failure.json`，不覆盖原始证据。完成后保留恢复库供审核，不自动DROP，不启动任何服务。相同脚本再次从已经被演练修改的库起跑会拒绝；重新完整演练须使用新的、重新验证过的恢复副本。

```powershell
python deploy/sub2api/singlecore_pg_backup307.py --manifest <active-manifest> --isolated-config <private-29490-config> --pg-bin <PG18-bin> --migrations <candidate/backend/migrations> --output-root <new-private-runtime-root>
python deploy/sub2api/singlecore_history_rehearsal.py
# 默认 PREPARED_NOT_RUN；只有显式 --execute 加全部固定输入才访问数据库
python deploy/sub2api/singlecore_history_rehearsal.py --execute --restore-receipt <new-receipt> --receipt-sha256 <reviewed-receipt-SHA> --isolated-config <private-29490-config> --migrations <candidate/backend/migrations> --importer <candidate/deploy/realyu/import_usage_history.py> --importer-sha256 <reviewed-SHA> --migration-sha256 <308-file-SHA> --source <sealed-S1> --source-sha256 <source-SHA> --installation-id <same-installation> --reviewed-manifest <reviewed-private-manifest> --output-root <new-private-runtime-root>
python -m unittest test_singlecore_pg_backup307 test_singlecore_history_rehearsal -v
```

7个新增工具防护测试通过：默认离线、精确307历史/runner pin、receipt/dump/schema文件实际哈希、目标端口与名称、PASS各项事实、24小时有效期、恢复时间顺序。源候选另有4个HTTP脚本安全/金额精度测试。

快照只表示某一时刻的一致备份，不是PITR或可覆盖今后客户交易的许可证。正式发布仍使用同一PG权威，308归档仅追加不可变事实；如展示回退，保留事实和receipt，不回滚余额、付款、Key用量或恢复旧SQLite。
