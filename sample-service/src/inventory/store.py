from __future__ import annotations

from dataclasses import replace

from .models import Item


class InventoryError(ValueError):
    """Raised when an inventory operation breaks a business rule."""


class InventoryStore:
    """In-memory inventory. Deliberately small so an agent can change it safely."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add(self, item: Item) -> None:
        if item.sku in self._items:
            raise InventoryError(f"sku already exists: {item.sku}")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        try:
            return self._items[sku]
        except KeyError:
            raise InventoryError(f"unknown sku: {sku}") from None

    def restock(self, sku: str, quantity: int) -> Item:
        self._require_positive(quantity)
        item = self.get(sku)
        updated = replace(item, quantity=item.quantity + quantity)
        self._items[sku] = updated
        return updated

    def sell(self, sku: str, quantity: int) -> Item:
        self._require_positive(quantity)
        item = self.get(sku)
        if quantity > item.quantity:
            raise InventoryError(
                f"insufficient stock for {sku}: have {item.quantity}, need {quantity}"
            )
        updated = replace(item, quantity=item.quantity - quantity)
        self._items[sku] = updated
        return updated

    def low_stock(self) -> list[Item]:
        return sorted(
            (item for item in self._items.values() if item.needs_reorder),
            key=lambda item: item.sku,
        )

    @staticmethod
    def _require_positive(quantity: int) -> None:
        if quantity <= 0:
            raise InventoryError("quantity must be greater than zero")
