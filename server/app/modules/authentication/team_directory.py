"""Module B's implementation of Module K's TeamDirectory port.

Module K manages teams through this port and never touches the `team` or
`team_membership` tables itself. Team status is stored lower-case by Module B
("active"/"disabled") and exposed upper-case ("ACTIVE"/"DISABLED") as the port expects.
"""

from uuid import UUID

from sqlmodel import Session, col, select

from app.modules.admin.ports import TeamSummary
from app.modules.authentication.model import Team, TeamMembership, UserIdentity


def _summary(team: Team) -> TeamSummary:
    return TeamSummary(id=team.id, name=team.name, status=team.status.upper())


class TeamDirectory:
    def list_teams(self, s: Session) -> list[TeamSummary]:
        return [_summary(t) for t in s.exec(select(Team).order_by(col(Team.name))).all()]

    def get_team(self, s: Session, team_id: UUID) -> TeamSummary | None:
        team = s.get(Team, team_id)
        return _summary(team) if team else None

    def find_by_name(self, s: Session, name: str) -> TeamSummary | None:
        team = s.exec(select(Team).where(Team.name == name)).first()
        return _summary(team) if team else None

    def create_team(self, s: Session, name: str, member_emails: list[str]) -> TeamSummary:
        """Create the team and pre-register its members by email.

        Each member binds their Google account on first login (Module B matches the
        verified email). An email already registered to another team is rejected.
        """
        from app.core.errors import AppError

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

    def set_status(self, s: Session, team_id: UUID, status: str) -> TeamSummary:
        team = s.exec(select(Team).where(Team.id == team_id).with_for_update()).one()
        team.status = status.lower()
        s.add(team)
        s.flush()
        return _summary(team)
