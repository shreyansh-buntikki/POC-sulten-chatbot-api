"""
NLID (Natural Language Intent Detection) Agent
Analyzes user queries to identify intent and extract entities
"""
import json
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from apps.fastapi.src.agents.base_agent import BaseAgent


class NLIDAgent(BaseAgent):
    """
    Natural Language Intent Detection Agent

    Analyzes user queries to identify:
    - Intent type (what the user wants to do)
    - Entities (ingredients, cuisines, dietary restrictions, etc.)
    - Parameters (time, servings, difficulty, etc.)
    - Filters (regional, seasonality, tags, allergies)
    - Whether query is cooking-related
    """

    # Intent definitions
    INTENTS = {
        'recipe_search': 'User wants to find or search for recipes',
        'nutritional_info': 'User asks about nutritional information',
        'ingredient_substitution': 'User wants ingredient substitutes',
        'recommendation': 'User wants personalized recommendations',
        'general_chat': 'General conversation or greeting'
    }

    def __init__(self, db: Session, openai_client=None):
        super().__init__(db, openai_client)
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the system prompt for intent detection"""
        return self._format_system_prompt("""Your task is to analyze user queries for a recipe and cooking platform.

You must identify:
1. **is_cooking_related** - Is this query related to cooking, recipes, or food? (true/false)
2. **intent** - What does the user want to do?
3. **entities** - Key ingredients, cuisines, dietary restrictions mentioned
4. **parameters** - Time constraints, servings, difficulty level, etc.
5. **filters** - Additional filters for recipe search

Supported intents:
- recipe_search: Finding recipes based on ingredients, cuisine, dietary needs, etc.
- nutritional_info: Questions about calories, macros, vitamins, health benefits
- ingredient_substitution: Asking for alternatives to ingredients
- recommendation: Asking for personalized suggestions (what should I cook?)
- general_chat: Greetings, small talk, general questions

IMPORTANT:
- If the query is NOT related to cooking, recipes, food, or nutrition, set is_cooking_related to false
- Examples of non-cooking queries: sports, politics, technology, weather, travel (unless food-related)
- Examples of cooking queries: recipes, ingredients, cooking techniques, nutrition, meal planning

Filters to extract:
- excluded_ingredients: Ingredients user wants to avoid (allergies, preferences)
- regional: Regional cuisine preferences (e.g., "italian", "asian", "mediterranean")
- seasonality: Seasonal preferences (e.g., "summer", "winter", "festive")
- tags: Specific tags or categories (e.g., "vegetarian", "quick", "healthy")

Respond ONLY with valid JSON in this exact format:
{
  "is_cooking_related": true/false,
  "intent": "intent_name",
  "entities": {
    "ingredients": ["ingredient1", "ingredient2"],
    "cuisines": ["cuisine1"],
    "dietary": ["restriction1"],
    "meal_type": "breakfast/lunch/dinner/snack",
    "keywords": ["other", "keywords"]
  },
  "parameters": {
    "max_time": 30,
    "servings": 4,
    "difficulty": "easy/medium/hard"
  },
  "filters": {
    "excluded_ingredients": ["allergen1", "allergen2"],
    "regional": ["italian", "asian"],
    "seasonality": ["summer", "festive"],
    "tags": ["vegetarian", "quick"]
  },
  "summary": "Brief summary of what the user is looking for"
}

Examples:
- "I need a quick dinner recipe with chicken, no peanuts" → recipe_search (excluded_ingredients: ["peanuts"])
- "How many calories in pasta?" → nutritional_info
- "What can I use instead of eggs, I'm allergic?" → ingredient_substitution
- "What should I make for dinner tonight?" → recommendation
- "Hello" or "How are you?" → general_chat
- "Who won the football game?" → general_chat (is_cooking_related: false)
- "What's the weather today?" → general_chat (is_cooking_related: false)
""")

    async def process(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the query to detect intent and extract entities

        Args:
            query: User's query text
            context: Additional context (user preferences, history, etc.)

        Returns:
            Dictionary with detected intent, entities, and parameters
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": query}
        ]

        # Call OpenAI with JSON response format
        response = self._call_openai(messages, temperature=0.3, response_format="json_object")

        # Parse the JSON response
        result = self._extract_json_from_response(response)

        if not result:
            # Fallback to general chat if parsing fails
            return {
                "is_cooking_related": self._is_cooking_related_fallback(query),
                "intent": "general_chat",
                "entities": {"keywords": [query[:50]]},
                "parameters": {},
                "filters": {
                    "excluded_ingredients": [],
                    "regional": [],
                    "seasonality": [],
                    "tags": []
                },
                "summary": query,
                "confidence": "low"
            }

        # Ensure filters exist
        if "filters" not in result:
            result["filters"] = {
                "excluded_ingredients": [],
                "regional": [],
                "seasonality": [],
                "tags": []
            }

        # Ensure is_cooking_related exists
        if "is_cooking_related" not in result:
            result["is_cooking_related"] = True

        # Add confidence score based on intent clarity
        result["confidence"] = self._assess_confidence(result, query)
        result["original_query"] = query

        return result

    def _is_cooking_related_fallback(self, query: str) -> bool:
        """
        Fallback method to check if query is cooking-related

        Args:
            query: User's query text

        Returns:
            True if query appears to be cooking-related
        """
        query_lower = query.lower()

        # Cooking-related keywords
        cooking_keywords = [
            'recipe', 'cook', 'food', 'ingredient', 'meal', 'breakfast',
            'lunch', 'dinner', 'snack', 'dessert', 'bake', 'roast',
            'fry', 'boil', 'grill', 'nutrition', 'calorie', 'protein',
            'carb', 'vitamin', 'healthy', 'diet', 'vegetarian', 'vegan',
            'gluten', 'allerg', 'substitute', 'flavor', 'taste', 'dish',
            'cuisine', 'chef', 'kitchen', 'spice', 'herb', 'sauce'
        ]

        # Check if any cooking keyword is present
        return any(keyword in query_lower for keyword in cooking_keywords)

    def _assess_confidence(self, result: Dict, query: str) -> str:
        """
        Assess confidence level of the intent detection

        Args:
            result: Parsed result from LLM
            query: Original query text

        Returns:
            Confidence level: high, medium, or low
        """
        # If not cooking related, low confidence
        if not result.get("is_cooking_related", True):
            return "high"  # High confidence it's not for us

        # High confidence if entities are found
        entities = result.get("entities", {})
        if entities.get("ingredients") or entities.get("cuisines") or entities.get("dietary"):
            return "high"

        # Medium confidence if parameters are found
        if result.get("parameters", {}) or result.get("filters", {}).get("excluded_ingredients"):
            return "medium"

        # Low confidence for general queries
        return "low" if result.get("intent") == "general_chat" else "medium"

    def get_intent_description(self, intent_name: str) -> str:
        """Get description for an intent"""
        return self.INTENTS.get(intent_name, "Unknown intent")

    def is_query_supported(self, nlid_result: Dict) -> tuple:
        """
        Check if the query is supported by this cooking assistant

        Args:
            nlid_result: Result from NLID agent processing

        Returns:
            Tuple of (is_supported, rejection_message)
        """
        if not nlid_result.get("is_cooking_related", True):
            return False, (
                "I'm a cooking assistant who can help you with recipes, ingredients, "
                "and nutrition. I can't help with that topic, but I'd be happy to "
                "assist you with any cooking-related questions!"
            )

        return True, None
