import arrow
from fastapi import APIRouter, status

from apps.fastapi import logger
from libs.utils.config import ENVIRONMENT

index_route = APIRouter(prefix="/fastapi", tags=["Index"])


class IndexRoutes:

    @staticmethod
    @index_route.get("/")
    def root():
        logger.info("Index Route accessed ...")
        return {
            "success": True,
            "status_code": status.HTTP_200_OK,
            "message": "Server is up and running 🚀🚀🚀",
        }

    @staticmethod
    @index_route.get("/health-check")
    def health_check_route():
        logger.info("Health Check Route accessed ...")
        return {
            "success": True,
            "status_code": status.HTTP_200_OK,
            "message": "Server is up and running 🚀🚀🚀",
            "datetime": arrow.now().format("YYYY-MM-DD HH:mm"),
            "environment": ENVIRONMENT,
        }
