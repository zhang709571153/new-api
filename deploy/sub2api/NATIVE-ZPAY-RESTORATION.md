# 原生 ZPay 支付宝恢复操作

## 2026-10-12 — UX9 ZPay：已上线，真实 ¥5 订单已原生补到账

operation `native-ux9-20261012-r5` 于 2026-10-12 19:10:53 CST ACTIVE，维护入口关闭 30.628937 秒；请求、资金预占与批量作业排空后切换，没有中断在途请求。19:11:09 CST，真实实付 ¥5 订单通过原生拥有者查单恢复为 COMPLETED，钱包增加 ¥5，只有一笔充值入账；重复原生核验未重复加钱。原始回调 400 保留，本次到账来自主动查单，没有宣称商户通知重试已成功。

上线后公网/本地只读检查 47/47 通过，含登录、中文/英文页面、历史/趋势/团队汇总及币种展示。19:10:58–19:13:08 CST 六条监测路径各 14/14 健康，最大间隔 10.011229 秒；维护期间 9 条失败采样保留，ACTIVE 后未见失败。这些是有限时间的 HTTP 健康采样，不是长期稳定保证。 已确认实付5元及原始公网回调400，根因为JSON字符串包装；不是商户没付款或完全没有回调。过期且尚未保存商户流水的本次场景可通过原生订单拥有者verify恢复，隔离PG已验证；不能手工SQL加钱。实际生产结果见[LIVE](../../LIVE-DEPLOYMENT.md)与[UX9证据](../../lab/sub2api_e2e/ux9-zpay-20261012.json)。下方是历史配置过程，不据旧文档重复建商户。

## 前置核对

核对当前 EXE/版本与 309/310 完整 migration；使用现有管理员登录态。保留原数据库、钱包和团队账本，不能直接 SQL 给用户加余额。商户密钥从既有私有配置或私下交接材料取得，不放 Git、日志、URL 输出或聊天正文。不要打印创建/更新 provider 的完整 API 响应；GET 列表隐藏密钥，而创建响应可能含配置实体。

本机读取旧数据库 `options` 的 `EpayId`、`EpayKey`、`PayAddress`，分别映射下方 `pid`、`pkey`、`apiBase`；这些是同一个现有商户，不是申请新商户。已验证网关根地址为 `https://zpayz.cn`，只读查询返回 code=1，不代表本轮真实入账已通过。

先读取 `/api/v1/admin/payment/config`、`/api/v1/admin/payment/providers` 和 `/api/v1/payment/checkout-info`。本轮准备时充值关闭、provider 和充值订单均为空，已存在六个在售套餐，最小充值额为1。重跑前重新核验，不能据旧文档重复创建 provider，也不顺带改变套餐上下架。

## 通过原生管理 API 配置

所有请求都使用原有管理员认证，仅在本机回环或可信 HTTPS 上进行。先创建禁用实例，凭据只在调用进程内组合：

`POST /api/v1/admin/payment/providers`

```json
{
  "provider_key": "easypay",
  "name": "ZPay 支付宝",
  "enabled": false,
  "supported_types": ["alipay"],
  "payment_mode": "popup",
  "sort_order": 0,
  "limits": "",
  "refund_enabled": false,
  "allow_user_refund": false,
  "config": {
    "pid": "<现有商户号>",
    "pkey": "<现有商户密钥，仅内存>",
    "apiBase": "https://zpayz.cn",
    "compatibilityMode": "zpay",
    "notifyUrl": "https://api.realyu.fun/api/v1/payment/webhook/easypay",
    "returnUrl": "https://api.realyu.fun/payment/result"
  }
}
```

记录响应 ID 和状态即可。通过 `PUT /api/v1/admin/payment/providers/<ID>`，提交 `{"enabled":true}`，触发原生字段校验/驱动刷新。**当前支付总开关已开启，因此该步骤也会使六个原在售套餐恢复付款能力，余额充值则仍关闭。** 这属于恢复既有购买功能的范围；不更改商品内容。`popup` 使用旧版已使用的 `submit.php`；空值和 `qrcode` 走另一种接口，不能误写成 `redirect`。可见支付来源是 `easypay_alipay`，不是商户实例 ID。

在保持既有资金含义、手续费0、到账倍率1、无赠送阶梯的前提下，最后开放入口：

`PUT /api/v1/admin/payment/config`

```json
{
  "enabled": true,
  "balance_disabled": false,
  "enabled_payment_types": ["alipay"],
  "payment_visible_method_alipay_enabled": true,
  "payment_visible_method_alipay_source": "easypay_alipay"
}
```

其它字段省略以保留原设置；不添加用户汇率设置，不修改钱包、模型单价、套餐价格和消费倍率。读回配置：`enabled=true`、`balance_disabled=false`、支付方式仅 `alipay`；checkout 为支付宝、CNY、`managed_recharge=true`，六套餐内容不变。来源和可见开关不由 config GET 完整返回，需要只读核对 settings 中对应两个字段；checkout 有 alipay 本身不足以证明来源设置正确。provider还须核对popup、网关、两条回调地址。写请求前记尝试步骤，超时/未知结果后只读核对最终状态，不盲目重放。

## 真实付款验收

使用明确获授权的个人账号登录态，先记录本人可用余额；团队/套餐资金不能作为个人充值目标。经公网 `POST /api/v1/payment/orders` 创建**一笔**小额测试：

```json
{
  "order_type": "balance",
  "amount": 10,
  "payment_type": "alipay",
  "payment_source": "hosted_redirect",
  "return_url": "https://api.realyu.fun/payment/result",
  "is_mobile": false
}
```

当前建单没有业务幂等保证，网络不明时先查询订单，不盲目重复 POST。核实真实收银台显示实付¥10、返回本站路径正确；让用户在自己的支付应用完成付款。不得伪造商户成功回调或靠修改数据库制造成功。

EasyPay popup创建响应的可选 `currency` 可能省略，前端默认CNY；订单GET详情返回CNY。不可仅因创建响应缺少该可选字段判定金额错误并重复建单；仍须核对支付金额、手续费、现金/到账快照和实际收银台。本次通过同一订单恢复验收，保留最初脚本误判。

实际观察到的合法收银路径为 `zpayz.cn/submit.php` → `api.z-pay.cn` → 支付宝官方 `openapi`/`unitradeprod`/`excashier`。HTTP抓取需要正常cookie会话；无cookie错误页或原始HTML中的隐藏成功/过期模板不证明可付款或已付款。本次最终以CUA真实可见的10.00元及二维码确认扫码基线，未点击支付。支付完成只认服务端核验和账本。

付款后核对：真实公网通知、服务端签名与商户查单、订单 COMPLETED、仅一次入账、个人展示余额增加¥10、团队资金不变。回跳页面不等于付款成功；重复通知/查单不能再次增加余额。

漏通知时，使用订单本人认证的 `POST /api/v1/payment/orders/verify`，提交 `{"out_trade_no":"<该订单号>"}`，它会主动向商户核验。匿名查单仅反映已落库状态。没有用户实际付款时记录 `WAITING_FOR_PAYMENT`，不能标为完整支付 E2E 通过。

## 异常与后续部署

发生配置错误先关闭新的余额充值入口，保留 provider、原凭据和回调/查单能力，继续处理在途订单。不要删除商户实例，不重置账本，也不重跑要求“充值关闭且从未有余额订单”的首次更新计划回退 UX5。

商户开通状态、签约渠道和限额须当次确认。SMTP成功邮件本轮未验收，原生模板尚有 USD 文案；该项与网页人民币充值分开追踪。服务器迁移应带走最新完整订单、provider加密数据及原稳定加密密钥，不能仅复制这份公开模板。
