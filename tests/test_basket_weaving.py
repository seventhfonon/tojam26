"""Basket weaving Community tuning helpers."""

from __future__ import annotations

from app import constants as c


def test_hours_zero_means_no_effects():
    assert c.basket_weaving_loyalty_per_second(0) == 0.0
    assert c.basket_weaving_cash_per_second(0, 100) == 0.0


def test_loyalty_decreases_as_hours_increase():
    assert c.basket_weaving_loyalty_per_second(1) > c.basket_weaving_loyalty_per_second(4)


def test_cash_increases_with_hours_fixed_population():
    pop = 40
    assert c.basket_weaving_cash_per_second(1, pop) < c.basket_weaving_cash_per_second(
        4, pop
    )


def test_cash_scales_linearly_with_population():
    assert abs(
        c.basket_weaving_cash_per_second(3, 20)
        - 2 * c.basket_weaving_cash_per_second(3, 10)
    ) < 1e-9


def test_hours_clamped():
    assert c.basket_weaving_hours_clamped(-99) == 0
    assert c.basket_weaving_hours_clamped(99) == c.BASKET_WEAVING_HOURS_MAX
