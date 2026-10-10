"""HTTP routes for Module B authentication and identity."""

import inspect
from secrets import token_urlsafe
from typing import Annotated, Any, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.contracts.principal import Principal
from app.core.db import get_db
from app.core.errors import AppError
from app.core.principal import get_principal
from app.modules.authentication.google import verify_google_token
from app.modules.authentication.jwt import create_access_token
from app.modules.authentication.oauth import authorization_url, exchange_code
from app.modules.authentication.schemas import GoogleLoginRequest, MeResponse, TokenResponse
from app.modules.authentication.service import AuthenticationService

router = APIRouter(prefix="/auth", tags=["authentication"])
_OAUTH_STATE_COOKIE = "google_oauth_state"


async def _resolved(value: Any) -> Any:
    """Tests may install synchronous hooks; the real Google calls are async."""
    return await value if inspect.isawaitable(value) else value


def _google_verifier(request: Request) -> Callable[[str], Any]:
    configured = getattr(request.app.state, "google_token_verifier", None)
    if configured is not None:
        return configured
    return lambda credential: verify_google_token(
        credential, request.app.state.settings.google_oauth_client_id
    )


def _issue_token(
    request: Request, session: Session, google_identity: dict[str, str]
) -> TokenResponse:
    service = AuthenticationService(session, request.app.state.settings)
    principal = service.principal_for_google_identity(
        google_identity["google_subject"], google_identity["email"]
    )
    if principal is None:
        raise AppError(
            "ACCOUNT_NOT_ALLOWED", "Google account is not registered for this workshop.", 403
        )
    return TokenResponse(
        access_token=create_access_token(
            user_id=principal.user_id,
            email=principal.email or google_identity["email"],
            team_id=str(principal.team_id) if principal.team_id else None,
            role=principal.role,
            settings=request.app.state.settings,
        )
    )


@router.get("/google/login", include_in_schema=True)
def google_login_redirect(request: Request) -> RedirectResponse:
    state = token_urlsafe(32)
    response = RedirectResponse(
        authorization_url(request.app.state.settings, state), status_code=302
    )
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/google/callback", response_class=RedirectResponse, status_code=302)
async def google_login_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1)],
    state: Annotated[str, Query(min_length=1)],
    session: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not expected_state or expected_state != state:
        raise AppError("OAUTH_STATE_INVALID", "OAuth sign-in could not be validated.", 400)
    configured_exchange = getattr(request.app.state, "google_code_exchange", None)
    id_token = await _resolved(
        configured_exchange(code)
        if configured_exchange is not None
        else exchange_code(code, request.app.state.settings)
    )
    google_identity = await _resolved(_google_verifier(request)(id_token))
    if google_identity is None:
        raise AppError("INVALID_GOOGLE_CREDENTIAL", "Google credential is invalid.", 401)
    token = _issue_token(request, session, google_identity)
    response = RedirectResponse(
        "flutterwars://auth-callback?" + urlencode({"access_token": token.access_token}),
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    return response


@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    google_identity = await _resolved(_google_verifier(request)(payload.credential))
    if google_identity is None:
        raise AppError("INVALID_GOOGLE_CREDENTIAL", "Google credential is invalid.", 401)
    return _issue_token(request, session, google_identity)


@router.get("/me", response_model=MeResponse)
def get_me(principal: Annotated[Principal, Depends(get_principal)]) -> MeResponse:
    return MeResponse(
        user_id=principal.user_id,
        email=principal.email,
        team_id=str(principal.team_id) if principal.team_id else None,
        role=principal.role,
    )
