# Financial Forecast Agent: evaluation results

Synthetic panel: 30 users (steady 8, freelancer 7, high_spender 8, saver 7), 28,525 rows, seed 42, SHA-256 `801ee5f4158e1688…`. Time-based rolling origin: 15 origins (2025-04-15 … 2026-06-15, the 15th of each month), horizons 30, 60, 90 days, 1,350 forecasts through the real `generate_forecast`. LLM mode: none.

## 1. Variable spending: fixed models vs per-user backtest selection (all users)

Target: window spending that is not a regular payment of a detected recurring bill. B2 needs 12 months, so its n is smaller (later origins only).

| Horizon | Method | n | MAE | RMSE | sMAPE | MAPE | 80% coverage | Mean width |
|---|---|---|---|---|---|---|---|---|
| 30 | B0 fixed | 450 | ₹10,148 | ₹15,641 | 27.1 | 27.7 | 56.9% | ₹18,995 |
| 30 | B1 fixed | 450 | ₹9,086 | ₹13,492 | 24.4 | 25.0 | 51.8% | ₹16,766 |
| 30 | B2 fixed | 270 | ₹8,077 | ₹11,778 | 22.5 | 23.0 | 54.1% | ₹14,934 |
| 30 | B3 fixed | 450 | ₹8,874 | ₹13,171 | 24.3 | 25.0 | 55.3% | ₹16,549 |
| 30 | S (selection) | 450 | ₹9,210 | ₹13,452 | 25.3 | 26.2 | 46.9% | ₹14,153 |
| 60 | B0 fixed | 450 | ₹17,947 | ₹27,292 | 24.5 | 24.6 | 55.8% | ₹35,814 |
| 60 | B1 fixed | 450 | ₹15,264 | ₹22,042 | 21.1 | 21.1 | 50.4% | ₹32,296 |
| 60 | B2 fixed | 270 | ₹11,403 | ₹15,973 | 16.7 | 16.2 | 61.5% | ₹27,151 |
| 60 | B3 fixed | 450 | ₹14,910 | ₹21,187 | 20.8 | 20.8 | 54.9% | ₹31,747 |
| 60 | S (selection) | 450 | ₹15,504 | ₹21,750 | 22.0 | 22.1 | 46.4% | ₹27,600 |
| 90 | B0 fixed | 450 | ₹26,212 | ₹38,739 | 24.4 | 24.6 | 54.4% | ₹50,013 |
| 90 | B1 fixed | 450 | ₹21,383 | ₹29,684 | 20.1 | 20.0 | 51.1% | ₹45,564 |
| 90 | B2 fixed | 270 | ₹14,891 | ₹19,623 | 15.2 | 14.7 | 62.6% | ₹39,662 |
| 90 | B3 fixed | 450 | ₹20,937 | ₹28,625 | 19.8 | 19.7 | 55.3% | ₹44,879 |
| 90 | S (selection) | 450 | ₹22,086 | ₹30,199 | 21.3 | 21.4 | 46.9% | ₹40,180 |

## 2. Variable-spend MAE per persona

| Persona | Horizon | B0 | B1 | B2 | B3 | S | Regret (S − best of B0/B1/B3) | Best fixed |
|---|---|---|---|---|---|---|---|---|
| steady | 30 | ₹7,412 | ₹6,889 | ₹6,649 | ₹6,848 | ₹7,404 | ₹556 | B3 |
| steady | 60 | ₹13,546 | ₹11,656 | ₹10,499 | ₹11,520 | ₹12,577 | ₹1,057 | B3 |
| steady | 90 | ₹20,476 | ₹16,769 | ₹13,721 | ₹16,692 | ₹18,434 | ₹1,742 | B3 |
| freelancer | 30 | ₹9,092 | ₹7,566 | ₹7,601 | ₹7,781 | ₹7,836 | ₹270 | B1 |
| freelancer | 60 | ₹15,172 | ₹12,601 | ₹9,930 | ₹13,057 | ₹13,255 | ₹654 | B1 |
| freelancer | 90 | ₹21,801 | ₹17,975 | ₹13,092 | ₹18,265 | ₹18,531 | ₹555 | B1 |
| high_spender | 30 | ₹15,560 | ₹14,501 | ₹11,227 | ₹13,691 | ₹13,649 | ₹-42 | B3 |
| high_spender | 60 | ₹27,123 | ₹23,658 | ₹15,145 | ₹22,847 | ₹22,008 | ₹-839 | B3 |
| high_spender | 90 | ₹38,849 | ₹32,371 | ₹19,000 | ₹32,174 | ₹31,101 | ₹-1,074 | B3 |
| saver | 30 | ₹8,146 | ₹6,930 | ₹6,585 | ₹6,776 | ₹7,577 | ₹800 | B3 |
| saver | 60 | ₹15,266 | ₹12,459 | ₹9,632 | ₹11,566 | ₹13,665 | ₹2,099 | B3 |
| saver | 90 | ₹22,737 | ₹17,505 | ₹13,330 | ₹15,616 | ₹19,513 | ₹3,897 | B3 |
| all | 30 | ₹10,148 | ₹9,086 | ₹8,077 | ₹8,874 | ₹9,210 | ₹337 | B3 |
| all | 60 | ₹17,947 | ₹15,264 | ₹11,403 | ₹14,910 | ₹15,504 | ₹594 | B3 |
| all | 90 | ₹26,212 | ₹21,383 | ₹14,891 | ₹20,937 | ₹22,086 | ₹1,149 | B3 |

## 3. Which model the agent chose (share of forecasts, all users)

| Horizon | B0 naive | B1 MA-3 | B2 seasonal | B3 exp. smoothing | Low confidence |
|---|---|---|---|---|---|
| 30 | 22.7% | 42.7% | 16.0% | 18.7% | 11.3% |
| 60 | 22.7% | 42.7% | 16.0% | 18.7% | 20.7% |
| 90 | 22.7% | 42.7% | 16.0% | 18.7% | 29.6% |

## 4. Decomposition ablation (all users)

S = recurring items on their dates + selected model on the variable rest. A = selection on the raw monthly total (expenses), or flat monthly income/outflow spread over the days (net cash flow, end balance).

| Quantity | Horizon | Method | MAE | sMAPE | 80% coverage | Mean width |
|---|---|---|---|---|---|---|
| total_expenses | 30 | S decomposed | ₹9,239 | 15.8 | 44.9% | ₹14,153 |
| total_expenses | 30 | A no_decomposition | ₹9,140 | 15.5 | 46.4% | ₹15,390 |
| total_expenses | 30 | B1 no_decomposition | ₹9,026 | 15.1 | 52.2% | ₹17,393 |
| total_expenses | 60 | S decomposed | ₹15,195 | 13.4 | 48.0% | ₹27,600 |
| total_expenses | 60 | A no_decomposition | ₹14,670 | 12.8 | 51.1% | ₹31,899 |
| total_expenses | 60 | B1 no_decomposition | ₹14,916 | 12.8 | 51.6% | ₹34,944 |
| total_expenses | 90 | S decomposed | ₹21,561 | 12.8 | 49.6% | ₹40,180 |
| total_expenses | 90 | A no_decomposition | ₹21,191 | 12.5 | 53.6% | ₹50,056 |
| total_expenses | 90 | B1 no_decomposition | ₹21,049 | 12.2 | 53.6% | ₹52,142 |
| net_cashflow | 30 | S decomposed | ₹19,159 | 87.2 | 33.8% | ₹14,153 |
| net_cashflow | 30 | A no_decomposition | ₹19,599 | 89.5 | 36.7% | ₹15,840 |
| net_cashflow | 60 | S decomposed | ₹27,746 | 78.8 | 37.6% | ₹27,600 |
| net_cashflow | 60 | A no_decomposition | ₹27,565 | 77.1 | 38.2% | ₹33,513 |
| net_cashflow | 90 | S decomposed | ₹36,761 | 77.2 | 37.3% | ₹40,180 |
| net_cashflow | 90 | A no_decomposition | ₹35,828 | 75.2 | 43.1% | ₹53,519 |
| end_balance | 30 | S decomposed | ₹31,087 | 40.6 | 26.0% | ₹14,227 |
| end_balance | 30 | A no_decomposition | ₹30,952 | 39.5 | 29.1% | ₹15,870 |
| end_balance | 60 | S decomposed | ₹48,119 | 51.6 | 22.2% | ₹27,901 |
| end_balance | 60 | A no_decomposition | ₹47,904 | 51.2 | 22.2% | ₹33,744 |
| end_balance | 90 | S decomposed | ₹65,457 | 62.2 | 19.1% | ₹40,919 |
| end_balance | 90 | A no_decomposition | ₹65,361 | 61.8 | 21.1% | ₹54,099 |

## 5. Cash-crunch warning (balance below zero inside the window)

`lower_band` = the service's rule (the LOWER balance band goes below 0); `point` = the point trajectory does.

| Horizon | Method | Event rate | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|---|
| 30 | S lower_band | 6.4% | 13 | 17 | 16 | 404 | 43.3% | 44.8% | 44.1% |
| 30 | S point | 6.4% | 12 | 9 | 17 | 412 | 57.1% | 41.4% | 48.0% |
| 30 | A lower_band | 6.4% | 9 | 8 | 20 | 413 | 52.9% | 31.0% | 39.1% |
| 30 | A point | 6.4% | 7 | 2 | 22 | 419 | 77.8% | 24.1% | 36.8% |
| 60 | S lower_band | 10.4% | 20 | 38 | 27 | 365 | 34.5% | 42.6% | 38.1% |
| 60 | S point | 10.4% | 16 | 16 | 31 | 387 | 50.0% | 34.0% | 40.5% |
| 60 | A lower_band | 10.4% | 14 | 27 | 33 | 376 | 34.1% | 29.8% | 31.8% |
| 60 | A point | 10.4% | 10 | 5 | 37 | 398 | 66.7% | 21.3% | 32.3% |
| 90 | S lower_band | 14.7% | 27 | 53 | 39 | 331 | 33.8% | 40.9% | 37.0% |
| 90 | S point | 14.7% | 22 | 18 | 44 | 366 | 55.0% | 33.3% | 41.5% |
| 90 | A lower_band | 14.7% | 17 | 48 | 49 | 336 | 26.2% | 25.8% | 26.0% |
| 90 | A point | 14.7% | 11 | 10 | 55 | 374 | 52.4% | 16.7% | 25.3% |

| Persona | 30-day event rate | S lower_band recall | S lower_band precision |
|---|---|---|---|
| steady | 0.0% | 100.0% | 100.0% |
| freelancer | 17.1% | 33.3% | 60.0% |
| high_spender | 9.2% | 63.6% | 35.0% |
| saver | 0.0% | 100.0% | 100.0% |

## 6. Ablations on S (variable spending, all users)

| Horizon | Configuration | MAE | 80% coverage | Mean width |
|---|---|---|---|---|
| 30 | default | ₹9,210 | 46.9% | ₹14,153 |
| 30 | select_window=6 | ₹9,281 | 54.0% | ₹17,931 |
| 30 | select_window=12 | ₹9,237 | 51.3% | ₹16,569 |
| 30 | interval=fixed_width | ₹9,210 | 61.3% | ₹17,303 |
| 60 | default | ₹15,504 | 46.4% | ₹27,600 |
| 60 | select_window=6 | ₹16,096 | 52.2% | ₹34,766 |
| 60 | select_window=12 | ₹15,705 | 51.1% | ₹32,467 |
| 60 | interval=fixed_width | ₹15,504 | 64.7% | ₹34,581 |
| 90 | default | ₹22,086 | 46.9% | ₹40,180 |
| 90 | select_window=6 | ₹23,047 | 54.4% | ₹48,935 |
| 90 | select_window=12 | ₹22,459 | 52.0% | ₹46,226 |
| 90 | interval=fixed_width | ₹22,086 | 65.1% | ₹51,896 |

## 7. History length (variable-spend MAE; 9 origins with ≥ 12 months: 2025-10-15 … 2026-06-15)

| Horizon | Months of history | B0 | B1 | B2 | B3 | S | S coverage |
|---|---|---|---|---|---|---|---|
| 30 | 3 | ₹10,897 | ₹9,920 | n/a | ₹10,426 | ₹9,920 | 58.1% |
| 30 | 6 | ₹10,897 | ₹9,920 | n/a | ₹9,540 | ₹10,030 | 59.3% |
| 30 | 12 | ₹10,897 | ₹9,920 | ₹8,077 | ₹9,487 | ₹9,723 | 50.4% |
| 60 | 3 | ₹19,595 | ₹16,494 | n/a | ₹17,921 | ₹16,494 | 63.3% |
| 60 | 6 | ₹19,595 | ₹16,494 | n/a | ₹15,960 | ₹17,532 | 61.9% |
| 60 | 12 | ₹19,595 | ₹16,494 | ₹11,403 | ₹15,197 | ₹16,364 | 50.4% |
| 90 | 3 | ₹27,589 | ₹22,169 | n/a | ₹24,896 | ₹22,169 | 66.3% |
| 90 | 6 | ₹27,589 | ₹22,169 | n/a | ₹21,873 | ₹24,598 | 63.7% |
| 90 | 12 | ₹27,589 | ₹22,169 | ₹14,891 | ₹20,161 | ₹22,466 | 44.8% |

## 8. Recurrence detection vs ground truth (all user-origins)

| Persona | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| steady | 56.1% | 100.0% | 71.9% | 480 | 376 | 0 |
| freelancer | 49.0% | 100.0% | 65.8% | 315 | 328 | 0 |
| high_spender | 59.3% | 100.0% | 74.5% | 480 | 329 | 0 |
| saver | 58.1% | 100.0% | 73.5% | 420 | 303 | 0 |
| all | 55.9% | 100.0% | 71.7% | 1695 | 1336 | 0 |

## 9. LLM explanation

Main runs: template only, 0 LLM calls per forecast.
Faithfulness was not measured in this run (use `--llm real`, synthetic data only).

Caveats: synthetic data (do not claim real-world accuracy). The variable target uses the recurring items detected at each origin, like the training series. Actual end balances include flows the agent does not model (refunds, own-account top-ups and sweeps). Interval coverage is a target of 80%; read it next to the width.
