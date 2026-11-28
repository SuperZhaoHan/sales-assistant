"""2.6 角色门槛：单测不碰 HTTP，直接量「谁能不能确认」。"""

from __future__ import annotations

import pytest

from app.core.errors import PermissionDeniedError
from app.security.roles import CONFIRM_MIN_ROLE, ROLE_RANK, ensure_confirm_permission, make_actor


@pytest.mark.parametrize("role", ["viewer"])
def test_viewer_is_denied(role: str) -> None:
    actor = make_actor("u-view", role)
    with pytest.raises(PermissionDeniedError) as excinfo:
        ensure_confirm_permission(actor)
    assert excinfo.value.details["code"] == "ROLE_NOT_ALLOWED"
    assert excinfo.value.details["required"] == CONFIRM_MIN_ROLE


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operator_and_admin_pass(role: str) -> None:
    ensure_confirm_permission(make_actor("u-ok", role))


def test_no_headers_means_anonymous_viewer() -> None:
    actor = make_actor(None, None)
    assert actor.actor_id == "anonymous"
    assert actor.role == "viewer"
    assert actor.rank == ROLE_RANK["viewer"]


def test_role_is_normalized_but_unknown_is_rejected() -> None:
    assert make_actor("u-1", " Operator ").role == "operator"
    with pytest.raises(PermissionDeniedError) as excinfo:
        make_actor("u-1", "superuser")
    assert excinfo.value.details["code"] == "ROLE_UNKNOWN"


def test_rank_order_is_monotonic() -> None:
    assert ROLE_RANK == {"viewer": 0, "operator": 1, "admin": 2}
