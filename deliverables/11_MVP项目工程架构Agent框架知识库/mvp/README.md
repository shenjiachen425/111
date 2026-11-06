# 拾光连锁 · 会员权益智能体 MVP

针对六人访谈识别的痛点（解释不一致、核验慢、无审计、异常无转人工、规则缺口），
以 **MVP** 方式在 **员工端 + 管理端** 两个端口落地，显式包含三大组件：
**工程架构 / Agent 框架 / 知识库**。

---

## 一、工程架构（分层）

```
┌──────────────────────────────────────────────────────────┐
│                      前端双端口                          │
│   员工端 employee.html          管理端 manager.html       │
│   (查询助手/核销/代生成码/转人工)  (知识库/审计/处理台/概览)│
└───────────────┬───────────────────────┬──────────────────┘
                │  HTTPS(SSE) + JWT      │
┌───────────────▼───────────────────────▼──────────────────┐
│                  FastAPI 无状态后端 (app.py)             │
│  /api/employee/*                /api/manager/*           │
│  角色鉴权(employee)             角色鉴权(manager)          │
├──────────────────────────────────────────────────────────┤
│  核心组件 core/                                              │
│   · agent_framework  Agent 编排器 + Tool + IntentRouter    │
│   · knowledge_base   结构化知识库(版本化/检索/发版)          │
│   · auth             JWT 角色鉴权(employee/manager)        │
│   · util             业务编号/核销码/时间                   │
├──────────────────────────────────────────────────────────┤
│  数据层 db.py (SQLite)                                     │
│   sessions / messages / redemptions / escalations /        │
│   kb_versions  —— 全链路可追溯审计链                        │
└──────────────────────────────────────────────────────────┘
```

**设计要点**
- 单一代码库、单一后端、按路径与角色区分双端，避免口径分裂（对应红线 RED-2）。
- 无状态 JWT + 多源 token（body.token 首选），规避发布网关不透传 Authorization 头。
- 所有页面 `no-cache`，根治浏览器缓存误显示。
- 知识库、Agent、鉴权均为独立模块，可独立替换/升级（如 KB 升级为向量检索、Agent 接入真实 LLM）。

---

## 二、Agent 框架

文件：`core/agent_framework.py`，是一个**与业务解耦的工具调用型智能体框架**：

| 组件 | 职责 |
|---|---|
| `Tool` / `ToolResult` | 工具抽象；业务能力封装为可注册工具 |
| `IntentRouter` | 意图识别（规则化；可整体替换为 LLM 分类器） |
| `Agent` | 编排器：按意图规划并调用工具，产出结构化 `AgentResult` |
| `Memory` | 会话记忆（按 session 存储，支撑上下文） |
| `LLMAdapter` | 可插拔大模型适配器（MVP 留接口，未启用） |
| 业务工具 | `knowledge_query`（按意图检索券规则）、`escalation`（异常转人工） |

**AgentResult（结构化输出，全可追溯）**
`answer / intent / coupon_key / sources / version / escalate / reason / handler / need_confirm / biz_id / trace`

- 未识别券 → 调 `escalation` 工具 → 转人工 + 落库 `escalations`。
- 兑换券退货追回 → 规则缺口 → 调 `escalation`（接手人：活动策划/数据风控）。
- 正常 → 调 `knowledge_query` → 答案必带事实引用 `sources` 与 `version`（单一出口）。

MVP 默认规则路由，无需外部大模型即可运行、可测试；生产可把 `IntentRouter` 换成 LLM，其余体系不变。

---

## 三、知识库

文件：`core/knowledge_base.py` + `data/knowledge.json`

- **单一口径出口**：所有解释/计算/选券/核销都源自 `COUPONS` 结构化数据。
- **版本化**：每条券带 `version` + `effective_date`；`meta` 记录当前版本/责任方/过期阈值。
- **管理端可运维**：`upsert` 入库（待发版）→ `publish_version` 发版并写 `kb_versions` 快照，答案口径随版本号更新，保证可追溯。
- **可演进**：接口形态兼容后续升级为向量检索（RAG）。

---

## 四、解决的核心痛点（对照访谈）

| 痛点 | 员工端 / 管理端解法 |
|---|---|
| PT-1 解释不一致 | 知识库单一出口 + 版本化；员工端查询附引用与版本 |
| PT-2 核验慢(3分钟→40秒) | 员工端输入核销码+金额秒级出价 |
| PT-3 投诉高(240→100) | 解释一致 + 快速核销 + 转人工兜底 |
| PT-4 规则缺口(兑换券退货) | Agent 强制转人工，管理端处理台跟踪 |
| PT-5/PT-7 售后/孤岛 | 管理端审计看板 + 转人工处理台统一视图 |
| PT-6 版本化缺失 | 知识库版本管理 + 发版快照 |

---

## 五、运行

```bash
cd mvp
pip install fastapi uvicorn
python app.py            # 默认 8001 端口
# 员工端: http://localhost:8001/employee
# 管理端: http://localhost:8001/manager
```

演示账号：
- 员工端 `employee01 / Employee@123456`
- 管理端 `manager01 / Manager@123456`

---

## 六、目录结构

```
mvp/
├── app.py                  # FastAPI 入口（双端路由 + API）
├── db.py                   # SQLite 数据层（审计/核销/转人工/版本）
├── README.md               # 本文件（工程架构说明）
├── core/
│   ├── agent_framework.py  # Agent 框架（Tool/IntentRouter/Agent/Memory）
│   ├── knowledge_base.py   # 知识库（版本化/检索/发版）
│   ├── auth.py             # JWT 角色鉴权
│   └── util.py             # 工具函数
├── data/
│   └── knowledge.json      # 结构化知识库（4 类券 + 版本/来源）
└── static/
    ├── employee.html       # 员工端
    └── manager.html        # 管理端
```
