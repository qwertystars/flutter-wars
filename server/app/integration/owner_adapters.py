"""Ledger (E), Inventory (F) and Catalog (D) behind the I/J ports.

Each adapter calls only the owning module's service, inside the caller's session
and transaction: none of them commits, rolls back or opens a session. Owner errors
are translated to the I/J port errors that Trading and Auction document.

References written to the owners' tables:
  ledger    trade/<trade_id> (DEBIT purchase, CREDIT resale),
            auction_bid/<bid_id> (hold), auction/<auction_id> (CAPTURE)
  inventory marketplace/<trade_id or auction_id>
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

from sqlmodel import Session

from app.core.errors import AppError
from app.integration.errors import (
    ConfigurationRequired,
    InsufficientCredits,
    InsufficientInventory,
    InvalidListing,
)
from app.modules.catalog import service as catalog
from app.modules.inventory import service as inventory
from app.modules.ledger import service as ledger

ACTOR = "system:marketplace"
_PORT_ERRORS = {
    "INSUFFICIENT_CREDITS": InsufficientCredits,
    "WALLET_NOT_FOUND": InsufficientCredits,
    "INSUFFICIENT_QUANTITY": InsufficientInventory,
    "WIDGET_NOT_FOUND": InvalidListing,
    "WIDGET_ARCHIVED": InvalidListing,
}


@contextmanager
def _port_errors() -> Iterator[None]:
    try:
        yield
    except AppError as exc:
        port_error = _PORT_ERRORS.get(exc.code)
        if port_error is None or isinstance(exc, port_error):
            raise
        raise port_error() from None


class LedgerAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_accounts(self, *, team_ids: Sequence[UUID]) -> None:
        with _port_errors():
            ledger.lock_wallets(self.session, list(team_ids))

    def debit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None:
        if amount == 0:
            return
        with _port_errors():
            ledger.debit(
                self.session, team_id, amount,
                ref_type="trade", ref_id=str(trade_id), reason="Market purchase", actor=ACTOR,
            )

    def credit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None:
        if amount == 0:
            return
        with _port_errors():
            ledger.credit(
                self.session, team_id, amount,
                ref_type="trade", ref_id=str(trade_id), reason="Market resale", actor=ACTOR,
            )

    def _hold(self, team_id: UUID, reservation_id: UUID):
        return ledger.get_active_reservation(
            self.session, team_id, ref_type="auction_bid", ref_id=str(reservation_id)
        )

    def reserve(self, *, team_id: UUID, reservation_id: UUID, additional_amount: int) -> None:
        """Raise the bid's hold by `additional_amount` (a hold is replaced, never edited)."""
        if additional_amount == 0:
            return  # a zero first bid holds nothing
        with _port_errors():
            current = self._hold(team_id, reservation_id)
            total = additional_amount
            if current is not None:
                total += current.amount
                ledger.release(self.session, current.id)
            ledger.reserve(
                self.session, team_id, total, ref_type="auction_bid", ref_id=str(reservation_id)
            )

    def release(self, *, team_id: UUID, reservation_id: UUID) -> None:
        with _port_errors():
            current = self._hold(team_id, reservation_id)
            if current is not None:
                ledger.release(self.session, current.id)

    def settle(self, *, team_id: UUID, reservation_id: UUID, amount: int, reference: UUID) -> None:
        with _port_errors():
            current = self._hold(team_id, reservation_id)
            held = current.amount if current is not None else 0
            if held != amount:
                raise ConfigurationRequired("Auction hold does not match the winning bid.")
            if current is None:
                return  # a zero winning bid has nothing to capture
            ledger.capture(
                self.session, current.id,
                ref_type="auction", ref_id=str(reference), reason="Auction won", actor=ACTOR,
            )


class InventoryAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, *, team_id: UUID, widget_id: str, quantity: int, reference: UUID) -> None:
        with _port_errors():
            inventory.increment(
                self.session, team_id, widget_id, quantity,
                ref_type="marketplace", ref_id=str(reference),
                reason="Marketplace award", actor=ACTOR,
            )

    def remove(self, *, team_id: UUID, widget_id: str, quantity: int, reference: UUID) -> None:
        with _port_errors():
            inventory.decrement(
                self.session, team_id, widget_id, quantity,
                ref_type="marketplace", ref_id=str(reference),
                reason="Market resale", actor=ACTOR,
            )


class CatalogAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def validate_widget(self, *, widget_id: str, operation: Literal["buy", "sell", "award"]) -> None:
        """Buying and awarding need an active widget; resale accepts an archived one."""
        with _port_errors():
            if operation == "sell":
                catalog.get_widget(self.session, widget_id)
            else:
                catalog.require_active_widget(self.session, widget_id)
