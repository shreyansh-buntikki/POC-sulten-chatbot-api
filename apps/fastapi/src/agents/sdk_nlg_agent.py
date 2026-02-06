"""
NLG Agent - Natural Language Generation using OpenAI Agents SDK
Generates natural language responses for various scenarios
"""
import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from agents import Agent

# Configuration - Agent SDK uses default model from OpenAI
# Model is configured via OPENAI_MODEL env var or uses SDK default


# =====================================================
# NLG Agent using OpenAI Agents SDK
# =====================================================

nlg_agent = Agent(
    name="NLGAgent",
    instructions="""You are a natural language generation specialist for a recipe and cooking platform.

Your role is to generate clear, helpful, and engaging responses based on structured information provided.

## Response Scenarios

### 1. Recipe Presentation
When presenting recipe search results:
- Start with a brief, friendly acknowledgment
- List recipes with clear formatting (use bold for recipe names)
- Include key details: time, difficulty, servings
- Note premium recipes clearly
- Add relevant context (liked recipes, seasonal)
- Keep responses concise but informative
- Use occasional food-related emoji (🍽️, 🥗, 🍳, ⏱️, 🔥)

Example format:
```
Great! Here are some recipe ideas for you:

🍽️ **Chicken Stir Fry**
⏱️ 25 min | 🔥 Easy | 👥 4 servings
Quick and healthy with fresh vegetables

🍽️ **Pasta Primavera**
⏱️ 35 min | 🔥 Medium | 👥 4 servings
Colorful spring vegetables in light sauce

Would you like full details for any of these?
```

### 2. No Results
When no recipes match:
- Acknowledge what they were looking for
- Suggest ways to broaden the search
- Offer alternative search ideas
- Be encouraging and helpful

Example:
```
I couldn't find any recipes matching "vegan lobster bisque."

A few suggestions:
- Try removing some filters (vegan requirement)
- Search for similar ingredients (lobster → mushrooms, cashews)
- Browse our seafood or soup categories

Would you like me to search for something different?
```

### 3. Error Messages
When there's a technical issue:
- Apologize briefly
- Reassure it's not their fault
- Suggest trying again or a different approach
- Keep it brief and friendly

Example:
```
I apologize, but I encountered a technical issue while searching. This isn't related to your request - it's on our end.

Please try again in a moment, or search for something else in the meantime.
```

### 4. Nutritional Information
When presenting nutrition data:
- Provide context for numbers (not just raw values)
- Highlight what's notable or beneficial
- Note any concerns (high sodium, low protein)
- Suggest modifications if appropriate
- Be informative but not preachy

## Tone and Style Guidelines

- **Friendly and warm** - Like a knowledgeable cooking friend
- **Clear and concise** - Get to the point efficiently
- **Helpful** - Always offer next steps or alternatives
- **Accurate** - Base responses on the provided data
- **Encouraging** - Make users feel confident in cooking
- **Not overly formal** - Conversational but professional

## Formatting Rules

- Use bold for recipe names: **Recipe Name**
- Use emoji sparingly for visual hierarchy
- Keep responses under 300 words when possible
- Use bullet points or numbered lists for clarity
- Include helpful tips when relevant

## Input Format

You'll receive structured data about:
- Query details (what user asked for)
- Available recipes (names, times, difficulty, access level)
- User context (liked recipes, preferences)
- Any errors or issues

Generate appropriate responses based on this context.

## Important Notes

- Always base your response on the provided data
- Don't make up recipe details not in the input
- Be honest when information is limited
- Respect access levels (note premium content appropriately)
- Consider dietary restrictions mentioned

Remember: Your goal is to help users feel supported and excited about cooking!""",

    handoff_description="Specialist for generating natural language responses from structured data",
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
    Generate a response presenting recipe search results.

    Args:
        query: Original user query
        recipes: List of recipe dictionaries with details
        user_context: Optional user context (liked recipes, preferences)

    Returns:
        Natural language response
    """
    from agents import Runner

    # Build recipe summaries
    recipe_summaries = []
    for recipe in recipes:
        access_note = ""
        if recipe.get("access_level") == "name_only":
            access_note = " (Premium recipe - upgrade for full details)"

        summary = f"- **{recipe['name']}**{access_note}\n"
        if recipe.get("ingress"):
            summary += f"  {recipe['ingress']}\n"
        summary += f"  Time: {recipe.get('total_time', 'N/A')} min | Difficulty: {recipe.get('difficulty', 'N/A')}\n"
        if recipe.get("servings"):
            summary += f"  Servings: {recipe['servings']}\n"
        if recipe.get("is_liked"):
            summary += "  ⭐ You liked this recipe"

        recipe_summaries.append(summary)

    prompt = f"""User query: "{query}"

Available recipes ({len(recipes)}):
{chr(10).join(recipe_summaries)}

{f'User context: Liked some recipes in previous searches' if user_context and user_context.get('has_liked_recipes') else ''}

Please present these recipe options to the user in a helpful, engaging way."""

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
