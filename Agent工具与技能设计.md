# 拾光连锁智能体系统 · Agent 工具与技能设计（第八步 · 待确认）

> 范围：把"固定流程问答"升级为"会自主决策的智能体"。本步**只设计工具（Function Calling 规范）与 skill，说明调用顺序约束与提示词防绕过机制**，确认后再编码。
> 约束对齐：已确认一页纸需求中智能体边界为「仅建议/只读、不自主修改会员资产」。本设计将"自主决策"收敛在**报销草稿（可回退、需审批）**域，会员权益域仍保持只读辅助。

---

## 一、设计原则（先定边界，再定工具）

1. **草稿即终点，不是工单即生效**：智能体可自主跑完「检索→校验→建草稿→路由审批」，但**绝不自主提交/改资产**；提交由人工在回路中点击。
2. **校验不可跳过**：`create_draft` 必须有前置 `validate_invoice` 通过 + `check_budget` 充足，否则服务端直接拒绝（不只靠提示词）。
3. **全链路可重建**：每次工具调用都带 `trace_id`、记审计（呼应一页纸审计基线）。
4. **转人工是合法终点**：任何不确定/超阈值/校验失败，必须 `escalate_to_human`，不得硬编或编造。

---

## 二、建议的工具（Function Calling 规范）

> 标注：【读】只读 【写】写草稿/状态。所有工具返回结构化 JSON，含 `ok` 与 `trace_id`。

| # | 工具 | 类型 | 职责 | 前置条件（硬约束） |
|---|---|---|---|---|
| T1 | `search_policy` | 【读】 | 检索报销制度条文（RAG），返回带来源片段 | 无 |
| T2 | `validate_invoice` | 【读】 | 校验发票：类型/税号/金额/日期/抬头/重复报销 | 无（可独立先跑） |
| T3 | `check_budget` | 【读】 | 查员工/部门当期预算余额 | 无 |
| T4 | `create_draft` | 【写】 | 生成报销草稿（**非提交**），落 `drafts` 表 | **必须** T2 返回 `valid=true` 且 T3 返回 `sufficient=true` |
| T5 | `route_approval` | 【写】 | 按金额/规则把草稿路由给审批人（BPM） | **必须** T4 已成功生成 draft_id |
| T6 | `query_status` | 【读】 | 查草稿/报销当前状态与审批节点 | 有 draft_id |
| T7 | `escalate_to_human` | 【写】 | 转人工：带触发原因+接手人建议+上下文 | 校验失败/超阈值/不确定时**必须**调用 |
| T8 | `flag_suspicious` | 【写】 | 标记疑似违规报销（联动审计页） | 发现可疑模式时 |
| T9 | `log_audit` | 【写】 | 追加审计事件（多数工具内部自动调用，亦显式可用） | 无 |

### 关键工具 JSON Schema（节选，便于确认规范）

```json
{
  "name": "validate_invoice",
  "description": "校验一张发票是否合规。调用 create_draft 前必须先调用本工具且 valid=true。",
  "parameters": {
    "type": "object",
    "properties": {
      "invoice_no":   {"type": "string", "description": "发票号码"},
      "amount":      {"type": "number", "description": "金额（元）"},
      "tax_id":      {"type": "string", "description": "销售方税号"},
      "invoice_type":{"type": "string", "enum": ["增值税专票","增值税普票","电子发票","其他"]},
      "date":        {"type": "string", "description": "开票日期 YYYY-MM-DD"},
      "buyer_name":  {"type": "string", "description": "购方/抬头"}
    },
    "required": ["invoice_no","amount","tax_id","invoice_type","date"]
  }
}
```
```json
{
  "name": "create_draft",
  "description": "仅生成报销草稿，绝不提交。前置：validate_invoice(valid=true) 且 check_budget(sufficient=true)。",
  "parameters": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "invoice_no": {"type": "string"},
            "amount": {"type": "number"},
            "category": {"type": "string", "description": "差旅/办公/招待…"}
          }
        }
      },
      "validate_ref": {"type": "string", "description": "前置 validate_invoice 返回的校验凭证ID，服务端会复核"}
    },
    "required": ["items","validate_ref"]
  }
}
```

> **服务端兜底（防绕过核心）**：`create_draft` 收到请求后，**不信任模型自述**，而是用 `validate_ref` 重新执行校验逻辑；若服务端校验未通过，直接返回 `ok=false` 并记审计异常——即便提示词被绕过，工具层面也拒绝建单。

---

## 三、建议的 Skill（可复用能力包）

Skill = 封装「专属提示词 + 工具集 + 守卫规则 + 版本」的可复用单元（呼应架构选型"技能拆分"）。

| # | Skill | 组成工具 | 说明 |
|---|---|---|---|
| S1 | `policy_qa_skill` | T1 | 纯问答（即现有固定流程），回答末尾附引用 |
| S2 | `reimbursement_skill` | T1→T2→T3→T4→T5→T6，失败走 T7/T8 | 报销全流程自主编排（草稿级） |
| S3 | `compliance_check_skill` | T2+T3+T1 | 仅做合规与预算核验，不出草稿，供人工调用 |
| S4 | `human_handoff_skill` | T7 | 统一转人工话术与上下文打包 |

> 编排建议：`reimbursement_skill` 为默认员工入口；`policy_qa_skill` 覆盖纯咨询；`compliance_check_skill` 供管理员/审计复用；`human_handoff_skill` 作为各 skill 的兜底出口。

---

## 四、调用顺序约束（重点）

### 4.1 强顺序（硬闸门）
```
search_policy(T1) ─┐
                    ├─► validate_invoice(T2) ─► [valid=true?]
check_budget(T3) ──┘                    │
                                        ├─ 否 ─► escalate_to_human(T7) 或 flag_suspicious(T8) → 结束
                                        │
                                        是 ─► create_draft(T4) ─► route_approval(T5) ─► query_status(T6) → 等人工提交
```
**强制规则（写进工具描述 + 服务端）：**
- **R1**：`create_draft` 之前**必须**存在同会话内 `validate_invoice` 返回 `valid=true` 且 `check_budget` 返回 `sufficient=true`；否则服务端拒绝。
- **R2**：`route_approval` **必须**在 `create_draft` 成功并获得 `draft_id` 之后调用。
- **R3**：`submit`（最终提交）**不提供为工具**——只能由人工在界面点击，模型无此能力（落实"不自主修改资产"）。
- **R4**：`validate_invoice` 失败 → **必须** `escalate_to_human` 或 `flag_suspicious`，**禁止**改参数重试绕过（如私自下调金额再建单）。
- **R5**：同一发票号重复出现 → `validate_invoice` 标记重复，禁止再次进入建单分支。

### 4.2 终止条件（防止无限循环）
- 草稿已建且已路由 → 停，调用 `query_status` 告知用户"待审批"，等待人工。
- 连续 2 次校验失败或超 3 轮未决 → 强制 `escalate_to_human`。

---

## 五、如何用系统提示词约束"不得跳过校验直接建单"

提示词分两层：**行为层（告诉模型该怎么做）+ 禁区层（硬性否定约束）**。

### 5.1 行为层（正向）
> "你是报销助理。处理报销时，严格按 `search_policy → validate_invoice → check_budget → create_draft → route_approval` 的顺序使用工具。每调用一个写操作前，先展示上一步的校验结论。"

### 5.2 禁区层（负向硬约束）
> "**绝对禁止**以下行为，违反任一条即属严重错误：
> 1. 未先调用 `validate_invoice` 并取得 `valid=true`，就调用 `create_draft`；
> 2. 为通过校验而**自行修改/下调/虚构**发票金额、税号、日期；
> 3. 对重复发票号或校验失败的单据再次尝试建单；
> 4. 调用任何能直接提交、改余额、改会员资产的工具（你根本没有这些工具）。
> 若你发现自己无法满足前置条件，立即停止并调用 `escalate_to_human`，说明原因与已掌握信息。"

### 5.3 提示词之外的三道保险（必须同时上）
1. **服务端闸门**：`create_draft` 用 `validate_ref` 重算校验，提示词被绕过也拒绝（见 4.1 R1）。
2. **审计异常检测**：若检测到"无前置 `validate_invoice` 即 `create_draft`"的会话轨迹，记 `audit_flags` 异常并告警。
3. **低温度 + 结构化**：`temperature≈0`、要求模型在调用 `create_draft` 前先输出"校验结论摘要"，便于人工与审计审阅。

---

## 六、与既有步骤的衔接

- **第六步 `/chat`**：当前是固定流程问答；升级后由 `policy_qa_skill` 承接纯咨询，报销意图则路由到 `reimbursement_skill`。
- **第七步管理后台**：`flag_suspicious`(T8) 写入的标记在审计页可见；`create_draft` 草稿可纳入审计筛选。
- **数据模型**：需新增 `drafts`（草稿）、`approvals`（审批路由）、`tool_calls`（每次工具调用审计）三表（编码阶段补 DDL）。
- **权限**：`reimbursement_skill` 仅员工可用；`compliance_check_skill` 员工/管理员可用；工具级权限沿用 RBAC+ABAC。

---

## 七、待确认项（确认后分批编码）

1. **范围确认**：同意把"自主决策"收敛在**报销草稿域**（不碰会员资产/最终提交），还是你希望首期就开放某类"部分自动执行"？
2. **工具集**：上面 T1~T9 是否够用？要不要加 `query_employee_profile`（查员工职级/差标）、`ocr_invoice`（发票图片识别）？
3. **提交权限**：确认"最终提交"由人工点击、模型无此工具（R3）？
4. **校验强度**：`create_draft` 的服务端重算（防绕过）是否采用（强烈建议是）？
5. **Skill 组织**：默认员工入口是单一 `reimbursement_skill` 自动分流，还是用户显式选"问答/报销"模式？
6. **草稿存储**：新增 `drafts/approvals/tool_calls` 三表（SQLite 延续）还是直接上 PostgreSQL+pgvector？

> 默认决策（无异议即据此编码）：报销草稿域 / T1~T9 全保留（OCR 后续再加） / 提交仅人工 / 服务端重算校验开启 / 单一 skill 自动分流 / 延续 SQLite（生产迁 PG）。
