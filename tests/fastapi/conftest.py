import logging

import pytest
from fastapi.testclient import TestClient

from apps.fastapi.app import app


@pytest.fixture()
def client():
    with TestClient(app=app) as client:
        yield client


@pytest.fixture(autouse=True)
def suppress_httpx_logs():

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def get_logger():

    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    return logger
