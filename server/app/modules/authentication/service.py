"""Module B identity/team lookup and current-principal validation."""

from collections.abc import Callable
from uuid import UUID

from sqlmodel import Session, select

from app.contracts.principal import Principal
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.authentication.jwt import decode_access_token
from app.modules.authentication.model import Team, TeamMembership, UserIdentity

ORGANIZER_ROLE = "organizer"

# Module K registers who is an active organizer (by Google-verified email). Module B
# only asks; it never reads K's tables. Unset means no organizer-only logins.
OrganizerLookup = Callable[[Session, str], bool]
_organizer_lookup: OrganizerLookup | None = None


def set_organizer_lookup(lookup: OrganizerLookup | None) -> None:
    global _organizer_lookup
    _organizer_lookup = lookup


def _is_organizer(session: Session, email: str | None) -> bool:
    return bool(email) and _organizer_lookup is not None and _organizer_lookup(session, email)


class AuthenticationService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def principal_for_google_identity(self, google_subject: str, email: str) -> Principal | None:
        user = self.session.exec(
            select(UserIdentity).where(UserIdentity.google_subject == google_subject)
        ).first()
        if user is None:
            user = self.session.exec(
                select(UserIdentity).where(UserIdentity.email == email.lower())
            ).first()
            if user is None and _is_organizer(self.session, email.lower()):
                # Organizers are registered in Module K, not as team members.
                user = UserIdentity(google_subject=google_subject, email=email.lower())
                self.session.add(user)
                self.session.flush()
            elif user is None or user.google_subject is not None:
                return None
            else:
                user.google_subject = google_subject
                self.session.add(user)
        if user is None or user.id is None:
            return None
        membership = self.session.exec(
            select(TeamMembership).where(TeamMembership.user_identity_id == user.id)
        ).first()
        if membership is None:
            if _is_organizer(self.session, user.email):
                return Principal(user_id=str(user.id), role=ORGANIZER_ROLE, email=user.email)
            return None
        team = self.session.get(Team, membership.team_id)
        if team is None or team.status != "active" or team.id is None:
            return None
        return Principal(
            user_id=str(user.id), team_id=str(team.id), role=membership.role, email=user.email
        )

    def principal_for_token(self, token: str) -> Principal:
        claims = decode_access_token(token, self.settings)
        user_id = claims.get("sub")
        team_id = claims.get("team_id")
        if not isinstance(user_id, str) or not isinstance(team_id, (str, type(None))):
            raise AppError("INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401)
        try:
            numeric_user_id = int(user_id)
            team_uuid = UUID(team_id) if team_id is not None else None
        except ValueError as exc:
            raise AppError(
                "INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401
            ) from exc
        user = self.session.get(UserIdentity, numeric_user_id)
        if user is None:
            raise AppError(
                "IDENTITY_NOT_FOUND", "Authenticated identity is no longer available.", 401
            )
        if team_uuid is None:
            # Organizer-only token: re-checked on every request so removal is immediate.
            if not _is_organizer(self.session, user.email):
                raise AppError(
                    "TEAM_ACCESS_DENIED", "Authenticated identity cannot access this team.", 403
                )
            return Principal(user_id=str(user.id), role=ORGANIZER_ROLE, email=user.email)
        membership = self.session.exec(
            select(TeamMembership).where(
                TeamMembership.user_identity_id == numeric_user_id,
                TeamMembership.team_id == team_uuid,
            )
        ).first()
        if membership is None:
            raise AppError(
                "TEAM_ACCESS_DENIED", "Authenticated identity cannot access this team.", 403
            )
        team = self.session.get(Team, team_uuid)
        if team is None or team.status != "active":
            raise AppError(
                "TEAM_ACCESS_DENIED", "Authenticated identity cannot access this team.", 403
            )
        return Principal(
            user_id=str(user.id), team_id=str(team.id), role=membership.role, email=user.email
        )
