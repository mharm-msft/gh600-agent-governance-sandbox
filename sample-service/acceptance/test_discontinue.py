"""Acceptance criteria for TASK-A (see labs/01-architecture-and-sdlc/tasks/task-a-discontinue.md).

These tests are intentionally NOT part of the default test run. They are the agent's
definition of done: a human wrote them first, the agent must make them pass.

Run from the sample-service folder:  python -m pytest acceptance/test_discontinue.py
"""

import pytest

from inventory import InventoryError, InventoryStore, Item


@pytest.fixture
def store() -> InventoryStore:
    store = InventoryStore()
    store.add(Item(sku="A-1", name="Anchor", quantity=2, reorder_level=5))
    store.add(Item(sku="B-2", name="Bolt", quantity=2, reorder_level=5))
    return store


def test_discontinue_marks_the_item(store):
    assert store.discontinue("A-1").discontinued is True
    assert store.get("A-1").discontinued is True


def test_new_items_are_not_discontinued():
    assert Item(sku="C-3", name="Clamp").discontinued is False


def test_discontinued_items_cannot_be_sold_or_restocked(store):
    store.discontinue("A-1")
    with pytest.raises(InventoryError, match="discontinued"):
        store.sell("A-1", 1)
    with pytest.raises(InventoryError, match="discontinued"):
        store.restock("A-1", 1)


def test_discontinued_items_are_excluded_from_low_stock(store):
    store.discontinue("A-1")
    assert [item.sku for item in store.low_stock()] == ["B-2"]


def test_discontinue_unknown_sku_is_a_business_error(store):
    with pytest.raises(InventoryError, match="unknown sku"):
        store.discontinue("Z-9")
