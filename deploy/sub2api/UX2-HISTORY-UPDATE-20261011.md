# UX2、308 历史归档与 Windows 接手教程

本文说明如何在**当前单核心、同一 PostgreSQL** 上追加 308 并更新程序，以及如何把已经运行的整站迁到另一台 Windows Server。两者的数据入口不同。308 发布不重新导入客户、不替换数据库；新机应恢复迁机时的完整原生 PG，不能从旧 SQLite 重新建立客户权威。

**编写时状态（2026-10-11 01:23 CST）：** 最终 UX2 r5 候选已验收，但 Windows 提权返回 `NOT_STARTED / InvalidOperationException`，没有 UX2 `ACTIVE` 回执。只读复核仍为旧 UX、299 项迁移，未应用 308、未进行生产历史归档。后续状态以 [UX2 发布记录](UX2-RELEASE-20261011.md)、[LIVE-DEPLOYMENT](../../LIVE-DEPLOYMENT.md) 和当前私有回执为准；本教程不是上线凭证。

## 1. 先选择正确路径

| 当前状态与目标 | 应做的事 | 不应重放的流程 |
|---|---|---|
| 现机 UX，迁移截至 307，升级 UX2 | 新备份/隔离恢复验证 → 候选验收 → 本站 308 增量计划 → 程序 ACTIVE → 一次性历史归档 | 初次 New API → Sub2API 的 S0/S1 客户导入、旧余额桥接 |
| 已是 UX2，308 已存在但历史未归档 | 核验当前版本、308 结构和封存来源；按同一已审 manifest 归档 | 再次执行已完成的程序更新计划或手工重跑 DDL |
| UX2 与历史都已完成，迁往新 Windows Server | 迁机时完整 PG/必要 Redis 状态及私有材料 → 新机隔离验收 → 最终短写冻结和切流 | 用旧 SQLite 或较早 PG 备份覆盖上线后的交易；重复创建套餐/用户/Key |

新机流程详见 [SINGLECORE-WINDOWS-HANDOFF](SINGLECORE-WINDOWS-HANDOFF.md)。[SINGLECORE-HOST-CUTOVER](SINGLECORE-HOST-CUTOVER.md) 是初次同机跨核心迁移，不是跨机 `pg_restore` 或日常升级工具。

## 2. Git 交付与私有交付

Git 内包含源码补丁、版本元数据、程序/迁移/归档工具、教程与脱敏测试结果。Git **不包含**生产数据库、Redis 备份、env、完整运行 manifest、账户 OAuth、客户 Key、JWT/TOTP/旧身份签名材料、商户及 SG/Tunnel 凭据，也不包含旧会话原始日志或逐行客户历史。

数据库、封存 S1、私有配置、已审历史 manifest、备份恢复证据和当前运行回执，通过受控私有渠道传递，校验哈希并限制目录 ACL。Git 中的聚合结果不能代替这些输入。原始档案即使只含内部 ID，也不作为公开附件上传。

本次冻结交付的辨识值：

| 项目 | 值 |
|---|---|
| 上游 | Sub2API `v0.2.15`，`f2669c8cf62555cd92389b3f55920e9e6e7c6ff2` |
| 原始开发源码提交 | `f98d4b6c56f0f56a5d08dbd351c8a4af6ab6a3b7`，clean |
| 完整补丁 SHA256 | `c414a0376a9ce112ff31f45c8e74475eb8b35b2f9575b62c38e63d5e9c5a007b` |
| 逐文件清单 | [singlecore-source.json](singlecore-source.json)，383 个改动文件 |
| 最终版本 | `realyu-singlecore-v0.2.15-20261011-ux2` |
| 原机 r5 binary SHA256 | `aef7b287ba4fdea3dca6590cc10bedf1e5ed8b90be639bde4d01cbc60001453c` |
| 308 原生迁移 checksum | `3ee70f5ffb0910c1693641c686a24e567ea2c0b87d32a7cb2084cffdccb3048c` |

这是该次交付的固定来源，不是“拉取上游 latest”的指令。新机重新编译产生的 binary 可以有不同 SHA，必须用它自己的干净构建、版本和 E2E 证据绑定新计划。

## 3. 重建源码与新机提交的区别

[Prepare-SingleCore.ps1](Prepare-SingleCore.ps1) 会 fetch 固定上游提交，再应用完整补丁并核对文件。它**不会取回原机 f98 提交历史，也不会替你提交改动**。直接加 `-Build` 会从有补丁但未提交的树构建，不能当作 `vcs.modified=false` 的正式发布产物。

新机应按以下顺序准备：

1. 校验交付仓库提交、原始 metadata、补丁 SHA 和固定上游；先用 `Prepare-SingleCore.ps1 -Destination <新的源码目录>` 重建，不加 `-Build`。目录必须不存在，不能重置正在运行的目录。
2. 验证完整补丁应用、383 个文件的内容/大小、全部迁移与预期清单；查看 `git status`、完整 diff，确认无额外代码或私有资料。使用交付中的重建记录作为对照，不能仅看改动文件数。
3. 在**这个重建仓库**提交已核对的内容，形成新的本地 clean commit。它通常不会等于 `f98…`；不得伪造提交、强写原提交号或把上游 HEAD 当作 RealYu 成品源码。
4. 另建本机构建元数据/回执，记录 `delivery_source_commit=f98…`、交付 metadata/补丁的实际字节 SHA、逐文件核对结果、本地 clean commit、工具链和构建命令。保持原交付 metadata 与 patch 不变；本机派生记录须能追溯到交付树。
5. 从该 clean commit 构建并复核 Go buildinfo 的本地 `vcs.revision`、`vcs.modified=false`、嵌入版本和新 binary SHA。构建后再次核对受版本控制的源码没有改变，再进行隔离 HTTP/浏览器验收。
6. 本机发布计划绑定**本机提交、本机派生 metadata、实际源码文件和新 binary SHA**，并保留交付 provenance。当前原机 r3 准备器要求原机 f98，不是新机通用生成器；新机需独立审核自己的准备入口。

### 字节 SHA、Git blob 与换行

原机已封存计划固定的是磁盘实际字节。其 `singlecore-source.json` 使用 CRLF，原始 SHA256 为 `06efbb393eb727fc08a9a0d26608b18b47b7d400f61ee84e78896e701fa1d666`；Git 的 `*.json` 规则会规范化成 LF，因此新 checkout 的 JSON 语义可以相同而文件 SHA 不同。Git object ID 也不是文件 SHA256。

**不要为匹配 Git 而改写原机已封存的 697 个文件；不要把原机 plan/receipt 的 SHA 套在新 checkout 上。** 接手者分别记录交付 Git/blob 来源和本机文件 SHA，使用本站实际字节生成新 pins。数据库中的历史迁移 checksum 必须逐项匹配，不允许改换行后伪造 checksum；原生 checksum 是 UTF-8 SQL 去首尾空白后计算 SHA，内部 CR/LF 仍影响结果。

## 4. 可交付工具的作用

| 工具 | 实际能力与边界 |
|---|---|
| [prepare_history_update_inputs.example.py](prepare_history_update_inputs.example.py) | 完全离线的输入检查示例：校验交付来源、补丁、清单内容、299+308 迁移及显式 binary SHA；不启动程序、不连接数据库、不读凭据、不提权 |
| [singlecore_pg_backup307.py](singlecore_pg_backup307.py) | 当前固定客户 PG 的 307/299 一致快照及全新隔离库恢复验证；不是任意 DSN 的备份工具 |
| [singlecore_history_rehearsal.py](singlecore_history_rehearsal.py) | 只在经验证的唯一 29490 恢复库执行 308 与完整归档；不开放生产目标 |
| [singlecore_history_update.py](singlecore_history_update.py) | 同机、固定 UX 基线、同 PG 的 308 + 程序更新；默认只读预检，`--execute` 才执行；**不导入历史** |
| 重建源码中的 `deploy/realyu/import_usage_history.py` | 默认只读 inspect；显式 `--apply` 按已审 manifest 一次性归档；不建 schema、不更新服务、不重放扣费 |

离线示例不是 `prepare-plan.py` 的通用替代品。其结果格式为 `review-only-history-inputs-v1`，状态为 `LOCAL_INPUTS_CHECKED_NOT_DEPLOYABLE`；没有可执行计划、服务/数据库权威校验，也不验证 binary 与源码的构建关系。**不可把结果重命名成 plan.json，或改成 `format:1` 绕过本站审查。** 现有私有准备器和 `Activate-Ux2.ps1` 依赖原机路径、权威与 pins，不随 Git 提供可直接执行的生产计划。

以下仅为离线检查的参数模板，路径/变量由接手者先按自己的目录定义；`$deliveryCommit` 指原交付来源，非新机本地 HEAD：

```powershell
python "$handoff\deploy\sub2api\prepare_history_update_inputs.example.py" `
  --source-metadata "$handoff\deploy\sub2api\singlecore-source.json" `
  --source-root "$sourceRoot" --candidate-binary "$candidateBinary" `
  --expected-candidate-sha256 "$reviewedBinarySha" `
  --expected-version "realyu-singlecore-v0.2.15-20261011-ux2" `
  --expected-source-commit "$deliveryCommit" --output "$newPrivateInputReport"
```

Python 3.11+，无外部依赖；输出目录必须已存在，输出文件必须是新路径。示例即使通过，后续仍需本站权威、备份、clean build 和完整运行验收。示例的 5 项离线测试见 [test_prepare_history_update_inputs.py](test_prepare_history_update_inputs.py)。

## 5. 现机 307 → 308 发布顺序

### A. 发布前准备

1. 从实际 SCM/Session 0、运行 exe SHA、manifest/env、`ACTIVE/opened` 权威回执识别当前服务。当前约定为 `RealYuApi`、native 18300、透明 bridge 18301、PG 28490 客户库、Redis 28391 DB 1；名称含 `candidate` 的实际客户数据库也不能被误当成测试库。不得启动旧 New API、影子 Sub2API 或 prewarm 写者。
2. 核对 `schema_migrations` 全部 299 项及源 SQL checksum，不只看最大编号 307；308 只能是已审文件。当前 updater 固定旧 UX binary/version、DB identity、bin root 和汇率 7，目标不同应另审适配，不能放宽成任意库。
3. 使用 307 入口取得新的只读一致快照，核对 custom dump SHA，并在唯一隔离恢复库验证 schema、全部表行摘要与迁移记录相同。计划要求 24 小时内有效 PASS 证据。备份 `schema.sha256` 是规范化 catalog 摘要，`schema_dump.sha256` 是导出文件哈希，二者不能互换。
4. 隔离运行候选，验证真实角色权限、默认 Key、历史分页/缓存/金额、中英文和窄屏；没有需要时不调用付费模型。完整规模演练与证据边界见 [HISTORY-SCALE-REHEARSAL](HISTORY-SCALE-REHEARSAL-20261011.md)。
5. 创建新的私有 operation，逐项 pin 当前 manifest/env/authority、旧/新 binary、源码/metadata/patch、所有依赖 runner、全量迁移、备份/恢复证据及历史 manifest。目标采用固定 bin root 下的新版本相邻目录及新 env 文件，不覆盖旧版本目录。`database_migrations` 必须为 `true`。
6. 用正常 Windows PowerShell 5.1 和已审核 Python 做只读 preflight，保留输出、首错、版本及实际输入 SHA。检查即时活动请求、资金 hold/batch 和采样新鲜度，不能用过时“空闲”结果代替执行时 drain。

原机旧 r1/r2 计划和失败记录保留，但其 binary 原路径已失效，不能重新启用；r3 也只属于原机。不要把开发机绝对路径复制到新机器执行。

### B. 执行和自动恢复

真正执行需要正常管理员终端或 Windows UAC，这是 SCM 操作权限，不增加新的业务审批。本站准备器应先默认预检，再明确 `-Execute`；底层对应下列参数形式。**只有完整的本站私有计划才可传入，不能传离线示例报告。**

```powershell
python "$handoff\deploy\sub2api\singlecore_history_update.py" --plan "$reviewedSitePlan" --expected-plan-sha256 "$reviewedPlanSha"
# 上一行成功且处于实际发布窗口后，管理员终端显式执行：
python "$handoff\deploy\sub2api\singlecore_history_update.py" --plan "$reviewedSitePlan" --expected-plan-sha256 "$reviewedPlanSha" --execute
```

更新器持有发布锁，关闭全局入口并在计划的 30–60 秒内排空；仍有请求/hold/batch 则拒绝继续，不强断长流。停 `RealYuApi` 后核实端口及自有 PID/StartTime，按原生 advisory lock 应用唯一 308；取锁约 2 秒、语句约 8 秒超时，每个文件事务内写入 DDL 与 `schema_migrations`。新程序启动前核验四表结构、FK/索引/约束、归档状态和全部迁移，新程序不应再有 pending migration。

这次只新增四张历史表并更新程序，不调整 SG、Tunnel、Redis scope、注册/支付开关或汇率。更新器允许档案全空或精确符合已审 manifest，但归档状态不能在更新途中改变，因此**不要并发运行 importer**。

新程序失败时，自动恢复仅限同一 PG 上已核对兼容的旧 UX 程序，保留 308 表和已存在档案；此 308 更新不受“已有托管订单就不能恢复程序”的旧 306 条件影响。若归档/结构漂移或恢复验证失败，保持维护门并留证据。没有 down migration、数据库还原或旧 SQLite 回退。

`ACTIVE/opened` 后已产生新写入，只能在当前数据库继续修复。不能用备份倒退订单、余额或 Key 用量；再次改程序也要重新审查兼容性和生成新 operation。

## 6. 程序 ACTIVE 后归档历史

归档源码及完整 API 合同在重建仓库 `deploy/realyu/USAGE-HISTORY.md`。308 新表为 `realyu_legacy_self_usage`、`realyu_legacy_deleted_actor_usage`、`realyu_legacy_usage_details`、`realyu_usage_history_imports`。

1. 只使用已完成封存的原 S1 和其预先核对 SHA；读取前后重新验 SHA。导入器拒绝任何同名 `-wal`、`-shm`、`-journal`，即使空文件也拒绝。**不要删除 WAL 来使检查通过**，也不能用 `immutable=1` 假装实时 SQLite 已封存。
2. 私有目标配置必须与当前 active migration state 的 installation/source SHA 一致。默认只接受 loopback:29490 的明确 rehearsal 库；本机生产须显式 `--allow-production-archive`，并匹配 28490、固定客户库/owner 和 `mode=active-history-archive`。新机更改 DB identity 时，先审查对应白名单适配，不能取消限制或拿供给影子库代替。
3. 先只读 inspect，在新路径生成 manifest/report，审核来源、installation、分区计数、全部 ID 摘要、逐事实 hash、整数 quota 和 Token 总额。生产 inspect 同样需要显式生产开关。
4. ACTIVE 和发布进程结束后，使用**同一已审 manifest** 执行 apply；只在四张新表中追加允许字段。它在单事务、advisory lock 下核验映射并提交事实与唯一 receipt，5 秒锁/60 秒语句超时；不修改钱包、权益、成员额度、native usage、旧团队事实或旧隔离数据。

以下仍是参数模板，生产开关仅用于已核对的当前客户库：

```powershell
python "$sourceRoot\deploy\realyu\import_usage_history.py" `
  --source "$sealedS1" --sha256 "$sealedSha" --private-target "$privateTarget" `
  --allow-production-archive --manifest-out "$newManifest" --report "$newInspectReport"

python "$sourceRoot\deploy\realyu\import_usage_history.py" `
  --source "$sealedS1" --sha256 "$sealedSha" --private-target "$privateTarget" `
  --allow-production-archive --apply --reviewed-manifest "$reviewedManifest" --report "$newApplyReport"
```

工具拒绝覆盖已有报告。若事务已提交但报告写出失败，用同一来源、同一 manifest 和新报告路径重跑，核对 `idempotent_replay`；不要换源、删除 receipt 或重放扣费。归档部分存在、映射改变、既有 receipt/事实 hash 不一致都会拒绝，不能静默修补。

这次封存来源共 31,295 条：24,520 原团队事实、6,703 personal、10 legacy_unknown、58 已删除 actor、4 原隔离。新增 self 6,713、详情 31,291；原 4 条隔离继续不展示。该计数是固定封存来源的核对值，不是不断增加的线上总请求数。self 按 actor 归属，不按付款者；平台全量只给实时全局 admin，旧 role10 不因此扩权。未知缓存保持 null，不把缓存重复加进旧 input；客户人民币金额按当前汇率只换算一次。

## 7. 发布后只读验收与故障边界

- 同时核对私有 `ACTIVE/opened`、实际 SCM Session 0/进程归属/exe SHA、`/api/status` 的版本、`engine=sub2api`、`single_core=true` 及 PG/Redis。单个 ready=200 不能替代这些检查。
- 迁移记录应为原 299 + 精确 308，共 300；核对四表/索引/FK及 receipt。归档后按封存分区核对行数、ID 唯一、整数 quota、Token/hash，不通过小数容差掩盖差异。
- 用现有授权身份只读验收 `/api/v1/realyu/usage` 与 `/summary` 的 self/personal/platform、分页/日期/模型筛选和越权拒绝。归档完成前 `legacy_archive_complete=false`，完成后本 installation 应为 true；它不表示恢复了旧提示词、响应正文或原本未知的缓存字段。
- 浏览器确认中英文、桌面/手机布局、登录后工作台、默认个人/团队 Key、队长不出现加入表单、真实复制和 CSV；未执行的设备/操作明确记为待验。[完整清单](UX-HANDOFF-SMOKE-CHECKLIST.md)和[矩阵](UX-E2E-MATRIX.md)可复用，候选通过不等于公网发布通过。
- 复用现有六路径观测和真实 SG flow/连接采样，保留首次失败、时间、CF-Ray/请求 ID、维护区间、恢复首样本和最大采样间隔。短时采样只说明观察窗口，不是长期稳定保证。

线上客户请求会正常改变余额、usage 等动态表，不能拿并发前后 hash 不同就判定归档改账。隔离完整演练已证明 122 张原业务表不变；生产仍需按归档事务写集与并发区间核查。若历史归属有错，先限制对应新查询并保留不可变档案，再用独立修正迁移处理，不直接覆盖事实。

## 8. 将整站迁往另一台 Windows Server

迁机应拿**迁机时**的新原生 PG 一致快照，保留所有 ID、设置、支付/订单、身份/Key、余额/权益及归档 receipt。若源机已完成 308，四表和 receipt 随 PG 恢复，不需再从 SQLite 导入。307 专用备份工具此时会拒绝 300 项历史；需独立审核支持当前完整 migration map 的备份/恢复入口，不能删除 308 记录或伪装为 307。

新机按 [Windows 总交接](SINGLECORE-WINDOWS-HANDOFF.md)、[原生 Windows 依赖](NATIVE-WINDOWS.md)、[SG 独立服务](SG-HY2-SERVICE-HANDOFF.md)和 [host runtime](host-runtime/README.md) 准备自己的 WinSW/SCM、loopback 端口、PG/Redis、zoneinfo/运行资源及最小 ACL。SG 必须开机无用户登录即可运行；不要依赖桌面 Clash Verge。保留客户 username/password、原 Key、域名及稳定签名材料。

先在未接入生产 Tunnel 的新机隔离验收。恢复副本不要开启真实账号 OAuth 刷新或后台业务写者；最终短写冻结后停止旧权威写者，转移最新一致状态，再让新机单独成为 writer/refresh owner。Redis 的续聊、TTL 和账户归属须按总交接核对，不能把旧影子 scope 的 affinity 直接灌入。恢复准备好之前不加入线上 Tunnel 接流量。

切流后的新写入继续以新 PG 为权威。旧机可保留为关闭写入的审计/恢复材料，不能与新机同时刷新同一 OAuth，也不能用旧备份覆盖新机客户交易。新机计划、备份/恢复和 E2E 回执重新封存；Git 上拿到代码与教程，并不意味着私有运行资料和机器权限已经交付完成。
