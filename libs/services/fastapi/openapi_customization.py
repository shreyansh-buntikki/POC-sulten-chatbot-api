from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def custom_openapi(app: FastAPI):
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Ensure "components" exists
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    # Add security schemes safely
    openapi_schema["components"].setdefault("securitySchemes", {})[
        "basicAuth"
    ] = {"type": "http", "scheme": "basic"}

    # Apply security globally
    openapi_schema["security"] = [{"basicAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema
