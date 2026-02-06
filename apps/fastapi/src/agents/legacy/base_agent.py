"""
Base Agent Class
Abstract base class for all AI agents in the chatbot system
"""
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# OpenAI configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')


class BaseAgent(ABC):
    """
    Abstract base class for all AI agents

    All agents must inherit from this class and implement the process method
    """

    def __init__(self, db: Session, openai_client: Optional[OpenAI] = None):
        """
        Initialize the agent

        Args:
            db: SQLAlchemy database session
            openai_client: Optional OpenAI client (if None, creates default)
        """
        self.db = db
        self.client = openai_client or (OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None)
        self.model = OPENAI_MODEL

        if not self.client:
            raise ValueError("OpenAI API key not configured. Please set OPENAI_API_KEY in .env")

    @abstractmethod
    async def process(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the user query and return a response

        Args:
            query: User's query text
            context: Additional context including:
                - user_uid: User identifier (optional)
                - session_id: Chat session identifier
                - conversation_history: List of previous messages
                - entities: Extracted entities from NLID agent
                - preferences: User preferences

        Returns:
            Dictionary containing:
                - response: Response text
                - metadata: Additional metadata (sources, confidence, etc.)
                - next_agent: Optional next agent to call
        """
        pass

    def _call_openai(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 500,
        response_format: Optional[str] = None
    ) -> str:
        """
        Call OpenAI API with the provided messages

        Args:
            messages: List of message dictionaries with role and content
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            response_format: Optional response format (e.g., "json_object")

        Returns:
            Response text from OpenAI
        """
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            if response_format:
                kwargs["response_format"] = {"type": response_format}

            response = self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error calling OpenAI: {e}")
            return "I apologize, but I encountered an error processing your request."

    def _format_system_prompt(self, prompt: str) -> str:
        """
        Format a system prompt for the agent

        Args:
            prompt: The raw prompt text

        Returns:
            Formatted system prompt
        """
        return f"""You are a helpful AI assistant for Sulten, a recipe and cooking platform.

{prompt}

Guidelines:
- Be friendly and conversational
- Provide practical, actionable advice
- When mentioning ingredients or recipes, be specific
- If you're unsure about something, acknowledge it
- Keep responses concise but informative
"""

    def _extract_json_from_response(self, response: str) -> Optional[Dict]:
        """
        Attempt to extract JSON from a response

        Args:
            response: Response text that may contain JSON

        Returns:
            Parsed JSON dict or None if parsing fails
        """
        import json
        import re

        # Try to find JSON in the response
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Try parsing the whole response
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return None

    def get_agent_name(self) -> str:
        """Return the name of this agent"""
        return self.__class__.__name__
