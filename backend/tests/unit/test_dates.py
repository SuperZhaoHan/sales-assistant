from __future__ import annotations

from app.agent.dates import extract_dates, sentences_with, ungrounded


def test_extract_iso_date() -> None:
    assert extract_dates("到期日 2026-03-01，请注意") == [("2026-03-01", "2026-03-01")]


def test_extract_chinese_date_with_spaces() -> None:
    """模型在话术里写"2026 年 3 月 1 日"，旧正则只认 2026-03-01，这条会漏。"""
    assert extract_dates("原定到期日为 2026 年 3 月 1 日") == [("2026 年 3 月 1 日", "2026-03-01")]


def test_extract_three_digit_month_and_day() -> None:
    assert extract_dates("2026年3月5日") == [("2026年3月5日", "2026-03-05")]


def test_impossible_date_is_dropped_not_flagged() -> None:
    """2 月 30 日不存在：这是模型写错格式，不该当成"编造了一个真实日期"上报。"""
    assert extract_dates("2026年2月30日") == []


def test_ungrounded_flags_invented_iso_date() -> None:
    found = ungrounded("到期日 2026-04-01", ["2026-03-01"])
    assert found == [("2026-04-01", "2026-04-01")]


def test_ungrounded_flags_invented_chinese_date() -> None:
    found = ungrounded("上次续保是 2026年5月20日", ["2026-03-01"])
    assert found == [("2026年5月20日", "2026-05-20")]


def test_grounded_date_is_not_flagged_even_in_chinese_form() -> None:
    """中文写法和 ISO 写法指向同一天时，必须视为同一件事，不能因为写法不同就误报。"""
    assert ungrounded("到期日 2026 年 3 月 1 日", ["2026-03-01"]) == []


def test_no_date_returns_empty() -> None:
    assert extract_dates("张先生您好，想确认一下续保安排") == []


def test_sentences_with_returns_full_sentence() -> None:
    text = "【阶段】D。续保日期 2026-04-01 已过。请尽快联系。"
    assert sentences_with(text, "2026-04-01") == ["续保日期 2026-04-01 已过"]
