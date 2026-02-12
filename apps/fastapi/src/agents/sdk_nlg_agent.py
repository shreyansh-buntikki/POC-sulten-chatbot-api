"""
NLG Agent - Natural Language Generation using OpenAI Agents SDK
Generates natural language responses for various scenarios

IMPORTANT: The frontend renders recipe cards from metadata, NOT from the text response.
The NLG agent should ONLY generate a brief 1-liner intro, NOT list recipes.
"""
import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from agents import Agent

load_dotenv()

# Model configuration from environment
NLG_AGENT_MODEL = os.getenv('NLG_AGENT_MODEL', 'gpt-5-mini')


# =====================================================
# NLG Agent using OpenAI Agents SDK
# =====================================================

nlg_agent = Agent(
    name="NLGAgent",
    model=NLG_AGENT_MODEL,
    instructions="""You are a natural language generation specialist for a recipe and cooking platform.

CRITICAL: The frontend renders recipe cards from structured metadata. Your response should ONLY be a brief 1-liner introduction, NOT a full recipe listing.

## Response Scenarios

### 1. Recipe Results (Most Common)
When recipe search results are available:
- Generate ONLY a 1-liner intro saying what you found
- DO NOT list recipes or include recipe details - the frontend handles that
- Keep it under 20 words
- Be friendly and concise

Examples:
- "Great! I found 5 pasta recipes for you."
- "I found 8 quick dinner recipes matching your search."
- "Here are 3 vegetarian recipes with chickpeas."
- "I found 6 Italian recipes under 30 minutes."

### 2. No Results
When no recipes match:
- Acknowledge what they were looking for
- Suggest ways to broaden the search
- Offer alternative search ideas
- Keep it to 2-3 sentences

Example:
"I couldn't find any recipes matching 'vegan lobster bisque.' Try removing some filters or searching for similar ingredients like mushrooms or cashews."

### 3. Error Messages
When there's a technical issue:
- Apologize briefly
- Reassure it's not their fault
- Suggest trying again

Example:
"I apologize, but I encountered a technical issue while searching. Please try again in a moment."

## Tone and Style

- **Friendly and warm** - Like a knowledgeable cooking friend
- **Clear and concise** - Get to the point efficiently
- **Single sentence** for recipe results (1-liner)
- **No recipe listings** - frontend handles that from metadata

Remember: Your goal is to provide a brief, friendly introduction. The frontend will display the actual recipe cards!""",

    handoff_description="Specialist for generating brief natural language responses from structured data",
)


# =====================================================
# Helper functions for specific scenarios
# =====================================================

async def generate_recipe_response(
    query: str,
    recipes: List[Dict[str, Any]],
    user_context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Generate a brief 1-liner intro for recipe search results.

    IMPORTANT: The frontend renders recipe cards from metadata.
    This function should ONLY generate a 1-liner intro, NOT list recipes.

    Args:
        query: Original user query
        recipes: List of recipe dictionaries with details
        user_context: Optional user context (liked recipes, preferences)

    Returns:
        Brief 1-liner natural language response
    """
    from agents import Runner

    # Build a simple prompt with just the count and query context
    # NO recipe details - frontend handles rendering
    prompt = f"""User query: "{query}"

Found {len(recipes)} recipe(s) matching their search.

Generate a brief 1-liner intro (under 20 words) saying what you found.
DO NOT list recipes or include recipe details - the frontend will display recipe cards from metadata.

Examples:
- "Great! I found {len(recipes)} recipe(s) for you."
- "I found {len(recipes)} recipes matching your search."
- "Here are {len(recipes)} recipes for {query[:30]}..."

Keep it friendly and concise."""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_no_results_response(
    query: str,
    intent: str,
    entities: Dict[str, Any],
    filters: Dict[str, Any]
) -> str:
    """
    Generate a response when no recipes match.

    Args:
        query: Original user query
        intent: Detected intent
        entities: Extracted entities
        filters: Applied filters

    Returns:
        Natural language response
    """
    from agents import Runner

    prompt = f"""User query: "{query}"

No recipes matched their search.

Analysis:
- Intent: {intent}
- Entities they mentioned: {entities}
- Filters applied: {filters}

Generate a helpful response suggesting alternatives or ways to broaden the search."""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_error_response(
    query: str,
    error_message: str
) -> str:
    """
    Generate a response for technical errors.

    Args:
        query: Original user query
        error_message: Error details (for context, don't show to user)

    Returns:
        Natural language response
    """
    from agents import Runner

    prompt = f"""User query: "{query}"

A technical error occurred while processing their request.

Generate a brief, friendly apology and suggest they try again or search for something else.
Don't mention the technical error details to the user."""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output
