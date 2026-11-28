"""domain pack 的加载契约：配置写错必须启动即失败，不能静默兜底。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.agent.intent import classify_intent
from app.agent.state import Intent
from app.core.errors import ValidationError
from app.domains import get_domain, load_domain


def test_unknown_domain_raises_with_available_list() -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_domain("education")

    error = exc_info.value
    assert "education" in str(error)
    assert error.details["available"] == ["insurance"]


def test_missing_files_are_reported_all_at_once(tmp_path: Path) -> None:
    pack_dir = tmp_path / "motor"
    pack_dir.mkdir()
    (pack_dir / "domain.yaml").write_text("name: motor\ndisplay_name: 车险\n", encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    assert exc_info.value.details["missing"] == [
        "templates.yaml",
        "intents.yaml",
        "rules.yaml",
        "corpus",
        "seed",
    ]


def test_yaml_name_must_match_directory(tmp_path: Path) -> None:
    pack_dir = tmp_path / "motor"
    (pack_dir / "corpus").mkdir(parents=True)
    (pack_dir / "seed").mkdir()
    (pack_dir / "domain.yaml").write_text("name: auto\ndisplay_name: 车险\n", encoding="utf-8")
    (pack_dir / "templates.yaml").write_text("templates: {}\n", encoding="utf-8")
    (pack_dir / "intents.yaml").write_text("intents:\n  chitchat:\n    - 你好\n", encoding="utf-8")
    (pack_dir / "rules.yaml").write_text("stages: {}\n", encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    assert exc_info.value.details["declared"] == "auto"


def test_template_missing_section_fails_at_load(tmp_path: Path) -> None:
    pack_dir = tmp_path / "motor"
    (pack_dir / "corpus").mkdir(parents=True)
    (pack_dir / "seed").mkdir()
    (pack_dir / "domain.yaml").write_text("name: motor\ndisplay_name: 车险\n", encoding="utf-8")
    (pack_dir / "templates.yaml").write_text(
        "templates:\n  motor_summary:\n    version: v1\n    role: 你是助理\n"
        "    task: 判断阶段\n    constraints:\n      - 不编造\n",
        encoding="utf-8",
    )
    (pack_dir / "intents.yaml").write_text("intents:\n  chitchat:\n    - 你好\n", encoding="utf-8")
    (pack_dir / "rules.yaml").write_text("stages: {}\n", encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    assert exc_info.value.details["template"] == "motor_summary"


def test_insurance_pack_loads_within_budget() -> None:
    started = time.perf_counter()
    pack = load_domain("insurance")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert pack.name == "insurance"
    assert pack.display_name == "保险私域续保"
    assert set(pack.templates) == {"insurance_summary"}
    assert len(pack.rules["stages"]) == 4
    assert pack.rules["thresholds"]["retrieval_min_score"] == 0.35
    assert 0 < elapsed_ms < 100


def test_get_domain_caches_the_same_object() -> None:
    assert get_domain("insurance") is get_domain("insurance")


def _write_pack(
    root: Path,
    intents_text: str,
    entry_template: str | None = "motor_summary",
) -> Path:
    """造一个最小可加载的 pack；entry_template=None 表示 domain.yaml 里不写这一项。"""
    pack_dir = root / "motor"
    (pack_dir / "corpus").mkdir(parents=True)
    (pack_dir / "seed").mkdir()
    meta = "name: motor\ndisplay_name: 车险\n"
    if entry_template is not None:
        meta += f"entry_template: {entry_template}\n"
    (pack_dir / "domain.yaml").write_text(meta, encoding="utf-8")
    (pack_dir / "templates.yaml").write_text(
        "templates:\n  motor_summary:\n    version: v1\n    role: 你是助理\n"
        "    task: 判断阶段\n    constraints:\n      - 不编造\n"
        "    output_format: 三行\n",
        encoding="utf-8",
    )
    (pack_dir / "rules.yaml").write_text("stages: {}\n", encoding="utf-8")
    (pack_dir / "intents.yaml").write_text(intents_text, encoding="utf-8")
    return pack_dir


def test_pack_ships_intent_table() -> None:
    pack = load_domain("insurance")

    assert list(pack.intents) == ["delete_task", "create_task", "ask_customer", "ask_knowledge"]
    assert "建个待办" in pack.intents["create_task"]
    assert "保单" in pack.intents["ask_customer"]
    assert "理赔" in pack.intents["ask_knowledge"]


def test_unknown_intent_key_fails_at_load(tmp_path: Path) -> None:
    _write_pack(tmp_path, "intents:\n  make_task:\n    - 建个待办\n")

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    error = exc_info.value
    assert error.details["intent"] == "make_task"
    assert error.details["allowed"] == [
        "ask_customer",
        "ask_knowledge",
        "chitchat",
        "create_task",
        "delete_task",
    ]


def test_empty_intent_keywords_fail_at_load(tmp_path: Path) -> None:
    _write_pack(tmp_path, "intents:\n  ask_customer:\n    - ''\n")

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    assert exc_info.value.details["intent"] == "ask_customer"


def test_same_sentence_is_classified_differently_by_pack() -> None:
    pack = load_domain("insurance")
    sentence = "这个学员试听到哪一步了"

    # 保险词表里没有"学员""试听"，整句落进闲聊
    assert classify_intent(sentence, pack.intents) is Intent.CHITCHAT
    # 换成课程词表，同一句话命中查客户
    assert classify_intent(sentence, {"ask_customer": ("学员", "试听")}) is Intent.ASK_CUSTOMER


def test_default_table_still_used_when_no_pack_given() -> None:
    assert classify_intent("帮我看下客户张总现在什么阶段") is Intent.ASK_CUSTOMER


def test_pack_declares_entry_template() -> None:
    pack = load_domain("insurance")

    assert pack.entry_template == "insurance_summary"
    assert pack.entry.name == "insurance_summary"
    # facts 是 1.6 加的编号化事实清单：模型只能引用这里发过的编号
    assert pack.entry.variables == ("profile", "assets", "history", "facts")


def test_entry_template_is_required(tmp_path: Path) -> None:
    _write_pack(tmp_path, "intents:\n  chitchat:\n    - 你好\n", entry_template=None)

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    assert exc_info.value.details["file"] == "domain.yaml"


def test_entry_template_must_point_to_existing_template(tmp_path: Path) -> None:
    _write_pack(tmp_path, "intents:\n  chitchat:\n    - 你好\n", entry_template="course_summary")

    with pytest.raises(ValidationError) as exc_info:
        load_domain("motor", root=tmp_path)

    error = exc_info.value
    assert error.details["entry_template"] == "course_summary"
    assert error.details["available"] == ["motor_summary"]
