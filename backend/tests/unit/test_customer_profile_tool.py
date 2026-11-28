"""1.2 验收：查得到、字段齐、已脱敏、库里没有明文。

这一批用例的共同逻辑是「少一块就红」：
- 去掉入库时的 mask_pii → 库里有明文那条红；
- 输出里漏掉 status 或 expires_at → 字段齐那条红；
- 把「未找到」写成「无记录」→ 区分两种空值那条红。
"""

# [TUTOR-JAVA] 相当于编译期约定：类型注解不求值，只为 IDE 与 mypy 服务
from __future__ import annotations

# [TUTOR-JAVA] Path ≈ java.nio.file.Path：拼路径不靠裸字符串
from pathlib import Path

# [TUTOR-JAVA] pytest ≈ JUnit 5：fixture 当 @BeforeEach，raises 当 assertThrows
import pytest

# [TUTOR-JAVA] 导入假的规划器，用来验「注册的工具真有人会调」
from app.agent.planner import FakeLLMPlanner

# [TUTOR-JAVA] ValidationError ≈ 项目自定义 RuntimeException，带结构化 details
from app.core.errors import ValidationError

# [TUTOR-JAVA] load_domain ≈ 加载一份配置类：读目录，返回领域包对象
from app.domains import load_domain

# [TUTOR-JAVA] 只为给 fixture 标返回类型，运行期不做类型检查
from app.store.sqlite_store import CustomerStore

# [TUTOR-JAVA] 装配三件套 ≈ @Bean 方法：开库、建索引、登记工具
from app.tools.bootstrap import build_retriever, open_store, register_domain_tools

# [TUTOR-JAVA] 工厂方法：把 store 闭包进去，产出一个 Tool 实例
from app.tools.customer_profile import build_customer_profile_tool

# [TUTOR-WHY] 注册表是模块级全局单例，不清空会串到下一个用例
from app.tools.registry import reset_registry

# [TUTOR-JAVA] resolve() ≈ toRealPath()：拿到本文件所在目录的真实路径
# [TUTOR-WHY] parents[2] 往上跳两层锚到 backend/，绝不写死绝对路径
SEED_DIR = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/seed"

# [TUTOR-WHY] 明文提成常量：用例里只要搜得到这几个数字，就说明泄漏了
PLAINTEXT_PHONE = "13812348000"
PLAINTEXT_ID = "110101199001011234"


# [TUTOR-JAVA] @pytest.fixture ≈ @BeforeEach：每个用例前各跑一次，互不共享
@pytest.fixture
def store() -> CustomerStore:
    # [TUTOR-WHY] 先清注册表再建库：顺序反了会把上个用例的工具带进来
    reset_registry()
    # [TUTOR-WHY] ":memory:" 是内存库，跑完即弃，仓库里不会落 .db 文件
    return open_store(":memory:", SEED_DIR)


# [TUTOR-JAVA] fixture 参数 store ≈ 声明依赖，pytest 先造好再注入
@pytest.fixture
def tool(store: CustomerStore):
    # [TUTOR-WHY] 每个用例一个全新工具实例，避免跨用例残留状态
    return build_customer_profile_tool(store)


# [TUTOR-WHY] 这条验「灌进去了」且「再灌一次不炸、不多行」
def test_seed_rows_loaded_and_idempotent(store: CustomerStore) -> None:
    # [TUTOR-WHY] 4 个人是种子文件写死的，数量变了说明有人动过种子
    assert store.contact_count() == 4
    # [TUTOR-WHY] 张伟名下 2 条资产，后面「持有资产 2 条」的断言依赖它
    assert len(store.list_assets("c-1001")) == 2
    # [TUTOR-WHY] 返回 (4, 5)：INSERT OR REPLACE ≈ merge，重复灌不报主键冲突
    assert store.load_seed(SEED_DIR) == (4, 5)
    # [TUTOR-WHY] 灌第二次行数不变：幂等要真幂等，不能只是「没抛异常」
    assert store.contact_count() == 4


# [TUTOR-WHY] 全仓库唯一允许直查原表的用例：它要验的就是「库里长什么样」
def test_no_plaintext_in_tables(store: CustomerStore) -> None:
    # [TUTOR-JAVA] 列表推导 ≈ Stream.map(...).collect(toList())：一行取一列
    phones = [row["phone"] for row in store.raw_rows("contacts")]
    # [TUTOR-JAVA] 同样写法取身份证列，两列分开断言便于定位是哪列泄漏
    id_cards = [row["id_card"] for row in store.raw_rows("contacts")]
    # [TUTOR-WHY] 先 join 再查子串：单个号码不会跨行拼出假命中
    assert PLAINTEXT_PHONE not in " ".join(phones)
    # [TUTOR-WHY] 18 位身份证在库里连一位完整数字都不该出现
    assert PLAINTEXT_ID not in " ".join(id_cards)
    # [TUTOR-WHY] 比「不含明文」更硬：格式须恰好是 [PHONE]，改掩码会红
    assert all(phone == "[PHONE]" for phone in phones)


# [TUTOR-WHY] asyncio_mode=auto 已配置，async 用例不用加任何装饰器
async def test_output_has_expiry_status_and_interaction(tool) -> None:
    # [TUTOR-JAVA] await ≈ future.get()：等这次工具执行完再往下走
    text = await tool.run({"customer_name": "张伟"})
    # [TUTOR-WHY] 断具体值和格式，不只断「非空」：这三字段是 1.3 的输入契约
    assert "到期：2026-03-01" in text
    # [TUTOR-WHY] 状态与到期日必须同时在，否则模型判不了续保优先级
    assert "状态：待续保" in text
    # [TUTOR-WHY] 最近互动是「多久没跟进」的唯一来源，缺了摘要只能瞎猜
    assert "最近互动：2026-11-18" in text
    # [TUTOR-WHY] 条数也要断：漏一条资产，模型就少算一张保单
    assert "持有资产 2 条" in text


# [TUTOR-WHY] 存储层已脱敏，这里再验一次出库：防的是未来 join 出明文
async def test_output_has_no_plaintext(tool) -> None:
    # [TUTOR-JAVA] 再调一次工具，等价于再发一次调用，不共享上次结果
    text = await tool.run({"customer_name": "张伟"})
    # [TUTOR-WHY] 手机号明文一条都不许出现在工具输出里
    assert PLAINTEXT_PHONE not in text
    # [TUTOR-WHY] 输出会进上下文，进上下文就等于进了日志，故一并拦住
    assert PLAINTEXT_ID not in text


# [TUTOR-WHY] 未找到要明说，且不能回显其他客户姓名
async def test_unknown_customer_says_not_found_without_listing_others(tool) -> None:
    # [TUTOR-WHY] 张三不在种子里，这条用的是「确实查不到」这个事实
    text = await tool.run({"customer_name": "张三"})
    # [TUTOR-WHY] 文案带上姓名，销售才知道自己打错了哪个名字
    assert "未找到客户：张三" in text
    # [TUTOR-WHY] 列出可选客户等于把通讯录整本喂进提示词，必须拦住
    assert "李娜" not in text and "陈建国" not in text


# [TUTOR-WHY] 王芳是种子里唯一 0 资产的人，专门用来测「无记录」分支
async def test_customer_without_assets_says_no_record(tool) -> None:
    # [TUTOR-JAVA] 同一个实例查第二个人，证明工具无状态、可重复调用
    text = await tool.run({"customer_name": "王芳"})
    # [TUTOR-WHY] 有此人但没资产，必须说「无记录」而不是「未找到」
    assert "持有资产：无记录" in text
    # [TUTOR-WHY] 反向约束：两种空值一旦合并，1.3 就会照着编保单
    assert "未找到" not in text


# [TUTOR-WHY] 参数层是模型输入的唯一拦截面，多传字段必须炸
async def test_extra_arg_rejected(tool) -> None:
    # [TUTOR-JAVA] pytest.raises ≈ assertThrows：捕获异常再断言其细节
    with pytest.raises(ValidationError) as caught:
        # [TUTOR-WHY] tenant_id 是本工具不认识的字段，forbid 会直接拒绝
        await tool.run({"customer_name": "张伟", "tenant_id": "t-999"})
    # [TUTOR-WHY] 报错必须点名字段：只说「参数错误」等于让人自己猜
    assert "tenant_id" in str(caught.value)


# [TUTOR-WHY] 空字符串被 min_length=1 拦住，属于最小号的边界用例
async def test_empty_name_rejected(tool) -> None:
    # [TUTOR-JAVA] with 块 ≈ try-with-resources 的括号部分：只管作用域
    with pytest.raises(ValidationError):
        # [TUTOR-WHY] 空姓名不查、不猜、不模糊匹配，直接报错
        await tool.run({"customer_name": ""})


# [TUTOR-WHY] 注册表与规划器对上：注册了却没人会调的工具等于挂块牌子
def test_registered_tools_are_known_to_planner(store: CustomerStore) -> None:
    # [TUTOR-JAVA] 加载领域包 ≈ 读配置类，规则语料都在包里，代码不认行业
    pack = load_domain("insurance")
    # [TUTOR-JAVA] 装配三件套一次调完，返回全部工具名（含顺序）
    names = register_domain_tools(pack, store, build_retriever(pack.root / "corpus"))
    # [TUTOR-WHY] 断元组而不是集合：顺序也是契约，它决定给模型的工具顺序
    assert names == ("customer_profile", "knowledge_search", "create_task", "delete_task")
    # [TUTOR-JAVA] issubset ≈ containsAll：只读两件套必须被 1.x 的假规划器认识
    assert set(FakeLLMPlanner.actions).issubset(set(names))
    # [TUTOR-WHY] create_task 是写工具，1.x 的「查→查→答」循环不该碰它；
    # 它只在 2.x 状态图的确认节点里被调，所以这里只断言它确实登记了
    assert {"create_task", "delete_task"} <= set(names)
