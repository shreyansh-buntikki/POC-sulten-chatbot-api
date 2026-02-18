"""
Chat Routes - API endpoints for chatbot operations
"""
import hashlib
import json
import time
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.fastapi import logger
from database import get_db
from apps.fastapi.src.services.chat_service import ChatService

chat_route = APIRouter(prefix="/chat", tags=["Chat"])


# =====================================================
# Response Cache for repeated queries
# =====================================================
# Cache format: {cache_key: {"response": ..., "timestamp": ...}}
_RESPONSE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL = 300  # 5 minutes cache TTL


def _get_cache_key(message: str, user_uid: Optional[str], language: Optional[str]) -> str:
    """Generate cache key from query parameters"""
    cache_data = {
        "message": message.lower().strip(),
        "user": user_uid or "anonymous",
        "language": language or "en"
    }
    return hashlib.md5(json.dumps(cache_data, sort_keys=True).encode()).hexdigest()


def _get_cached_response(cache_key: str) -> Optional[Dict[str, Any]]:
    """Get cached response if valid"""
    if cache_key in _RESPONSE_CACHE:
        cached = _RESPONSE_CACHE[cache_key]
        if time.time() - cached["timestamp"] < _CACHE_TTL:
            logger.info(f"[CACHE HIT] Returning cached response for key: {cache_key[:8]}...")
            return cached["response"]
        else:
            # Expired, remove from cache
            del _RESPONSE_CACHE[cache_key]
    return None


def _set_cached_response(cache_key: str, response: Dict[str, Any]) -> None:
    """Cache a response"""
    _RESPONSE_CACHE[cache_key] = {
        "response": response,
        "timestamp": time.time()
    }
    # Clean old cache entries if cache is too large
    if len(_RESPONSE_CACHE) > 1000:
        oldest_keys = sorted(_RESPONSE_CACHE.keys(), key=lambda k: _RESPONSE_CACHE[k]["timestamp"])[:100]
        for key in oldest_keys:
            del _RESPONSE_CACHE[key]


# =====================================================
# Request/Response Models
# =====================================================

class MessageRequest(BaseModel):
    """Request model for sending a message"""
    session_id: Optional[str] = Field(None, description="Continue existing session (omit for new session)")
    user_uid: Optional[str] = Field(None, description="Optional user identifier")
    message: str = Field(..., min_length=1, description="User's message content")
    new_session: bool = Field(False, description="Force creation of a new session (use when 'Start New Chat' is clicked)")
    custom_prompts: Optional[Dict[str, str]] = Field(None, description="Optional custom prompts for agents (key: agent_key, value: prompt text)")

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": None,
                "user_uid": "user-123",
                "message": "I need a quick chicken recipe for dinner",
                "new_session": False,
                "custom_prompts": {"nlg_agent": "Custom prompt text..."}
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
        language: Optional[str] = Header(None, description="Language code (e.g., 'en', 'no') for filtering recipes"),
        db: Session = Depends(get_db)
    ):
        """
        Send a message to the chatbot

        - If `session_id` is provided, continues the existing conversation
        - If `session_id` is omitted and `new_session` is False, continues the user's most recent active session
        - If `session_id` is omitted and `new_session` is True, creates a new session (use for "Start New Chat")
        - Maintains context from the last 10 messages
        - Routes through appropriate AI agent based on intent
        - Language header filters recipes by languageId
        - **Response caching**: Repeated queries return instantly from cache (5 min TTL)
        """
        logger.info(f"Message received - session: {request.session_id}, user: {request.user_uid}, new_session: {request.new_session}, language: {language}")

        # Log custom prompts if provided
        if request.custom_prompts:
            logger.info(f"[CUSTOM PROMPTS] Received {len(request.custom_prompts)} custom prompt(s) from frontend")
            for agent_key in request.custom_prompts.keys():
                logger.info(f"[CUSTOM PROMPTS] - Agent: {agent_key}")

        # Check cache for identical queries (only for truly new sessions)
        if not request.session_id and request.new_session:
            cache_key = _get_cache_key(request.message, request.user_uid, language)
            cached_response = _get_cached_response(cache_key)
            if cached_response:
                return cached_response

        try:
            chat_service = ChatService(db)

            result = await chat_service.send_message(
                session_id=request.session_id,
                user_uid=request.user_uid,
                message=request.message,
                language=language,
                new_session=request.new_session,
                custom_prompts=request.custom_prompts
            )

            if "error" in result:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result
                )

            # Cache successful responses for new sessions only
            if not request.session_id and request.new_session and "response" in result:
                cache_key = _get_cache_key(request.message, request.user_uid, language)
                _set_cached_response(cache_key, result)

            return result

        except ValueError as e:
            # Check if this is a user not found error
            if "does not exist" in str(e):
                logger.error(f"User not found: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": str(e), "code": "USER_NOT_FOUND"}
                )
            # Other configuration errors
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

    @staticmethod
    @chat_route.get(
        "/sessions/{session_id}",
        status_code=status.HTTP_200_OK,
        response_model=SessionResponse,
        summary="Get session details",
        description="Retrieve details of a specific chat session"
    )
    def get_session(
        session_id: str,
        db: Session = Depends(get_db)
    ):
        """Get details of a specific chat session"""
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return session

    @staticmethod
    @chat_route.get(
        "/sessions/{session_id}/history",
        status_code=status.HTTP_200_OK,
        summary="Get session history",
        description="Retrieve full message history for a specific session"
    )
    def get_session_history(
        session_id: str,
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of messages"),
        db: Session = Depends(get_db)
    ):
        """Get full message history for a session"""
        chat_service = ChatService(db)
        history = chat_service.get_session_history(session_id, limit)

        if not history:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return history

    @staticmethod
    @chat_route.get(
        "/sessions/user/{user_uid}",
        status_code=status.HTTP_200_OK,
        summary="Get user's most recent session",
        description="Retrieve the session with the most recent user conversation. Returns the session that had the last user message, with all its conversations. The response includes 'active_session_id' at the top level for easy access - use this session_id for subsequent queries to continue the conversation. Note: Maximum 5 sessions are maintained per user."
    )
    def get_user_sessions(
        user_uid: str,
        limit: int = Query(50, ge=1, le=100, description="Maximum number of conversations to return"),
        db: Session = Depends(get_db)
    ):
        """Get the most recently active session for a user with all its conversations"""
        try:
            chat_service = ChatService(db)
            sessions = chat_service.get_user_sessions(user_uid, limit)

            # Extract the active session_id for easier frontend access
            active_session_id = sessions[0]["session_id"] if sessions and len(sessions) > 0 else None

            return {
                "success": True,
                "user_uid": user_uid,
                "active_session_id": active_session_id,  # Session ID to use for next queries
                "sessions": sessions,
                "count": len(sessions)
            }
        except ValueError as e:
            # Check if this is a user not found error
            if "does not exist" in str(e):
                logger.error(f"User not found: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": str(e), "code": "USER_NOT_FOUND"}
                )
            # Other errors
            logger.error(f"Error getting user sessions: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Failed to get user sessions", "code": "INTERNAL_ERROR"}
            )
        except Exception as e:
            logger.error(f"Error getting user sessions: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Failed to get user sessions", "code": "INTERNAL_ERROR"}
            )

    @staticmethod
    @chat_route.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_200_OK,
        summary="Delete session",
        description="Delete a chat session and all its messages"
    )
    def delete_session(
        session_id: str,
        db: Session = Depends(get_db)
    ):
        """Delete a chat session"""
        chat_service = ChatService(db)
        success = chat_service.delete_session(session_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return {
            "success": True,
            "message": "Session deleted successfully",
            "session_id": session_id
        }

    @staticmethod
    @chat_route.patch(
        "/sessions/{session_id}/close",
        status_code=status.HTTP_200_OK,
        summary="Close session",
        description="Mark a chat session as inactive (archived). The session data is preserved but won't appear in active sessions list."
    )
    def close_session(
        session_id: str,
        db: Session = Depends(get_db)
    ):
        """Close/archive a chat session"""
        chat_service = ChatService(db)
        success = chat_service.close_session(session_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return {
            "success": True,
            "message": "Session closed successfully",
            "session_id": session_id
        }

    @staticmethod
    @chat_route.patch(
        "/sessions/{session_id}/title",
        status_code=status.HTTP_200_OK,
        summary="Update session title",
        description="Update the title of a chat session"
    )
    def update_session_title(
        session_id: str,
        title: str = Query(..., min_length=1, max_length=255, description="New session title"),
        db: Session = Depends(get_db)
    ):
        """Update session title"""
        chat_service = ChatService(db)
        success = chat_service.update_session_title(session_id, title)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return {
            "success": True,
            "message": "Session title updated",
            "session_id": session_id,
            "title": title
        }

    @staticmethod
    @chat_route.get(
        "/sessions/{session_id}/filters",
        status_code=status.HTTP_200_OK,
        summary="Get session filters",
        description="Retrieve all active filters for a session"
    )
    def get_session_filters(
        session_id: str,
        db: Session = Depends(get_db)
    ):
        """
        Get all active filters for a session.

        Returns:
        - tags: Dietary preference tags (vegetarian, vegan, etc.)
        - cuisines: Cuisine filters (italian, mexican, etc.)
        - excluded_ingredients: Ingredients to exclude (allergies, dislikes)
        - excluded_recipe_ids: Recipe IDs excluded from previous negative feedback
        - difficulty: Difficulty filter (easy, medium, hard)
        - max_time: Maximum cooking time in minutes
        """
        chat_service = ChatService(db)
        filters = chat_service.get_session_filters(session_id)

        if filters is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return {
            "success": True,
            "session_id": session_id,
            "filters": filters
        }

    @staticmethod
    @chat_route.post(
        "/sessions/{session_id}/clear-filters",
        status_code=status.HTTP_200_OK,
        summary="Clear session filters",
        description="Clear all filters for a session (tags, cuisines, excluded ingredients, excluded recipes)"
    )
    def clear_session_filters(
        session_id: str,
        db: Session = Depends(get_db)
    ):
        """
        Clear all filters for a session.

        This resets:
        - Dietary preference tags
        - Cuisine filters
        - Excluded ingredients (allergies)
        - Excluded recipe IDs (from negative feedback)
        - Difficulty filter
        - Time filter

        Use this when the user says 'clear filters' or 'start over'.
        """
        chat_service = ChatService(db)
        result = chat_service.clear_session_filters(session_id)

        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"}
            )

        return {
            "success": True,
            "message": "All filters cleared successfully",
            "session_id": session_id,
            "cleared_items": result.get("cleared_items", [])
        }
