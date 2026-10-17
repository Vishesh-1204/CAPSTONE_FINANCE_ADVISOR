# app/api/routes/categories.py
#
# Purpose: read-only list of the category labels a user may choose, split by
# direction (debit vs credit). The frontend uses it to fill its dropdowns, so
# core/categories.py stays the ONE place where category names are defined.

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.categories import categories_for_direction
from app.models.user import User
from app.schemas.transaction import CategoryOptions

router = APIRouter(prefix="/api/categories", tags=["Categories"])


@router.get("/", response_model=CategoryOptions)
def get_categories(current_user: User = Depends(get_current_user)):
    """Labels allowed on a debit (expenses) and on a credit (income, redemption, transfers)."""
    return CategoryOptions(debit=categories_for_direction("debit"), credit=categories_for_direction("credit"))
