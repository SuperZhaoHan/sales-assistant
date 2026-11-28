"""把「身份头」接成 FastAPI 依赖。

只做取头 + 组装，判定逻辑都在 ``app.security.roles``，这样单测不需要起 HTTP。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

from app.security.roles import Actor, make_actor


def current_actor(
    x_actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    x_actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
) -> Actor:
    return make_actor(x_actor_id, x_actor_role)
