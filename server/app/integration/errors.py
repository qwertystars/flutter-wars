class BusinessError(Exception):
    code = "BUSINESS_ERROR"
    message = "The operation could not be completed."
    status_code = 409


class InsufficientCredits(BusinessError):
    code = "INSUFFICIENT_CREDITS"
    message = "Not enough available credits."


class InsufficientStock(BusinessError):
    code = "INSUFFICIENT_STOCK"
    message = "Not enough stock available."


class InvalidListing(BusinessError):
    code = "INVALID_LISTING"
    message = "Listing is unavailable for this operation."
    status_code = 404


class MarketNotOpen(BusinessError):
    code = "MARKET_NOT_OPEN"
    message = "The round does not allow this operation."


class IdempotencyConflict(BusinessError):
    code = "IDEMPOTENCY_CONFLICT"
    message = "Request key was already used with different inputs."


class InvalidPrice(BusinessError):
    code = "INVALID_PRICE"
    message = "An authoritative price is unavailable."
    status_code = 503


class AmountTooLarge(BusinessError):
    code = "AMOUNT_TOO_LARGE"
    message = "Amount exceeds the supported whole-credit range."
    status_code = 422


class ResaleNotAllowed(BusinessError):
    code = "RESALE_NOT_ALLOWED"
    message = "This listing does not permit resale."


class InsufficientInventory(BusinessError):
    code = "INSUFFICIENT_INVENTORY"
    message = "Not enough owned quantity."


class ConfigurationRequired(BusinessError):
    code = "CONFIGURATION_REQUIRED"
    message = "A required owner policy or integration is not configured."
    status_code = 503


class NotFound(BusinessError):
    code = "NOT_FOUND"
    message = "Resource not found."
    status_code = 404


class BidNotIncreasing(BusinessError):
    code = "BID_NOT_INCREASING"
    message = "The new bid must exceed your current bid."


class AuctionNotOpen(BusinessError):
    code = "AUCTION_NOT_OPEN"
    message = "The auction is not in the required bidding or settlement window."


class BidBelowMinimum(BusinessError):
    code = "BID_BELOW_MINIMUM"
    message = "Bid is below the configured minimum."
