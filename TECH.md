# Course Compass · 定向情报引擎技术说明

> 面向后端 / 算法 / 数据同学的交接文档。前端原型（`index.html`）已用**规则引擎 v0** 真实跑通定向情报全流程；本文描述其机制，并给出生产化架构与演进路线。
> 原型中「引擎与技术」页面（侧边栏最后一项）会**实时**展示规则命中表、评分公式与流水线，改档案 / 路径即变化，可作为活文档对照。

---

## 1. 功能定位

「定向情报」是一个** gated 个性化信息推送模块**：

- **触发前提**：`profile.saved === true` 且 `paths.length > 0`。未满足时显示门槛态，引导去建档 / 选路。
- **输入**：个人画像（学校 / 专业 / 年级 / 排名 / 均分 / 每周小时 / 可实习时间 / 已修课程 / 已有成果）+ 已选路径（保研 / 考研 / 留学 / 就业，多选）。
- **输出**：一组排序后的情报卡片，每条带「为什么推给你」的可解释信号、来源等级、可选截止倒计时。
- **设计原则**：可解释（每条都能说出命中了哪些画像信号）、可追溯（来源分级 A/B/C）、不承诺录取（门槛均为参考口径）。

情报类别（9 类）：

| cat | 含义 | 例子 |
|---|---|---|
| `opp` | 机会 | 支教保研 / 专项推免、选调 / 教师编隐性门槛 |
| `gate` | 门槛 | 校内保研名额名次差、外校夏令营前 30% |
| `deadline` | 截止 | 大创申报、考研报名、留学 Round1、暑期实习 |
| `conflict` | 冲突 | 已选路径两两时间冲突预警 |
| `evidence` | 成果 | 已有作业 / 经历 → 可核验证据翻译 |
| `gap` | 缺口 | 课程—岗位技能缺口、留学语言启动检查 |
| `budget` | 时间 | 每周时间预算不足以支撑多路径 |
| `trap` | 避坑 | 付费机构「全程托管」陷阱 |
| `action` | 行动 | 本周一件事 |

---

## 2. 数据流水线

```
公开数据源 ──> 采集·OCR ──> 结构化政策库 ──> 画像匹配引擎 ──> 排序·去重 ──> 推送触发 ──> 反馈闭环
(官网/研招网/                  (字段化 +          (规则 v0 →        (匹配分 +       (建档/周一/      (已读/收藏/
 招考公告)                    来源分级)          小模型 v2)        时效 + 衰减)     倒计时)          忽略 → 重排)
                                                    ▲                                                  │
                                                    │                                                  │
                                              个人画像库 (PostgreSQL · RLS) <──────────────────────────┘
```

- **公开政策库**与**个人画像库**物理隔离；匹配在服务端完成，画像不出库。
- 反馈信号（已读 / 收藏 / 忽略）回流，驱动下一轮重排与同类降权。

---

## 3. 规则引擎 v0

### 3.1 规则结构

每条规则是一个声明式对象：

```js
{
  id: 'zhijiao',                 // 稳定唯一 ID（也是情报 item ID）
  cat: 'opp',                    // 类别
  label: '支教保研 / 专项推免',   // 人类可读名称
  signals: ['排名百分比', '路径·保研'],  // 声明读取的画像信号（用于可解释展示）
  fn: (profile, paths) => item | null   // 触发函数：不满足返回 null
}
```

`fn` 返回的 `item`：

```js
{
  paths: ['baoyan'],     // 关联路径（用于路径加权与筛选）
  title, body, detail,   // 标题 / 正文 / 展开详情
  why: ['排名 18/120', '路径·保研'],  // 命中的具体信号值（可解释核心）
  src: '校团委 / 教育部专项计划通知',  // 来源
  lv: 'B',               // 来源等级 A 官方 / B 聚合 / C 待核
  date: '2026-10-12',    // 发布 / 核实日期
  deadline: null         // 可选硬截止 ISO 日期
}
```

### 3.2 当前规则清单（16 条）

| ID | 类别 | 读取信号 | 触发条件（简述） |
|---|---|---|---|
| `zhijiao` | opp | 排名百分比、路径·保研 | 选保研 或 排名≤前 30% |
| `dachuang` | deadline | 在校建档、倒计时 | 大创截止（12/10）未到 |
| `baoyan quota` | gate | 排名、专业人数、路径·保研 | 有排名且选保研 → 算名次差 |
| `camp threshold` | gate | 排名百分比、路径·保研 | 有排名且选保研 → 对比前 30% |
| `summer intern` | deadline | 路径·就业、成果数 | 选就业 → 暑期实习窗口倒计时 |
| `kaoyan reg` | deadline | 路径·考研、倒计时 | 选考研 → 报名硬日期 |
| `liuxue round1` | deadline | 均分、路径·留学 | 选留学 → Round1 + 均分判断 |
| `path conflict` | conflict | 多选路径、时间约束 | ≥2 路径且存在 danger/warn 冲突对 |
| `evidence translate` | evidence | 成果记录 | 有成果 → 翻译第一条 |
| `course gap` | gap | 已修课程、路径·就业 | 选就业 → 扫描数据库类课程有无 |
| `time budget` | budget | 每周小时、路径数 | 每周<6h 且 ≥2 路径 |
| `scam trap` | trap | 全用户 | 恒触发（保护性） |
| `monday action` | action | 首选路径 | 有路径 → 取第一条的本周一件事 |
| `xuandiao` | opp | 专业、路径·就业 | 选就业 且 专业匹配中文/师范/教育 |
| `lang course` | gap | 已修课程、路径·留学 | 选留学 且 课表无语言类课程 |
| `intern match` | opp | 可实习时间、路径·就业 | 选就业 且 可实习时间已确定 |

> 规则是**纯函数**，只读画像与路径，无副作用；新增情报 = 新增一条规则，无需改渲染层。

### 3.3 评分公式

```
score = 50                      // 基础分
      + catWeight               // 类别权重：冲突10 / 门槛8 / 截止6 / 机会6 / 成果4 / 缺口4 / 时间2 / 行动2 / 避坑0
      + why.length * 6          // 命中画像信号越多越相关
      + (路径匹配 ? 30 : 0)      // item.paths 命中已选路径 → 强加权
      + max(0, 40 - days/8)     // 有截止时：越近越紧急（days 为剩余天数）
      - (已读 ? 8 : 0)           // 已读衰减，避免重复打扰
```

排序可选三种：`score`（匹配分）/ `deadline`（截止临近，无截止排最后）/ `date`（最新发布）。

### 3.4 筛选与反馈

- **筛选维度**：分类 cat、路径 path、来源等级 lv、关键词 q（标题+正文+信号）、只看收藏 savedOnly。
- **显式反馈**：收藏（save）、忽略（dismiss，带撤销）、标记已读（read / read-all）、恢复忽略（restore）。
- 反馈写入状态，进入下一轮评分（已读衰减）与重排（v2 在线学习）。

---

## 4. 推送触发器

| 触发 | 时机 | 内容 |
|---|---|---|
| 建档完成 | 即时 | 首批定向情报 + 门槛诊断 |
| 每周巡航 | 周一 08:00 | 本周情报 + 本周一件事 |
| 硬日期 | 前 30 / 7 / 1 天 | 倒计时升级提醒 |
| 政策更新 | 事件驱动 | 命中画像的政策变更 |
| 反馈回流 | 收藏 / 忽略后 | 同类重排 / 降权 |

生产实现：定时任务（Celery beat）扫描 `deadline` 与画像匹配；政策库更新走事件总线（采集 hash 变化 → 触发受影响用户重算）。

---

## 5. 数据 Schema（核心三表）

```sql
profile (
  user_id      uuid PRIMARY KEY,
  school       text, major text, grade text,
  rank         int,  rank_total int, gpa numeric,
  weekly_hours int,  intern_window text,
  courses      jsonb,   -- [{name, credit, term, status}]
  achievements jsonb,   -- [{title, role, result, type}]
  paths        text[],  -- enum: baoyan/kaoyan/liuxue/jiuye
  updated_at   timestamptz
);
-- RLS: USING (user_id = auth.uid())

intel_item (
  id           text,
  cat          text,    -- opp/gate/deadline/conflict/evidence/gap/budget/trap/action
  paths        text[],
  title        text, body text, detail text,
  why          text[],  -- 可解释信号
  score        int,
  source_id    uuid REFERENCES policy_source(id),
  level        char,    -- A/B/C
  published_at date, deadline date
);

policy_source (
  id           uuid PRIMARY KEY,
  url          text, title text, publisher text,
  level        char,    -- A 官方 / B 聚合 / C 待核
  fetched_at   timestamptz, hash text,
  fields       jsonb    -- 结构化政策字段
);

intel_feedback (
  user_id      uuid, item_id text,
  action       text,    -- read / save / dismiss
  created_at   timestamptz,
  PRIMARY KEY (user_id, item_id, action)
);
```

---

## 6. API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/intel/feed` | 入参 `{profile, paths, filters}`；返回排序后的 `intel_item[]`（含 `why` 与 `score`）。 |
| POST | `/api/intel/feedback` | `{item_id, action: read\|save\|dismiss}`；写反馈表，驱动重排与降权。 |
| POST | `/api/intel/rerank` | **预留 · 小模型**：对候选集语义重排 + 摘要，v2 启用。 |
| POST | `/api/evidence/analyze` | **预留 · 小模型**：作业文本 → 证据 bullets，替换规则兜底。 |
| GET | `/api/policy/sources?major=&path=` | 公开政策库检索（v1），按专业 / 路径过滤，返回分级来源。 |

**模型预留位**：原型中证据引擎与情报重排均留出 disabled 的模型选项与接口位，v0 走规则、v2 无缝切小模型，前端契约不变。

---

## 7. 隐私与合规

- **行级隔离 RLS**：画像表 `user_id = auth.uid()` 强制隔离，跨用户不可读。
- **最小化采集**：只收诊断必需字段；**不爬教务系统**、不存身份证 / 成绩原件 / 手机号。
- **可删除**：一键清空档案与反馈，级联删除派生情报。
- **来源分级**：A 官方 / B 聚合 / C 待核；C 级不进推送、只作线索。
- **不承诺录取**：所有门槛为参考口径，标注「以当年官方办法为准」。
- 课程信息全部由用户主动提供或勾选确认；上传解析与公开推荐都只是候选，导入由本人核对。

---

## 8. 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React + TypeScript（生产）；当前原型为单文件 Vanilla HTML/CSS/JS |
| 后端 | FastAPI (Python) |
| 存储 | PostgreSQL + RLS |
| 文件 | 对象存储（培养方案原件） |
| 检索 | LangGraph + RAG（v1 政策库召回） |
| 重排 | 小模型（v2 语义重排 / 摘要 / 证据生成） |
| 排程 | OR-Tools（时间约束 / 冲突求解） |
| 图谱 | Cytoscape（课程—技能—岗位图） |
| 任务 | Celery / 定时采集 |

---

## 9. 演进路线

- **v0 · 规则引擎（当前）**：可解释规则 + 画像匹配 + 评分排序。零模型依赖，原型已跑通。
- **v1 · 政策库检索**：结构化公开政策库 + RAG 检索，按专业 / 路径召回，来源分级与时效校验。
- **v2 · 小模型重排**：语义重排 + 摘要 + 证据生成，反馈闭环在线学习；接口已预留。

---

## 10. 原型 ↔ 生产映射

| 原型（index.html） | 生产对应 |
|---|---|
| `INTEL_RULES[]` + `buildIntel()` | `/api/intel/feed` 内的规则匹配 + 评分服务 |
| `intelVisible()` 筛选排序 | feed 接口的 filters / sort 参数 |
| `state.intelRead/Saved/Dismissed` | `intel_feedback` 表 + `/api/intel/feedback` |
| `state.profile` (localStorage) | `profile` 表 (RLS) |
| `CURRICULA` 公开课程库 | 政策库 / 课程图谱（v1 检索） |
| 「引擎与技术」页实时规则表 | 内部规则调试 / 可解释性看板 |
| 证据引擎 disabled 模型选项 | `/api/evidence/analyze`（v2） |

> 前端只负责功能与页面排版；本文档供后端 / 算法同学按契约实现，规则与评分逻辑可直接从原型 `INTEL_RULES` / `buildIntel` 平移。