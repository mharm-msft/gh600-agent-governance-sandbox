from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Item:
    """One stocked product. Instances are immutable; use dataclasses.replace to change one."""

    sku: str
    name: str
    quantity: int = 0
    reorder_level: int = 5

    def __post_init__(self) -> None:
        if not self.sku.strip():
            raise ValueError("sku must not be blank")
        if not self.name.strip():
            raise ValueError("name must not be blank")
        if self.quantity < 0:
            raise ValueError("quantity must not be negative")
        if self.reorder_level < 0:
            raise ValueError("reorder_level must not be negative")

    @property
    def needs_reorder(self) -> bool:
        return self.quantity <= self.reorder_level
