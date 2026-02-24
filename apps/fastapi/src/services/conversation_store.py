"""
Conversation Store Service
Handles storage and retrieval of chat message history
"""
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc

from models import ChatSession, ChatMessage, ChatMessageRoleEnum


class ConversationStore:
    """
    Manages chat conversation storage and retrieval
    Handles both sessions and individual messages
    """

    def __init__(self, db: Session):
        self.db = db
        self.default_context_limit = 10

    def create_session(
        self,
        user_uid: Optional[str] = None,
        title: Optional[str] = None
    ) -> ChatSession:
        """
        Create a new chat session

        Args:
            user_uid: Optional user identifier (for authenticated users)
            title: Optional session title (auto-generated if not provided)

        Returns:
            Created ChatSession object
        """
        try:
            session = ChatSession(
                user_uid=user_uid,
                title=title or "New conversation"
            )
            self.db.add(session)
            self.db.commit()
            self.db.refresh(session)
            return session
        except Exception as e:
            self.db.rollback()
            raise

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """
        Retrieve a session by ID

        Args:
            session_id: Session UUID

        Returns:
            ChatSession object or None if not found
        """
        return self.db.query(ChatSession).filter(
            ChatSession.id == session_id
        ).first()

    def get_user_sessions(
        self,
        user_uid: str,
        limit: int = 50,
        include_inactive: bool = False
    ) -> List[ChatSession]:
        """
        Get all sessions for a user

        Args:
            user_uid: User identifier
            limit: Maximum number of sessions to return
            include_inactive: Whether to include inactive sessions

        Returns:
            List of ChatSession objects
        """
        query = self.db.query(ChatSession).filter(ChatSession.user_uid == user_uid)

        if not include_inactive:
            query = query.filter(ChatSession.is_active == True)

        return query.order_by(desc(ChatSession.updated_at)).limit(limit).all()

    def get_most_recent_active_session(
        self,
        user_uid: str
    ) -> Optional[ChatSession]:
        """
        Get the most recently active session for a user based on last user message.

        Uses a single optimized query with JOIN instead of N+1 queries.
        This is more efficient than get_user_sessions + looping through messages.

        Args:
            user_uid: User identifier

        Returns:
            Most recently active ChatSession or None if no sessions exist
        """
        from sqlalchemy import func

        # Single query: JOIN sessions with messages, filter by user messages,
        # group by session, order by max message created_at
        subquery = self.db.query(
            ChatMessage.session_id,
            func.max(ChatMessage.created_at).label('last_user_msg_time')
        ).filter(
            ChatMessage.role == 'user'
        ).group_by(
            ChatMessage.session_id
        ).subquery()

        # Join with sessions, filter by user and active status
        result = self.db.query(ChatSession).join(
            subquery,
            ChatSession.id == subquery.c.session_id
        ).filter(
            ChatSession.user_uid == user_uid,
            ChatSession.is_active == True
        ).order_by(
            desc(subquery.c.last_user_msg_time)
        ).first()

        # If no session with user messages, fall back to most recent session by updated_at
        if not result:
            result = self.db.query(ChatSession).filter(
                ChatSession.user_uid == user_uid,
                ChatSession.is_active == True
            ).order_by(
                desc(ChatSession.updated_at)
            ).first()

        return result

    def get_sessions_with_last_message_time(
        self,
        user_uid: str,
        include_inactive: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get sessions with their last user message timestamp in a single query.
        Optimized to avoid N+1 queries when sorting sessions by activity.

        Args:
            user_uid: User identifier
            include_inactive: Whether to include inactive sessions

        Returns:
            List of dicts with 'session' and 'last_user_msg_time' keys,
            sorted by last_user_msg_time descending (most recent first)
        """
        from sqlalchemy import func

        # Subquery to get last user message time per session
        subquery = self.db.query(
            ChatMessage.session_id,
            func.max(ChatMessage.created_at).label('last_user_msg_time')
        ).filter(
            ChatMessage.role == 'user'
        ).group_by(
            ChatMessage.session_id
        ).subquery()

        # Base query for sessions
        query = self.db.query(
            ChatSession,
            subquery.c.last_user_msg_time
        ).outerjoin(
            subquery,
            ChatSession.id == subquery.c.session_id
        ).filter(
            ChatSession.user_uid == user_uid
        )

        if not include_inactive:
            query = query.filter(ChatSession.is_active == True)

        # Order by last message time (nulls last), then by session updated_at
        results = query.order_by(
            desc(subquery.c.last_user_msg_time),
            desc(ChatSession.updated_at)
        ).all()

        # Format results
        return [
            {
                "session": row[0],
                "last_user_msg_time": row[1] or row[0].created_at  # Fallback to session creation
            }
            for row in results
        ]

    def update_session_title(self, session_id: str, title: str) -> bool:
        """
        Update session title

        Args:
            session_id: Session UUID
            title: New title

        Returns:
            True if successful, False otherwise
        """
        session = self.get_session(session_id)
        if not session:
            return False

        session.title = title
        session.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def update_session_activity(self, session_id: str) -> bool:
        """
        Update session's last activity timestamp

        Args:
            session_id: Session UUID

        Returns:
            True if successful, False otherwise
        """
        session = self.get_session(session_id)
        if not session:
            return False

        session.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def close_session(self, session_id: str) -> bool:
        """
        Mark a session as inactive

        Args:
            session_id: Session UUID

        Returns:
            True if successful, False otherwise
        """
        session = self.get_session(session_id)
        if not session:
            return False

        session.is_active = False
        session.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and all its messages

        Args:
            session_id: Session UUID

        Returns:
            True if successful, False otherwise
        """
        session = self.get_session(session_id)
        if not session:
            return False

        # Messages will be deleted automatically due to CASCADE
        self.db.delete(session)
        self.db.commit()
        return True

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ChatMessage:
        """
        Add a message to a session

        Args:
            session_id: Session UUID
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            metadata: Optional metadata (intent, agent info, etc.)

        Returns:
            Created ChatMessage object
        """
        try:
            message = ChatMessage(
                session_id=session_id,
                role=role,
                content=content,
                meta=metadata or {}
            )
            self.db.add(message)

            # Update session activity (don't commit here - let the main commit happen)
            session = self.get_session(session_id)
            if session:
                session.updated_at = datetime.utcnow()

            # Single commit at the end
            self.db.commit()
            self.db.refresh(message)

            # Log successful save
            import logging
            logger = logging.getLogger("apps.fastapi")
            logger.info(f"[CONVERSATION STORE] Saved {role} message {message.id} for session {session_id}")

            return message
        except Exception as e:
            self.db.rollback()

            # Log error details
            import logging
            logger = logging.getLogger("apps.fastapi")
            logger.error(f"[CONVERSATION STORE] Failed to save {role} message for session {session_id}: {e}")
            logger.error(f"[CONVERSATION STORE] Content length: {len(content)}, Metadata size: {len(str(metadata)) if metadata else 0}")

            raise

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        role: Optional[str] = None
    ) -> List[ChatMessage]:
        """
        Get messages from a session

        Args:
            session_id: Session UUID
            limit: Maximum number of messages (None for all)
            role: Optional filter by role

        Returns:
            List of ChatMessage objects in chronological order
        """
        query = self.db.query(ChatMessage).filter(ChatMessage.session_id == session_id)

        if role:
            query = query.filter(ChatMessage.role == role)

        # When limit is specified, we want the MOST RECENT N messages,
        # not the oldest N. Use a subquery to get the last N by created_at,
        # then order the result chronologically for AI context.
        if limit:
            # Subquery: get the IDs of the most recent N messages
            subquery = self.db.query(ChatMessage.id).filter(
                ChatMessage.session_id == session_id
            )
            if role:
                subquery = subquery.filter(ChatMessage.role == role)
            subquery = subquery.order_by(ChatMessage.created_at.desc()).limit(limit)

            # Main query: get those messages ordered chronologically
            query = query.filter(ChatMessage.id.in_(subquery))

        query = query.order_by(ChatMessage.created_at)

        return query.all()

    def get_context(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Dict[str, str]]:
        """
        Get conversation context for AI processing

        Args:
            session_id: Session UUID
            limit: Maximum number of recent messages (default 10)

        Returns:
            List of message dictionaries formatted for OpenAI API
        """
        messages = self.get_messages(session_id, limit=limit)

        return [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in messages
        ]

    def get_context_for_openai(
        self,
        session_id: str,
        limit: int = 10,
        include_system_prompt: bool = False
    ) -> List[Dict[str, str]]:
        """
        Get conversation context formatted specifically for OpenAI Chat Completions API

        Args:
            session_id: Session UUID
            limit: Maximum number of recent messages
            include_system_prompt: Whether to include system messages

        Returns:
            List of message dictionaries in OpenAI format
        """
        messages = self.get_messages(session_id, limit=limit)

        context = []
        for msg in messages:
            # Skip system messages unless explicitly requested
            if msg.role == "system" and not include_system_prompt:
                continue

            context.append({
                "role": msg.role,
                "content": msg.content
            })

        return context

    def get_conversation_summary(self, session_id: str) -> Dict[str, Any]:
        """
        Get a summary of a conversation

        Args:
            session_id: Session UUID

        Returns:
            Dictionary with conversation statistics
        """
        session = self.get_session(session_id)
        if not session:
            return None

        messages = self.get_messages(session_id)

        user_messages = [m for m in messages if m.role == "user"]
        assistant_messages = [m for m in messages if m.role == "assistant"]

        return {
            "session_id": str(session.id),
            "title": session.title,
            "user_uid": session.user_uid,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            "is_active": session.is_active,
            "total_messages": len(messages),
            "user_message_count": len(user_messages),
            "assistant_message_count": len(assistant_messages),
            "first_message": user_messages[0].content if user_messages else None,
            "last_message": messages[-1].content if messages else None
        }

    def cleanup_old_sessions(self, days: int = 30) -> int:
        """
        Delete inactive sessions older than specified days

        Args:
            days: Number of days after which to delete inactive sessions

        Returns:
            Number of sessions deleted
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        old_sessions = self.db.query(ChatSession).filter(
            ChatSession.updated_at < cutoff_date,
            ChatSession.is_active == False
        ).all()

        count = len(old_sessions)
        for session in old_sessions:
            self.db.delete(session)

        self.db.commit()
        return count

    def generate_session_title(self, session_id: str) -> Optional[str]:
        """
        Auto-generate a title for a session based on first message

        Args:
            session_id: Session UUID

        Returns:
            Generated title or None
        """
        # Get first user message
        first_message = self.db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user"
        ).order_by(ChatMessage.created_at).first()

        if not first_message:
            return None

        # Truncate and clean up the message
        content = first_message.content.strip()
        if len(content) > 50:
            content = content[:47] + "..."

        return content
