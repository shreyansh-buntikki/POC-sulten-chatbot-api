"""
Chat Routes - API endpoints for chatbot operations
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.fastapi import logger
from database import get_db
from apps.fastapi.src.services.chat_service import ChatService

chat_route = APIRouter(prefix="/chat", tags=["Chat"])


# =====================================================
# Request/Response Models
# =====================================================

class MessageRequest(BaseModel):
    """Request model for sending a message"""
    session_id: Optional[str] = Field(None, description="Continue existing session (omit for new session)")
    user_uid: Optional[str] = Field(None, description="Optional user identifier")
    message: str = Field(..., min_length=1, description="User's message content")

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": None,
                "user_uid": "user-123",
                "message": "I need a quick chicken recipe for dinner"
            }
        }


class MessageResponse(BaseModel):
    """Response model for message exchange"""
    session_id: str
    user_message: dict
    assistant_message: dict
    metadata: dict


class SessionResponse(BaseModel):
    """Response model for session details"""
    session_id: str
    user_uid: Optional[str]
    title: str
    created_at: Optional[str]
    updated_at: Optional[str]
    is_active: bool


class SessionHistoryResponse(BaseModel):
    """Response model for session history"""
    session: dict
    messages: List[dict]


class ErrorResponse(BaseModel):
    """Error response model"""
    error: str
    code: str
    details: Optional[str] = None


# =====================================================
# Routes
# =====================================================

class ChatRoutes:
    """Route handlers for chat operations"""

    @staticmethod
    @chat_route.post(
        "/message",
        status_code=status.HTTP_200_OK,
        response_model=MessageResponse,
        summary="Send a message",
        description="Send a message to the chatbot and receive a response. Creates a new session if session_id is not provided."
    )
    async def send_message(
        request: MessageRequest,
        db: Session = Depends(get_db)
    ):
        """
        Send a message to the chatbot

        - If `session_id` is provided, continues the existing conversation
        - If `session_id` is omitted, creates a new session
        - Maintains context from the last 10 messages
        - Routes through appropriate AI agent based on intent
        """
        logger.info(f"Message received - session: {request.session_id}, user: {request.user_uid}")

        try:
            chat_service = ChatService(db)

            result = await chat_service.send_message(
                session_id=request.session_id,
                user_uid=request.user_uid,
                message=request.message
            )

            if "error" in result:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result
                )

            return result

        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Chatbot configuration error", "code": "CONFIG_ERROR"}
            )
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Failed to process message", "code": "PROCESSING_ERROR"}
            )

    # @staticmethod
    # @chat_route.get(
    #     "/sessions/{session_id}",
    #     status_code=status.HTTP_200_OK,
    #     response_model=SessionResponse,
    #     summary="Get session details",
    #     description="Retrieve details of a specific chat session"
    # )
    # def get_session(
    #     session_id: str,
    #     db: Session = Depends(get_db)
    # ):
    #     """Get details of a specific chat session"""
    #     chat_service = ChatService(db)
    #     session = chat_service.get_session(session_id)
    #
    #     if not session:
    #         raise HTTPException(
    #             status_code=status.HTTP_404_NOT_FOUND,
    #             detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
    #         )
    #
    #     return session

    # @staticmethod
    # @chat_route.get(
    #     "/sessions/{session_id}/history",
    #     status_code=status.HTTP_200_OK,
    #     response_model=SessionHistoryResponse,
    #     summary="Get session history",
    #     description="Retrieve full message history for a specific session"
    # )
    # def get_session_history(
    #     session_id: str,
    #     limit: int = Query(100, ge=1, le=1000, description="Maximum number of messages"),
    #     db: Session = Depends(get_db)
    # ):
    #     """Get full message history for a session"""
    #     chat_service = ChatService(db)
    #     history = chat_service.get_session_history(session_id, limit)
    #
    #     if not history:
    #         raise HTTPException(
    #             status_code=status.HTTP_404_NOT_FOUND,
    #             detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
    #         )
    #
    #     return history

    @staticmethod
    @chat_route.get(
        "/sessions/user/{user_uid}",
        status_code=status.HTTP_200_OK,
        summary="Get user sessions",
        description="Retrieve all chat sessions for a specific user"
    )
    def get_user_sessions(
        user_uid: str,
        limit: int = Query(50, ge=1, le=100, description="Maximum number of sessions"),
        db: Session = Depends(get_db)
    ):
        """Get all chat sessions for a user"""
        chat_service = ChatService(db)
        sessions = chat_service.get_user_sessions(user_uid, limit)

        return {
            "success": True,
            "user_uid": user_uid,
            "sessions": sessions,
            "count": len(sessions)
        }

    # @staticmethod
    # @chat_route.delete(
    #     "/sessions/{session_id}",
    #     status_code=status.HTTP_200_OK,
    #     summary="Delete session",
    #     description="Delete a chat session and all its messages"
    # )
    # def delete_session(
    #     session_id: str,
    #     db: Session = Depends(get_db)
    # ):
    #     """Delete a chat session"""
    #     chat_service = ChatService(db)
    #     success = chat_service.delete_session(session_id)
    #
    #     if not success:
    #         raise HTTPException(
    #             status_code=status.HTTP_404_NOT_FOUND,
    #             detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
    #         )
    #
    #     return {
    #         "success": True,
    #         "message": "Session deleted successfully",
    #         "session_id": session_id
    #     }
    #
    # @staticmethod
    # @chat_route.patch(
    #     "/sessions/{session_id}/title",
    #     status_code=status.HTTP_200_OK,
    #     summary="Update session title",
    #     description="Update the title of a chat session"
    # )
    # def update_session_title(
    #     session_id: str,
    #     title: str = Query(..., min_length=1, max_length=255, description="New session title"),
    #     db: Session = Depends(get_db)
    # ):
    #     """Update session title"""
    #     chat_service = ChatService(db)
    #     success = chat_service.update_session_title(session_id, title)
    #
    #     if not success:
    #         raise HTTPException(
    #             status_code=status.HTTP_404_NOT_FOUND,
    #             detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
    #         )
    #
    #     return {
    #         "success": True,
    #         "message": "Session title updated",
    #         "session_id": session_id,
    #         "title": title
    #     }
