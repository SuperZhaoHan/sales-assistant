"""2.4 接口层：无待确认线程时 confirm 必须是 409，而不是 200/500。

接口不自己判业务，它只把服务层的结论映射成状态码；
所以这里断言的是「映射没写错」，业务判定在 tests/unit/test_confirm.py。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_confirm_unknown_thread_returns_409(client: TestClient) -> None:
    response = client.post(
        "/v1/tasks/confirm", json={"thread_id": "t-24-api-ghost", "approved": True}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["details"]["code"] == "NO_PENDING_CONFIRM"
    assert body["error"]["details"]["status"] == "missing"


def test_confirm_rejects_extra_fields(client: TestClient) -> None:
    response = client.post(
        "/v1/tasks/confirm",
        json={"thread_id": "t-24-api-ghost", "approved": True, "task_id": "tk-伪造"},
    )

    assert response.status_code in {400, 422}, "多塞字段必须被拒，不能让调用方指定写什么"
