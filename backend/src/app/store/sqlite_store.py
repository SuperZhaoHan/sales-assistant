"""客户档案存储层：联系人、持有资产两张表。

为什么 1.2 先用 SQLite 而不是 Postgres：
- 1.2 的验收是「查得到、已脱敏、库里没有明文」，不需要向量检索；
- Postgres 要在容器里跑，1.5 才需要它（pgvector 在那边）；
- 用标准库 sqlite3，零新依赖，测试里 ``:memory:`` 就能跑，不拖 CI。
替换点留在 ``CustomerStore`` 这个接口上：1.5 换成 Postgres 时，工具层一行不改。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.core.errors import ValidationError
from app.security.masking import mask_pii

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    id_card TEXT NOT NULL,
    advisor TEXT NOT NULL,
    last_interaction_at TEXT
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    product TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT,
    premium_cents INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);
CREATE INDEX IF NOT EXISTS idx_assets_contact ON assets(contact_id);
"""

# 白名单：表名/列名不参与 SQL 拼接，静态扫描（bandit B608）也不会报
_RAW_QUERIES = {
    "contacts": "SELECT * FROM contacts",
    "assets": "SELECT * FROM assets",
}

# 允许改的列也走白名单：(表, 列) -> SQL。没有这条约束，就等于把「改哪一列」交给调用方。
_UPDATES = {
    ("contacts", "last_interaction_at"): "UPDATE contacts SET last_interaction_at = ? WHERE id = ?",
    ("assets", "status"): "UPDATE assets SET status = ? WHERE id = ?",
}


@dataclass(frozen=True, slots=True)
class Contact:
    """一行联系人。字段只读，取出来以后没人能就地改它。"""

    id: str
    name: str
    phone: str
    id_card: str
    advisor: str
    last_interaction_at: str


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    contact_id: str
    product: str
    status: str
    expires_at: str
    premium_cents: int


class CustomerStore:
    """两张表的读写入口。同步实现，异步调用方用 ``asyncio.to_thread`` 包一层。"""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # [TUTOR-JAVA] check_same_thread=False ≈ 允许跨线程复用连接，代价是串行化得自己负责
        self._conn: sqlite3.Connection | None = None
        # [TUTOR-JAVA] 一把对象锁 ≈ synchronized 的粗粒度版本：串行化所有写，简单但损失并发
        self._lock = threading.Lock()

    def open(self) -> CustomerStore:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ValidationError("存储未打开：先调 open()", details={"store": self._path})
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ 灌数据

    def load_seed(self, seed_dir: str | Path) -> tuple[int, int]:
        """从 ``seed/contacts.yaml`` 与 ``seed/assets.yaml`` 灌数据，返回各表行数。

        「INSERT OR REPLACE」≈ JPA 的 merge（upsert）：id 已存在就整行覆盖，而不是插入报错。
        """
        directory = Path(seed_dir)
        contacts = self._read_list(directory / "contacts.yaml", "contacts")
        assets = self._read_list(directory / "assets.yaml", "assets")

        with self._lock:
            for row in contacts:
                self.conn.execute(
                    "INSERT OR REPLACE INTO contacts VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["id"],
                        row["name"],
                        # [TUTOR-WHY] 脱敏放在写入边界而不是读出时：库文件被拿走时不该是明文
                        mask_pii(str(row["phone"])),
                        mask_pii(str(row["id_card"])),
                        row["advisor"],
                        row.get("last_interaction_at", ""),
                    ),
                )
            for row in assets:
                self.conn.execute(
                    "INSERT OR REPLACE INTO assets VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["id"],
                        row["contact_id"],
                        row["product"],
                        row["status"],
                        row.get("expires_at", ""),
                        int(row.get("premium_cents", 0)),
                    ),
                )
            self.conn.commit()
        return len(contacts), len(assets)

    @staticmethod
    def _read_list(path: Path, key: str) -> list[dict[str, str]]:
        if not path.exists():
            raise ValidationError(
                f"种子文件不存在：{path.name}", details={"path": str(path), "key": key}
            )
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = payload.get(key) or []
        if not rows:
            raise ValidationError(f"种子文件为空：{path.name}", details={"path": str(path)})
        return rows

    # ------------------------------------------------------------------ 写入

    def record_interaction(self, contact_id: str, at: str) -> None:
        """更新「最近互动时间」。

        库是唯一真相源：这里改完，下一次查询立刻看到新值 —— 任何一层都不该缓存
        ``last_interaction_at``，否则「改库后再问」就会拿到旧答案。
        """
        self._update("contacts", "last_interaction_at", at, contact_id)

    def set_asset_status(self, asset_id: str, status: str) -> None:
        """更新资产状态（待续保 / 已续保 / 已失效…）。取值由业务方保证，这一层不校验枚举。"""
        self._update("assets", "status", status, asset_id)

    def _update(self, table: str, column: str, value: str, row_id: str) -> None:
        # [TUTOR-WHY] 表和列都走白名单：更新语句一旦允许拼接，注入的入口就开了
        query = _UPDATES.get((table, column))
        if query is None:
            raise ValidationError(
                f"不允许更新 {table}.{column}",
                details={"table": table, "column": column},
            )
        with self._lock:
            cursor = self.conn.execute(query, (value, row_id))
            self.conn.commit()
        # [TUTOR-WHY] 行不存在就报错，不静默成功：否则「改库」这一步失败了却看起来成功了
        if cursor.rowcount == 0:
            raise ValidationError(
                f"要更新的行不存在：{row_id}",
                details={"table": table, "column": column, "id": row_id},
            )

    # ------------------------------------------------------------------ 查询

    def find_contact(self, name: str) -> Contact | None:
        """按姓名精确查。找不到返回 ``None``，由调用方决定怎么说「没找到」。"""
        row = self.conn.execute("SELECT * FROM contacts WHERE name = ?", (name,)).fetchone()
        return _to_contact(row) if row else None

    def list_assets(self, contact_id: str) -> tuple[Asset, ...]:
        rows = self.conn.execute(
            "SELECT * FROM assets WHERE contact_id = ? ORDER BY expires_at", (contact_id,)
        ).fetchall()
        return tuple(_to_asset(row) for row in rows)

    def contact_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0])

    def raw_rows(self, table: str) -> Iterable[sqlite3.Row]:
        """原样返回表里的行——只给测试用。"""
        # [TUTOR-WHY] 生产路径不许读它：绕过脱敏只需要一次「顺手查一下原始表」。
        # [TUTOR-WHY] 表名走白名单字典，不做字符串插值：拼接 SQL 是注入的入门姿势，静态扫描会拦。
        query = _RAW_QUERIES.get(table)
        if query is None:
            raise ValidationError(
                f"未知的表：{table}", details={"table": table, "available": sorted(_RAW_QUERIES)}
            )
        return self.conn.execute(query).fetchall()


def _to_contact(row: sqlite3.Row) -> Contact:
    return Contact(
        id=row["id"],
        name=row["name"],
        phone=row["phone"],
        id_card=row["id_card"],
        advisor=row["advisor"],
        last_interaction_at=row["last_interaction_at"] or "",
    )


def _to_asset(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        contact_id=row["contact_id"],
        product=row["product"],
        status=row["status"],
        expires_at=row["expires_at"] or "",
        premium_cents=int(row["premium_cents"]),
    )
