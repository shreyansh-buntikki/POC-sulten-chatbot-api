from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    success: bool
    code: str
    message: str
    details: Optional[Dict[str, Any] | str | List[Any]] = None


class SuccessResponse(BaseModel):
    success: bool
    code: str
    message: Optional[Dict[str, Any] | str | List[Any]] = None
