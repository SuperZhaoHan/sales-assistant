"""删除教学注释（以 ``[TUTOR-`` 开头的单行注释）。默认只统计，加 --apply 才写回。

用法：
    python scripts/strip_tutor_comments.py             # 只看有多少行会被删
    python scripts/strip_tutor_comments.py --apply     # 真的删掉

教学注释全部写成单行、自成一行，所以整行删除永远不会破坏 Python 语法。
脚本删完会用 ast.parse 复核一遍，语法不过就报错退出，不覆盖原文件。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

TAG = "[TUTOR-"
TARGETS = ("src/app", "tests")


def strip_file(path: Path, apply: bool) -> int:
    original = path.read_text(encoding="utf-8")
    kept = [line for line in original.splitlines(keepends=True) if TAG not in line]
    removed = len(original.splitlines()) - len(kept)
    if not removed:
        return 0

    candidate = "".join(kept)
    ast.parse(candidate)  # 语法复核：删出问题就抛异常，不覆盖原文件
    if apply:
        path.write_text(candidate, encoding="utf-8")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="删除 [TUTOR-*] 教学注释")
    parser.add_argument("--apply", action="store_true", help="真正写回文件")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    files = [p for target in TARGETS for p in (root / target).rglob("*.py")]

    total = 0
    hits = 0
    for path in sorted(files):
        removed = strip_file(path, args.apply)
        if removed:
            hits += 1
            total += removed
            print(f"{path.relative_to(root)}  {removed} 行")

    if not hits:
        print("没有教学注释")
        return 0

    action = "已删除" if args.apply else "可删除（未改动，加 --apply 生效）"
    print(f"\n{hits} 个文件，{total} 行 {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
