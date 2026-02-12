"""
Chat Service - Main service for chatbot operations
Coordinates conversation management and AI agent orchestration
"""
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from openai import OpenAI

from apps.fastapi import logger
from apps.fastapi.src.services.conversation_store import ConversationStore
from apps.fastapi.src.services.pipeline_orchestrator_sdk import RecipeSearchPipelineSDK
from models import ChatSession, ChatMessage, ChatMessageRoleEnum, User
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

    def _validate_user_exists(self, user_uid: str) -> None:
        """
        Validate that a user exists in the database.

        Args:
            user_uid: User identifier to validate

        Raises:
            ValueError: If user does not exist
        """
        user = self.db.query(User).filter(User.uid == user_uid).first()
        if not user:
            logger.error(f"[CHAT SERVICE] User not found: {user_uid}")
            raise ValueError(f"User '{user_uid}' does not exist")

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
            List of session dictionaries with query, answer, and recipe details
        """
        # Validate user exists before loading conversation history
        self._validate_user_exists(user_uid)

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

            if not messages:
                logger.warning(f"[CHAT SERVICE] Session {s.id} has no messages")
                result.append(session_data)
                continue

            # Find first user message (query)
            first_user_msg = next((m for m in messages if m.role == "user"), None)
            if first_user_msg and first_user_msg.content:
                session_data["user_message"] = {
                    "id": str(first_user_msg.id),
                    "content": first_user_msg.content,
                    "created_at": first_user_msg.created_at.isoformat() if first_user_msg.created_at else None
                }
            else:
                logger.warning(f"[CHAT SERVICE] Session {s.id} has no user message with content")

            # Find last assistant message (answer)
            assistant_msgs = [m for m in messages if m.role == "assistant"]
            if assistant_msgs:
                last_assistant = assistant_msgs[-1]
                if last_assistant.content:
                    session_data["assistant_message"] = {
                        "id": str(last_assistant.id),
                        "content": last_assistant.content,
                        "created_at": last_assistant.created_at.isoformat() if last_assistant.created_at else None
                    }
                else:
                    logger.warning(f"[CHAT SERVICE] Session {s.id} has empty assistant message")

                # Include metadata with recipe details if present
                if last_assistant.meta:
                    # Build metadata object with all fields
                    metadata = {}
                    for key, value in last_assistant.meta.items():
                        if key != "recipes":  # Skip recipes for now, will add separately
                            metadata[key] = value

                    # Add recipes to metadata if present
                    recipes = last_assistant.meta.get("recipes")
                    if recipes:
                        metadata["recipes"] = recipes

                    if metadata:  # Only add metadata if there's something in it
                        session_data["metadata"] = metadata
            else:
                logger.warning(f"[CHAT SERVICE] Session {s.id} has no assistant messages")

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
        message: str,
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a message and get AI response using the 10-stage pipeline

        Args:
            session_id: Existing session ID (None creates new session)
            user_uid: Optional user identifier
            message: User's message content
            language: Optional language code (e.g., 'en', 'no') for filtering recipes

        Returns:
            Dictionary with response and updated session info
        """
        # Validate user exists if user_uid is provided
        if user_uid:
            self._validate_user_exists(user_uid)

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
                user_uid=user_uid,
                language=language or "en"  # Default to 'en' if not provided
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
            # Include full recipe details in metadata if present
            # Convert enums to strings for JSON serialization
            retrieval_strategy = result["metadata"].get("retrieval_strategy")
            retrieval_strategy_str = str(retrieval_strategy) if retrieval_strategy else None

            assistant_metadata = {
                "intent": result["metadata"].get("intent"),
                "retrieval_strategy": retrieval_strategy_str,
                "num_results": result["metadata"].get("num_results"),
                "is_cooking_related": result["metadata"].get("is_cooking_related"),
                "created_at": datetime.utcnow().isoformat()
            }

            # Store full recipe details in metadata for proper UI rendering
            # This ensures chat history displays recipe cards with ingredients/instructions dropdowns
            if "recipes" in result["metadata"]:
                recipes = result["metadata"]["recipes"]
                assistant_metadata["recipes"] = [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "description": r.get("description"),
                        "ingress": r.get("ingress"),
                        "difficulty": r.get("difficulty"),
                        "prep_time": r.get("prep_time"),
                        "cook_time": r.get("cook_time"),
                        "total_time": r.get("total_time"),
                        "image": r.get("image"),
                        "servings": r.get("servings"),
                        "similarity": r.get("similarity"),
                        "priority_score": r.get("priority_score"),
                        "access_level": r.get("access_level"),
                        "is_liked": r.get("is_liked"),
                        "is_created": r.get("is_created"),
                        "is_bundle_recipe": r.get("is_bundle_recipe"),
                        "is_bundle_free_recipe": r.get("is_bundle_free_recipe"),
                        "bundle_name": r.get("bundle_name"),
                        "ingredients": r.get("ingredients", []),
                        "instructions": r.get("instructions", []),
                    }
                    for r in recipes
                ]

            # Add search context to assistant metadata for future refinements
            # This ensures the vector_query is persisted across requests
            # Note: pipeline_metadata is at root level of result, not nested under metadata
            pipeline_metadata = result.get("pipeline_metadata", {})
            if pipeline_metadata and pipeline_metadata.get("vector_query"):
                # Merge pipeline metadata into assistant metadata
                assistant_metadata.update({
                    "vector_query": pipeline_metadata.get("vector_query"),
                    "intent": pipeline_metadata.get("intent"),
                    "filters": pipeline_metadata.get("filters", {})
                })

            assistant_msg = self.conversation_store.add_message(
                session.id,
                "assistant",
                result["response"],
                assistant_metadata
            )
            logger.info(f"[CHAT SERVICE] Saved assistant message for session {session.id}")
        except Exception as e:
            # If we can't save the assistant message, delete the user message to prevent incomplete sessions
            # This ensures sessions always have paired user-assistant messages
            self.db.rollback()
            assistant_msg = None
            logger.error(f"[CHAT SERVICE] Failed to save assistant message for session {session.id}: {e}")
            import traceback
            logger.error(f"[CHAT SERVICE] Traceback: {traceback.format_exc()}")

            # Delete the user message to prevent incomplete session in database
            try:
                from models import ChatMessage
                self.db.query(ChatMessage).filter(ChatMessage.id == user_msg.id).delete()
                self.db.commit()
                logger.warning(f"[CHAT SERVICE] Deleted user message {user_msg.id} to prevent incomplete session")
            except Exception as delete_error:
                self.db.rollback()
                logger.error(f"[CHAT SERVICE] Failed to delete user message {user_msg.id}: {delete_error}")

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
            "metadata": result["metadata"],
            "persisted": assistant_msg is not None  # Flag indicating if messages were saved to database
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
