"""
Chat Service - Main service for chatbot operations
Coordinates conversation management and AI agent orchestration
"""
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from openai import OpenAI

from apps.fastapi.src.services.conversation_store import ConversationStore
from apps.fastapi.src.services.pipeline_orchestrator_sdk import RecipeSearchPipelineSDK
from models import ChatSession, ChatMessage, ChatMessageRoleEnum
from database import get_db

# Configuration
MAX_CONTEXT_MESSAGES = int(os.getenv('MAX_CONTEXT_MESSAGES', '10'))


class ChatService:
    """
    Main service for chatbot operations

    Handles:
    - Session management (create, retrieve, delete)
    - Message processing through the 10-stage recipe search pipeline
    - Context management for conversation history
    """

    def __init__(self, db: Session):
        self.db = db
        self.conversation_store = ConversationStore(db)

        # Initialize OpenAI client
        openai_api_key = os.getenv('OPENAI_API_KEY')
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY not configured in environment")

        self.openai_client = OpenAI(api_key=openai_api_key)

        # Initialize recipe search pipeline (SDK-based 10-stage architecture)
        self.pipeline = RecipeSearchPipelineSDK(db, self.openai_client)

    async def create_session(
        self,
        user_uid: Optional[str] = None,
        title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new chat session

        Args:
            user_uid: Optional user identifier
            title: Optional session title

        Returns:
            Dictionary with session details
        """
        session = self.conversation_store.create_session(user_uid, title)

        return {
            "session_id": str(session.id),
            "user_uid": session.user_uid,
            "title": session.title,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "is_active": session.is_active
        }

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session details

        Args:
            session_id: Session UUID

        Returns:
            Session dictionary or None
        """
        session = self.conversation_store.get_session(session_id)
        if not session:
            return None

        summary = self.conversation_store.get_conversation_summary(session_id)
        return summary

    def get_user_sessions(
        self,
        user_uid: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get all sessions for a user with first user message and last assistant message

        Args:
            user_uid: User identifier
            limit: Maximum number of sessions

        Returns:
            List of session dictionaries with query and answer
        """
        sessions = self.conversation_store.get_user_sessions(user_uid, limit)

        result = []
        for s in sessions:
            session_data = {
                "session_id": str(s.id),
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "is_active": s.is_active
            }

            # Get messages for this session
            messages = self.conversation_store.get_messages(str(s.id))

            # Find first user message (query)
            first_user_msg = next((m for m in messages if m.role == "user"), None)
            if first_user_msg:
                session_data["user_query"] = first_user_msg.content

            # Find last assistant message (answer)
            assistant_msgs = [m for m in messages if m.role == "assistant"]
            if assistant_msgs:
                session_data["assistant_answer"] = assistant_msgs[-1].content

            result.append(session_data)

        return result

    def get_session_history(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get full message history for a session

        Args:
            session_id: Session UUID
            limit: Optional message limit

        Returns:
            Dictionary with session info and messages
        """
        summary = self.conversation_store.get_conversation_summary(session_id)
        if not summary:
            return None

        messages = self.conversation_store.get_messages(session_id, limit=limit)

        return {
            "session": summary,
            "messages": [
                {
                    "message_id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    "metadata": msg.meta
                }
                for msg in messages
            ]
        }

    async def send_message(
        self,
        session_id: Optional[str],
        user_uid: Optional[str],
        message: str
    ) -> Dict[str, Any]:
        """
        Send a message and get AI response using the 10-stage pipeline

        Args:
            session_id: Existing session ID (None creates new session)
            user_uid: Optional user identifier
            message: User's message content

        Returns:
            Dictionary with response and updated session info
        """
        # Step 1: Get or create session
        if session_id:
            session = self.conversation_store.get_session(session_id)
            if not session:
                return {"error": "Session not found", "code": "SESSION_NOT_FOUND"}
        else:
            # Generate title from first message
            title = message[:50] + "..." if len(message) > 50 else message
            session = self.conversation_store.create_session(user_uid, title)

        # Step 2: Save user message
        user_msg = self.conversation_store.add_message(
            session.id,
            "user",
            message,
            {"created_at": datetime.utcnow().isoformat()}
        )

        # Step 3: Process through the 10-stage recipe search pipeline
        try:
            result = await self.pipeline.process_query(
                query=message,
                session_id=str(session.id),
                user_uid=user_uid
            )
        except Exception as e:
            # Ensure database is in clean state after pipeline error
            self.db.rollback()

            # Fallback error response
            result = {
                "response": "I apologize, but I encountered an error processing your request. Please try again.",
                "metadata": {"error": str(e), "error_type": type(e).__name__}
            }

        # Step 4: Save assistant response
        try:
            assistant_msg = self.conversation_store.add_message(
                session.id,
                "assistant",
                result["response"],
                {
                    "intent": result["metadata"].get("intent"),
                    "retrieval_strategy": result["metadata"].get("retrieval_strategy"),
                    "num_results": result["metadata"].get("num_results"),
                    "is_cooking_related": result["metadata"].get("is_cooking_related"),
                    "created_at": datetime.utcnow().isoformat()
                }
            )
        except Exception as e:
            # If we can't save the assistant message, at least return what we have
            self.db.rollback()
            assistant_msg = None

        # Step 5: Format response
        response_data = {
            "session_id": str(session.id),
            "user_message": {
                "id": str(user_msg.id),
                "content": message,
                "created_at": user_msg.created_at.isoformat() if user_msg.created_at else None
            },
            "assistant_message": {
                "id": str(assistant_msg.id) if assistant_msg else None,
                "content": result["response"],
                "created_at": assistant_msg.created_at.isoformat() if assistant_msg and assistant_msg.created_at else None
            },
            "metadata": result["metadata"]
        }

        return response_data

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and all its messages

        Args:
            session_id: Session UUID

        Returns:
            True if successful
        """
        return self.conversation_store.delete_session(session_id)

    def update_session_title(self, session_id: str, title: str) -> bool:
        """
        Update session title

        Args:
            session_id: Session UUID
            title: New title

        Returns:
            True if successful
        """
        return self.conversation_store.update_session_title(session_id, title)

    def get_context_summary(self, session_id: str) -> Dict[str, Any]:
        """
        Get a summary of the conversation context

        Args:
            session_id: Session UUID

        Returns:
            Context summary dictionary
        """
        summary = self.conversation_store.get_conversation_summary(session_id)
        if not summary:
            return None

        return {
            "session_id": summary["session_id"],
            "title": summary["title"],
            "message_count": summary["total_messages"],
            "last_activity": summary["updated_at"],
            "is_active": summary["is_active"]
        }
