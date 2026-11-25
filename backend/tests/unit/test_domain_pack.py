"""domain pack 的加载契约：配置写错必须启动即失败，不能静默兜底。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

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

    assert exc_info.value.details["missing"] == ["templates.yaml", "rules.yaml", "corpus", "seed"]


def test_yaml_name_must_match_directory(tmp_path: Path) -> None:
    pack_dir = tmp_path / "motor"
    (pack_dir / "corpus").mkdir(parents=True)
    (pack_dir / "seed").mkdir()
    (pack_dir / "domain.yaml").write_text("name: auto\ndisplay_name: 车险\n", encoding="utf-8")
    (pack_dir / "templates.yaml").write_text("templates: {}\n", encoding="utf-8")
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
