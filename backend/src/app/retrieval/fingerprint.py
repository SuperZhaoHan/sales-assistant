"""语料指纹：判断「这条知识要不要重新向量化」。

为什么不用「id 在不在库里」判断：
- id 是引用锚点，改正文时没人会顺手改 id（改了等于断链）；
- 只看 id，就会把「正文改过的老条目」当成已存在直接跳过，库里留着旧向量，
  检索结果和语料对不上，而且全程没有任何报错。
所以比对的是正文指纹，不是 id。指纹与向量用的是同一段文字，拼法一改两者一起变。

不截断 sha：64 位十六进制一行多占 48 字节，30 条语料省下的空间不如「少一次解释」值钱。
"""

from __future__ import annotations

import hashlib


def embed_text(title: str, text: str) -> str:
    """被向量化的那段字：标题 + 正文。标题也进向量，是为了救字面匹配（见 1.5）。"""
    return f"{title}。{text}"


def content_sha(title: str, text: str) -> str:
    """同一段文字必然同一指纹；正文改一个字指纹全变。"""
    return hashlib.sha256(embed_text(title, text).encode("utf-8")).hexdigest()
