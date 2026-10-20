
from app.db.models import Component


# Fixed recipe for one bicycle.
BICYCLE_RECIPE = {
    "frame": 1,
    "wheel": 2,
    "handlebar": 1,
    "seat": 1,
    "chain": 1,
    "pedal": 2,
    "brake set": 1,
}


def calculate_required_components(quantity: int) -> dict[str, int]:
    """Calculate the components required to build bicycles."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValueError("Quantity must be an integer.")

    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    return {
        component: amount * quantity
        for component, amount in BICYCLE_RECIPE.items()
    }


def validate_inventory(
    available_stock: dict[str, int],
    quantity: int,
) -> dict[str, int]:
    """Validate stock availability without modifying inventory."""
    required = calculate_required_components(quantity)

    insufficient_stock = {
        component: {
            "required": amount,
            "available": available_stock.get(component, 0),
        }
        for component, amount in required.items()
        if available_stock.get(component, 0) < amount
    }

    if insufficient_stock:
        details = ", ".join(
            f"{component}: required {values['required']}, "
            f"available {values['available']}"
            for component, values in insufficient_stock.items()
        )
        raise ValueError(f"Insufficient inventory: {details}")

    return required
