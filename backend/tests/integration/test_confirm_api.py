"""2.4 接口层：无待确认线程时 confirm 必须是 409，而不是 200/500。

接口不自己判业务，它只把服务层的结论映射成状态码；
所以这里断言的是「映射没写错」，业务判定在 tests/unit/test_confirm.py。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

OPERATOR = {"X-Actor-Id": "u-001", "X-Actor-Role": "operator"}


def test_confirm_unknown_thread_returns_409(client: TestClient) -> None:
    response = client.post(
        "/v1/tasks/confirm",
        json={"thread_id": "t-24-api-ghost", "approved": True},
        headers=OPERATOR,
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
        headers=OPERATOR,
    )

    assert response.status_code in {400, 422}, "多塞字段必须被拒，不能让调用方指定写什么"


def test_viewer_cannot_confirm_and_is_audited(client: TestClient) -> None:
    """2.6：viewer 能查不能确认；拒绝要留痕，且一次 403 之后库里仍是 0 行任务。"""

    response = client.post(
        "/v1/tasks/confirm",
        json={"thread_id": "t-26-viewer", "approved": True},
        headers={"X-Actor-Id": "u-view", "X-Actor-Role": "viewer"},
    )

    assert response.status_code == 403, "viewer 不得越过写门槛"
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["details"]["code"] == "ROLE_NOT_ALLOWED"
    assert body["error"]["details"]["required"] == "operator"

    actions = client.app.state.store.list_actions("t-26-viewer")  # type: ignore[attr-defined]
    assert len(actions) == 1, "被拒绝的确认必须留痕，否则查不出谁尝试过越权"
    assert actions[0]["status"] == "forbidden"
    assert "actor=u-view" in str(actions[0]["detail"])


def test_missing_role_header_fails_closed(client: TestClient) -> None:
    """拿不到身份 = 最小权限，不是放行。"""

    response = client.post("/v1/tasks/confirm", json={"thread_id": "t-26-anon", "approved": True})

    assert response.status_code == 403
    assert response.json()["error"]["details"]["role"] == "viewer"


def test_unknown_role_is_rejected_not_downgraded(client: TestClient) -> None:
    """角色拼错不能静默降级成 viewer（配置错误会被藏起来）。"""

    response = client.post(
        "/v1/tasks/confirm",
        json={"thread_id": "t-26-badrole", "approved": True},
        headers={"X-Actor-Id": "u-002", "X-Actor-Role": "superuser"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"]["code"] == "ROLE_UNKNOWN"
