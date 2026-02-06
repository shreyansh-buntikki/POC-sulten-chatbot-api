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

            # Update session activity
            self.update_session_activity(session_id)

            self.db.commit()
            self.db.refresh(message)
            return message
        except Exception as e:
            self.db.rollback()
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

        query = query.order_by(ChatMessage.created_at)

        if limit:
            query = query.limit(limit)

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
