from flask import session
from server.app.models.store import Store
from server.app.models.user import User


def get_authorized_store(store_id):
    """Return an active store only when the current active account owns it."""
    user_id = session.get("user_id")

    # bool is a subclass of int in Python, so reject it explicitly.
    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
        or not isinstance(store_id, int)
        or isinstance(store_id, bool)
        or store_id <= 0
    ):
        return None

    return (
        Store.query.join(User, Store.user_id == User.id)
        .filter(
            Store.id == store_id,
            Store.user_id == user_id,
            Store.status == "active",
            User.account_status == "active",
        )
        .first()
    )
