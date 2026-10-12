import pytest

from inventory import InventoryError, InventoryStore, Item


@pytest.fixture
def store() -> InventoryStore:
    store = InventoryStore()
    store.add(Item(sku="B-2", name="Bolt", quantity=3, reorder_level=5))
    store.add(Item(sku="A-1", name="Anchor", quantity=10, reorder_level=5))
    return store


def test_add_rejects_duplicate_sku(store):
    with pytest.raises(InventoryError, match="already exists"):
        store.add(Item(sku="A-1", name="Another anchor"))


def test_get_unknown_sku_is_a_business_error(store):
    with pytest.raises(InventoryError, match="unknown sku"):
        store.get("Z-9")


def test_restock_increases_quantity(store):
    assert store.restock("A-1", 5).quantity == 15


def test_sell_decreases_quantity(store):
    assert store.sell("A-1", 4).quantity == 6


def test_sell_more_than_available_is_rejected_and_changes_nothing(store):
    with pytest.raises(InventoryError, match="insufficient stock"):
        store.sell("B-2", 4)
    assert store.get("B-2").quantity == 3


@pytest.mark.parametrize("quantity", [0, -1])
def test_non_positive_quantities_are_rejected(store, quantity):
    with pytest.raises(InventoryError, match="greater than zero"):
        store.restock("A-1", quantity)
    with pytest.raises(InventoryError, match="greater than zero"):
        store.sell("A-1", quantity)


def test_low_stock_is_sorted_by_sku(store):
    store.add(Item(sku="A-0", name="Awl", quantity=0, reorder_level=1))
    assert [item.sku for item in store.low_stock()] == ["A-0", "B-2"]
