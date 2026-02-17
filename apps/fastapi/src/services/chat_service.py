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

    def _cleanup_old_sessions(self, user_uid: str, max_sessions: int = 5) -> None:
        """
        Delete old sessions to maintain maximum session limit per user.
        Keeps the most recent sessions based on last user message timestamp.

        Args:
            user_uid: User identifier
            max_sessions: Maximum number of sessions to keep (default 5)
        """
        try:
            # Get all active sessions for user
            sessions = self.conversation_store.get_user_sessions(user_uid, limit=100)

            if len(sessions) <= max_sessions:
                return  # Nothing to cleanup

            # Get last user message timestamp for each session
            sessions_with_timestamps = []
            for s in sessions:
                messages = self.conversation_store.get_messages(str(s.id))
                user_msgs = [m for m in messages if m.role == "user"]

                if user_msgs:
                    last_user_msg_time = user_msgs[-1].created_at
                else:
                    last_user_msg_time = s.created_at  # Fallback to session creation time

                sessions_with_timestamps.append({
                    "session": s,
                    "last_user_msg_time": last_user_msg_time
                })

            # Sort by last user message timestamp (most recent first)
            sessions_with_timestamps.sort(
                key=lambda x: x["last_user_msg_time"] or datetime.min,
                reverse=True
            )

            # Delete sessions beyond the limit
            sessions_to_delete = sessions_with_timestamps[max_sessions:]

            for item in sessions_to_delete:
                session_to_delete = item["session"]
                logger.info(f"[CHAT SERVICE] Deleting old session {session_to_delete.id} for user {user_uid}")
                self.conversation_store.delete_session(str(session_to_delete.id))

            if sessions_to_delete:
                logger.info(f"[CHAT SERVICE] Cleaned up {len(sessions_to_delete)} old sessions for user {user_uid}")

        except Exception as e:
            logger.error(f"[CHAT SERVICE] Error cleaning up old sessions for user {user_uid}: {e}")

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
        Get the session with the most recent user conversation for a user.
        Returns the session that had the last user message, with all its conversations.
        Note: Maximum 5 sessions are maintained per user (old ones auto-deleted on new session creation).

        Args:
            user_uid: User identifier
            limit: Maximum number of messages to return per session (default 50)

        Returns:
            List containing the most recently active session with all its conversations
        """
        logger.info(f"[CHAT SERVICE] get_user_sessions called for user: {user_uid}")

        # Validate user exists before loading conversation history
        self._validate_user_exists(user_uid)

        # Get all active sessions for user
        sessions = self.conversation_store.get_user_sessions(user_uid, limit=100)
        logger.info(f"[CHAT SERVICE] Found {len(sessions)} active sessions for user {user_uid}")

        if not sessions:
            logger.warning(f"[CHAT SERVICE] No sessions found for user {user_uid}")
            return []

        # Find the session with the most recent user message
        most_recent_session = None
        most_recent_time = None

        for s in sessions:
            logger.info(f"[CHAT SERVICE] Checking session {s.id}: is_active={s.is_active}")

            # Verify session still exists and is active (defensive check)
            if not s.is_active:
                logger.info(f"[CHAT SERVICE] Session {s.id} is inactive, skipping")
                continue

            messages = self.conversation_store.get_messages(str(s.id))
            logger.info(f"[CHAT SERVICE] Session {s.id} has {len(messages)} total messages")
            user_msgs = [m for m in messages if m.role == "user"]

            if user_msgs:
                last_user_msg_time = user_msgs[-1].created_at
                logger.info(f"[CHAT SERVICE] Session {s.id} has {len(user_msgs)} user messages, last at {last_user_msg_time}")
                if most_recent_time is None or (last_user_msg_time and last_user_msg_time > most_recent_time):
                    most_recent_time = last_user_msg_time
                    most_recent_session = s

        # If no session with user messages, try to find any active session
        if not most_recent_session and sessions:
            logger.info(f"[CHAT SERVICE] No session with user messages found, looking for any active session")
            for s in sessions:
                if s.is_active:
                    most_recent_session = s
                    logger.info(f"[CHAT SERVICE] Found active session without user messages: {s.id}")
                    break

        if not most_recent_session:
            logger.warning(f"[CHAT SERVICE] No most_recent_session found for user {user_uid}")
            return []

        logger.info(f"[CHAT SERVICE] Most recent session: {most_recent_session.id}")

        # Final verification that session exists in database (prevents race conditions)
        verified_session = self.conversation_store.get_session(str(most_recent_session.id))
        if not verified_session:
            logger.warning(f"[CHAT SERVICE] Session {most_recent_session.id} disappeared during get_user_sessions")
            return []

        # Get all messages for the verified session
        messages = self.conversation_store.get_messages(str(verified_session.id), limit=limit)
        logger.info(f"[CHAT SERVICE] Retrieved {len(messages)} messages for verified session {verified_session.id}")

        if not messages:
            logger.warning(f"[CHAT SERVICE] No messages found for session {verified_session.id}")
            return []

        # Build the session response - use verified_session for consistency
        session_data = {
            "session_id": str(verified_session.id),
            "title": verified_session.title,
            "created_at": verified_session.created_at.isoformat() if verified_session.created_at else None,
            "updated_at": verified_session.updated_at.isoformat() if verified_session.updated_at else None,
            "is_active": verified_session.is_active
        }

        # Find first user message and last assistant message (original structure)
        user_msgs = [m for m in messages if m.role == "user"]
        assistant_msgs = [m for m in messages if m.role == "assistant"]

        logger.info(f"[CHAT SERVICE] Building response: {len(user_msgs)} user msgs, {len(assistant_msgs)} assistant msgs")

        # Add all messages as a flat array for frontend consumption
        session_data["messages"] = [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "metadata": m.meta if m.meta else None
            }
            for m in messages
        ]

        if user_msgs:
            first_user_msg = user_msgs[0]
            session_data["user_message"] = {
                "id": str(first_user_msg.id),
                "content": first_user_msg.content,
                "created_at": first_user_msg.created_at.isoformat() if first_user_msg.created_at else None
            }

        if assistant_msgs:
            last_assistant = assistant_msgs[-1]
            session_data["assistant_message"] = {
                "id": str(last_assistant.id),
                "content": last_assistant.content,
                "created_at": last_assistant.created_at.isoformat() if last_assistant.created_at else None
            }

            # Include metadata with recipe details if present (original structure)
            if last_assistant.meta:
                metadata = {}
                for key, value in last_assistant.meta.items():
                    metadata[key] = value
                if metadata:
                    session_data["metadata"] = metadata

        # Add conversations array with all message pairs (new addition)
        conversations = []
        i = 0
        while i < len(messages):
            conversation = {}

            # Get user message
            if i < len(messages) and messages[i].role == "user":
                user_msg = messages[i]
                conversation["user_message"] = {
                    "id": str(user_msg.id),
                    "content": user_msg.content,
                    "created_at": user_msg.created_at.isoformat() if user_msg.created_at else None
                }
                i += 1

            # Get assistant message (if exists)
            if i < len(messages) and messages[i].role == "assistant":
                assistant_msg = messages[i]
                conversation["assistant_message"] = {
                    "id": str(assistant_msg.id),
                    "content": assistant_msg.content,
                    "created_at": assistant_msg.created_at.isoformat() if assistant_msg.created_at else None
                }

                # Include metadata with recipe details if present
                if assistant_msg.meta:
                    conv_metadata = {}
                    for key, value in assistant_msg.meta.items():
                        conv_metadata[key] = value
                    if conv_metadata:
                        conversation["metadata"] = conv_metadata
                i += 1

            if conversation:
                conversations.append(conversation)

        session_data["conversations"] = conversations
        logger.info(f"[CHAT SERVICE] Returning {len(conversations)} conversation pairs in response")

        return [session_data]


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
        language: Optional[str] = None,
        new_session: bool = False
    ) -> Dict[str, Any]:
        """
        Send a message and get AI response using the 10-stage pipeline

        Args:
            session_id: Existing session ID (None to auto-detect or create new)
            user_uid: Optional user identifier
            message: User's message content
            language: Optional language code (e.g., 'en', 'no') for filtering recipes
            new_session: If True, always create a new session (for "Start New Chat" button)
                        If False and no session_id, try to continue user's most recent session

        Returns:
            Dictionary with response and updated session info
        """
        # Validate user exists if user_uid is provided
        if user_uid:
            self._validate_user_exists(user_uid)

        # Step 1: Get or create session
        session = None

        if session_id:
            # Explicit session_id provided - try to use it
            session = self.conversation_store.get_session(session_id)
            if not session:
                # Session not found - this can happen if it was cleaned up
                # Instead of failing, try to continue user's most recent session or create new one
                logger.warning(f"[CHAT SERVICE] Session {session_id} not found, attempting recovery for user {user_uid}")
                if user_uid and not new_session:
                    # Try to get user's most recent active session
                    user_sessions = self.conversation_store.get_user_sessions(user_uid, limit=1, include_inactive=False)
                    if user_sessions:
                        session = user_sessions[0]
                        logger.info(f"[CHAT SERVICE] Recovered to most recent session: {session.id}")
                # If still no session, will create new one below
            elif not session.is_active:
                # Session exists but is inactive - treat as not found and recover
                logger.warning(f"[CHAT SERVICE] Session {session_id} is inactive, attempting recovery for user {user_uid}")
                if user_uid and not new_session:
                    user_sessions = self.conversation_store.get_user_sessions(user_uid, limit=1, include_inactive=False)
                    if user_sessions:
                        session = user_sessions[0]
                        logger.info(f"[CHAT SERVICE] Recovered to active session: {session.id}")
                else:
                    session = None  # Will create new session below
            elif user_uid and session.user_uid and session.user_uid != user_uid:
                # Session belongs to a different user - don't use it, recover instead
                logger.warning(f"[CHAT SERVICE] Session {session_id} belongs to different user ({session.user_uid}), recovering for user {user_uid}")
                user_sessions = self.conversation_store.get_user_sessions(user_uid, limit=1, include_inactive=False)
                if user_sessions:
                    session = user_sessions[0]
                    logger.info(f"[CHAT SERVICE] Recovered to user's own session: {session.id}")
                else:
                    session = None  # Will create new session below
            else:
                logger.info(f"[CHAT SERVICE] Using provided session: {session.id}")

        if not session and not new_session and user_uid:
            # No session_id, not forcing new session, and user_uid provided
            # Try to continue the user's most recent active session
            user_sessions = self.conversation_store.get_user_sessions(user_uid, limit=1, include_inactive=False)
            if user_sessions:
                session = user_sessions[0]
                logger.info(f"[CHAT SERVICE] Continuing most recent active session: {session.id}")

        if not session:
            # Create new session (either new_session=True or no existing session found)
            title = message[:50] + "..." if len(message) > 50 else message
            session = self.conversation_store.create_session(user_uid, title)
            logger.info(f"[CHAT SERVICE] Created new session: {session.id}")

            # Cleanup old sessions - maintain max 5 sessions per user
            if user_uid:
                self._cleanup_old_sessions(user_uid, max_sessions=5)

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
                        "recipe_cost": r.get("recipe_cost"),
                        "nutritional_info": r.get("nutritional_info"),
                        "seasonality": r.get("seasonality"),
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

    def close_session(self, session_id: str) -> bool:
        """
        Close/archive a session (mark as inactive)

        The session data is preserved but won't appear in active sessions list.

        Args:
            session_id: Session UUID

        Returns:
            True if successful
        """
        return self.conversation_store.close_session(session_id)

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

    def get_session_filters(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get all active filters for a session.

        Args:
            session_id: Session UUID

        Returns:
            Dictionary of active filters or None if session not found
        """
        from apps.fastapi.src.services.session_memory_manager import SessionMemoryManager

        session_manager = SessionMemoryManager(self.db)
        session = session_manager.load_session(session_id)

        if not session:
            return None

        filters = {
            "tags": session.filters.tags if session.filters else [],
            "cuisines": session.filters.cuisines if session.filters else [],
            "excluded_ingredients": session.excluded_ingredients or [],
            "excluded_recipe_ids": session.excluded_recipe_ids or [],
            "difficulty": session.filters.difficulty if session.filters else None,
            "max_time": session.filters.max_time if session.filters else None,
            "last_vector_query": session.context_entities.last_vector_query if session.context_entities else None,
            "last_intent": session.last_intent
        }

        # Count active filters
        active_filter_count = sum([
            len(filters["tags"]),
            len(filters["cuisines"]),
            len(filters["excluded_ingredients"]),
            len(filters["excluded_recipe_ids"]),
            1 if filters["difficulty"] else 0,
            1 if filters["max_time"] else 0
        ])
        filters["active_filter_count"] = active_filter_count

        return filters

    def clear_session_filters(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Clear all filters for a session.

        Args:
            session_id: Session UUID

        Returns:
            Dictionary with cleared items list or None if session not found
        """
        from apps.fastapi.src.services.session_memory_manager import SessionMemoryManager

        session_manager = SessionMemoryManager(self.db)
        session = session_manager.load_session(session_id)

        if not session:
            return None

        # Track what was cleared
        cleared_items = []

        if session.filters and session.filters.tags:
            cleared_items.append(f"tags: {', '.join(session.filters.tags)}")
        if session.filters and session.filters.cuisines:
            cleared_items.append(f"cuisines: {', '.join(session.filters.cuisines)}")
        if session.excluded_ingredients:
            cleared_items.append(f"excluded ingredients: {', '.join(session.excluded_ingredients[:5])}")
        if session.excluded_recipe_ids:
            cleared_items.append(f"excluded recipes: {len(session.excluded_recipe_ids)} recipes")
        if session.filters and session.filters.difficulty:
            cleared_items.append(f"difficulty: {session.filters.difficulty}")
        if session.filters and session.filters.max_time:
            cleared_items.append(f"max time: {session.filters.max_time} minutes")

        # Clear filters using session manager
        session = session_manager.clear_filters(session)

        logger.info(f"[CHAT SERVICE] Cleared filters for session {session_id}: {cleared_items}")

        return {
            "session_id": session_id,
            "cleared_items": cleared_items
        }
