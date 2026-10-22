# tests/test_recurrence_detector.py
#
# Detecting salary/rent/SIPs and projecting them onto future dates.

from datetime import date

from app.services.forecasting.recurrence_detector import (
    Event, RecurringItem, detect_recurring, is_active, is_occurrence_of, project_occurrences,
)


def _monthly(payee, amounts, day=5, direction="debit", year=2026, first_month=5):
    return [Event(payee, date(year, first_month + i, day), amount, direction) for i, amount in enumerate(amounts)]


def test_rent_is_detected_as_recurring():
    items = detect_recurring(_monthly("landlord@upi", [18000, 18000, 18000, 18000]))
    assert len(items) == 1
    assert items[0].typical_amount == 18000 and items[0].typical_day == 5


def test_two_occurrences_are_not_enough():
    assert detect_recurring(_monthly("netflix", [649, 649])) == []


def test_a_changing_bill_is_not_recurring():
    assert detect_recurring(_monthly("bescom", [1200, 2600, 900, 3100])) == []


def test_a_changing_bill_is_not_recurring_even_on_recent_occurrences():
    assert detect_recurring(_monthly("bescom", [1200, 2600, 900, 3100]), recent_occurrences=3) == []


def test_irregular_timing_is_not_recurring():
    events = [Event("shop", date(2026, 5, 1), 500, "debit"), Event("shop", date(2026, 5, 9), 500, "debit"),
              Event("shop", date(2026, 5, 20), 500, "debit")]
    assert detect_recurring(events) == []


def test_salary_is_recurring_credit():
    items = detect_recurring(_monthly("acme payroll", [60000] * 4, day=1, direction="credit"))
    assert items[0].direction == "credit"


def test_salary_hike_uses_the_new_level_only_with_recent_occurrences():
    # 4 months at 60,000 then a 20% hike: on ALL payments the median is 60,000 and 72,000 fails the 15% test
    salary = _monthly("acme payroll", [60000] * 4 + [72000] * 3, day=1, direction="credit", first_month=2)
    assert detect_recurring(salary) == []
    items = detect_recurring(salary, recent_occurrences=3)
    assert items[0].typical_amount == 72000 and items[0].occurrences == 3   # the NEW level's payments


def test_a_one_off_payment_to_the_same_payee_does_not_hide_the_rent():
    rent = _monthly("landlord@upi", [18000] * 5, first_month=5)
    deposit = Event("landlord@upi", date(2026, 8, 25), 50000, "debit")       # lands among the last 3
    assert detect_recurring(rent + [deposit]) == []                           # original rule: vetoed
    items = detect_recurring(rent + [deposit], recent_occurrences=3)
    assert items[0].typical_amount == 18000 and items[0].occurrences == 5


def test_a_swinging_bill_is_not_made_regular_by_picking_its_similar_months():
    # 5,000 / 6,000 / 5,500 are within 15% of each other, but the latest months swing
    groceries = _monthly("kirana", [5000, 6000, 5500, 6500, 2000], first_month=5)
    assert detect_recurring(groceries, recent_occurrences=3) == []


def test_irregular_timing_still_fails_with_recent_occurrences():
    events = [Event("shop", date(2026, 5, d), 500, "debit") for d in (1, 9, 20, 28)]
    assert detect_recurring(events, recent_occurrences=3) == []


def test_salary_hike_on_all_occurrences_gives_a_level_never_paid():
    # 3 + 3 months: the median is 66,000 and both levels pass the 15% test -> a made-up typical amount
    salary = _monthly("acme payroll", [60000] * 3 + [72000] * 3, day=1, direction="credit", first_month=3)
    assert detect_recurring(salary)[0].typical_amount == 66000
    assert detect_recurring(salary, recent_occurrences=3)[0].typical_amount == 72000


def test_staleness_is_measured_from_the_latest_data():
    item = RecurringItem("ott", "debit", 199, 10, 5, last_seen=date(2026, 6, 10))
    assert is_active(item, reference=date(2026, 7, 20), stale_days=45)
    assert not is_active(item, reference=date(2026, 9, 30), stale_days=45)     # cancelled after June


def test_only_regular_amounts_are_occurrences():
    rent = RecurringItem("landlord@upi", "debit", 18000, 5, 4, last_seen=date(2026, 8, 5))
    assert is_occurrence_of(rent, 18500, 0.15)
    assert not is_occurrence_of(rent, 50000, 0.15)        # a one-off deposit stays variable


def test_projection_lists_future_dates_only():
    item = RecurringItem("rent", "debit", 18000, 5, 4, last_seen=date(2026, 8, 5))
    assert list(project_occurrences(item, date(2026, 8, 20), date(2026, 11, 30))) == [
        date(2026, 9, 5), date(2026, 10, 5), date(2026, 11, 5)]


def test_projection_moves_day_31_to_month_end():
    item = RecurringItem("emi", "debit", 5000, 31, 4, last_seen=date(2026, 8, 31))
    assert list(project_occurrences(item, date(2026, 9, 1), date(2026, 11, 30))) == [
        date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30)]


def test_projection_crosses_the_year_boundary():
    item = RecurringItem("sip", "debit", 3000, 10, 5, last_seen=date(2026, 11, 10))
    assert list(project_occurrences(item, date(2026, 12, 1), date(2027, 1, 31))) == [
        date(2026, 12, 10), date(2027, 1, 10)]


def test_an_early_payment_is_not_projected_again():
    salary = RecurringItem("acme payroll", "credit", 60000, 1, 6, last_seen=date(2026, 9, 30))   # paid early
    assert list(project_occurrences(salary, date(2026, 10, 1), date(2026, 11, 30))) == [date(2026, 11, 1)]
