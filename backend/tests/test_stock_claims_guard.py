# tests/test_stock_claims_guard.py
#
# Purpose: claims guard v3 for stock text (Phase 7A-5, spec section 5.9), tested in BOTH
# directions: advice and prediction wording is rejected, and neutral descriptions of the past
# pass. Also checks the Phase 6 guard is unchanged (it must not start flagging stock advice,
# or the Phase 6B evaluation numbers would silently change).
# Note for the paper: these sentences were written by the guard's author (no teammate rules),
# so they show the guard does what it was designed to do, not its recall on independent text.

import pytest

from app.services.investing.claims_guard import (
    find_prohibited_claims, find_prohibited_stock_claims, is_compliant, is_stock_text_compliant,
)

ADVICE = [
    "You should buy this stock now.",
    "Investors should sell before the results.",
    "You should consider holding the shares.",
    "We recommend buying on dips.",
    "I suggest you exit the position.",
    "Our recommendation is to accumulate.",
    "This is a recommendation to buy.",
    "The stock is a strong buy.",
    "Analysts call it a sell.",
    "Buy now while it is cheap.",
    "Sell this stock before the weekend.",
    "Now is a good time to invest.",
    "It may be the right moment to buy.",
    "It is worth buying at this level.",
    "Book profits after the rally.",
    "Add it to your portfolio.",
    "The shares look undervalued.",
    "The stock is overvalued at this price.",
    "With RSI at 75 the stock is overbought.",
    "It looks oversold after the fall.",
    "Traders may go long here.",
]

PREDICTIONS = [
    "The price will rise next week.",
    "The stock will fall after the results.",
    "Shares are going to double.",
    "The stock is set to rally.",
    "It is likely to recover soon.",
    "The price is expected to climb.",
    "The shares should go up from here.",
    "It is about to crash.",
    "Analysts see a price target of ₹3,200.",
    "The target price is ₹3,200.",
    "There is upside potential of 20%.",
    "The stock will outperform the index.",
    "It will beat the market this year.",
    "The stock will definitely gain.",
    "This share will give good returns.",
    "Returns are guaranteed.",                      # Phase 6 promises still caught
]

NEUTRAL = [
    "The price fell 4% over the last month.",
    "The price rose 3% this week.",
    "Compared with a buy-and-hold approach, the strategy traded more often.",
    "A buy and hold investor would have seen an 18% fall from the peak.",
    "A sell-off in banking stocks pulled the index down.",
    "The last close was above both the 50-day and the 200-day averages.",
    "RSI is 72, which is high by the common convention.",
    "Volatility was 24% a year over the last 252 trading days.",
    "On 95% of days the one-day loss was smaller than 2.1%.",
    "Beta against the Nifty 50 was 1.1, so the stock moved more than the index.",
    "The largest fall from a peak was 18% in the last year.",
    "Prices can fall sharply, and past moves do not predict future prices.",
    "Headlines over the last week were mostly negative.",
    "There were not enough headlines for a reliable sentiment score.",
    "Holding periods differ between investors.",
    "Buyers and sellers traded 1.2 million shares.",
    "The company announced a share buyback.",
    "Its target market is small businesses.",
    "Past data cannot tell what the price will be.",
]


@pytest.mark.parametrize("text", ADVICE)
def test_advice_is_rejected(text):
    assert not is_stock_text_compliant(text), find_prohibited_stock_claims(text)


@pytest.mark.parametrize("text", PREDICTIONS)
def test_predictions_are_rejected(text):
    assert not is_stock_text_compliant(text), find_prohibited_stock_claims(text)


@pytest.mark.parametrize("text", NEUTRAL)
def test_neutral_descriptions_pass(text):
    assert is_stock_text_compliant(text), find_prohibited_stock_claims(text)


def test_matches_are_reported_in_order():
    found = find_prohibited_stock_claims("Guaranteed! The price will rise, so buy now.")
    assert found == ["guaranteed", "will rise", "buy now"]


@pytest.mark.parametrize("text", ["You should buy this stock now.", "The price will rise next week.",
                                  "The shares look undervalued."])
def test_the_phase_6_guard_is_unchanged(text):
    """v3 is a separate function: the Phase 6 guard must still ignore stock advice wording."""
    assert is_compliant(text) and find_prohibited_claims(text) == []


# --- v3.1 (engine 7.1, after the Phase 7B evaluation) ---
# Each line exercises one pattern added from a v3 miss in evaluation/stocks_guard_cases.csv.
V31_REJECTED = [
    "Investors must trim their holdings.",                 # A19: wider action list after a modal
    "We would avoid the shares this quarter.",             # A05
    "Selling now seems prudent.",                          # A04
    "This is a good entry level for new investors.",       # A06
    "Consider reducing your stake.",                       # A07
    "It is the best time to get in.",                      # A16
    "A solid stock to own for a decade.",                  # A18
    "Stay invested through the volatility.",               # A14
    "Do not sell during the dip.",                         # A14
    "Add on any weakness.",                                # A10 sentence-initial imperative
    'Switch to a cheaper fund.',                           # A15
    "Expect a sharp move higher.",                         # P02, P18
    "The shares should hit 900 soon.",                     # P04
    "A recovery is on the way.",                           # P05, P17
    "The uptrend is likely to continue.",                  # P06
    "Upside looks capped.",                                # P07
    "The stock is primed for a breakout.",                 # P08
    "This share cannot go much higher.",                   # P12
    "There is a strong chance the stock climbs.",          # P13
    "The price keeps falling next month.",                 # P20
]

V31_PASSES = [
    "No one can tell whether the shares will rise.",       # H08 disclaiming frame
    "A low RSI does not mean the price will rebound.",     # H19 disclaiming frame
    "The stock fell soon after the results.",              # "soon after" is the past
    "Holdings of mutual funds rose last month.",           # not a sentence-initial "Hold"
    "The price changed by -3.2% over the last month.",
]


@pytest.mark.parametrize("text", V31_REJECTED)
def test_v31_rejects_the_patterns_added_after_the_evaluation(text):
    assert not is_stock_text_compliant(text), text


@pytest.mark.parametrize("text", V31_PASSES)
def test_v31_disclaiming_frames_and_past_tense_pass(text):
    assert is_stock_text_compliant(text), find_prohibited_stock_claims(text)


def test_v3_is_kept_unchanged_for_the_before_after_comparison():
    from app.services.investing.claims_guard import STOCK_GUARD_VERSION, find_prohibited_stock_claims_v3
    assert STOCK_GUARD_VERSION == "v3.1"
    assert find_prohibited_stock_claims_v3("Stay invested through the volatility.") == []
    assert find_prohibited_stock_claims_v3("You should buy now.") == ["you should buy"]   # matches cannot overlap


def test_v31_reports_each_phrase_once():
    found = find_prohibited_stock_claims("You should buy now.")
    assert len(found) == len(set(found))


def test_known_gap_negated_predictions_are_not_caught():
    """Documented gap (docs/modules/stocks-evaluation.md): no set-A miss covered it, so v3.1 adds no pattern."""
    assert is_stock_text_compliant("The price will not fall.")
