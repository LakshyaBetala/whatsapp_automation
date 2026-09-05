"""Tally XML parsing/query-building tests.

Focus: the debtor discovery walk, which decides whether a shop sees ANY of its
customers at all.
"""
from tally_agent import tally_xml




# ── Rename-proof debtor discovery (the MANJUSHA bug) ────────────────────────
# A shop whose Tally has the reserved "Sundry Debtors" group RENAMED (Tally
# allows it) reported ZERO customers on every sync while still reporting
# success, so the owner's dashboard said "All clear" on a dead install.

_GROUPS_RENAMED = """<ENVELOPE><BODY><DATA><COLLECTION>
  <GROUP NAME="Party A/c"><PARENT></PARENT><RESERVEDNAME>Sundry Debtors</RESERVEDNAME></GROUP>
  <GROUP NAME="Indore"><PARENT>Party A/c</PARENT><RESERVEDNAME></RESERVEDNAME></GROUP>
  <GROUP NAME="Gwalior"><PARENT>Indore</PARENT><RESERVEDNAME></RESERVEDNAME></GROUP>
  <GROUP NAME="Sundry Creditors"><PARENT></PARENT><RESERVEDNAME>Sundry Creditors</RESERVEDNAME></GROUP>
</COLLECTION></DATA></BODY></ENVELOPE>"""


def test_groups_query_asks_for_reserved_name():
    # Without RESERVEDNAME a renamed group is invisible - this is the whole fix.
    assert "ReservedName" in tally_xml.build_groups_query("ACME")


def test_parse_group_reserved_reads_reserved_names():
    res = tally_xml.parse_group_reserved(_GROUPS_RENAMED)
    assert res["Party A/c"] == "Sundry Debtors"
    assert res["Indore"] == ""


def test_debtor_root_found_when_group_is_renamed():
    parent = tally_xml.parse_groups(_GROUPS_RENAMED)
    reserved = tally_xml.parse_group_reserved(_GROUPS_RENAMED)
    assert tally_xml.debtor_root_names(parent, reserved) == {"Party A/c"}


def test_renamed_root_still_collects_every_subgroup():
    parent = tally_xml.parse_groups(_GROUPS_RENAMED)
    reserved = tally_xml.parse_group_reserved(_GROUPS_RENAMED)
    groups = tally_xml.debtor_group_names(parent, reserved)
    assert {"Party A/c", "Indore", "Gwalior"} <= groups
    assert "Sundry Creditors" not in groups     # creditors are never debtors


def test_without_reserved_map_behaviour_is_unchanged():
    # Older callers pass no reserved map: fall back to the literal name.
    parent = {"Sundry Debtors": "", "Route 1": "Sundry Debtors", "Cash": ""}
    groups = tally_xml.debtor_group_names(parent)
    assert {"Sundry Debtors", "Route 1"} <= groups
    assert "Cash" not in groups


def test_renamed_group_would_have_returned_nothing_before():
    # Regression guard: the OLD literal-name logic finds no real group here.
    parent = tally_xml.parse_groups(_GROUPS_RENAMED)
    assert tally_xml.debtor_group_names(parent) & {"Party A/c", "Indore"} == set()


def test_top_level_groups_reports_the_real_chart_of_accounts():
    parent = tally_xml.parse_groups(_GROUPS_RENAMED)
    assert tally_xml.top_level_groups(parent) == ["Party A/c", "Sundry Creditors"]
