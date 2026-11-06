# 12 测试方案（正常 / 边界 / 对抗 / 权限 / 故障）

> 编号：12 ｜ 来源：ima 笔记「工程技术与创新应用项目汇总 · docs/src/test」
> 原始要求（ima 原文）：基于正常、边界、对抗、权限和故障场景，设计一个具体量化的 test 方案，md 格式和 docx 文档分别保存

---

## 1. 测试环境
- 被测系统：拾光连锁 · 会员权益智能体 MVP（员工端 + 管理端）
- 后端：FastAPI（localhost:8001），SQLite 持久化
- 鉴权：JWT 无状态，POST 经 body.token，GET 经 X-Token / Authorization / 查询参数
- 演示账号：员工端 employee01 / Employee@123456；管理端 manager01 / Manager@123456
- 知识库基线：v2026.12（4 类券：man200_30 / disc85 / voucher5 / exchangeA）

## 2. 量化通过标准（通用）
- 功能正确性：返回 HTTP 状态码与关键字段与预期一致。
- 鉴权：越权/无 token 一律 401；参数非法一律 400。
- 性能：单笔查询/核销接口 P95 < 1s（本地 SQLite 场景）。
- 可审计：每一次核销、转人工、发版事件均可在审计看板追溯。
- 刚性红线：退货/追回/解释冲突类一律 escalate=true 且不得自动放行（usable=false）。

## 3. 五类场景用例

### 场景 1 · 正常（Normal）
验证核心主流程在合规输入下的正确闭环。

| 用例 | 名称 | 接口 | 输入 | 预期结果 | 通过判定 |
|---|---|---|---|---|---|
| TC1.1 | 员工端登录 | POST /api/login | body: {username:employee01, password:Employee@123456} | 200，返回 token（含 sub/role/name） | 返回有效 JWT，role=employee |
| TC1.2 | 权益精准查询 | POST /api/employee/chat | body: {token, session_id, question:'满200减30怎么用'} | SSE meta: sources 非空、version=v2026.12、biz_id 非空、escalate=false | 回答命中满200减30规则且带来源与版本 |
| TC1.3 | 代生成核销码 | POST /api/employee/generate_code | body: {token, coupon_key:'man200_30'} | 200，返回 6 位 code 与 biz_id、rule_version | 生成唯一 6 位码并落库 |
| TC1.4 | 核销计价（满减） | POST /api/employee/redeem | body: {token, code, order_amount:268} | 200，new_amount=238.00（268-30），usable=true | 满200减30正确扣减 |

### 场景 2 · 边界（Boundary）
验证券规则边界与非法输入的防御。

| 用例 | 名称 | 接口 | 输入 | 预期结果 | 通过判定 |
|---|---|---|---|---|---|
| TC2.1 | 抵用券不满门槛 | POST /api/employee/redeem | generate_code(voucher5) 后 redeem(order_amount:30) | usable=false，reason 含'满50元可用' | 低于 50 元门槛判不可用 |
| TC2.2 | 空核销码 | POST /api/employee/redeem | body: {token, code:'', order_amount:100} | 400 或 usable=false | 空码被拒绝，不计入核销 |
| TC2.3 | 零/负金额 | POST /api/employee/redeem | body: {token, code, order_amount:0} | usable=false 或 400 | 金额边界被拦截 |
| TC2.4 | 未知优惠券 | POST /api/employee/generate_code | body: {token, coupon_key:'not_exist'} | 400，message:'未知优惠券' | 未知券被拒绝 |

### 场景 3 · 对抗（Adversarial）
验证高风险场景强制升级与抗异常输入。

| 用例 | 名称 | 接口 | 输入 | 预期结果 | 通过判定 |
|---|---|---|---|---|---|
| TC3.1 | 兑换券退货 | POST /api/employee/chat | question:'兑换券A我要退货怎么处理' | escalate=true，handler=人工/运营，need_confirm=true | 退货类强制转人工，不自动放行 |
| TC3.2 | 无效 token 调用 | POST /api/employee/chat | body: {token:'bad', question:'...'} | 401，message:'未授权' | 伪造 token 被拒绝 |
| TC3.3 | 异常/超长输入 | POST /api/employee/chat | question: 500 个特殊字符/随机串 | 不 500，返回正常应答或转人工 | 系统不崩溃，优雅降级 |
| TC3.4 | 强行核销已转人工券 | redeem 前经 chat 触发 escalate | 对 escalate 券尝试 redeem | usable=false 或 escalate 提示转人工 | 高风险券不得自动核销 |

### 场景 4 · 权限（Permission）
验证最小权限与角色隔离。

| 用例 | 名称 | 接口 | 输入 | 预期结果 | 通过判定 |
|---|---|---|---|---|---|
| TC4.1 | 无 token 访问管理端 | GET /api/manager/kb | 不带任何 token | 401 | 未认证被拒 |
| TC4.2 | 员工越权访问管理端 | GET /api/manager/kb | header: X-Token=员工 token | 401（role 不符） | 员工不可读管理端 |
| TC4.3 | 管理端访问员工接口 | POST /api/employee/chat | body: {token=管理 token} | 401（role 不符） | 管理不可冒用员工端 |
| TC4.4 | 错误密码登录 | POST /api/login | password 错误 | 401，message:'账号或密码或角色错误' | 错误凭证被拒 |

### 场景 5 · 故障（Failure）
验证版本化、持久化与并发一致性。

| 用例 | 名称 | 接口 | 输入 | 预期结果 | 通过判定 |
|---|---|---|---|---|---|
| TC5.1 | 知识库发版后旧版不可见 | POST /api/manager/kb/publish | body: {token, version:'v2026.13', note:'测试发版'} | 200，meta.version=v2026.13；GET /kb 仅见新版本 | 发版生效，版本可溯 |
| TC5.2 | 服务重启数据持久化 | 重启 uvicorn 后查 | restart 后端，GET /api/manager/audit | 重启前后 redemptions/escalations 记录一致（SQLite 落盘） | 数据不丢失 |
| TC5.3 | 同码并发核销 | redeem 同一 code 两次 | 第一次 usable=true；第二次应失败 | 第二次 400/usable=false（码已 used，不可重复核销） | 防重复核销 |
| TC5.4 | 发版回退 | upsert 旧规则 + 重新 publish | body 恢复基线后 publish | version 更新且快照可审计 | 支持回退并留痕 |

