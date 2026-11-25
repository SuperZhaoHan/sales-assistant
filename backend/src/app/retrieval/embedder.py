""" Embedder（/ɪmˈbedər/，向量化器）：把一段文字变成一个定长向量。

1.5 先用「哈希 embedder」把管线跑通：
- 确定性：同一段文字每次得到同一个向量，评测数字才可复现；
- 零依赖：不联网、不下载模型，30 条语料一次灌库毫秒级；
- 但它只做字面匹配（字符二元组），语义相近但用词不同的问句会漏。

接真模型时只换这一层：实现同一个 ``embed``，Retriever 和存储层一行都不用改。
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

# 只保留中文与字母数字，标点、空格、换行一律丢掉 —— 否则「，」也会被算进特征
_KEEP = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


class Embedder(Protocol):
    """可替换边界：任何实现了 ``dim`` 与 ``embed`` 的对象都能顶上来。"""

    dim: int

    def embed(self, text: str) -> tuple[float, ...]: ...


class HashingEmbedder:
    """字符 n-gram 哈希到固定维度，再归一化。"""

    # [TUTOR-JAVA] __init__ 里的默认参数是「编译期常量」，实例化时求值一次；
    # [TUTOR-JAVA] 不像 Java 的 final 字段必须在声明处赋值，Python 习惯写在签名里。
    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim 必须为正数")
        self.dim = dim

    def embed(self, text: str) -> tuple[float, ...]:
        cleaned = _KEEP.sub(" ", text).strip().lower()
        if not cleaned:
            return tuple([0.0] * self.dim)

        # [TUTOR-WHY] 用二元组而不是单字：单字「保」「险」区分度太低，二元组能带上词序信息。
        grams = self._grams(cleaned)
        # 累加计数到桶里（哈希桶碰撞是允许的，256 个桶装 30 条语料绰绰有余）
        buckets = [0.0] * self.dim
        for gram in grams:
            buckets[self._bucket(gram)] += 1.0

        # [TUTOR-JAVA] 归一化 ≈ 把向量投影到单位圆上，之后相似度就是点积，不用再除模长。
        norm = sum(value * value for value in buckets) ** 0.5
        if norm == 0.0:
            return tuple(buckets)
        return tuple(value / norm for value in buckets)

    @staticmethod
    def _grams(cleaned: str) -> list[str]:
        parts = cleaned.split()
        grams: list[str] = []
        for part in parts:
            if len(part) <= 1:
                grams.append(part)
                continue
            grams.extend(part[i : i + 2] for i in range(len(part) - 1))
        return grams

    def _bucket(self, gram: str) -> int:
        # [TUTOR-WHY] 用 blake2b 而不是内置 hash()：后者每个进程加盐，重启后分数会变，
        # [TUTOR-WHY] 评测数字就不可复现了。
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dim
