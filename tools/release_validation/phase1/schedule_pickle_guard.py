"""Bounded static recognizer for a small protocol-4 Celery schedule contract.

No Unpickler, imports from pickle globals, callable invocation, or object loading.
Unknown encodings/shapes are rejected, including valid pickles outside this subset.
"""

from dataclasses import dataclass
from datetime import date
import pickletools
import re

from runtime_guard import GuardError, need

MAX_OPS = 10000
MAX_DEPTH = 32
GLOBALS = {
    ("datetime", "datetime"),
    ("datetime", "timedelta"),
    ("datetime", "timezone"),
    ("celery.beat", "ScheduleEntry"),
    ("celery.schedules", "schedule"),
    ("celery.schedules", "crontab"),
}
# These are inert tokens for one observed UTC encoding, not constructors.
ZONEINFO_TOKENS = {("builtins", "getattr"), ("zoneinfo", "ZoneInfo")}
MARK = object()


@dataclass(eq=False)
class Node:
    kind: str
    value: object
    state: object = None


def parse(raw):
    """Build inert typed nodes; opcodes never invoke encoded callables."""
    need(type(raw) is bytes and 0 < len(raw) <= 1024**2, "schedule_pickle_size")
    ops = []
    for op, arg, pos in pickletools.genops(raw):
        need(len(ops) < MAX_OPS, "schedule_pickle_budget")
        ops.append((op.name, arg, pos))
    need(ops and ops[0] == ("PROTO", 4, 0), "schedule_pickle_protocol")
    need(ops[-1] == ("STOP", None, len(raw) - 1), "schedule_pickle_stop")
    stack, memo = [], []
    frame_end = None

    def take(count):
        need(len(stack) >= count, "schedule_pickle_stack")
        values = stack[-count:] if count else []
        need(all(isinstance(item, Node) for item in values), "schedule_pickle_mark")
        if count:
            del stack[-count:]
        return values

    def marked():
        need(MARK in stack, "schedule_pickle_mark")
        index = len(stack) - 1 - stack[::-1].index(MARK)
        values = stack[index + 1 :]
        del stack[index:]
        return values

    def setitems(items):
        need(
            stack and isinstance(stack[-1], Node) and stack[-1].kind == "dict",
            "schedule_pickle_dict",
        )
        need(len(items) % 2 == 0, "schedule_pickle_pairs")
        target = stack[-1].value
        for key, value in zip(items[::2], items[1::2]):
            need(key.kind == "str" and key.value not in target, "schedule_pickle_key")
            target[key.value] = value

    for index, (name, arg, pos) in enumerate(ops):
        end = ops[index + 1][2] if index + 1 < len(ops) else len(raw)
        if index == 0:
            continue
        if name == "FRAME":
            need(frame_end is None or pos == frame_end, "schedule_pickle_nested_frame")
            need(type(arg) is int and 0 < arg <= len(raw) - end, "schedule_pickle_frame")
            frame_end = end + arg
            continue
        if frame_end is not None:
            need(pos < frame_end and end <= frame_end, "schedule_pickle_frame_boundary")
        if name == "STOP":
            need(len(stack) == 1 and isinstance(stack[0], Node), "schedule_pickle_stack")
            need(frame_end is None or frame_end == len(raw), "schedule_pickle_frame_end")
            return stack[0]
        if name == "MARK":
            stack.append(MARK)
        elif name == "MEMOIZE":
            need(stack and isinstance(stack[-1], Node), "schedule_pickle_memo_stack")
            memo.append(stack[-1])
        elif name in {"BINGET", "LONG_BINGET"}:
            need(type(arg) is int and 0 <= arg < len(memo), "schedule_pickle_memo")
            stack.append(memo[arg])
        elif name in {"NONE", "NEWTRUE", "NEWFALSE"}:
            stack.append(Node("atom", {"NONE": None, "NEWTRUE": True, "NEWFALSE": False}[name]))
        elif name in {"BININT", "BININT1", "BININT2", "LONG1", "LONG4"}:
            need(type(arg) is int and -(2**63) <= arg < 2**63, "schedule_pickle_integer")
            stack.append(Node("int", arg))
        elif name in {"SHORT_BINUNICODE", "BINUNICODE", "BINUNICODE8"}:
            need(type(arg) is str and len(arg) <= 1024, "schedule_pickle_string")
            stack.append(Node("str", arg))
        elif name in {"SHORT_BINBYTES", "BINBYTES", "BINBYTES8"}:
            need(type(arg) is bytes and len(arg) <= 1024, "schedule_pickle_bytes")
            stack.append(Node("bytes", arg))
        elif name in {"EMPTY_DICT", "EMPTY_TUPLE"}:
            stack.append(
                Node(
                    "dict" if name == "EMPTY_DICT" else "tuple", {} if name == "EMPTY_DICT" else ()
                )
            )
        elif name in {"TUPLE1", "TUPLE2", "TUPLE3", "TUPLE"}:
            values = marked() if name == "TUPLE" else take(int(name[-1]))
            stack.append(Node("tuple", tuple(values)))
        elif name == "SETITEM":
            setitems(take(2))
        elif name == "SETITEMS":
            setitems(marked())
        elif name == "STACK_GLOBAL":
            module, symbol = take(2)
            need(module.kind == symbol.kind == "str", "schedule_pickle_global_args")
            need(
                (module.value, symbol.value) in GLOBALS | ZONEINFO_TOKENS,
                "schedule_pickle_global",
            )
            stack.append(Node("global", (module.value, symbol.value)))
        elif name == "REDUCE":
            function, args = take(2)
            need(args.kind == "tuple", "schedule_pickle_reduce")
            if function.kind == "global" and function.value == ("builtins", "getattr"):
                values = args.value
                need(
                    len(values) == 2
                    and values[0].kind == "global"
                    and values[0].value == ("zoneinfo", "ZoneInfo")
                    and values[1].kind == "str"
                    and values[1].value == "_unpickle",
                    "schedule_pickle_zoneinfo_attribute",
                )
                stack.append(Node("zoneinfo_unpickle_token", None))
            elif function.kind == "zoneinfo_unpickle_token":
                values = args.value
                need(
                    len(values) == 2
                    and values[0].kind == "str"
                    and values[0].value == "UTC"
                    and values[1].kind == "int"
                    and values[1].value == 1,
                    "schedule_pickle_zoneinfo_utc",
                )
                stack.append(Node("utc_zoneinfo", None))
            else:
                need(
                    function.kind == "global" and function.value in GLOBALS,
                    "schedule_pickle_reduce",
                )
                stack.append(Node("object", (function.value, args)))
        elif name == "BUILD":
            state = take(1)[0]
            need(stack and isinstance(stack[-1], Node), "schedule_pickle_build_stack")
            target = stack[-1]
            need(
                target.kind == "object"
                and target.value[0] == ("celery.schedules", "crontab")
                and target.state is None
                and state.kind == "dict"
                and not state.value,
                "schedule_pickle_build",
            )
            target.state = state
        else:
            raise GuardError("schedule_pickle_opcode")
    raise GuardError("schedule_pickle_stop")


def as_kind(node, kind):
    need(node.kind == kind, "schedule_shape_" + kind)
    return node.value


def is_atom(node, value):
    return node.kind == "atom" and node.value is value


def object_args(node, symbol, count):
    need(node.kind == "object" and node.value[0] == symbol, "schedule_shape_object")
    args = as_kind(node.value[1], "tuple")
    need(len(args) == count, "schedule_shape_arity")
    return args


def timedelta_args(node):
    args = object_args(node, ("datetime", "timedelta"), 3)
    days, seconds, micros = [as_kind(value, "int") for value in args]
    need(
        0 <= days <= 999999999 and 0 <= seconds < 86400 and 0 <= micros < 1000000,
        "schedule_shape_timedelta",
    )
    return days, seconds, micros


def utc_datetime(node):
    stamp, zone = object_args(node, ("datetime", "datetime"), 2)
    data = as_kind(stamp, "bytes")
    need(len(data) == 10, "schedule_shape_datetime")
    # Fixed datetime calendar validation, never an encoded class constructor.
    year, month, day = int.from_bytes(data[:2], "big"), data[2], data[3]
    need(1 <= month <= 12, "schedule_shape_datetime")  # fold bit is outside supported UTC shape
    date(year, month, day)
    need(
        data[4] < 24
        and data[5] < 60
        and data[6] < 60
        and int.from_bytes(data[7:], "big") < 1000000,
        "schedule_shape_datetime",
    )
    if zone.kind == "utc_zoneinfo":
        need(zone.value is None and zone.state is None, "schedule_shape_utc")
        return
    offset = object_args(zone, ("datetime", "timezone"), 1)[0]
    need(timedelta_args(offset) == (0, 0, 0), "schedule_shape_utc")


def schedule_shape(node):
    need(node.kind == "object", "schedule_shape_schedule")
    symbol = node.value[0]
    if symbol == ("celery.schedules", "schedule"):
        interval, relative, nowfun = object_args(node, symbol, 3)
        need(any(timedelta_args(interval)), "schedule_shape_positive_interval")
        need(is_atom(relative, False) and is_atom(nowfun, None), "schedule_shape_interval_options")
        return
    args = object_args(node, ("celery.schedules", "crontab"), 5)
    need(node.state is None or not as_kind(node.state, "dict"), "schedule_shape_cron_state")
    for value, (low, high) in zip(args, ((0, 59), (0, 23), (0, 6), (1, 31), (1, 12))):
        if value.kind == "int":
            need(low <= value.value <= high, "schedule_shape_cron_range")
        else:
            text = as_kind(value, "str")
            if text == "*":
                continue
            if re.fullmatch(r"\*/[1-9][0-9]*", text):
                need(int(text[2:]) <= high + 1 - low, "schedule_shape_cron_step")
            else:
                need(
                    re.fullmatch(r"[0-9]{1,2}", text) is not None and low <= int(text) <= high,
                    "schedule_shape_cron_range",
                )


def check_graph(root):
    """Reject cycles/expansion bombs, including through memoized mutable containers."""
    active, visited = set(), 0

    def walk(node, depth):
        nonlocal visited
        visited += 1
        need(visited <= MAX_OPS and depth <= MAX_DEPTH, "schedule_shape_budget")
        need(id(node) not in active, "schedule_shape_cycle")
        active.add(id(node))
        if node.kind == "tuple":
            children = node.value
        elif node.kind == "dict":
            children = node.value.values()
        elif node.kind == "object":
            children = [node.value[1]] + ([node.state] if node.state is not None else [])
        else:
            children = ()
        for child in children:
            walk(child, depth + 1)
        active.remove(id(node))

    walk(root, 0)


def validate_value(key, raw, expected):
    """Require exact UTC metadata and seven expected inert ScheduleEntry descriptions."""
    root = parse(raw)
    check_graph(root)
    if key == b"__version__":
        need(
            re.fullmatch(r"5\.[0-9]+\.[0-9]+", as_kind(root, "str")) is not None,
            "schedule_shape_version",
        )
    elif key == b"tz":
        need(as_kind(root, "str") == "UTC", "schedule_shape_timezone")
    elif key == b"utc_enabled":
        need(is_atom(root, True), "schedule_shape_utc_enabled")
    else:
        need(key == b"entries", "schedule_metadata")
        entries = as_kind(root, "dict")
        need(set(entries) == set(expected) and len(entries) == 7, "schedule_shape_entries")
        for name, entry in entries.items():
            ename, task, last, count, sched, args, kwargs, options = object_args(
                entry, ("celery.beat", "ScheduleEntry"), 8
            )
            need(
                as_kind(ename, "str") == name and as_kind(task, "str") == expected[name],
                "schedule_shape_entry_identity",
            )
            utc_datetime(last)
            need(as_kind(count, "int") >= 0, "schedule_shape_run_count")
            schedule_shape(sched)
            need(
                not as_kind(args, "tuple") and not as_kind(kwargs, "dict"),
                "schedule_shape_task_arguments",
            )
            opts = as_kind(options, "dict")
            need(
                set(opts) == {"queue"}
                and as_kind(opts["queue"], "str") in {"default", "backup", "crawler", "report"},
                "schedule_shape_queue",
            )
