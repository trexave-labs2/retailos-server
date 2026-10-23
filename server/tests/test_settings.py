from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.user import User
from server.app.utils.security import verify_password

def _register_and_login(client, email="owner@example.com", password="secret123"):
    response = client.post("/auth/register", json={
        "username": "Store Owner", "email": email, "password": password
    })
    assert response.status_code == 201
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return login.json["data"]["user"]["store_id"]

def test_owner_can_update_store_name_and_persist(client):
    store_id = _register_and_login(client)
    response = client.patch("/settings/store", json={"store_id": store_id, "name": "  Noble Mart  "})
    assert response.status_code == 200
    assert response.json["data"]["store"]["name"] == "Noble Mart"
    assert Store.query.get(store_id).name == "Noble Mart"

def test_store_name_update_rejects_other_owners_store(client):
    first_store = _register_and_login(client, "first@example.com")
    original_name = Store.query.get(first_store).name
    client.post("/auth/logout")
    _register_and_login(client, "second@example.com")
    response = client.patch("/settings/store", json={"store_id": first_store, "name": "Stolen Store"})
    assert response.status_code == 403
    assert Store.query.get(first_store).name == original_name

def test_store_name_update_validates_name(client):
    store_id = _register_and_login(client)
    response = client.patch("/settings/store", json={"store_id": store_id, "name": "   "})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"

def test_password_change_verifies_current_password_and_invalidates_sessions(client):
    _register_and_login(client)
    response = client.post("/auth/change-password", json={
        "current_password": "wrong123", "new_password": "newsecret123"
    })
    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_INVALID_CURRENT_PASSWORD"

    response = client.post("/auth/change-password", json={
        "current_password": "secret123", "new_password": "newsecret123"
    })
    assert response.status_code == 200
    assert response.json["message"] == "PASSWORD_CHANGED_LOGIN_AGAIN"
    assert client.get("/auth/me").status_code == 401

    login_old = client.post("/auth/login", json={"email": "owner@example.com", "password": "secret123"})
    assert login_old.status_code == 401
    login_new = client.post("/auth/login", json={"email": "owner@example.com", "password": "newsecret123"})
    assert login_new.status_code == 200
    user = User.query.filter_by(email="owner@example.com").first()
    assert verify_password("newsecret123", user.password_hash)
    assert "password" not in login_new.get_data(as_text=True).lower()

def test_password_change_rejects_unchanged_password(client):
    _register_and_login(client)
    response = client.post("/auth/change-password", json={
        "current_password": "secret123", "new_password": "secret123"
    })
    assert response.status_code == 400
    assert response.json["error"]["code"] == "PASSWORD_UNCHANGED"
