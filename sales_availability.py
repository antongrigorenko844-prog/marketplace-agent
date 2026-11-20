"""Temporary sales holds applied to outgoing offers, separately from physical stock."""

# Remove the exact seller SKU after the owner releases the hold.
PAUSED_OFFER_IDS = frozenset({"0cw0am"})


def quantity_for_sale(offer_id, catalog_quantity):
    return 0 if offer_id in PAUSED_OFFER_IDS else catalog_quantity
