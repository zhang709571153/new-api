# 单核心跨数据库切换

这是新工具，不能用旧 `host_cutover.py` 的同 SQLite 程序回退代替。当前开发/测试不等于生产已激活。`validate` 只读本地输入，`prepare-database`、`stage` 和 `activate` 都要求显式计划文件 SHA256。2026-10-10 已完成独立目标库准备和 S0；核心是否接管必须核对 `authority-receipt.json` 与最新公开验收记录。

## 固定隔离边界

生产 PG 服务保留 28490，旧 `sub2api_production` 影子库不改。新客户库仅允许 `realyu_singlecore_20261010_candidate`，角色仅允许 `realyu_singlecore_owner_20261010`，installation 固定 `realyu-singlecore-production-20261010`。新角色无 superuser/CREATEDB/CREATEROLE/继承/复制/bypass-RLS 权限，数据库 PUBLIC 权限撤销。角色和数据库带 installation comment；已有未归属对象不接管、不重置密码、不删除。

`singlecore_migrate.validate_target` 默认 rehearsal 仍禁止 PG28490。只有上述 `mode=staging` 的精确组合允许共用集群；active phase、已有新资金/usage/支付、新未映射客户/Key 的保护保持。该例外没有开放 shadow DB、任意数据库名或外部地址。

Redis 保留 28391 服务，新系统使用 DB1。`stage` 首次检查 DB1 必须为空；最终复制前再次 SCAN，只允许现存、合法 group/account ID 且有正数剩余 TTL 的 response affinity，发现 owner/auth/billing 或其他未知 key 就拒绝。初始化 migrations 应使用另外的隔离 Redis，不要先启动 native 污染 DB1。切换只复制 `sticky_session:<group>:openai:response:<sha256>` 且值为存在的 account ID；保留剩余 TTL 并扣除读取/复制用时，绝不延长已有更短 TTL。旧 owner、auth、余额、订阅、刷新锁、登录及并发状态不复制。DB1 必须由主部署确认专属目标，工具绝不 FLUSH。

程序入口使用现有 RealYuApi 18300；`manifest['singlecore_api']={exe,env_file,working_dir,sha}` 是与 service_entry 的接口，env_file 为 flat string JSON map。RealYuBridge 18301 和域名保持，PG/Redis 服务不重启。旧 Sub 服务 IDs 为 `RealYuSub2API20261009`、`RealYuSub2APIPrewarm20261009`。

## 计划与命令

复制 `singlecore-host-plan.example.json` 到私有 runtime；填具体路径、旧/新版本、所有固定输入 SHA。target_config 示例仅有字段结构，密码必须由运营方私有生成，不能用示例值上线：

```json
{"mode":"staging","host":"127.0.0.1","port":28490,
 "database":"realyu_singlecore_20261010_candidate",
 "user":"realyu_singlecore_owner_20261010","password":"<private-random-32-plus>",
 "installation_id":"realyu-singlecore-production-20261010","group_mapping":{"default":2}}
```

env_file 必须指向同一个数据库/角色/密码与 Redis DB1，`REALYU_FUNDING_ENABLED=true`、`TOKEN_REFRESH_ENABLED=false`。原 namespace/identity_secret 的旧续聊兼容配置、支付 inbox 验签配置及出口代理由主部署预先核对，不能省略后宣称全兼容。输入文件和快照含秘密，不入 Git；runtime 目录 ACL 应在开始前确认，仅部署操作者、SYSTEM/必要服务账户可访问。

本次正式目标的 `proxy_overrides` 固定为代理 ID 1、`http://127.0.0.1:17897`、空用户名/密码。S0 和最终 S1 都在保留原供给快照的前提下应用同一个经审阅映射，不能让 S1 原代理配置覆盖 SG 路线。逐账号 `proxy_id` 保持原 ID 并读回验证，receipt 只记录 ID/端口/匹配结论。原库不更改，SG 代理服务须由主部署先完成持续运行与真实上游验收。不能依赖全局 HTTP_PROXY 代替账号绑定。

必需 SHA pins 包括 native exe/env、target config、价格 mapping、history importer，以及本工具与 `singlecore_migrate.py`、`singlecore_supply.py`、`singlecore_pricing.py`、`snapshot_sqlite.py`；修改任一输入后应由主部署重新审阅并刷新 plan SHA。

```powershell
python singlecore_host_cutover.py validate --plan <private-plan.json>
python singlecore_host_cutover.py prepare-database --plan <private-plan.json> --expected-plan-sha256 <file-sha>
# 新库创建后，用已冻结 native 程序在隔离端口初始化 migrations；停止该 bootstrap。
python singlecore_host_cutover.py stage --plan <private-plan.json> --expected-plan-sha256 <file-sha>
# 最终管理员操作，由主部署在真实 E2E 和全部输入复核之后执行：
python singlecore_host_cutover.py activate --plan <private-plan.json> --expected-plan-sha256 <file-sha>
```

`stage` 顺序：只读最新供给快照→目标禁调度/剥 refresh→价格卡→S0 SQLite backup→客户绝对状态/逐主体精确对账→团队历史。stage 不开流、不改旧服务。失败留下私有证据；不要通过删除收据自动重跑已经不明的操作，应检查目标状态或另建演练库。不能把原生 shadow 用户/授信余额导成客户。

## 维护期状态机

1. 持有已有 release OS 锁，独占创建 maintenance marker；已有他人 marker 不覆盖。保存旧 manifest 私有副本。
2. marker 阻止新 API/网站写请求和支付回调（回调收到503应重试），轮询 bridge 活跃数（包括 WS）、旧 gateway 活跃数、异步任务及未结预占。全部归零即继续，不等待连续空闲15分钟或20分钟。总预算最多120秒，其中预留30秒恢复，常规切换尝试最多90秒；更短预算按四分之一预留。
3. 停 RealYuApi、旧 prewarm、旧 Sub；确认 SCM stopped 且18300/28090无遗留 listener。既有 PG/Redis/bridge 不动。此时 source SQLite 与 supply 已没有这些旧写者。
4. 验当前源价格规则仍等于演练配置；封存 S1 backup。用同 installation 新 operation 做绝对期末状态导入和逐用户/Key/团队/套餐/周cap对账，再补历史。未决消费预占不允许忽略；旧 pending payments 在验签持久 inbox 已启用时可保留待处理，不合成履约。
5. 读取最终源供给，要求账号/代理/group policy库存与 S0 相符；事务更新新库 accounts/proxies/account_groups 最终凭据/状态/限速，应用上述代理 override 并保留客户价卡。库存变化先重新演练，不能猜映射。供给 settings 仍使用明确允许名单，不导 shadow JWT/身份/支付凭据。复制选择性 affinity。
6. 封存新客户资金状态哈希；原子写 manifest native 分支；PG `phase=active`；启动 native。此时 gate 仍关，后台 refresh 禁用，只有无推理 health/status 验证，避免门前轮换凭据破坏旧服务恢复。
7. 校验 `/health`、新版本/engine、18300 实际进程路径以及新资金状态未变。**先持久 OPENING 收据，再移动本操作 marker 开门**。开门后记录 ACTIVE，旧 Sub/prewarm 仍停止。后台 refresh 由主部署公开验收后单独启用；新请求路径仍可刷新账号。

排空查询、S1 SQLite backup/导入/历史、最终供给、Redis复制、资金封存及PG权威标记均在受控子进程中执行，使用同一单调时钟 deadline；旧 admin 请求不再继承固定40秒超时。超时终止本次子进程树并等待结束，按独立 application_name 终止/确认仅目标角色的本次 PG 会话退出，才能进行恢复。真实 PG 回归验证：子进程已执行未提交写入并阻塞后被取消，会话归零且写入回滚。无法确认退出时保留 gate，不能假装可以回退。

120秒包含预留恢复时间，不在其后再追加30秒。进程终止确认和异常 Windows/文件系统操作仍可能超出墙钟预算，故不承诺任何故障下硬性120秒恢复；没有证据证明恢复完成时保留门并报告。SCM调用自身受剩余预算约束，原生 API 停止后还检查18300无遗留监听，再恢复旧 manifest。

## 回退边界

- OPENING 前失败：停止可能启动的新 API；确认新库无资金/usage/支付/团队审计写入且资金哈希未变，恢复 staging、旧 manifest 和三个旧服务，健康确认后仅打开本操作 marker。**不恢复/覆盖任何数据库文件**。最终供给只读复制，新程序门前禁后台refresh且无推理，因此旧凭据仍由旧服务持有。
- 已写 OPENING、开门结果不明或 ACTIVE：一律 forward recovery，不能自动回到旧 SQLite，即使暂时仍看得到 marker。新交易可能已经落入 PG；只有经演练的反向迁移/逐笔对账才能改变账本权威。
- 新库在开门前出现业务写入或资金哈希变化：保持维护，不能因“还没公开”就丢弃该写入。
- WS socket/进程内 turn state 无法复制；旧连接须正常结束或由客户端重新连接，affinity 只保证可延续路由的必要状态，不保证上游历史永不失效。

## 明确审阅的旧记录例外

旧 `consumed` 一律不能当作结算成功。仅在单条旧记录已核实套餐/钱包预占均为零、创建和最后更新时间相同且超过7天、当前没有对应消费日志时，可在私有 plan 添加 `legacy_zero_reservation_archive={record_id,row_sha256,treatment:"archive_unchanged_zero_amount_v1"}`。`row_sha256` 是完整原行 canonical JSON 的 SHA256，包含身份、请求和全部账务字段；不是只按时间/状态批量忽略。排空与 S1 使用同一验证：该行消失、任意字段改变、出现对应日志或非零额度都拒绝；其他新增未结预占继续等待/阻断。S1 精确保留 `realyu_legacy_records` 原行并读回验证，不改源状态，不称 settled，不退款/补扣；客户余额与套餐按 S1 原样绝对期末状态逐主体对账。

旧 role10 不能提升为 Sub 全局 admin。明确启用 `legacy_scoped_admin_policy={policy:"native_user_preserve_scoped_team_access_v1",expected_count:<已核实人数>}` 后，S1 验证 native role=user、原 source_role=10、状态、团队owner及成员映射，并逐团队比对自身owner权限和低角色owner的管理边界。人数或映射变化拒绝；只在这些条件通过后接受原 scoped-admin blocker。原系统root仍按既有导入规则映射admin。此policy不是豁免账户/资金对账。

当前65项测试通过，包括状态机的长流排空、预算提前中止、停服务半途失败、阻塞S1子进程退出后恢复、OPENING不明拒回退、新账本写入拒恢复、marker所有权、receipt防重放、精确staging/输入SHA保护；Redis允许名单/TTL/冲突/目标未知cache拒绝；旧零额记录精确指纹/无新日志/新pending仍阻断；6项真实隔离PG最终供给状态更新、SG路由不被S1覆盖、价格不覆盖、库存变化拒绝、事务中途失败整体回滚、超时事务结束、role10权限映射与错误提权拒绝。最新证据为私有 `runtime/singlecore-host-cutover-exact-legacy-policies.log`；首次61项日志保留。SCM真实安装/切换、公网验收由主部署单独完成，不能用这些合成测试替代。
