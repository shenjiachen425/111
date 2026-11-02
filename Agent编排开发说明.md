# 拾光连锁智能体系统 · Agent 编排开发说明（第九步）

> 承接：第八步《Agent 工具与技能设计》。本步**实现** Agent 执行循环，交付 `agent_harness.py`（已本地跑通 3 个轨迹）。
> 代码：`agent_harness.py`；运行：`python3.11 agent_harness.py demo [normal|invalid|cancel]`

---

## 一、框架选择：轻量自研 ReAct Harness（不引入 LangChain/AutoGen）

| 维度 | 自研 ReAct Harness（采用） | 通用框架（LangChain/AutoGen） |
|---|---|---|
| human-in-the-loop 打断 | `confirm_fn` 回调一处插入，干净 | 需自定义 Callback / 状态机，绕 |
| 服务端闸门 + 日志 | 固定位置插入，可控可审计 | 需包裹 Tool 基类 |
| 依赖与同构 | 零额外依赖，复用 stage1 链路 | 引入重依赖，与现有代码异构成本高 |
| 后续迁移 | 核心模块直接搬入 `app/agents/` | 迁移成本高 |

> 结论：本阶段数据量小、强调「强制校验 + 人工确认 + 全链路审计」，自研 Harness 最契合，也是架构选型「可替换/可控」原则的体现。

---

## 二、执行循环（需求1：决策 → 执行 → 回填 → 再决策，≤5 轮）

```
┌─────────────────────────────────────────────────────────────┐
│  for round in 1..5:                                          │
│    1) 决策：decide(prompt) → <decision>{action, action_input}│
│    2) 若是 final_answer → 返回并结束                          │
│    3) 若是 create_draft → 先 dry-run 预览 → confirm_fn 中断  │
│        ├ 员工拒绝 → 记日志 + 转人工 + 结束                    │
│        └ 员工确认 → commit=True 正式建单                       │
│    4) 执行工具（计时）→ 写 tool_calls 日志                    │
│    5) 结果回填进 trajectory，进入下一轮                       │
└─────────────────────────────────────────────────────────────┘
超 5 轮 → 强制 escalate_to_human 收尾
```

- **决策器可注入**：`ScriptedDecider`（demo 回放）/ `LLMDecider`（复用 stage1 `call_llm`，供应商可配置）。
- **回填**：每轮的工具名+参数+结果摘要拼回提示词，模型在下一轮能看到「已做了什么」。
- **检索复用**：`search_policy` 直接复用 stage1 的 `hybrid_retrieve`（FAISS + BM25）。

---

## 三、每轮日志（需求2：工具名/参数/耗时/结果摘要）

`tool_calls` 表（供「阶段5」分析）：

```sql
CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  trace_id TEXT,
  round_no INTEGER,
  tool_name TEXT,
  thought TEXT,
  params_json TEXT,
  duration_ms INTEGER,
  result_ok INTEGER,
  result_summary TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
```

每轮 `_exec` 执行后调用 `_log_tool_call` 落库；`trace_id` 贯穿一次会话，可据此重建完整处理链路（呼应一页纸审计基线）。

---

## 四、create_draft 确认中断（需求3：调用前强制中断，员工确认后才执行）

```
模型决策 action=create_draft
   │
   ├─ _exec(create_draft, commit=False)   ← 仅取预览，不落库（dry-run）
   │
   ├─ on_event("draft_confirmation", preview)  ← 流式发给员工
   │
   ├─ confirm_fn(preview, session_id)     ← 员工在界面确认/取消
   │     ├ 确认 → _exec(create_draft, commit=True)  ← 服务端闸门复核后落库
   │     └ 取消 → 记「员工拒绝确认」日志 + escalate_to_human + 结束（不建单）
```

**服务端闸门（防绕过核心）**：`create_draft(commit=True)` 复核 `validate_ref` 是否由 `validate_invoice` 签发并存入会话态；即便提示词被绕过、模型伪造 ref，服务端也拒绝建单。这与第八步「提示词禁区 + 服务端兜底」一致。

---

## 五、3 个典型对话流程的预期轨迹（需求4）

### 轨迹 A — 正常报销（校验通过 → 确认 → 路由审批）
| 轮 | 动作 | 结果摘要 |
|---|---|---|
| 1 | `validate_invoice` | 校验通过，发票 INV-001（3200 元） |
| 2 | `check_budget` | 预算充足，剩余 5000 元 |
| 3 | `create_draft`（确认✅） | 已建草稿 #1，合计 3200 元 |
| 4 | `route_approval` | 草稿 #1 路由至 直属主管 |
| 5 | `final_answer` | 「报销草稿已生成并提交直属主管审批，合计 3200 元。」 |

### 轨迹 B — 发票不合规（校验失败 → 转人工，不建单）
| 轮 | 动作 | 结果摘要 |
|---|---|---|
| 1 | `validate_invoice` | 校验未通过：金额非正；缺少税号 |
| 2 | `escalate_to_human` | 已转人工：校验未通过，不建单 |
| 3 | `final_answer` | 「发票校验未通过，已转人工处理，请勿重复提交。」 |

### 轨迹 C — 校验通过但员工拒绝确认（不建单）
| 轮 | 动作 | 结果摘要 |
|---|---|---|
| 1 | `validate_invoice` | 校验通过，发票 INV-003（1500 元） |
| 2 | `check_budget` | 预算充足，剩余 5000 元 |
| 3 | `create_draft`（确认❌） | 员工拒绝确认，草稿未生成 → 转人工 |
| 3 | `final_answer` | 「您取消了草稿确认，该报销未生成，已转人工跟进。」 |

> 运行 `python3.11 agent_harness.py demo normal|invalid|cancel` 可复现上述三条轨迹与 `tool_calls` 日志。

---

## 六、与既有步骤衔接

- **第六/七步**：`confirm_fn` 在 SSE `/chat` 中表现为「发送 draft_confirmation 事件 → 等待员工下一条消息『确认/取消』」；`create_draft` 落库的草稿可纳入审计页筛选。
- **第八步**：本步把第八步的 T1~T9 工具落地为 `TOOLS` 注册表，`validate_invoice→create_draft` 的强顺序由「服务端闸门 + 提示词禁区」双重保障。
- **生产迁移**：`agent.db` 并入 `chat.db`；`AgentHarness` / `TOOLS` / `_log_tool_call` 搬入 `app/agents/`；`create_draft` 接真实发票/OCR 与预算服务。

---

## 七、运行方式

```bash
# 依赖（沙箱已装）：faiss-cpu, rank_bm25, jieba, numpy
python3.11 agent_harness.py demo normal    # 正常报销
python3.11 agent_harness.py demo invalid   # 发票不合规
python3.11 agent_harness.py demo cancel    # 员工取消确认
```

> 注：`agent.db` 为运行期生成数据，不纳入版本库；`报销制度.md` 为 stage1 示例语料，被 `search_policy` 复用。
