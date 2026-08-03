"""Guard against the hand-duplicated enums drifting (audit P1).

The Python Enums in app/models.py mirror the Postgres enum types in
migrations/001_initial_schema.sql by hand. If someone edits one and forgets the
other, inserts fail at runtime with an opaque DB error. This test fails loudly at
CI time instead, comparing the VALUES on both sides.
"""
import os
import re

from app.models import BillStatus, Lang, MessageType, Plan, SyncType

_SQL = os.path.join(os.path.dirname(__file__), "migrations", "001_initial_schema.sql")

# Postgres enum type name -> the Python Enum that must match it.
_MAP = {
    "plan_tier": Plan,
    "lang": Lang,
    "bill_status": BillStatus,
    "message_type": MessageType,
    "sync_type": SyncType,
}


def _sql_enums() -> dict[str, set]:
    sql = open(_SQL, encoding="utf-8").read()
    out: dict[str, set] = {}
    for name, body in re.findall(r"create type (\w+) as enum\s*\((.*?)\)", sql, re.S | re.I):
        out[name] = set(re.findall(r"'([^']+)'", body))
    return out


def test_python_enums_match_the_sql_enum_types():
    sql = _sql_enums()
    for sql_name, enum_cls in _MAP.items():
        assert sql_name in sql, f"enum type '{sql_name}' not found in 001 schema"
        py = {e.value for e in enum_cls}
        assert py == sql[sql_name], (
            f"{enum_cls.__name__} has drifted from SQL '{sql_name}': "
            f"in Python only={py - sql[sql_name]}, in SQL only={sql[sql_name] - py}. "
            f"Update BOTH app/models.py and migrations/001_initial_schema.sql.")
