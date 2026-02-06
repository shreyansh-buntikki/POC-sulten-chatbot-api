"""
Orchestrator Agent (Master Agent)
Coordinates all other agents and manages conversation flow
"""
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from openai import OpenAI

from apps.fastapi.src.agents.base_agent import BaseAgent
from apps.fastapi.src.agents.nlid_agent import NLIDAgent
from apps.fastapi.src.agents.recipe_retrieval_agent import RecipeRetrievalAgent
from apps.fastapi.src.agents.nutritional_agent import NutritionalAgent


class OrchestratorAgent(BaseAgent):
    """
    Master Orchestrator Agent

    Responsibilities:
    1. Checks if query is cooking-related
    2. Detects user intent using NLID Agent
    3. Routes queries to appropriate specialized agent
    4. Maintains conversation context
    5. Formats and synthesizes responses
    6. Handles multi-agent workflows if needed
    """

    # Intent to Agent mapping
    INTENT_AGENT_MAP = {
        'recipe_search': 'recipe_retrieval',
        'nutritional_info': 'nutritional',
        'ingredient_substitution': 'nutritional',  # Reuse nutritional agent for now
        'recommendation': 'recipe_retrieval',      # Reuse recipe agent for now
        'general_chat': 'general'
    }

    def __init__(self, db: Session, openai_client: Optional[OpenAI] = None):
        super().__init__(db, openai_client)

        # Initialize specialized agents
        self.nlid_agent = NLIDAgent(db, openai_client)
        self.recipe_agent = RecipeRetrievalAgent(db, openai_client)
        self.nutrition_agent = NutritionalAgent(db, openai_client)

        self.general_prompt = self._build_general_prompt()

    def _build_general_prompt(self) -> str:
        """Build prompt for general chat"""
        return self._format_system_prompt("""You are a helpful cooking assistant for Sulten, a recipe and cooking platform.

Your role is to:
- Be friendly and conversational
- Help users with general cooking questions
- Guide users to ask about recipes, ingredients, or nutrition
- Provide helpful tips and suggestions

Keep responses warm and engaging (under 150 words).

Example greetings:
- "Hello! 👋 I'm your Sulten cooking assistant. I can help you find recipes, learn about nutrition, or answer cooking questions. What would you like to explore today?"
- "Hi there! Looking for cooking inspiration? Tell me what you're in the mood for, and I'll help you find the perfect recipe!"
""")

    async def process(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process user query through the agent pipeline

        Args:
            query: User's message
            context: Conversation context including history and user info

        Returns:
            Dictionary with response and metadata
        """
        # Step 1: Detect intent and extract entities
        intent_result = await self.nlid_agent.process(query, context)

        # Step 2: Check if query is cooking-related
        is_supported, rejection_message = self.nlid_agent.is_query_supported(intent_result)

        if not is_supported:
            # Query is not cooking-related
            return {
                "response": rejection_message,
                "metadata": {
                    "intent": "not_supported",
                    "confidence": "high",
                    "is_cooking_related": False
                }
            }

        intent = intent_result.get("intent", "general_chat")
        entities = intent_result.get("entities", {})
        parameters = intent_result.get("parameters", {})
        filters = intent_result.get("filters", {})
        confidence = intent_result.get("confidence", "medium")

        # Step 3: Route to appropriate agent with full context
        agent_context = {
            **context,
            "entities": entities,
            "parameters": parameters,
            "filters": filters  # Pass filters to specialized agents
        }

        agent_response = await self._route_to_agent(
            query, intent, agent_context
        )

        # Step 4: Prepare metadata
        metadata = {
            "intent": intent,
            "confidence": confidence,
            "entities": entities,
            "parameters": parameters,
            "filters": filters,
            "is_cooking_related": True,
            "agent_used": intent
        }

        # Add agent-specific metadata
        if "metadata" in agent_response:
            metadata.update(agent_response["metadata"])

        return {
            "response": agent_response["response"],
            "metadata": metadata
        }

    async def _route_to_agent(
        self,
        query: str,
        intent: str,
        context: Dict
    ) -> Dict[str, Any]:
        """
        Route query to the appropriate specialized agent

        Args:
            query: Original user query
            intent: Detected intent
            context: Conversation context with entities, parameters, and filters

        Returns:
            Response from the appropriate agent
        """
        # Route based on intent
        if intent == 'recipe_search':
            return await self.recipe_agent.process(query, context)

        elif intent == 'nutritional_info':
            return await self.nutrition_agent.process(query, context)

        elif intent == 'ingredient_substitution':
            # For now, route to nutritional agent
            # Can be replaced with dedicated substitution agent later
            return await self._handle_substitution(query, context)

        elif intent == 'recommendation':
            # For now, route to recipe agent
            # Can be replaced with dedicated recommendation agent later
            return await self._handle_recommendation(query, context)

        else:  # general_chat or unknown
            return await self._handle_general_chat(query, context)

    async def _handle_substitution(self, query: str, context: Dict) -> Dict[str, Any]:
        """Handle ingredient substitution requests"""
        messages = [
            {"role": "system", "content": self._format_system_prompt("""You are a cooking expert helping users find ingredient substitutes.

When suggesting substitutes:
- Consider the ingredient's role in the recipe (binding, flavor, texture)
- Suggest common, accessible alternatives
- Mention any cooking adjustments needed
- Keep suggestions practical and helpful

Keep responses under 200 words.""")},
            {"role": "user", "content": query}
        ]

        response = self._call_openai(messages, temperature=0.7, max_tokens=250)

        return {
            "response": response,
            "metadata": {"agent_used": "substitution"}
        }

    async def _handle_recommendation(self, query: str, context: Dict) -> Dict[str, Any]:
        """Handle personalized recommendation requests"""
        # For POC, use recipe search with a general query
        recommendation_query = f"Recommend a recipe based on: {query}"

        return await self.recipe_agent.process(recommendation_query, context)

    async def _handle_general_chat(self, query: str, context: Dict) -> Dict[str, Any]:
        """Handle general chat and greetings"""
        messages = [
            {"role": "system", "content": self.general_prompt},
            {"role": "user", "content": query}
        ]

        # Check for conversation history to personalize
        history = context.get("conversation_history", [])
        if history:
            # Add recent messages for context
            recent = history[-4:] if len(history) > 4 else history
            for msg in recent:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        response = self._call_openai(messages, temperature=0.8, max_tokens=200)

        return {
            "response": response,
            "metadata": {"agent_used": "general"}
        }

    def get_agent_for_intent(self, intent: str) -> Optional[str]:
        """Get the agent name for a given intent"""
        return self.INTENT_AGENT_MAP.get(intent)

    def get_supported_intents(self) -> Dict[str, str]:
        """Get all supported intents and their descriptions"""
        return self.nlid_agent.INTENTS
