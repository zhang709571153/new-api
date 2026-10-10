# RealYu UX 交接冒烟清单（2026-10-11）

这是操作清单，不是上线成功声明。逐项填写`PASS / FAIL / NOT_RUN`、版本/SHA、时间、环境、唯一证据路径；任何首错原件保留。源码冻结时r1 HTTP已51/51，r2/r3浏览器覆盖及两个待复验缺陷见[验收矩阵](UX-E2E-MATRIX.md)。最终候选/正式发布结果只追加交接仓`lab/sub2api_e2e/`，不要改写既有失败结果。

## 1. 候选冻结前后（当前最后一轮待跑）

- [ ] 确认源码提交、补丁/构建清单、二进制SHA、嵌入前端匹配；同名版本不等于同一二进制。保留r1/r2/r3/r4产物，最终r5使用独立文件名。
- [ ] 确认受控候选仅127.0.0.1:29481，专属PG29490；核对PID、exe、启动时间、receipt与`/api/status`。不借用生产库或启动完整恢复库的gateway/refresh。
- [ ] 用既有fixture receipt运行`ux_history_http_e2e.py --mode run`的51项；不得重复seed。HTTP角色：匿名、管理员、普通、成员、队长、旧role10。完整JSON/拒绝码/金额精确值都必须核对，不能只检查200。
- [ ] 正常测试前后wallet、funding、subscription、member usage、key额度、payment指纹相同；临时invite恢复。遇异常先保存，再定位，不以补款/删记录让断言变绿。
- [ ] 最终候选中文/英文×桌面/窄屏复验：快速切语言后立即导航首次即可到文档；上游disabled状态不是裸翻译键；记录实际URL/文字和新截图。
- [ ] 抽查首页、价格USD、文档页签、成员工作台、调用记录、平台概览、队长团队、成员仅本人、普通`/keys`隐藏与redirect、logo回首页。平台概览必须是`/admin/dashboard`，不能用管理员自己的`/dashboard`替代。
- [ ] Setup默认完整命令可见、眼睛能隐藏；personal/team切换先清旧Key，复制使用原有canonical；无额外Key下拉。只用合成fixture观察，公开截图隐藏完整命令/Key，日志不得带凭据。
- [ ] 分别登记浏览器console窗口和网络失败；0条console错误不等于所有请求成功。无横向溢出不等于所有触控/键盘交互可用。

## 2. 308发布前（必须由发布主流程完成，未在本清单中执行）

- [ ] 保存当前manifest、binary/env/launcher/helper的SHA与服务状态，确认预期唯一维护门、恢复脚本和时限；不同时跑两个发布器。
- [ ] 核对生产只读完整PG备份、schema迁移清单、独立恢复PASS及dump SHA一致，基线完整到307（299项）。完整恢复实例必须禁gateway/refresh，保留备份私密ACL。
- [ ] 核对308文件SHA及runner checksum、审核后的计划pins和全规模演练receipt。全规模31295条是历史事实归档验收，不是31295次模型重放。
- [ ] 明确短维护窗口只更换程序/施加已审核增量schema，不退回旧SQLite。已有真实订单/账务的新PG是唯一权威；备份是恢复材料，不是上线后任意回滚客户余额的许可。
- [ ] 按发布器阶段记录停止/排空、DDL、启动/health、开门；失败保留phase/首错。仅在已证明兼容且未违反新业务语义时恢复旧程序，同库账本不倒退。无法确认进程/事务退出时不能盲目恢复第二写者。

## 3. 一次性历史归档（308成功后，由主流程显式执行）

- [ ] 确认目标active installation与308已安装，使用审核的immutable S1快照及manifest；不读取活动SQLite WAL作为快照，不改旧源行。
- [ ] 私有连接参数从当前manifest/env读取，只允许审核目标数据库/owner；命令参数只传私有文件路径，原始客户记录、备份、Key、密码不得进Git。
- [ ] 先inspect并逐项比较source SHA、manifest、已存在team事实/孤立记录与目标installation；不接受只比较总数的宽松匹配。
- [ ] apply只写4张独立history事实/receipt表；不改wallet、subscription、key、funding、native usage，不重新结算、退款或补扣。无需为该事实追加暂停所有正常调用。
- [ ] 核对31295个source ID逐一覆盖且不重复：普通主体可见31233，platform31291（含58个已删除actor、actor为null），另4条deleted-team保持隔离。10条未明确scope仅原actor本人/admin可见；不归payer、不造用户。
- [ ] 核对输入/输出Token、精确quota成本总额与审计SHA；cache未知为null，显式0保留；旧input已包含cache时不能再加。原始日期保留。
- [ ] 同一输入再运行验证幂等，使用新报告路径保留第一次receipt。发布后并发正常流量可能改变native账务，核对时按请求增量解释，不能强求静态全库hash不变，更不能为了对齐而回写账本。

完整隔离演练已证明原122张业务表行摘要不变；生产执行仍需独立receipt。精确导入命令与私有args由本轮归档交接记录提供，不将凭据写进此清单。

## 4. 正式发布后只读冒烟（待跑）

- [ ] 公网完整响应确认版本/SHA对应release receipt；检查域名、TLS、公开首页/价格/文档中英文、canonical/hreflang/noindex边界。未登录目录不得暴露account/group/provider/代理/内部配置。
- [ ] 普通self默认累计包含本人personal+各队本人调用；personal筛选不包含team。队长payer身份不能读取他人personal。未授权platform/list/summary为403，伪造self user_id为400。
- [ ] 管理员platform能按真实actor筛选请求；删除actor为“未记录”且不回源身份私密字段；原生诊断独立保留，不把其仅native趋势当跨迁移累计。
- [ ] 首页/调用记录的累计请求、Token、客户费用与API/PG只读摘要对应；客户CNY只换一次，内部USD/quota不改、模型参考USD不改、group倍率不重复。
- [ ] 原有个人/团队canonical安装卡可用，普通`/keys`redirect，队长不能join，成员只能看本人；只读检查不创建额外Key或修改授权。
- [ ] 保存资金验收before/after和请求去重证据。若必要的授权模型smoke产生真实消费，按request ID核对一次settled/usage、actor/payer/team与实际扣款；不得为了UI历史重复模型调用。
- [ ] 公网失败先分清业务响应、共享出口/隧道、浏览器、单设备环境；health/单样本成功不等于持续SLA。

## 5. 明确未覆盖，不能随本次通过宣传

| 项目 | 本轮证据边界 | 后续验收 |
|---|---|---|
| 实机Safari/Android | IAB真实浏览器窄屏，不是实体移动浏览器 | iOS Safari及Android实机：登录/输入/复制/滚动/语言/前后台恢复 |
| 真商户/退款 | 未启用或收取真实商户交易；隔离钱包套餐证据独立保留 | 另行审核商户配置、CNY实付/到账、签名、重复回调、退款闭环 |
| 客户安装器 | 文档四应用/OS页签和生成命令复制 | 各目标OS实际安装/升级/恢复、现有会话保留及客户端完整调用 |
| 模型PDF/搜索/WS等 | 本轮51项HTTP不发模型请求；旧模型E2E是不同版本/目的证据 | 如本次变更影响对应链路，再按授权执行完整体、续聊、逐轮账务E2E |
| 持续稳定性 | 本轮有限时间与样本 | 单独设定观测窗口、错误分类及告警，不能用冒烟次数承诺SLA |

交接只上传脱敏源码/清单/汇总；私有原始截图、fixture、连接文件、备份及用户明细留受控目录。任何`NOT_RUN`保持原样，不因版本已发布自动变成`PASS`。
