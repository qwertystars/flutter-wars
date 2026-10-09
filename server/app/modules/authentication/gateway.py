"""Module B's gateway (app/contracts/identity.py): token checks and the team directory.

Module K manages teams through this gateway and never touches the `team` or
`team_membership` tables itself. Team status is stored lower-case by Module B
("active"/"disabled") and exposed upper-case ("ACTIVE"/"DISABLED").
"""

from uuid import UUID

from sqlmodel import Session, col, select

from app.contracts.identity import TeamSummary
from app.contracts.principal import Principal
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.authentication.model import Team, TeamMembership, UserIdentity
from app.modules.authentication.service import AuthenticationService


def _summary(team: Team) -> TeamSummary:
    return TeamSummary(id=team.id, name=team.name, status=team.status.upper())


class IdentityGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    def principal_for_token(self, token: str, settings: Settings) -> Principal:
        return AuthenticationService(self.session, settings).principal_for_token(token)

    def team_exists(self, team_id: UUID) -> bool:
        return self.session.exec(select(Team.id).where(Team.id == team_id)).first() is not None

    def list_teams(self) -> list[TeamSummary]:
        return [_summary(t) for t in self.session.exec(select(Team).order_by(col(Team.name))).all()]

    def get_team(self, team_id: UUID) -> TeamSummary | None:
        team = self.session.get(Team, team_id)
        return _summary(team) if team else None

    def find_by_name(self, name: str) -> TeamSummary | None:
        team = self.session.exec(select(Team).where(Team.name == name)).first()
        return _summary(team) if team else None

    def create_team(self, name: str, member_emails: list[str]) -> TeamSummary:
        """Create the team and pre-register its members by email.

        Each member binds their Google account on first login (Module B matches the
        verified email). An email already registered to another team is rejected.
        """
        s = self.session
        team = Team(name=name)
        s.add(team)
        s.flush()
        for email in dict.fromkeys(e.strip().lower() for e in member_emails if e.strip()):
            user = s.exec(select(UserIdentity).where(UserIdentity.email == email)).first()
            if user is None:
                user = UserIdentity(email=email)
                s.add(user)
                s.flush()
            elif s.exec(
                select(TeamMembership).where(TeamMembership.user_identity_id == user.id)
            ).first():
                raise AppError(
                    "MEMBER_ALREADY_IN_TEAM", "A member already belongs to another team.", 409,
                    email=email,
                )
            s.add(TeamMembership(user_identity_id=user.id, team_id=team.id))
        s.flush()
        return _summary(team)

    def set_status(self, team_id: UUID, status: str) -> TeamSummary:
        s = self.session
        team = s.exec(select(Team).where(Team.id == team_id).with_for_update()).one()
        team.status = status.lower()
        s.add(team)
        s.flush()
        return _summary(team)
