# Budget Planning Agent: evaluation results

Synthetic data, 3 personas. Plan for 2026-07 built from the 3 months before it; scored against actual spending in 2026-07, 2026-08, 2026-09. MAE, MAPE and adherence are means over those 3 months. LLM mode: none.

## Main comparison (per persona)

| Persona | Method | Feasible | Spendable | MAE | MAPE | Adherence | Overspend | Unused limit |
|---|---|---|---|---|---|---|---|---|
| frugal | B0 baseline | yes | ₹41,362 | ₹1,138 | 90.3% | 84.4% | ₹907 | ₹16,165 |
| frugal | B1 default | yes | ₹41,362 | ₹79 | 6.0% | 22.2% | ₹673 | ₹39 |
| irregular | B0 baseline | yes | ₹49,711 | ₹1,707 | 48.3% | 73.3% | ₹11,920 | ₹13,681 |
| irregular | B1 default | yes | ₹49,711 | ₹1,649 | 21.6% | 63.6% | ₹9,990 | ₹8,154 |
| overspender | B0 baseline | yes | ₹56,120 | ₹2,019 | 36.8% | 37.8% | ₹21,592 | ₹8,690 |
| overspender | B1 default | yes | ₹56,120 | ₹1,162 | 25.5% | 25.0% | ₹13,424 | ₹522 |

## Ablations (mean over personas)

| Configuration | MAE | MAPE | Adherence | Overspend | Unused limit | Feasibility rate |
|---|---|---|---|---|---|---|
| B0 baseline | ₹1,621 | 58.4% | 65.2% | ₹11,473 | ₹12,845 | 100.0% |
| B1 default | ₹964 | 17.7% | 37.0% | ₹8,029 | ₹2,905 | 100.0% |
| B1 trim=0.00 | ₹947 | 16.7% | 44.4% | ₹7,599 | ₹3,180 | 100.0% |
| B1 trim=0.05 | ₹955 | 17.1% | 37.0% | ₹7,810 | ₹3,039 | 100.0% |
| B1 trim=0.10 | ₹964 | 17.7% | 37.0% | ₹8,029 | ₹2,905 | 100.0% |
| B1 trim=0.20 | ₹984 | 19.0% | 34.9% | ₹8,480 | ₹2,652 | 100.0% |
| B1 lookback=1 | ₹1,143 | 20.0% | 36.2% | ₹8,194 | ₹4,268 | 100.0% |
| B1 lookback=2 | ₹1,033 | 17.3% | 34.9% | ₹7,812 | ₹3,896 | 100.0% |
| B1 lookback=3 | ₹964 | 17.7% | 37.0% | ₹8,029 | ₹2,905 | 100.0% |
| B1 investments_as_spending | ₹1,069 | 20.3% | 39.7% | ₹8,897 | ₹3,307 | 100.0% |

## Investments counted as spending (budget distortion)

| Persona | History spend (correct) | History spend (naive) | Distortion | Limit given to non-spending | Adherence correct | Adherence naive |
|---|---|---|---|---|---|---|
| frugal | ₹25,880 | ₹33,880 | 30.9% | ₹7,200 | 22.2% | 22.2% |
| irregular | ₹47,820 | ₹51,487 | 7.7% | ₹3,300 | 63.6% | 63.6% |
| overspender | ₹68,517 | ₹76,517 | 11.7% | ₹1,400 | 25.0% | 33.3% |

## Simulated overrides (oracle user, July)

| Persona | Override rate | Mean relative override size |
|---|---|---|
| frugal | 0.0% | n/a |
| irregular | 18.2% | 50.0% |
| overspender | 58.3% | 73.2% |

Caveat: synthetic actuals do not react to the budget, so adherence measures how realistic the limits are, not whether a person would follow them. Adherence alone rewards loose budgets (B0 gives the frugal persona far more than it spends), so read it next to overspend and unused limit. MAE also includes the INTENDED discretionary trim, so compare methods, not absolute values.
