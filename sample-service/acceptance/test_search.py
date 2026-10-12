"""Acceptance criteria for TASK-B (see labs/05-multi-agent/tasks/task-b-search.md).

Not part of the default test run. Run from the sample-service folder:
    python -m pytest acceptance/test_search.py
"""

import pytest

from inventory import InventoryStore, Item


@pytest.fixture
def store() -> InventoryStore:
    store = InventoryStore()
    store.add(Item(sku="B-2", name="Brass bolt"))
    store.add(Item(sku="A-1", name="Anchor bolt"))
    store.add(Item(sku="C-3", name="Clamp"))
    return store


def test_search_is_case_insensitive_and_sorted_by_sku(store):
    assert [item.sku for item in store.search("BOLT")] == ["A-1", "B-2"]


def test_search_with_no_match_returns_an_empty_list(store):
    assert store.search("washer") == []


def test_blank_search_text_is_rejected(store):
    with pytest.raises(ValueError):
        store.search("   ")
