"""角色与写权限：本服务只做「授权」，不做「认证」。

身份从哪来：真实部署里由网关/SSO 注入（``X-Actor-Id`` / ``X-Actor-Role``），
并且网关**必须剥掉**客户端自带的同名头——否则谁都能自称 admin。
这个前提写在这里，是为了面试时能立刻回答「你的鉴权在哪一层」。

为什么默认是 viewer：拿不到身份时给最小权限，属于 fail-closed（失败时收口，而不是放行）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.errors import PermissionDeniedError

Role = Literal["viewer", "operator", "admin"]

#: 角色等级。数字只用于比较，别当业务含义用。
ROLE_RANK: dict[str, int] = {"viewer": 0, "operator": 1, "admin": 2}

#: 确认写操作需要的最低角色。
CONFIRM_MIN_ROLE: Role = "operator"

ANONYMOUS = "anonymous"


@dataclass(frozen=True, slots=True)
class Actor:
    """一次请求的调用方：谁、以什么身份。"""

    actor_id: str
    role: Role

    @property
    def rank(self) -> int:
        return ROLE_RANK[self.role]


def make_actor(actor_id: str | None, role: str | None) -> Actor:
    """从两个头拼出调用方；角色不认识就拒绝，而不是猜。

    为什么不把未知角色降级成 viewer：降级看起来安全，实际会把配置错误藏起来——
    运维以为配了 operator，系统默默按 viewer 跑，写操作全被拒却不报错。
    """

    raw_role = (role or "viewer").strip().lower()
    if raw_role not in ROLE_RANK:
        raise PermissionDeniedError(
            f"未知角色：{raw_role}",
            details={"code": "ROLE_UNKNOWN", "role": raw_role},
        )
    return Actor(actor_id=(actor_id or "").strip() or ANONYMOUS, role=raw_role)  # type: ignore[arg-type]


def ensure_confirm_permission(actor: Actor) -> None:
    """确认动作的角色门槛：viewer 可查不可确认。"""

    if actor.rank < ROLE_RANK[CONFIRM_MIN_ROLE]:
        raise PermissionDeniedError(
            f"角色 {actor.role} 无权确认写操作（需要 {CONFIRM_MIN_ROLE}）",
            details={
                "code": "ROLE_NOT_ALLOWED",
                "role": actor.role,
                "required": CONFIRM_MIN_ROLE,
            },
        )
