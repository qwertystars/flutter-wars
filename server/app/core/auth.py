"""Shared access helpers over Module B's verified principal.

Module B (app/core/principal.py) proves WHO is calling and which team they belong
to. Module K (app/modules/admin/authz.py) decides who is an organizer, from its own
`organizer` table, on every request. Feature modules use these helpers instead of
reading tokens themselves.
"""

from fastapi import Depends

from app.contracts.principal import Principal
from app.core.errors import AppError
from app.core.principal import get_principal

__all__ = ["Principal", "get_principal", "require_participant"]


def require_participant(principal: Principal = Depends(get_principal)) -> Principal:
    """The caller must act for a team (participants; not organizer-only logins)."""
    if principal.team_id is None:
        raise AppError("NOT_A_TEAM_MEMBER", "This endpoint needs a team member login.", 403)
    return principal
