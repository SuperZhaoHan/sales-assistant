"""阶段 2 汇总口（一）：写操作识别正确率。

要回答的不是「意图分类器准不准」，而是更业务的一句话：
**该弹确认的有没有弹，不该弹的有没有去打扰人。**

所以每个用例都真跑一遍图，判据是「有没有停在 await_confirm」，
而不是只看 classify_intent 的返回值——分类对了但路由没接上，同样算错。
"""

from __future__ import annotations

import asyncio
import statistics
import time

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import GraphDeps, build_graph, thread_config
from app.agent.tasks import make_committer
from app.store.sqlite_store import CustomerStore

#: (话术, 期望是否"写操作需要确认", 说明)
CASES: tuple[tuple[str, bool, str], ...] = (
    ("帮张伟建个待办，3 天后回访", True, "标准写法"),
    ("记一下，李娜下周二要续保", True, "口语化「记一下」"),
    ("提醒我给王强打电话确认条款", True, "「提醒我」+ 知识词混在一句"),
    ("安排一下明天上午给陈晨发续保方案", True, "「安排一下」"),
    ("创建任务：赵敏保单到期前 7 天跟进", True, "正式说法"),
    ("这个客户下周要约见面，帮我记一下", True, "「客户」与写意图同句"),
    ("给我建个待办，张伟犹豫期还剩两天", True, "写 + 政策词"),
    ("帮我安排一下周六回访刘洋", True, "「安排一下」无客户名"),
    ("帮我查一下张伟的保单，然后建个待办提醒我", True, "查 + 写混合，应按写处理"),
    ("把张伟那条待办删了", True, "删任务：无任务号，靠 find_task 认目标"),
    ("张伟这单先关了吧", True, "口头「关单」也是写，但词表没有"),
    ("张伟现在什么阶段", False, "只读：阶段"),
    ("李娜的保单什么时候到期", False, "只读：到期"),
    ("犹豫期退保怎么算", False, "只读：政策"),
    ("理赔流程是什么", False, "只读：流程"),
    ("王强的资产情况怎么样", False, "只读：资产"),
    ("这个客户跟到哪一步了", False, "只读：进度"),
    ("今天天气不错啊", False, "闲聊"),
    ("谢谢，辛苦了", False, "闲聊"),
    ("周末愉快", False, "闲聊"),
    ("你先忙，我等下再说", False, "闲聊"),
)


async def main() -> None:
    store = CustomerStore(":memory:").open()

    async def lookup_customer(name: str) -> str:
        return f"{name}，保单 2026-12-05 到期，最近一次跟进 11-20"

    async def retrieve_knowledge(_query: str) -> str:
        return "犹豫期 15 天内退保按现金价值计算"

    async def find_task(customer_name: str) -> str | None:
        task = store.find_open_task(customer_name)
        return task.id if task is not None else None

    async def delete_task(task_id: str) -> None:
        store.close_task(task_id)

    # 删除类用例需要一个已存在的待办，否则会先撞上「没有可撤销的待办」
    store.add_task(customer_name="张伟", title="3 天后回访", dedupe_key="det-seed-task")

    deps = GraphDeps(
        lookup_customer=lookup_customer,
        retrieve_knowledge=retrieve_knowledge,
        commit_task=make_committer(store),
        find_task=find_task,
        delete_task=delete_task,
    )
    graph = build_graph(deps, checkpointer=InMemorySaver())

    hits = 0
    false_confirm: list[str] = []
    latencies: list[float] = []

    for index, (text, expected_write, note) in enumerate(CASES):
        thread = f"det-{index:02d}"
        started = time.perf_counter()
        result = await graph.ainvoke({"task": text, "customer_name": "张伟"}, thread_config(thread))
        latencies.append((time.perf_counter() - started) * 1000)
        paused = "__interrupt__" in result

        if paused == expected_write:
            hits += 1
        elif expected_write:
            false_confirm.append(f"漏判（该确认没确认）：{text}（{note}）")
        else:
            false_confirm.append(f"误判（不该确认却弹了）：{text}（{note}）")

    total = len(CASES)
    write_cases = sum(1 for _, want, _ in CASES if want)
    print(f"用例 {total} 条（写 {write_cases} / 非写 {total - write_cases}）")
    print(f"识别正确率    {hits}/{total} = {hits / total:.1%}")
    print(f"单条耗时中位  {statistics.median(latencies):.2f} ms  合计 {sum(latencies):.1f} ms")
    for item in false_confirm:
        print("  " + item)
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
