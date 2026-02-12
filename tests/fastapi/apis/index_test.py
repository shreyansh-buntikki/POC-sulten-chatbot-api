def test_server_is_up_and_running(client, get_logger):
    response = client.get("/")
    get_logger.info(f"response received - {response.json()}")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_fastapi_server_is_up_and_running(client, get_logger):
    response = client.get("/fastapi")
    get_logger.info(f"response received - {response.json()}")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_fastapi_health_check_route(client, get_logger):
    response = client.get("/fastapi/health-check")
    get_logger.info(f"response received - {response.json()}")
    assert response.status_code == 200
    assert response.json()["success"] is True
