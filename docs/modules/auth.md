# Module: Auth

**Files:** `api/routes/auth.py`, `services/auth_service.py`, `core/security.py`, `api/deps.py`,
`models/user.py`, `schemas/user.py`, `schemas/token.py`

## Why it exists
Every piece of financial data belongs to exactly one user. Auth proves who is calling so every
query can be filtered by that user's id.

## How it works
1. **Register** (`POST /api/auth/register`): `auth_service.register_user` rejects a duplicate email (400)
   and stores a **bcrypt** hash of the password (`security.hash_password`). The password itself is never stored.
2. **Login** (`POST /api/auth/login`): standard OAuth2 password form (`username` = email).
   `authenticate_user` checks the hash; `create_access_token` signs a JWT (HS256, `SECRET_KEY`)
   whose `sub` is the user id and `exp` is now + `ACCESS_TOKEN_EXPIRE_MINUTES` (60).
3. **Every protected route** depends on `get_current_user` (`api/deps.py`): reads
   `Authorization: Bearer ...`, verifies signature and expiry, loads the `User`, or raises 401.

A JWT is **signed, not encrypted**: anyone can read its payload, nobody can change it without `SECRET_KEY`.
That is why it carries only the user id.

## Rules every new route must follow
- Take the user from `Depends(get_current_user)`. Never accept `user_id` in a body or query.
- Filter every query by `current_user.id`. Return 404 (not 403) for someone else's id.

## Common mistakes
- Sending JSON to `/login` (it expects a form) → 422.
- Leaving `SECRET_KEY=changeme` outside local development. Set a long random value in `.env`.
- Treating the frontend's auth guard as security. Only the backend's 401 protects data.

## How to test
- Automated: the HTTP tests bypass login by overriding `get_current_user` (see `tests/test_transactions_api.py`),
  so business logic is tested without tokens.
- Manual (Swagger): register → **Authorize** with the email/password → call `GET /api/transactions/` (200);
  log out of Swagger and call again (401).

## Limitations / next
- No refresh tokens, password reset or rate limiting on login (Phase 12/13 hardening).
- The Test Console keeps the token in `localStorage` (test-only); Phase 12 moves to httpOnly cookies.
