# tests/test_categories_api.py
#
# GET /api/categories returns the labels from core/categories.py, split by
# direction, so the frontend never keeps its own copy. Login is bypassed (user 1).

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import categories as category_routes
from app.core.categories import CATEGORY_NAMES, CREDIT_CATEGORY_NAMES
from app.models.user import User


def test_lists_categories_per_direction(db):
    app = FastAPI()
    app.include_router(category_routes.router)
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)

    r = TestClient(app).get("/api/categories/")

    assert r.status_code == 200
    assert r.json() == {"debit": CATEGORY_NAMES, "credit": CREDIT_CATEGORY_NAMES}
    assert "Salary" not in r.json()["debit"]
