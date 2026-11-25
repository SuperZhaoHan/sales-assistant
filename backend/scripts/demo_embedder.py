"""把 embedder 的四步拆开打印出来：看不懂向量时，先看具体输入输出。

跑法：cd backend && .venv/bin/python scripts/demo_embedder.py
"""

from __future__ import annotations

from app.retrieval.embedder import _KEEP, HashingEmbedder

TEXTS = ("核保说要加费是什么意思", "核保结论分为五档", "意外险按职业类别定价")


def main() -> None:
    embedder = HashingEmbedder()

    print("== 第 1 步：清洗（只留中文/字母/数字）==")
    for text in TEXTS:
        print(f"  {text}  ->  {clean(text)!r}")

    print("\n== 第 2 步：切成二元组 ==")
    for text in TEXTS:
        grams = embedder._grams(clean(text))
        print(f"  {text}  ->  {grams}  共 {len(grams)} 个")

    print("\n== 第 3 步：入桶 + 归一化 -> 256 个浮点 ==")
    for text in TEXTS:
        vector = embedder.embed(text)
        nonzero = [(i, round(x, 3)) for i, x in enumerate(vector) if x]
        print(f"  {text}: 非零 {len(nonzero)}/256 个，前 4 个 -> {nonzero[:4]}")

    print("\n== 第 4 步：点积（越接近 1 越像）==")
    vectors = [embedder.embed(text) for text in TEXTS]
    for i in range(len(TEXTS)):
        for j in range(i + 1, len(TEXTS)):
            print(f"  「{TEXTS[i]}」vs「{TEXTS[j]}」 = {dot(vectors[i], vectors[j])}")


def clean(text: str) -> str:
    return _KEEP.sub(" ", text).strip().lower()


def dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return round(sum(a * b for a, b in zip(left, right, strict=True)), 3)


if __name__ == "__main__":
    main()
