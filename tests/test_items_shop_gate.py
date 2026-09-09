"""The browse lists' shop-row hiding is ONE documented contract.

`ui/pages/items/support.is_hidden_shop_item` is the only place the Items
browse lists decide whether a shop row is kept out:

    hidden(item)  ==  is_shop_item(id) and not (is_gear(item) and has_drops(id))

Reading it as "is a shop item" once dropped the Guild Merchant's ordinary
starter/town weapons from the Weapons tab when the shop source widened to the
drops index's shop-ONLY set. These tests hold the contract against exactly
that class of change: the gate has one implementation, and sourced equipment
is never hidden however far the shop predicate widens.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS_DIR = ROOT / "farever_companion" / "ui" / "pages" / "items"
CONTRACT_MODULE = ITEMS_DIR / "support.py"


def _is_shop_item_calls(tree: ast.AST) -> list[int]:
    """Line numbers of every `*.is_shop_item(...)` call in a parsed module."""
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) \
                and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "is_shop_item":
            out.append(node.lineno)
    return out


def test_the_shop_gate_has_one_implementation_under_the_items_pages():
    """Every `is_shop_item` call in the Items browse modules lives inside the
    contract function. A second call site is a second hiding path — the way
    the equipment regression got in — and this fails before it can ship."""
    found: dict[str, list[int]] = {}
    for path in sorted(ITEMS_DIR.glob("*.py")):
        calls = _is_shop_item_calls(ast.parse(path.read_text(encoding="utf-8")))
        if calls:
            found[path.name] = sorted(calls)
    assert set(found) == {CONTRACT_MODULE.name}, found

    tree = ast.parse(CONTRACT_MODULE.read_text(encoding="utf-8"))
    contract = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "is_hidden_shop_item")
    inside = sorted(_is_shop_item_calls(contract))
    assert inside == found[CONTRACT_MODULE.name], (inside, found)


def test_sourced_equipment_is_never_hidden_however_the_shop_source_widens(
        monkeypatch):
    """The contract's guarantee. Simulate the worst shop-source change — the
    predicate matching EVERY item — and assert a piece of equipment with a
    real source is never hidden, while non-equipment / unsourced stock still
    is. This makes the regression that dropped nine vendor weapons impossible:
    the equipment guard does not depend on what the shop set contains."""
    from farever_companion.data import items as idata
    from farever_companion.ui.pages.items import support

    monkeypatch.setattr(idata, "is_shop_item", lambda *_a, **_k: True)
    for it in idata.items():
        iid = it["id"]
        sourced_gear = idata.is_gear(it) and idata.has_drops(iid)
        assert support.is_hidden_shop_item(it) is (not sourced_gear), iid
    # the gear population is real, so the assertion above is not vacuous
    assert any(idata.is_gear(it) and idata.has_drops(it["id"])
               for it in idata.items())


def test_live_shop_rows_match_the_contract():
    """With the real shop source, the hidden rows are exactly the contract's
    and no sourced equipment is among them."""
    from farever_companion.data import items as idata
    from farever_companion.ui.pages.items import support

    hidden = {it["id"] for it in idata.items()
              if support.is_hidden_shop_item(it)}
    assert hidden, "no shop row is hidden — the contract is vacuous here"
    for it in idata.items():
        iid = it["id"]
        expected = (idata.is_shop_item(iid)
                    and not (idata.is_gear(it) and idata.has_drops(iid)))
        assert support.is_hidden_shop_item(it) == expected, iid
        if idata.is_gear(it) and idata.has_drops(iid):
            assert iid not in hidden, iid     # sourced equipment always lists
