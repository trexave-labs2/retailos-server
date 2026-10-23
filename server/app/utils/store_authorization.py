from flask import session
from server.app.models.store import Store


def get_authorized_store(store_id):
    """Return a store only when the current session owns the supplied ID."""
    user_id = session.get("user_id")

    # bool is a subclass of int in Python, so reject it explicitly.
    # IDs must be positive integers; malformed identifiers never reach a query.
    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
        or not isinstance(store_id, int)
        or isinstance(store_id, bool)
        or store_id <= 0
    ):
        return None

    return Store.query.filter_by(id=store_id, user_id=user_id).first()
