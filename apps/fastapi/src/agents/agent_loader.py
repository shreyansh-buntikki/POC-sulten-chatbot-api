"""
Agent Loader - Provides access to agent prompts
Prompts are loaded from code, not from database
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

from apps.fastapi import logger


@dataclass
class AgentPromptInfo:
    """Information about an agent's prompt"""
    agent_key: str
    agent_name: str
    description: str
    current_prompt: str
    model_name: str
    is_active: bool = True


def _get_all_agent_prompts() -> Dict[str, AgentPromptInfo]:
    """
    Get all agent prompts from the actual code.
    This imports the prompts directly from agent files.
    """
    prompts = {}

    # Import NLID Agent prompt
    try:
        from apps.fastapi.src.agents.sdk_nlid_agent import DEFAULT_NLID_PROMPT, NLID_AGENT_MODEL
        prompts["nlid_agent"] = AgentPromptInfo(
            agent_key="nlid_agent",
            agent_name="NLID Agent",
            description="Natural Language Intent Detection - Analyzes user queries to detect intent and extract entities for recipe and cooking platform",
            current_prompt=DEFAULT_NLID_PROMPT,
            model_name=NLID_AGENT_MODEL or "gpt-4o-mini",
            is_active=True
        )
    except ImportError as e:
        logger.warning(f"[AGENT LOADER] Could not import NLID agent prompt: {e}")

    # Import NLG Agent prompt
    try:
        from apps.fastapi.src.agents.sdk_nlg_agent import DEFAULT_NLG_PROMPT, NLG_AGENT_MODEL
        prompts["nlg_agent"] = AgentPromptInfo(
            agent_key="nlg_agent",
            agent_name="NLG Agent",
            description="Natural Language Generation - Generates warm, friendly responses for recipe recommendations and cooking assistance",
            current_prompt=DEFAULT_NLG_PROMPT,
            model_name=NLG_AGENT_MODEL or "gpt-4o-mini",
            is_active=True
        )
    except ImportError as e:
        logger.warning(f"[AGENT LOADER] Could not import NLG agent prompt: {e}")

    # Import Nutritional Agent prompt
    try:
        from apps.fastapi.src.agents.sdk_nutritional_agent import DEFAULT_NUTRITIONAL_PROMPT, NUTRITIONAL_AGENT_MODEL
        prompts["nutritional_agent"] = AgentPromptInfo(
            agent_key="nutritional_agent",
            agent_name="Nutritional Agent",
            description="Nutrition Expert - Helps users understand nutritional content of recipes and ingredients",
            current_prompt=DEFAULT_NUTRITIONAL_PROMPT,
            model_name=NUTRITIONAL_AGENT_MODEL or "gpt-4o-mini",
            is_active=True
        )
    except ImportError as e:
        logger.warning(f"[AGENT LOADER] Could not import Nutritional agent prompt: {e}")

    # Import Recipe Agent prompt
    try:
        from apps.fastapi.src.agents.sdk_recipe_agent import DEFAULT_RECIPE_PROMPT, RECIPE_AGENT_MODEL
        prompts["recipe_agent"] = AgentPromptInfo(
            agent_key="recipe_agent",
            agent_name="Recipe Agent",
            description="Recipe Search Specialist - Helps users discover recipes that match their needs",
            current_prompt=DEFAULT_RECIPE_PROMPT,
            model_name=RECIPE_AGENT_MODEL or "gpt-4o-mini",
            is_active=True
        )
    except ImportError as e:
        logger.warning(f"[AGENT LOADER] Could not import Recipe agent prompt: {e}")

    # Import Orchestrator Agent prompts
    try:
        from apps.fastapi.src.agents.sdk_orchestrator_agent import (
            DEFAULT_ORCHESTRATOR_PROMPT,
            DEFAULT_COOKING_GUARDRAIL_PROMPT,
            ORCHESTRATOR_AGENT_MODEL
        )
        prompts["orchestrator_agent"] = AgentPromptInfo(
            agent_key="orchestrator_agent",
            agent_name="Orchestrator Agent",
            description="Main Cooking Assistant Coordinator - Routes user queries to appropriate agents and manages the conversation flow",
            current_prompt=DEFAULT_ORCHESTRATOR_PROMPT,
            model_name=ORCHESTRATOR_AGENT_MODEL or "gpt-4o-mini",
            is_active=True
        )
        prompts["cooking_guardrail"] = AgentPromptInfo(
            agent_key="cooking_guardrail",
            agent_name="Cooking Guardrail",
            description="Guardrail Agent - Checks if user queries are related to cooking, recipes, food, or kitchen activities",
            current_prompt=DEFAULT_COOKING_GUARDRAIL_PROMPT,
            model_name=ORCHESTRATOR_AGENT_MODEL or "gpt-4o-mini",
            is_active=True
        )
    except ImportError as e:
        logger.warning(f"[AGENT LOADER] Could not import Orchestrator agent prompts: {e}")

    return prompts


# Cache for prompts (loaded once at module import)
_PROMPTS_CACHE: Dict[str, AgentPromptInfo] = {}


def get_agent_prompt(agent_key: str) -> Optional[AgentPromptInfo]:
    """
    Get the prompt info for a specific agent.

    Args:
        agent_key: The unique key for the agent

    Returns:
        AgentPromptInfo or None if not found
    """
    global _PROMPTS_CACHE

    if not _PROMPTS_CACHE:
        _PROMPTS_CACHE = _get_all_agent_prompts()

    return _PROMPTS_CACHE.get(agent_key)


def get_all_agent_prompts() -> List[AgentPromptInfo]:
    """
    Get all agent prompts.

    Returns:
        List of AgentPromptInfo objects
    """
    global _PROMPTS_CACHE

    if not _PROMPTS_CACHE:
        _PROMPTS_CACHE = _get_all_agent_prompts()

    return list(_PROMPTS_CACHE.values())


def get_prompt_text(agent_key: str) -> str:
    """
    Get just the prompt text for an agent.

    Args:
        agent_key: The unique key for the agent

    Returns:
        The prompt text or empty string if not found
    """
    info = get_agent_prompt(agent_key)
    return info.current_prompt if info else ""


def refresh_prompts():
    """
    Refresh the prompts cache (reload from code).
    Useful if prompts are updated dynamically.
    """
    global _PROMPTS_CACHE
    _PROMPTS_CACHE = _get_all_agent_prompts()
    logger.info(f"[AGENT LOADER] Refreshed prompts cache, loaded {len(_PROMPTS_CACHE)} agents")


class AgentPromptLoader:
    """
    Singleton class to manage loading agent prompts.
    Prompts are loaded from code, not from database.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_prompt(self, agent_key: str) -> Optional[AgentPromptInfo]:
        """Get prompt info for an agent"""
        return get_agent_prompt(agent_key)

    def get_all_prompts(self) -> List[AgentPromptInfo]:
        """Get all agent prompts"""
        return get_all_agent_prompts()

    def get_prompt_text(self, agent_key: str) -> str:
        """Get just the prompt text for an agent"""
        return get_prompt_text(agent_key)

    def refresh(self):
        """Refresh the prompts cache"""
        refresh_prompts()


# Global instance
prompt_loader = AgentPromptLoader()
