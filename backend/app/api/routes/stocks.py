# app/api/routes/stocks.py
#
# Purpose: HTTP endpoints of the Stock Intelligence Agent (Phase 7, spec section 4).
# HTTP only: the logic lives in services/stock_service.py.
#
# Every route needs a JWT, and the user is ALWAYS the one in the token (get_current_user).
# Every response carries `disclaimer` (schemas/stock.py): educational analysis, not advice.
# A {symbol} in the path goes through the same strict validator as request bodies, so raw
# text never reaches a provider URL or a query. Index symbols contain a caret: send ^NSEI
# URL-encoded as %5ENSEI.

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes.statements import read_upload
from app.core.stock_symbols import MAX_SYMBOL_LENGTH, normalize_symbol
from app.db.session import get_db
from app.models.user import User
from app.schemas.stock import (
    MAX_PRICE_DAYS, AnalysisHistoryResponse, PriceUploadResponse, PricesResponse, RefreshResponse,
    SentimentScoreRequest, SentimentScoreResponse, StockAnalysisResponse, StockStatusResponse, WatchlistAddRequest,
    WatchlistResponse,
)
from app.services import stock_service as svc

router = APIRouter(prefix="/api/stocks", tags=["Stocks"])


def valid_symbol(symbol: str = Path(..., max_length=MAX_SYMBOL_LENGTH + 10,
                                    description="e.g. RELIANCE.NS, or %5ENSEI for ^NSEI")) -> str:
    """Path symbol -> canonical symbol, or 422 (same rule as request bodies)."""
    try:
        return normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


# --- Status, headline scorer, watchlist (fixed paths) ---

@router.get("/status", response_model=StockStatusResponse)
def provider_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Providers in use, calls made today, cache age of your symbols, and whether FinBERT is installed."""
    return svc.get_status(db, current_user.id)


@router.post("/sentiment/score", response_model=SentimentScoreResponse)
def score_headline(body: SentimentScoreRequest, current_user: User = Depends(get_current_user)):
    """Scores one headline with `scorer`, or the active one (lexicon, or FinBERT in the ml profile). Not stored."""
    return svc.score_one_headline(body.headline, body.scorer)


@router.get("/watchlist", response_model=WatchlistResponse)
def read_watchlist(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Your symbols with the date of the newest cached bar."""
    return svc.list_watchlist(db, current_user.id)


@router.post("/watchlist", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
def add_symbol(body: WatchlistAddRequest, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """Adds a symbol (409 if it is already there or the list is full)."""
    return svc.add_to_watchlist(db, current_user.id, body.symbol)


@router.delete("/watchlist/{symbol}", response_model=WatchlistResponse)
def remove_symbol(symbol: str = Depends(valid_symbol), db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """Removes a symbol (404 if it is not on your watchlist). Cached market data is kept."""
    return svc.remove_from_watchlist(db, current_user.id, symbol)


# --- Per-symbol (must be on your watchlist) ---

@router.post("/{symbol}/refresh", response_model=RefreshResponse)
def refresh_symbol(symbol: str = Depends(valid_symbol), db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """
    Pulls prices and headlines through the cache: the provider is called at most once per symbol
    per STOCK_CACHE_TTL_HOURS, and not at all for a while after it rate-limits.
    """
    return svc.refresh(db, current_user.id, symbol)


@router.get("/{symbol}/prices", response_model=PricesResponse)
def symbol_prices(symbol: str = Depends(valid_symbol),
                  days: int = Query(365, ge=1, le=MAX_PRICE_DAYS, description="Calendar days of bars"),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Cached daily bars with the 50 and 200 day averages, for the chart. Never calls a provider."""
    return svc.get_chart_prices(db, current_user.id, symbol, days)


@router.post("/{symbol}/prices/upload", response_model=PriceUploadResponse)
async def upload_symbol_prices(symbol: str = Depends(valid_symbol), file: UploadFile = File(...),
                               db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    CSV fallback when no provider works: columns date, open, high, low, close (volume optional).
    Adds missing days only; cached provider bars are never overwritten (the cache is shared).
    """
    content = await read_upload(file, svc.MAX_UPLOAD_BYTES)
    return svc.upload_prices(db, current_user.id, symbol, content)


@router.post("/{symbol}/analysis", response_model=StockAnalysisResponse)
def analyze_symbol(symbol: str = Depends(valid_symbol), db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """
    Builds and stores an analysis: trend vs the 50/200 day averages, RSI, volatility, largest fall,
    one-day VaR, beta vs the benchmark and headline sentiment, explained in plain words.
    Describes the past only.
    """
    return svc.analyze(db, current_user.id, symbol)


@router.get("/{symbol}/analysis/latest", response_model=StockAnalysisResponse)
def latest_analysis(symbol: str = Depends(valid_symbol), db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """Your most recent analysis of this symbol (404 before the first one)."""
    return svc.latest_analysis(db, current_user.id, symbol)


@router.get("/{symbol}/analyses", response_model=AnalysisHistoryResponse)
def analysis_history(symbol: str = Depends(valid_symbol), limit: int = Query(20, ge=1, le=100),
                     db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Your analyses of this symbol, newest first (summary only)."""
    return svc.list_analyses(db, current_user.id, symbol, limit)


@router.get("/{symbol}/analyses/{analysis_id}", response_model=StockAnalysisResponse)
def analysis_by_id(analysis_id: int, symbol: str = Depends(valid_symbol), db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """One of your analyses in full (404 if it does not exist, is not yours, or is another symbol)."""
    return svc.get_analysis(db, current_user.id, symbol, analysis_id)
