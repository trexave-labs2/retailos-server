def test_health_endpoint_is_public_and_lightweight(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json["success"] is True
    assert response.json["message"] == "HEALTHY"
    assert response.json["data"]["status"] == "ok"
    assert response.json["data"]["ready"] is True
