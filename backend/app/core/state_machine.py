"""通用状态机基类 + 转移表。

按应用设计 Q8=C 决策：领域方法 + 显式转移表。
- 各业务模块在 domain.py 中声明 transition_table（list[TransitionRule]）
- 调用 .transition(action, actor) 检查合法性 + 触发 side_effects（具体副作用由 service 层注入回调）

U03/U04/U05/U10a 等单元在此基础上实现各自的状态机。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.core.exceptions import IllegalStateTransitionError

# ---------------------------------------------------------------------------
# 转移规则
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionRule:
    """状态转移定义。"""

    from_state: str
    action: str
    to_state: str
    actor_roles: tuple[str, ...] = ()  # 允许执行此转移的角色 code（空=不限）
    required_fields: tuple[str, ...] = ()  # 转移前必填字段
    side_effects: tuple[str, ...] = ()  # 副作用名称（service 层注入回调）


# ---------------------------------------------------------------------------
# 状态机基类
# ---------------------------------------------------------------------------

T = TypeVar("T")


@dataclass
class StateMachine(Generic[T]):
    """状态机基类。

    子类需声明：
        transition_table: tuple[TransitionRule, ...]
        state_attr: str = "status"   # 状态字段在被管理对象上的属性名
    """

    target: T
    transition_table: tuple[TransitionRule, ...] = field(default=())
    state_attr: str = "status"

    @property
    def current_state(self) -> str:
        return str(getattr(self.target, self.state_attr))

    def can_transition(self, action: str, *, actor_roles: list[str] | None = None) -> bool:
        rule = self._find_rule(action)
        if rule is None:
            return False
        if rule.actor_roles and actor_roles is not None:
            if not any(r in rule.actor_roles for r in actor_roles):
                return False
        return True

    def transition(
        self,
        action: str,
        *,
        actor_roles: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TransitionRule:
        """执行状态转移。

        返回匹配的 TransitionRule，调用方根据 rule.side_effects 决定后续动作。
        """
        rule = self._find_rule(action)
        if rule is None:
            raise IllegalStateTransitionError(
                f"状态 {self.current_state} 不允许动作 {action}",
                details={"current_state": self.current_state, "action": action},
            )

        # 角色校验
        if rule.actor_roles and actor_roles is not None:
            if not any(r in rule.actor_roles for r in actor_roles):
                raise IllegalStateTransitionError(
                    f"角色 {actor_roles} 不允许执行 {action}",
                    details={"required_roles": list(rule.actor_roles)},
                )

        # 必填字段校验
        if rule.required_fields and payload is not None:
            missing = [f for f in rule.required_fields if not payload.get(f)]
            if missing:
                raise IllegalStateTransitionError(
                    f"动作 {action} 缺失必填字段: {missing}",
                    details={"missing_fields": missing},
                )

        # 推进状态
        setattr(self.target, self.state_attr, rule.to_state)
        return rule

    def get_valid_actions(self, *, actor_roles: list[str] | None = None) -> list[str]:
        return [
            r.action
            for r in self.transition_table
            if r.from_state == self.current_state
            and (
                not r.actor_roles
                or actor_roles is None
                or any(role in r.actor_roles for role in actor_roles)
            )
        ]

    def _find_rule(self, action: str) -> TransitionRule | None:
        for r in self.transition_table:
            if r.from_state == self.current_state and r.action == action:
                return r
        return None


# ---------------------------------------------------------------------------
# 工具：副作用回调
# ---------------------------------------------------------------------------


SideEffectHandler = Callable[[TransitionRule, dict[str, Any]], None]


def run_side_effects(
    rule: TransitionRule,
    handlers: dict[str, SideEffectHandler],
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """根据 rule.side_effects 名称查 handlers 并执行。"""
    ctx = context or {}
    for name in rule.side_effects:
        handler = handlers.get(name)
        if handler is not None:
            handler(rule, ctx)
