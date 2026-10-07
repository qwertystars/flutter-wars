from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    """Foundation/Auth supplies this AFTER JWT and current eligibility verification."""

    team_id: UUID | None
    organizer: bool = False
