# Financial Forecast Agent: evaluation results

Synthetic panel: 30 users (steady 8, freelancer 7, high_spender 8, saver 7), 28,525 rows, seed 42, SHA-256 `801ee5f4158e1688…`. Time-based rolling origin: 15 origins (2025-04-15 … 2026-06-15, the 15th of each month), horizons 30, 60, 90 days, 1,350 forecasts through the real `generate_forecast`. LLM mode: none.

## 1. Variable spending: fixed models vs per-user backtest selection (all users)

Target: window spending that is not a regular payment of a detected recurring bill. B2 needs 12 months, so its n is smaller (later origins only).

| Horizon | Method | n | MAE | RMSE | sMAPE | MAPE | 80% coverage | Mean width |
|---|---|---|---|---|---|---|---|---|
| 30 | B0 fixed | 450 | ₹9,898 | ₹15,249 | 24.6 | 26.1 | 67.8% | ₹27,446 |
| 30 | B1 fixed | 450 | ₹8,991 | ₹13,281 | 23.2 | 24.1 | 68.7% | ₹23,530 |
| 30 | B2 fixed | 270 | ₹8,079 | ₹11,800 | 21.4 | 21.9 | 68.9% | ₹21,413 |
| 30 | B3 fixed | 450 | ₹8,847 | ₹12,935 | 23.1 | 24.3 | 71.1% | ₹23,982 |
| 30 | S (selection) | 450 | ₹9,095 | ₹13,302 | 23.7 | 25.2 | 62.9% | ₹20,594 |
| 60 | B0 fixed | 450 | ₹16,690 | ₹26,375 | 21.1 | 22.0 | 71.3% | ₹50,714 |
| 60 | B1 fixed | 450 | ₹14,809 | ₹21,486 | 19.6 | 20.0 | 65.3% | ₹43,174 |
| 60 | B2 fixed | 270 | ₹11,121 | ₹15,618 | 15.5 | 15.1 | 73.7% | ₹35,411 |
| 60 | B3 fixed | 450 | ₹14,563 | ₹20,802 | 19.4 | 19.9 | 69.6% | ₹43,481 |
| 60 | S (selection) | 450 | ₹14,645 | ₹21,140 | 19.8 | 20.4 | 64.7% | ₹37,982 |
| 90 | B0 fixed | 450 | ₹24,305 | ₹37,601 | 20.8 | 22.0 | 69.6% | ₹71,562 |
| 90 | B1 fixed | 450 | ₹20,809 | ₹28,974 | 18.7 | 18.9 | 65.3% | ₹60,593 |
| 90 | B2 fixed | 270 | ₹14,807 | ₹19,421 | 14.3 | 14.0 | 70.7% | ₹47,268 |
| 90 | B3 fixed | 450 | ₹20,472 | ₹28,010 | 18.4 | 18.8 | 69.1% | ₹60,665 |
| 90 | S (selection) | 450 | ₹21,102 | ₹29,431 | 19.2 | 19.9 | 61.3% | ₹54,014 |

## 2. Variable-spend MAE per persona

| Persona | Horizon | B0 | B1 | B2 | B3 | S | Regret (S − best of B0/B1/B3) | Best fixed |
|---|---|---|---|---|---|---|---|---|
| steady | 30 | ₹7,272 | ₹6,965 | ₹6,709 | ₹6,712 | ₹7,110 | ₹397 | B3 |
| steady | 60 | ₹12,439 | ₹11,464 | ₹10,666 | ₹11,224 | ₹12,006 | ₹782 | B3 |
| steady | 90 | ₹18,728 | ₹16,624 | ₹14,256 | ₹16,159 | ₹17,336 | ₹1,177 | B3 |
| freelancer | 30 | ₹8,643 | ₹7,594 | ₹7,568 | ₹7,917 | ₹7,906 | ₹312 | B1 |
| freelancer | 60 | ₹13,818 | ₹12,295 | ₹9,591 | ₹13,130 | ₹12,398 | ₹103 | B1 |
| freelancer | 90 | ₹19,867 | ₹17,416 | ₹12,722 | ₹17,981 | ₹17,761 | ₹344 | B1 |
| high_spender | 30 | ₹15,451 | ₹14,000 | ₹11,277 | ₹13,477 | ₹13,314 | ₹-163 | B3 |
| high_spender | 60 | ₹25,234 | ₹22,310 | ₹14,281 | ₹21,672 | ₹20,047 | ₹-1,624 | B3 |
| high_spender | 90 | ₹36,113 | ₹30,817 | ₹18,293 | ₹30,900 | ₹29,120 | ₹-1,697 | B1 |
| saver | 30 | ₹7,807 | ₹6,978 | ₹6,500 | ₹6,927 | ₹7,730 | ₹803 | B3 |
| saver | 60 | ₹14,654 | ₹12,574 | ₹9,560 | ₹11,688 | ₹13,735 | ₹2,047 | B3 |
| saver | 90 | ₹21,624 | ₹17,546 | ₹13,537 | ₹15,976 | ₹19,583 | ₹3,606 | B3 |
| all | 30 | ₹9,898 | ₹8,991 | ₹8,079 | ₹8,847 | ₹9,095 | ₹247 | B3 |
| all | 60 | ₹16,690 | ₹14,809 | ₹11,121 | ₹14,563 | ₹14,645 | ₹82 | B3 |
| all | 90 | ₹24,305 | ₹20,809 | ₹14,807 | ₹20,472 | ₹21,102 | ₹629 | B3 |

## 3. Which model the agent chose (share of forecasts, all users)

| Horizon | B0 naive | B1 MA-3 | B2 seasonal | B3 exp. smoothing | Low confidence |
|---|---|---|---|---|---|
| 30 | 25.1% | 44.9% | 14.0% | 16.0% | 10.9% |
| 60 | 25.1% | 44.9% | 14.0% | 16.0% | 19.3% |
| 90 | 25.1% | 44.9% | 14.0% | 16.0% | 28.0% |

## 4. Decomposition ablation (all users)

S = recurring items on their dates + selected model on the variable rest. A = selection on the raw monthly total (expenses), or flat monthly income/outflow spread over the days (net cash flow, end balance).

| Quantity | Horizon | Method | MAE | sMAPE | 80% coverage | Mean width |
|---|---|---|---|---|---|---|
| total_expenses | 30 | S decomposed | ₹9,115 | 15.5 | 62.9% | ₹20,594 |
| total_expenses | 30 | A no_decomposition | ₹9,140 | 15.5 | 64.0% | ₹21,789 |
| total_expenses | 30 | B1 no_decomposition | ₹9,026 | 15.1 | 68.7% | ₹24,158 |
| total_expenses | 60 | S decomposed | ₹14,634 | 12.8 | 64.2% | ₹37,982 |
| total_expenses | 60 | A no_decomposition | ₹14,670 | 12.8 | 66.0% | ₹42,254 |
| total_expenses | 60 | B1 no_decomposition | ₹14,916 | 12.8 | 65.6% | ₹45,828 |
| total_expenses | 90 | S decomposed | ₹21,060 | 12.4 | 61.8% | ₹54,014 |
| total_expenses | 90 | A no_decomposition | ₹21,191 | 12.5 | 65.1% | ₹62,778 |
| total_expenses | 90 | B1 no_decomposition | ₹21,049 | 12.2 | 66.7% | ₹66,613 |
| net_cashflow | 30 | S decomposed | ₹19,183 | 88.3 | 47.6% | ₹20,594 |
| net_cashflow | 30 | A no_decomposition | ₹19,599 | 89.5 | 47.1% | ₹22,240 |
| net_cashflow | 60 | S decomposed | ₹27,081 | 76.8 | 48.9% | ₹37,982 |
| net_cashflow | 60 | A no_decomposition | ₹27,565 | 77.1 | 51.6% | ₹43,869 |
| net_cashflow | 90 | S decomposed | ₹35,472 | 75.4 | 48.2% | ₹54,014 |
| net_cashflow | 90 | A no_decomposition | ₹35,828 | 75.2 | 52.7% | ₹66,241 |
| end_balance | 30 | S decomposed | ₹31,039 | 40.0 | 36.2% | ₹20,597 |
| end_balance | 30 | A no_decomposition | ₹30,952 | 39.5 | 38.0% | ₹22,242 |
| end_balance | 60 | S decomposed | ₹47,897 | 51.3 | 26.2% | ₹38,002 |
| end_balance | 60 | A no_decomposition | ₹47,904 | 51.2 | 27.8% | ₹43,888 |
| end_balance | 90 | S decomposed | ₹65,115 | 61.5 | 23.1% | ₹54,128 |
| end_balance | 90 | A no_decomposition | ₹65,361 | 61.8 | 25.8% | ₹66,345 |

## 5. Cash-crunch warning (balance below zero inside the window)

`lower_band` = the service's rule (the LOWER balance band goes below 0); `point` = the point trajectory does.

| Horizon | Method | Event rate | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|---|
| 30 | S lower_band | 6.4% | 12 | 22 | 17 | 399 | 35.3% | 41.4% | 38.1% |
| 30 | S point | 6.4% | 11 | 7 | 18 | 414 | 61.1% | 37.9% | 46.8% |
| 30 | A lower_band | 6.4% | 10 | 10 | 19 | 411 | 50.0% | 34.5% | 40.8% |
| 30 | A point | 6.4% | 7 | 2 | 22 | 419 | 77.8% | 24.1% | 36.8% |
| 60 | S lower_band | 10.4% | 21 | 46 | 26 | 357 | 31.3% | 44.7% | 36.8% |
| 60 | S point | 10.4% | 15 | 17 | 32 | 386 | 46.9% | 31.9% | 38.0% |
| 60 | A lower_band | 10.4% | 15 | 30 | 32 | 373 | 33.3% | 31.9% | 32.6% |
| 60 | A point | 10.4% | 10 | 5 | 37 | 398 | 66.7% | 21.3% | 32.3% |
| 90 | S lower_band | 14.7% | 28 | 56 | 38 | 328 | 33.3% | 42.4% | 37.3% |
| 90 | S point | 14.7% | 19 | 21 | 47 | 363 | 47.5% | 28.8% | 35.8% |
| 90 | A lower_band | 14.7% | 22 | 50 | 44 | 334 | 30.6% | 33.3% | 31.9% |
| 90 | A point | 14.7% | 11 | 10 | 55 | 374 | 52.4% | 16.7% | 25.3% |

| Persona | 30-day event rate | S lower_band recall | S lower_band precision |
|---|---|---|---|
| steady | 0.0% | 100.0% | 100.0% |
| freelancer | 17.1% | 27.8% | 62.5% |
| high_spender | 9.2% | 63.6% | 26.9% |
| saver | 0.0% | 100.0% | 100.0% |

## 6. Ablations on S (variable spending, all users; default interval = student_t)

| Horizon | Configuration | MAE | 80% coverage | Mean width |
|---|---|---|---|---|
| 30 | default | ₹9,095 | 62.9% | ₹20,594 |
| 30 | select_window=6 | ₹9,365 | 69.3% | ₹25,349 |
| 30 | select_window=12 | ₹9,157 | 66.7% | ₹22,957 |
| 30 | interval=empirical | ₹9,095 | 46.2% | ₹14,201 |
| 30 | interval=conformal | ₹9,095 | 65.6% | ₹22,015 |
| 30 | interval=fixed_width | ₹9,095 | 63.6% | ₹18,556 |
| 60 | default | ₹14,645 | 64.7% | ₹37,982 |
| 60 | select_window=6 | ₹15,644 | 70.2% | ₹47,704 |
| 60 | select_window=12 | ₹14,972 | 67.8% | ₹42,877 |
| 60 | interval=empirical | ₹14,645 | 48.7% | ₹27,747 |
| 60 | interval=conformal | ₹14,645 | 70.0% | ₹42,103 |
| 60 | interval=fixed_width | ₹14,645 | 69.1% | ₹37,104 |
| 90 | default | ₹21,102 | 61.3% | ₹54,014 |
| 90 | select_window=6 | ₹22,493 | 67.8% | ₹69,043 |
| 90 | select_window=12 | ₹21,565 | 64.0% | ₹60,977 |
| 90 | interval=empirical | ₹21,102 | 49.8% | ₹41,274 |
| 90 | interval=conformal | ₹21,102 | 69.3% | ₹60,472 |
| 90 | interval=fixed_width | ₹21,102 | 69.8% | ₹55,695 |

## 7. History length (variable-spend MAE; 9 origins with ≥ 12 months: 2025-10-15 … 2026-06-15)

| Horizon | Months of history | B0 | B1 | B2 | B3 | S | S coverage |
|---|---|---|---|---|---|---|---|
| 30 | 3 | ₹10,651 | ₹9,801 | n/a | ₹10,017 | ₹9,801 | 59.6% |
| 30 | 6 | ₹10,651 | ₹9,801 | n/a | ₹9,734 | ₹10,395 | 61.9% |
| 30 | 12 | ₹10,651 | ₹9,801 | ₹8,079 | ₹9,353 | ₹9,742 | 64.8% |
| 60 | 3 | ₹18,519 | ₹16,198 | n/a | ₹16,709 | ₹16,198 | 64.8% |
| 60 | 6 | ₹18,519 | ₹16,198 | n/a | ₹15,520 | ₹17,321 | 65.9% |
| 60 | 12 | ₹18,519 | ₹16,198 | ₹11,121 | ₹15,127 | ₹16,033 | 64.4% |
| 90 | 3 | ₹25,989 | ₹22,041 | n/a | ₹23,101 | ₹22,041 | 68.9% |
| 90 | 6 | ₹25,989 | ₹22,041 | n/a | ₹21,423 | ₹24,392 | 64.8% |
| 90 | 12 | ₹25,989 | ₹22,041 | ₹14,807 | ₹20,411 | ₹22,390 | 63.7% |

## 8. Recurrence detection vs ground truth (all user-origins)

| Persona | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| steady | 90.2% | 100.0% | 94.9% | 480 | 52 | 0 |
| freelancer | 92.9% | 100.0% | 96.3% | 315 | 24 | 0 |
| high_spender | 97.2% | 100.0% | 98.6% | 480 | 14 | 0 |
| saver | 95.9% | 100.0% | 97.9% | 420 | 18 | 0 |
| all | 94.0% | 100.0% | 96.9% | 1695 | 108 | 0 |

## 9. Recurrence detector: with vs without the frequency check (F6.1)

Scored on targets that do not depend on what was detected.

| Detector | Recurrence precision | Recall | Horizon | Total-expense MAE | Total-expense coverage | Net cash-flow MAE | Crunch F1 (lower band) |
|---|---|---|---|---|---|---|---|
| frequency check (default) | 94.0% | 100.0% | 30 | ₹9,115 | 62.9% | ₹19,183 | 38.1% |
| frequency check (default) | 94.0% | 100.0% | 60 | ₹14,634 | 64.2% | ₹27,081 | 36.8% |
| frequency check (default) | 94.0% | 100.0% | 90 | ₹21,060 | 61.8% | ₹35,472 | 37.3% |
| no frequency check (pre-F6.1) | 55.9% | 100.0% | 30 | ₹9,239 | 62.9% | ₹19,159 | 43.1% |
| no frequency check (pre-F6.1) | 55.9% | 100.0% | 60 | ₹15,195 | 63.6% | ₹27,746 | 37.5% |
| no frequency check (pre-F6.1) | 55.9% | 100.0% | 90 | ₹21,561 | 60.2% | ₹36,761 | 38.2% |

## 10. LLM explanation

Main runs: template only, 0 LLM calls per forecast.
Faithfulness was not measured in this run (use `--llm real`, synthetic data only).

Caveats: synthetic data (do not claim real-world accuracy). The variable target uses the recurring items detected at each origin, like the training series. Actual end balances include flows the agent does not model (refunds, own-account top-ups and sweeps). Interval coverage is a target of 80%; read it next to the width.
