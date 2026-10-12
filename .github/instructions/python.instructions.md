---
applyTo: "sample-service/**/*.py"
---

- Use type hints and `from __future__ import annotations`.
- Prefer small pure functions. Raise `InventoryError` for business-rule violations and `ValueError` for invalid values, as the existing code does.
- Keep `Item` immutable. Change it with `dataclasses.replace`.
- Every new behavior needs a regular test in `sample-service/tests/`, in addition to any acceptance test.
