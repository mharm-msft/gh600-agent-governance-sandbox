import pytest

from inventory import Item


def test_item_defaults():
    item = Item(sku="A-1", name="Widget")
    assert item.quantity == 0
    assert item.reorder_level == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sku": " ", "name": "Widget"},
        {"sku": "A-1", "name": ""},
        {"sku": "A-1", "name": "Widget", "quantity": -1},
        {"sku": "A-1", "name": "Widget", "reorder_level": -1},
    ],
)
def test_item_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        Item(**kwargs)


def test_needs_reorder_is_inclusive_of_the_reorder_level():
    assert Item(sku="A-1", name="Widget", quantity=5, reorder_level=5).needs_reorder
    assert not Item(sku="A-1", name="Widget", quantity=6, reorder_level=5).needs_reorder
