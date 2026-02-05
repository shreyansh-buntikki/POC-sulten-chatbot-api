import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic
from starlette.middleware.cors import CORSMiddleware

BASE_DIR = str(Path(__file__).resolve().parent.parent.parent)
sys.path.append(BASE_DIR)


from apps.fastapi import logger
from apps.fastapi.src.routes.index import index_route
from apps.fastapi.src.routes.users import users_route
from libs.services.fastapi.openapi_customization import custom_openapi
from libs.utils.common.exceptions.fastapi import AppException
from libs.utils.common.models.fastapi.responses import ErrorResponse
from libs.utils.config import FASTAPI_PORT, HOST

security = HTTPBasic(auto_error=False)

app = FastAPI(
    title="Python FASTAPI Jumpstart",
    version="0.0.1",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[],
    swagger_ui_parameters={"persistAuthorization": True},
)

app.openapi = lambda: custom_openapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    print(
        f"⚠️ AppException Caught: {exc.__class__.__name__}, Status: {exc.status_code}, Message: {exc.message}"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(
        f"🚨 Unhandled Exception: {exc.__class__.__name__}, Message: {str(exc)}"
    )
    return JSONResponse(
        status_code=500,
        content=(
            ErrorResponse(
                success=False,
                code="SERVER_ERROR",
                message="Internal Server Error",
                details=str(exc),
            )
        ).model_dump(),
    )


@app.get("/")
def root():
    logger.info("Fastapi Framework accessed ...")
    return {
        "success": True,
        "message": "FastAPI Server is up and running 🚀🚀🚀",
    }


app.include_router(index_route)
app.include_router(users_route)


if __name__ == "__main__":
    uvicorn.run("app:app", host=HOST, port=FASTAPI_PORT, reload=False)
