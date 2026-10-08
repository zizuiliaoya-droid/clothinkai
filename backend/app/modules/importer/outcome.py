"""导入行结果与「同批以第一个给值的行为准」（8a-6，设计 §4.2、§4.2.1，J3、J58）。

- ``RowKind``：一行的类别。一个对象的处理结果（新建 / 补空 / 覆盖 / 相同 / 冲突）汇总成一行，
  按优先级取一个：失败 > 冲突 > 新增 > 已覆盖 > 补空 > 重复已跳过（失败由异常表示，见 ``merge_kinds``）
- ``ImportRowContext``：runner 传给 ``upsert_with_context`` 的上下文；``batch_seen`` 必填，
  同一次批次执行的所有行共用一个，免得漏传后静默退化成逐行
- ``BatchSeen`` / ``screen_batch_seen``：同一次批次执行里，同一对象的同一比较字段以第一个给了值、
  且已提交的行为准；后面的行给了不同的值 → 按「文件没给值」处理并提示。``first_notice`` 给
  整批只提示一次的提示用（同样等行提交才生效）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue

from app.modules.importer.compare import FieldSpec, normalize


class RowKind(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    FILLED = "filled"
    SKIPPED = "skipped"
    CONFLICT = "conflict"


# 一行的类别优先级（数字越小越优先）；失败由异常表示，不在这里
_KIND_PRIORITY: dict[RowKind, int] = {
    RowKind.CONFLICT: 0,
    RowKind.INSERTED: 1,
    RowKind.UPDATED: 2,
    RowKind.FILLED: 3,
    RowKind.SKIPPED: 4,
}


def merge_kinds(kinds: Iterable[RowKind]) -> RowKind:
    """按「冲突 > 新增 > 已覆盖 > 补空 > 重复已跳过」取一行的类别；没有任何对象 → 重复已跳过。"""
    return min(kinds, key=_KIND_PRIORITY.__getitem__, default=RowKind.SKIPPED)


@dataclass(frozen=True)
class ImportRowContext:
    tenant_id: UUID
    source: str
    batch_id: UUID | None
    row_number: int
    actor_id: UUID | None
    batch_seen: BatchSeen  # 同一次批次执行的所有行共用一个（§4.2.1）；必填，不给默认值


@dataclass(frozen=True)
class FilledRecord:
    """一个被补空的对象（只有字段名、没有值）。"""

    object_type: str
    object_label: str
    fields: list[str]

    def to_json(self) -> dict[str, JsonValue]:
        names: list[JsonValue] = []
        names.extend(self.fields)
        return {"object_type": self.object_type, "object_label": self.object_label, "fields": names}


@dataclass
class RowOutcome:
    resource_id: UUID | None
    kind: RowKind
    warnings: list[str] = field(default_factory=list)
    filled: list[FilledRecord] = field(
        default_factory=list
    )  # (object_type, object_label, [字段名])


SeenKey = tuple[str, UUID, str]  # (对象类型, 对象 id, 字段名)


@dataclass
class BatchSeen:
    """一次批次执行里，每个（对象, 字段）第一个给了值、且已提交的行；以及整批只提示一次的提示。"""

    committed: dict[SeenKey, tuple[int, JsonValue]] = field(default_factory=dict)
    # 本行登记的，行事务提交后 commit_row() 才生效
    staged: dict[SeenKey, tuple[int, JsonValue]] = field(default_factory=dict)
    # 整批只提示一次的提示（如「图片」列是内嵌图片）：同样等本行提交才算提示过
    notified: set[str] = field(default_factory=set)
    staged_notices: set[str] = field(default_factory=set)

    def first_notice(self, key: str) -> bool:
        """这次执行里还没有已提交的行（也不是本行）给过这条提示 → True，并暂存登记。

        登记这条提示的行失败了，后面第一个提交的行照样带上它（提示不会丢）。
        """
        if key in self.notified or key in self.staged_notices:
            return False
        self.staged_notices.add(key)
        return True

    def first_differing_row(self, key: SeenKey, row_number: int, value: JsonValue) -> int | None:
        """value 是 normalize 之后的非空值。

        返回 None：照常处理（没登记过的先暂存登记）；返回 M：第 M 行登记了不同的值。
        """
        seen = self.committed.get(key) or self.staged.get(key)
        if seen is None:
            self.staged[key] = (row_number, value)
            return None
        first_row, first_value = seen
        return None if first_value == value else first_row

    def commit_row(self) -> None:
        self.committed.update(self.staged)
        self.staged.clear()
        self.notified |= self.staged_notices
        self.staged_notices.clear()

    def discard_row(self) -> None:
        self.staged.clear()
        self.staged_notices.clear()


def screen_batch_seen(
    ctx: ImportRowContext,
    object_type: str,
    object_id: UUID,
    specs: Iterable[FieldSpec],
    incoming: Mapping[str, object],
) -> tuple[dict[str, object], list[str]]:
    """比较 / 补空 / 覆盖之前调；新建对象写入后也调一次（只为登记）。

    返回去掉了不一致字段的 incoming 与提示。
    """
    kept = dict(incoming)
    warnings: list[str] = []
    for spec in specs:
        if spec.create_only or spec.name not in incoming:
            continue
        value = normalize(spec.kind, incoming[spec.name])
        if value is None:  # 文件没给值：不登记
            continue
        first = ctx.batch_seen.first_differing_row(
            (object_type, object_id, spec.name), ctx.row_number, value
        )
        if first is not None:
            # 按「文件没给值」处理：diff_fields 看不到它，不比较、不补空、不进 compared
            del kept[spec.name]
            warnings.append(
                f"第 {ctx.row_number} 行的{spec.label}与第 {first} 行不一致，按第 {first} 行处理"
            )
    return kept, warnings


__all__ = [
    "BatchSeen",
    "FilledRecord",
    "ImportRowContext",
    "RowKind",
    "RowOutcome",
    "SeenKey",
    "merge_kinds",
    "screen_batch_seen",
]
