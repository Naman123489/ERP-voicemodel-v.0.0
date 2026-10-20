
import pytest

from app.services.inventory_service import (
    calculate_required_components,
    validate_inventory,
)


def test_calculate_components_for_one_bicycle():
    required = calculate_required_components(1)

    assert required["frame"] == 1
    assert required["wheel"] == 2
    assert required["pedal"] == 2
    assert required["brake set"] == 1


def test_calculate_components_for_multiple_bicycles():
    required = calculate_required_components(3)

    assert required["frame"] == 3
    assert required["wheel"] == 6
    assert required["pedal"] == 6


def test_zero_quantity_is_rejected():
    with pytest.raises(ValueError):
        calculate_required_components(0)


def test_insufficient_stock_is_rejected():
    stock = {
        "frame": 10,
        "wheel": 5,
        "handlebar": 10,
        "seat": 10,
        "chain": 10,
        "pedal": 10,
        "brake set": 10,
    }

    with pytest.raises(ValueError, match="Insufficient inventory"):
        validate_inventory(stock, 3)


def test_valid_stock_is_accepted():
    stock = {
        "frame": 10,
        "wheel": 30,
        "handlebar": 10,
        "seat": 10,
        "chain": 10,
        "pedal": 30,
        "brake set": 10,
    }

    required = validate_inventory(stock, 3)

    assert required["wheel"] == 6
    assert required["frame"] == 3
    assert stock["wheel"] == 30
