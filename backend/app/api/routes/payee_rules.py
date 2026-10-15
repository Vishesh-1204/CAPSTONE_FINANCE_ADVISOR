# app/api/routes/payee_rules.py
#
# Purpose: list and delete the user's "remember this payee" rules.
# Rules are CREATED through PATCH /api/transactions/{id}/category with
# remember_payee=true, so there is deliberately no POST here.

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.transaction import PayeeRuleResponse
from app.services.payee_rule_service import delete_rule, list_rules

router = APIRouter(prefix="/api/payee-rules", tags=["Payee rules"])


@router.get("/", response_model=list[PayeeRuleResponse])
def get_payee_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your payee rules, newest first."""
    return list_rules(db, user_id=current_user.id)


@router.delete("/{rule_id}", status_code=204)
def remove_payee_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deletes one of your payee rules. Already-categorized rows keep their category."""
    delete_rule(db, user_id=current_user.id, rule_id=rule_id)
    return Response(status_code=204)
