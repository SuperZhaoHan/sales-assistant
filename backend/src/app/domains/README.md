# 换行业：一个目录，八个步骤

能零代码换的是**同一套销售动作**（保险 → 课程 → 车险 → 课程续购）。
不随行业变的部分：Agent 循环、停机判据、风控、写操作确认、审计。它们属于安全策略，不属于业务。

---

## 一、四件套分别是什么

| 文件 | 一句话 | 它进哪里 | 改错的后果 |
|---|---|---|---|
| `templates.yaml` **模板** | 话怎么说：四段 system prompt（角色 / 任务 / 约束 / 输出格式） | 渲染成 `system`，直接发给模型 | 模型跑偏、越权承诺、输出无法解析 |
| `rules.yaml` **规则** | 怎么判：阶段枚举、检索阈值、确认窗口 | 代码读它做判断 | 阶段判错、该拒答时不拒答 |
| `corpus/` **语料** | 能引什么：险种说明、理赔流程、话术库 | 1.5 切成块进 pgvector，检索命中后被引用 | 答不出、答错且没有出处 |
| `seed/` **种子** | 有什么事实：客户、持有资产、跟进任务 | 1.2 灌进 Postgres 的 `contacts / assets / tasks`，工具用 SQL 查 | 查不到客户，摘要全靠编 |

一句话记住区别：**语料是"模型读的书"，种子是"工具查的表"。**
两者都在 pack 里，但进入系统的路径完全不同——一个走向量库，一个进关系库。

再补一组容易混的：**模板是给模型看的话，规则是给代码看的数。**
"已报价待决策"这个阶段名在 `rules.yaml`，判断逻辑按它走；
而"这个阶段该说什么话"在 `templates.yaml`。

---

## 二、八个步骤

```bash
# 1. 拷目录（名字必须小写、无空格）
cp -R src/app/domains/insurance src/app/domains/course

# 2. 改身份：name 必须等于目录名，entry_template 指向下面要写的模板键
#    domain.yaml -> name: course / display_name: 课程私域续购 / entry_template: course_summary

# 3. templates.yaml：四段正文，键名用 course_summary
#    占位符保持 {profile} / {assets} / {history}（见第四节）

# 4. intents.yaml：四类意图的关键词，顺序即优先级，create_task 必须第一

# 5. rules.yaml：阶段名、阈值、确认窗口

# 6. 填 corpus/（知识文档）与 seed/（演示数据）；空目录要放 .gitkeep

# 7. config/base.yaml -> app.domain: course

# 8. 重启。日志里应该看到 domain=course templates=N rules=M
```

全程不碰任何 `.py`。

---

## 三、三条启动期校验（做错就起不来）

1. **目录名 = `domain.yaml` 的 `name`**，否则报"目录名与声明不一致"
2. **四个 yaml + `corpus/`、`seed/` 齐全**，缺件一次性报全（不是报一个改一个）
3. **`entry_template` 必填且必须指向 `templates.yaml` 里真实存在的键**，拼错一个字母也会被拦住

还有两条内容级校验：意图名必须是 `Intent` 的合法值（`ask_customer / ask_knowledge / create_task / chitchat`）；
每张模板必须四段齐全（角色、任务、约束、输出格式，缺一段整张不成立）。

---

## 四、占位符契约

三个变量名跨行业**保持不改**，由调用方统一填：

| 变量 | 含义 | 课程行业对应 |
|---|---|---|
| `{profile}` | 客户/学员基本资料 | 学员年级、意向课程 |
| `{assets}` | 已持有的东西 | 已购课程、剩余课时 |
| `{history}` | 最近互动记录 | 上次上课/试听反馈 |

改了名字，`chat.py` 那边传参就要跟着改——那就不再是"零代码"。

---

## 五、不该动的东西

| 目录 | 为什么不动 |
|---|---|
| `src/app/agent/` | 循环与停机：安全策略，不随行业变 |
| `src/app/risk/` | 风控、限流、预算：同上 |
| `src/app/observability/` | 指标与追踪：同上 |
| `src/app/tools/` | 工具协议（`is_write` / `idempotent`）：同上 |
| `src/app/domains/registry.py` | 加载与校验逻辑本身：所有行业共用 |

如果某次"换行业"改到了上面任何一个目录，说明这次的差异不是"换行业"，而是**换了一套动作**——
那属于新需求，该走正常的代码评审，而不是塞进 pack。
