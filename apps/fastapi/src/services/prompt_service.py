"""
Prompt Service - Manages agent prompts stored in the database
Provides CRUD operations for agent prompts and maintains version history
"""
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel

from apps.fastapi import logger
from models import AgentPrompt, AgentPromptHistory


class PromptUpdateRequest(BaseModel):
    """Request model for updating a prompt"""
    prompt: str
    user_uid: Optional[str] = None
    change_reason: Optional[str] = None


class PromptService:
    """Service for managing agent prompts"""

    def __init__(self, db: Session):
        self.db = db

    def get_prompts(
        self,
        agent_key: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> Tuple[List[AgentPrompt], int]:
        """Get all agent prompts with optional filtering"""
        query = self.db.query(AgentPrompt)

        if agent_key:
            query = query.filter(AgentPrompt.agent_key == agent_key)
        if is_active is not None:
            query = query.filter(AgentPrompt.is_active == is_active)

        prompts = query.order_by(AgentPrompt.agent_name).all()
        return prompts, len(prompts)

    def get_prompt(self, agent_key: str) -> Optional[AgentPrompt]:
        """Get a specific prompt by agent key"""
        return self.db.query(AgentPrompt).filter(
            AgentPrompt.agent_key == agent_key
        ).first()

    def get_prompt_by_key(self, agent_key: str) -> Optional[AgentPrompt]:
        """Alias for get_prompt - Get a specific prompt by agent key"""
        return self.get_prompt(agent_key)

    def get_prompt_by_id(self, prompt_id: UUID) -> Optional[AgentPrompt]:
        """Get a specific prompt by ID"""
        return self.db.query(AgentPrompt).filter(
            AgentPrompt.id == prompt_id
        ).first()

    def update_prompt(
        self,
        agent_key: str,
        request: PromptUpdateRequest
    ) -> Optional[AgentPrompt]:
        """
        Update an agent's prompt and create a history entry

        Args:
            agent_key: The agent's unique key
            request: PromptUpdateRequest with new_prompt, user_uid, change_reason

        Returns:
            Updated AgentPrompt or None if not found
        """
        prompt = self.get_prompt_by_key(agent_key)
        if not prompt:
            logger.warning(f"[PROMPT SERVICE] Agent prompt not found: {agent_key}")
            return None

        # Get the next version number
        latest_history = self.db.query(AgentPromptHistory).filter(
            AgentPromptHistory.agent_prompt_id == prompt.id
        ).order_by(desc(AgentPromptHistory.version)).first()

        next_version = (latest_history.version + 1) if latest_history else 1

        # Create history entry for the new prompt
        history_entry = AgentPromptHistory(
            agent_prompt_id=prompt.id,
            prompt_text=request.prompt,
            version=next_version,
            changed_by_user_uid=request.user_uid,
            change_reason=request.change_reason
        )
        self.db.add(history_entry)

        # Update the current prompt
        prompt.current_prompt = request.prompt

        try:
            self.db.commit()
            self.db.refresh(prompt)
            logger.info(f"[PROMPT SERVICE] Updated prompt for {agent_key} to version {next_version}")
            return prompt
        except Exception as e:
            self.db.rollback()
            logger.error(f"[PROMPT SERVICE] Error updating prompt: {e}")
            raise

    def get_prompt_history(
        self,
        agent_key: str,
        limit: int = 50
    ) -> Tuple[List[AgentPromptHistory], int]:
        """
        Get the version history for an agent's prompt

        Args:
            agent_key: The agent's unique key
            limit: Maximum number of history entries to return

        Returns:
            Tuple of (List of AgentPromptHistory entries, total count)
        """
        prompt = self.get_prompt_by_key(agent_key)
        if not prompt:
            return [], 0

        history = self.db.query(AgentPromptHistory).filter(
            AgentPromptHistory.agent_prompt_id == prompt.id
        ).order_by(desc(AgentPromptHistory.version)).limit(limit).all()

        return history, len(history)

    def create_prompt(
        self,
        agent_key: str,
        agent_name: str,
        description: str,
        current_prompt: str,
        model_name: Optional[str] = None,
        user_uid: Optional[str] = None
    ) -> AgentPrompt:
        """
        Create a new agent prompt entry

        Args:
            agent_key: Unique key for the agent
            agent_name: Human-readable name
            description: What the agent does
            current_prompt: The initial prompt text
            model_name: The model used (optional)
            user_uid: User creating the prompt (optional)

        Returns:
            Created AgentPrompt
        """
        # Check if already exists
        existing = self.get_prompt_by_key(agent_key)
        if existing:
            logger.warning(f"[PROMPT SERVICE] Agent prompt already exists: {agent_key}")
            return existing

        prompt = AgentPrompt(
            agent_key=agent_key,
            agent_name=agent_name,
            description=description,
            current_prompt=current_prompt,
            model_name=model_name,
            is_active=True
        )
        self.db.add(prompt)

        try:
            self.db.commit()
            self.db.refresh(prompt)

            # Create initial history entry
            history_entry = AgentPromptHistory(
                agent_prompt_id=prompt.id,
                prompt_text=current_prompt,
                version=1,
                changed_by_user_uid=user_uid,
                change_reason="Initial prompt creation"
            )
            self.db.add(history_entry)
            self.db.commit()

            logger.info(f"[PROMPT SERVICE] Created new prompt for {agent_key}")
            return prompt
        except Exception as e:
            self.db.rollback()
            logger.error(f"[PROMPT SERVICE] Error creating prompt: {e}")
            raise


def get_prompt_text(db: Session, agent_key: str, fallback_prompt: str) -> str:
    """
    Helper function to get prompt text from DB with fallback to hardcoded prompt

    Args:
        db: Database session
        agent_key: The agent's unique key
        fallback_prompt: The fallback prompt if DB entry doesn't exist

    Returns:
        Prompt text (from DB if available, otherwise fallback)
    """
    try:
        service = PromptService(db)
        prompt = service.get_prompt_by_key(agent_key)
        if prompt and prompt.is_active:
            return prompt.current_prompt
    except Exception as e:
        logger.warning(f"[PROMPT SERVICE] Error fetching prompt for {agent_key}: {e}")

    return fallback_prompt
