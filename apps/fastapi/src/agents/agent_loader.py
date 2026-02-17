"""
Agent Loader - Dynamically loads agent prompts from the database
Provides factory functions to create agents with database-stored prompts
"""
import os
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from apps.fastapi import logger


# Default prompts (fallback if database is unavailable)
DEFAULT_PROMPTS = {
    "nlid_agent": "You are an expert Natural Language Intent Detection system for a recipe and cooking platform.",
    "nlg_agent": "You are a warm, knowledgeable cooking assistant for a recipe platform.",
    "nutritional_agent": "You are a nutrition expert helping users understand the nutritional content of recipes and ingredients.",
    "recipe_agent": "You are a recipe search specialist helping users discover delicious recipes that match their needs.",
    "cooking_guardrail": "You are a guardrail that checks if a user query is related to cooking, recipes, food, or kitchen activities.",
    "orchestrator_agent": "You are the main cooking assistant coordinator for a recipe and food platform."
}


def get_agent_prompt(db: Session, agent_key: str) -> str:
    """
    Get the current prompt for an agent from the database.
    Falls back to default prompt if database is unavailable.

    Args:
        db: Database session
        agent_key: The unique key for the agent

    Returns:
        The prompt text
    """
    try:
        from models import AgentPrompt

        prompt = db.query(AgentPrompt).filter(
            AgentPrompt.agent_key == agent_key,
            AgentPrompt.is_active == True
        ).first()

        if prompt:
            logger.info(f"[AGENT LOADER] Loaded prompt from DB for {agent_key}")
            return prompt.current_prompt
        else:
            logger.warning(f"[AGENT LOADER] No DB prompt found for {agent_key}, using default")
            return DEFAULT_PROMPTS.get(agent_key, "")

    except Exception as e:
        logger.warning(f"[AGENT LOADER] Error loading prompt for {agent_key}: {e}, using default")
        return DEFAULT_PROMPTS.get(agent_key, "")


def get_all_agent_prompts(db: Session) -> Dict[str, str]:
    """
    Get all active agent prompts from the database.

    Args:
        db: Database session

    Returns:
        Dictionary mapping agent_key to prompt text
    """
    try:
        from models import AgentPrompt

        prompts = db.query(AgentPrompt).filter(
            AgentPrompt.is_active == True
        ).all()

        result = {}
        for prompt in prompts:
            result[prompt.agent_key] = prompt.current_prompt

        # Fill in any missing with defaults
        for key in DEFAULT_PROMPTS:
            if key not in result:
                result[key] = DEFAULT_PROMPTS[key]

        return result

    except Exception as e:
        logger.warning(f"[AGENT LOADER] Error loading prompts: {e}, using defaults")
        return DEFAULT_PROMPTS.copy()


class AgentPromptLoader:
    """
    Singleton class to manage loading and caching agent prompts.
    For now, no caching - always fetches from DB (as per requirements).
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_prompt(self, db: Session, agent_key: str) -> str:
        """Get prompt for an agent"""
        return get_agent_prompt(db, agent_key)

    def get_all_prompts(self, db: Session) -> Dict[str, str]:
        """Get all agent prompts"""
        return get_all_agent_prompts(db)


# Global instance
prompt_loader = AgentPromptLoader()
