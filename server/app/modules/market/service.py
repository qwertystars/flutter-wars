"""Market & Round Lifecycle service (Module G).

Round state machine (organizer actions):

    draft --open--> open --pause--> paused --open--> open
    open/paused --close--> closed --finalize--> finalized

Transitions use optimistic concurrency on market_round.version, and the
database allows at most one open-or-paused round per market.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select, update

from app.core.errors import AppError, Conflict, NotFound
from app.modules.catalog.service import get_widget
from app.modules.market import repository as repo
from app.modules.market.models import (
    LIVE_STATUSES,
    Market,
    MarketListing,
    MarketRound,
    MarketRoundEvent,
    RoundKind,
    RoundStatus,
)
from app.modules.pricing import service as pricing

Supply = int | Literal["infinite"]
Action = Literal["open", "pause", "close", "finalize"]

TRANSITIONS: dict[Action, tuple[frozenset[RoundStatus], RoundStatus]] = {
    "open": (frozenset({RoundStatus.DRAFT, RoundStatus.PAUSED}), RoundStatus.OPEN),
    "pause": (frozenset({RoundStatus.OPEN}), RoundStatus.PAUSED),
    "close": (frozenset({RoundStatus.OPEN, RoundStatus.PAUSED}), RoundStatus.CLOSED),
    "finalize": (frozenset({RoundStatus.CLOSED}), RoundStatus.FINALIZED),
}


@dataclass(frozen=True)
class ListingSpec:
    widget_id: int
    base_price: int
    supply: Supply
    max_per_purchase: int | None = None
    pricing_strategy: str = "static"
    pricing_params: dict[str, Any] | None = None


# --- markets ---


def create_market(session: Session, name: str) -> Market:
    if repo.active_market(session) is not None:
        raise Conflict("MARKET_ALREADY_ACTIVE", "An active market already exists.")
    market = Market(name=name)
    session.add(market)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict("MARKET_ALREADY_ACTIVE", "An active market already exists.") from None
    return market


def require_active_market(session: Session) -> Market:
    market = repo.active_market(session)
    if market is None:
        raise NotFound("NO_ACTIVE_MARKET", "No market has been configured.")
    return market


# --- rounds ---


def create_round(
    session: Session,
    *,
    name: str,
    kind: RoundKind,
    listings: list[ListingSpec],
    scheduled_open_at: datetime | None = None,
    scheduled_close_at: datetime | None = None,
) -> MarketRound:
    market = require_active_market(session)
    if scheduled_open_at and scheduled_close_at and scheduled_close_at <= scheduled_open_at:
        raise AppError("INVALID_SCHEDULE", "scheduled_close_at must be after scheduled_open_at.")
    sequence = (
        session.exec(
            select(func.max(MarketRound.sequence)).where(MarketRound.market_id == market.id),
        ).one()
        or 0
    ) + 1
    rnd = MarketRound(
        market_id=market.id,
        sequence=sequence,
        name=name,
        kind=kind,
        scheduled_open_at=scheduled_open_at,
        scheduled_close_at=scheduled_close_at,
    )
    session.add(rnd)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict(
            "ROUND_CREATE_CONFLICT", "Another round was created at the same time; retry."
        ) from None
    for spec in listings:
        add_listing(session, rnd.id, spec)
    return rnd


def get_round(session: Session, round_id: int) -> MarketRound:
    rnd = session.get(MarketRound, round_id)
    if rnd is None:
        raise NotFound("ROUND_NOT_FOUND", "Round not found.", round_id=round_id)
    return rnd


def lock_round(session: Session, round_id: int, *, exclusive: bool = False) -> MarketRound:
    """Lock the round row (FOR SHARE, or FOR UPDATE if exclusive) and return
    it freshly read, never a stale copy from the session's identity map.

    Lock order for everything touching a round: round -> listing_pricing ->
    market_listing -> other modules' rows. Trades and live pricing edits take
    the shared lock; lifecycle transitions and draft edits take the exclusive
    one, so draft edits are serialised with each other and with opening."""
    rnd = session.exec(
        select(MarketRound)
        .where(MarketRound.id == round_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True),
    ).one_or_none()
    if rnd is None:
        raise NotFound("ROUND_NOT_FOUND", "Round not found.", round_id=round_id)
    return rnd


def current_round(session: Session) -> MarketRound | None:
    """The round participants should see: the live one, else the latest one that ran."""
    market = repo.active_market(session)
    if market is None:
        return None
    return repo.live_round(session, market.id) or repo.latest_started_round(session, market.id)


def transition(
    session: Session,
    round_id: int,
    action: Action,
    *,
    now: datetime,
    actor: str,
    reason: str | None = None,
    expected_version: int | None = None,
) -> MarketRound:
    # Waits for in-flight trades and draft edits (which hold the row FOR SHARE),
    # so every check below sees their committed effects.
    rnd = lock_round(session, round_id, exclusive=True)
    # The UPDATE below synchronises `rnd` in the session, so keep the old values.
    from_status, from_version = RoundStatus(rnd.status), rnd.version
    # A stale view is reported as such, even if the action is also invalid now.
    if expected_version is not None and expected_version != from_version:
        raise Conflict(
            "ROUND_VERSION_CONFLICT",
            "The round was changed by someone else.",
            version=from_version,
            status=from_status,
        )
    allowed, target = TRANSITIONS[action]
    if from_status not in allowed:
        raise Conflict(
            "INVALID_ROUND_TRANSITION",
            f"Cannot {action} a round that is {from_status}.",
            action=action,
            status=from_status,
        )

    first_open = action == "open" and from_status == RoundStatus.DRAFT
    listing_ids = repo.listing_ids(session, rnd.id)
    if target in LIVE_STATUSES and from_status not in LIVE_STATUSES:
        live = repo.live_round(session, rnd.market_id)
        if live is not None:
            raise Conflict(
                "ANOTHER_ROUND_LIVE",
                "Another round in this market is already open or paused.",
                round_id=live.id,
            )
    if first_open:
        if not listing_ids:
            raise Conflict(
                "ROUND_HAS_NO_LISTINGS", "A round needs at least one listing before it can open."
            )
        archived = repo.archived_widget_ids(session, rnd.id)
        if archived:
            raise Conflict(
                "ROUND_HAS_ARCHIVED_WIDGETS",
                "Remove archived widgets before opening.",
                widget_ids=archived,
            )

    values: dict[str, Any] = {"status": target, "version": from_version + 1}
    if first_open:
        values["opened_at"] = now
    if action == "pause":
        values["paused_at"] = now
    elif action == "open":
        values["paused_at"] = None
    elif action == "close":
        # A trade that committed while we waited for the lock may carry a later
        # timestamp than this request; the close must not predate it.
        latest_trade = pricing.latest_activity(session, listing_ids)
        values["closed_at"] = max(now, latest_trade) if latest_trade else now
    elif action == "finalize":
        values["finalized_at"] = now

    statement = (
        update(MarketRound)
        .where(col(MarketRound.id) == rnd.id, col(MarketRound.version) == from_version)
        .values(**values)
    )
    try:
        result = session.exec(statement)  # type: ignore[call-overload]
    except IntegrityError:
        # Lost a race against another organizer opening a different round.
        raise Conflict(
            "ANOTHER_ROUND_LIVE", "Another round in this market is already open or paused."
        ) from None
    if result.rowcount != 1:
        raise Conflict(
            "ROUND_VERSION_CONFLICT", "The round was changed by someone else; reload and retry."
        )

    session.add(
        MarketRoundEvent(
            round_id=rnd.id,
            action=action,
            from_status=from_status,
            to_status=target,
            actor=actor,
            reason=reason,
        )
    )
    if first_open:
        pricing.on_round_opened(session, listing_ids, now)
    session.flush()
    session.refresh(rnd)
    return rnd


def round_events(session: Session, round_id: int) -> list[MarketRoundEvent]:
    get_round(session, round_id)
    return list(
        session.exec(
            select(MarketRoundEvent)
            .where(MarketRoundEvent.round_id == round_id)
            .order_by(col(MarketRoundEvent.id)),
        )
    )


# --- listings (editable only while the round is a draft) ---


def _require_draft(rnd: MarketRound) -> None:
    if rnd.status != RoundStatus.DRAFT:
        raise Conflict(
            "ROUND_NOT_EDITABLE",
            "Listings can only be changed while the round is a draft.",
            status=rnd.status,
        )


def _supply_columns(supply: Supply) -> tuple[int | None, int | None]:
    if supply == "infinite":
        return None, None
    if supply < 0:
        raise AppError("INVALID_SUPPLY", 'Supply must be zero or more, or "infinite".')
    return supply, supply


def _check_widget(session: Session, widget_id: int) -> None:
    widget = get_widget(session, widget_id)
    if widget is None:
        raise NotFound("WIDGET_NOT_FOUND", "Widget not found.", widget_id=widget_id)
    if widget.archived:
        raise Conflict("WIDGET_ARCHIVED", "Archived widgets cannot be listed.", widget_id=widget_id)


def _fresh_listing(session: Session, listing_id: int) -> MarketListing:
    """Re-read a listing after taking a round lock; it may have been deleted meanwhile."""
    listing = session.exec(
        select(MarketListing)
        .where(MarketListing.id == listing_id)
        .execution_options(populate_existing=True),
    ).one_or_none()
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    return listing


def _lock_draft_listing(session: Session, listing_id: int) -> MarketListing:
    _require_draft(lock_round(session, get_listing(session, listing_id).round_id, exclusive=True))
    return _fresh_listing(session, listing_id)


def add_listing(session: Session, round_id: int, spec: ListingSpec) -> MarketListing:
    rnd = lock_round(session, round_id, exclusive=True)
    _require_draft(rnd)
    _check_widget(session, spec.widget_id)
    if spec.base_price < 1:
        raise AppError("INVALID_PRICE", "base_price must be at least 1.")
    if repo.listing_for_widget(session, rnd.id, spec.widget_id) is not None:
        raise Conflict(
            "DUPLICATE_LISTING",
            "This widget is already listed in the round.",
            widget_id=spec.widget_id,
        )
    supply_total, stock = _supply_columns(spec.supply)
    listing = MarketListing(
        round_id=rnd.id,
        widget_id=spec.widget_id,
        base_price=spec.base_price,
        supply_total=supply_total,
        stock_remaining=stock,
        max_per_purchase=spec.max_per_purchase,
    )
    session.add(listing)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict(
            "DUPLICATE_LISTING",
            "This widget is already listed in the round.",
            widget_id=spec.widget_id,
        ) from None
    pricing.configure_listing(session, listing, spec.pricing_strategy, spec.pricing_params or {})
    return listing


_UNSET: Any = object()


def update_listing(
    session: Session,
    listing_id: int,
    *,
    base_price: int = _UNSET,
    supply: Supply = _UNSET,
    max_per_purchase: int | None = _UNSET,
    pricing_strategy: str = _UNSET,
    pricing_params: dict[str, Any] = _UNSET,
) -> MarketListing:
    listing = _lock_draft_listing(session, listing_id)
    if base_price is not _UNSET:
        if base_price < 1:
            raise AppError("INVALID_PRICE", "base_price must be at least 1.")
        listing.base_price = base_price
    if supply is not _UNSET:
        listing.supply_total, listing.stock_remaining = _supply_columns(supply)
    if max_per_purchase is not _UNSET:
        listing.max_per_purchase = max_per_purchase
    session.add(listing)
    current = pricing.get_config(session, listing.id)
    strategy, params = current.strategy, current.params
    if pricing_strategy is not _UNSET and pricing_strategy != strategy:
        strategy, params = pricing_strategy, {}  # old parameters belong to the old strategy
    if pricing_params is not _UNSET:
        params = pricing_params
    pricing.configure_listing(session, listing, strategy, params)
    return listing


def delete_listing(session: Session, listing_id: int) -> None:
    listing = _lock_draft_listing(session, listing_id)
    pricing.remove_listing(session, listing.id)
    session.flush()  # no ORM relationship, so order the deletes by hand
    session.delete(listing)


def get_listing(session: Session, listing_id: int) -> MarketListing:
    listing = session.get(MarketListing, listing_id)
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    return listing


def get_visible_listing(session: Session, listing_id: int) -> MarketListing:
    """A listing participants may see: anything outside draft rounds."""
    listing = session.get(MarketListing, listing_id)
    if listing is not None and get_round(session, listing.round_id).status != RoundStatus.DRAFT:
        return listing
    raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)


def listings_for_round(session: Session, round_id: int) -> list[MarketListing]:
    return repo.listings(session, round_id)


# --- internal contract for Transaction (I) and Auction (J) ---


@dataclass(frozen=True)
class TradableListing:
    listing_id: int
    round_id: int
    widget_id: int
    round_kind: RoundKind
    base_price: int
    infinite_supply: bool
    stock_remaining: int | None
    max_per_purchase: int | None


def lock_listing_for_trade(
    session: Session, listing_id: int, *, kind: RoundKind
) -> TradableListing:
    """Check, inside the caller's transaction, that a listing can be traded
    right now. Holds a shared lock on the round row until the transaction
    ends, so the round cannot be paused/closed under an in-flight trade.
    Call this first, before any pricing/ledger/inventory work."""
    listing = session.get(MarketListing, listing_id)
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    rnd = lock_round(session, listing.round_id)
    if rnd.status == RoundStatus.DRAFT:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    if rnd.status != RoundStatus.OPEN:
        raise Conflict("ROUND_NOT_OPEN", "Trading is not open for this round.", status=rnd.status)
    # Re-read under the lock (listings are frozen once open). Stock is a
    # snapshot only; take_stock is the authoritative check.
    listing = _fresh_listing(session, listing_id)
    if rnd.kind != kind:
        raise Conflict(
            "WRONG_ROUND_KIND", f"This listing belongs to a {rnd.kind} round.", kind=rnd.kind
        )
    return TradableListing(
        listing.id,
        rnd.id,
        listing.widget_id,
        RoundKind(rnd.kind),
        listing.base_price,
        listing.infinite_supply,
        listing.stock_remaining,
        listing.max_per_purchase,
    )


def take_stock(session: Session, listing_id: int, quantity: int) -> int | None:
    """Atomically remove `quantity` units. Returns the remaining stock (None if infinite)."""
    listing = get_listing(session, listing_id)
    if quantity < 1:
        raise AppError("INVALID_QUANTITY", "Quantity must be at least 1.", quantity=quantity)
    if listing.max_per_purchase is not None and quantity > listing.max_per_purchase:
        raise AppError(
            "QUANTITY_LIMIT_EXCEEDED",
            "Quantity is above the per-purchase limit.",
            max_per_purchase=listing.max_per_purchase,
        )
    stock = col(MarketListing.stock_remaining)
    result = session.exec(  # type: ignore[call-overload]
        update(MarketListing)
        .where(col(MarketListing.id) == listing_id, sa.or_(stock.is_(None), stock >= quantity))
        .values(stock_remaining=stock - quantity)
        .returning(stock),
    ).one_or_none()
    if result is None:
        session.refresh(listing)
        raise Conflict("OUT_OF_STOCK", "Not enough stock left.", available=listing.stock_remaining)
    session.expire(listing)
    return result[0]


def return_stock(session: Session, listing_id: int, quantity: int) -> int | None:
    """Put units back (resale). Infinite listings stay infinite."""
    get_listing(session, listing_id)
    if quantity < 1:
        raise AppError("INVALID_QUANTITY", "Quantity must be at least 1.", quantity=quantity)
    stock = col(MarketListing.stock_remaining)
    result = session.exec(  # type: ignore[call-overload]
        update(MarketListing)
        .where(col(MarketListing.id) == listing_id)
        .values(stock_remaining=stock + quantity)
        .returning(stock),
    ).one()
    session.expire_all()
    return result[0]
