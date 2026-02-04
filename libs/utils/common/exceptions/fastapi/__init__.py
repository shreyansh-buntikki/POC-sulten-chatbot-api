from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

from libs.utils.common.models.fastapi.responses import ErrorResponse


class AppException(HTTPException):
    """Base application exception."""

    def __init__(
        self,
        success: bool,
        message: str,
        code: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Dict[str, Any] | str | List[Any]] = None,
    ):
        self.success = success
        self.message = message
        self.code = code
        self.details = details
        super().__init__(status_code=status_code, detail=self.to_dict())

        print(
            f"🚨 Raising AppException: {self.__class__.__name__}, Status: {self.status_code}, Message: {self.message}"
        )

    def to_dict(self) -> dict:
        return ErrorResponse(
            success=self.success,
            message=self.message,
            code=self.code,
            details=self.details,
        ).model_dump()


class NotFoundError(AppException):
    """Resource not found."""

    def __init__(self, resource: str, resource_id: Any):
        super().__init__(
            success=False,
            message=f"{resource} with id {resource_id} not found",
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"resource": resource, "id": str(resource_id)},
        )


class ValidationError(AppException):
    """Validation error."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            success=False,
            message=message,
            code="VALIDATION_ERROR",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )
