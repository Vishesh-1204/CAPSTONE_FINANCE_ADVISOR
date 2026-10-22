# app/services/forecasting/__init__.py
#
# Purpose: the Financial Forecast Agent's (Phase 5) building blocks.
#
# Pure modules (no database, no LLM, no HTTP; plain Python, float maths):
#   forecast_models.py      naive / moving average / exponential smoothing / seasonal naive
#   metrics.py              MAE, RMSE, MAPE, sMAPE
#   backtest.py             rolling-origin backtest, model selection, prediction intervals
#   recurrence_detector.py  salary / rent / SIP detection and projection onto future dates
#   horizon.py              "30 days from 22 Oct" -> calendar months with day weights
#   cashflow_projector.py   daily balance trajectory with a band, and the cash-crunch date
#
# Money is float inside these modules (statistics), and is converted to exact
# Decimal (core/money.py) by the service before it is saved or returned.
