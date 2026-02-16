"""
NLG Agent - Natural Language Generation using OpenAI Agents SDK
Generates natural language responses for various scenarios

IMPORTANT: The frontend renders recipe cards from metadata, NOT from the text response.
The NLG agent should generate engaging responses that describe the value of the recipes
and enhance the user's experience - NOT list recipes and NEVER ask questions.
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
    instructions="""You are a warm, knowledgeable cooking assistant for a recipe platform.

CRITICAL: The frontend renders recipe cards from structured metadata. Your response should add value and context to the recipes - NOT list recipes and NEVER ask questions.

## Response Style

Be like a passionate food expert who:
- Highlights what makes these recipes special or valuable
- Describes flavors, techniques, or benefits
- Adds context that enhances the recipe cards
- Sounds confident and informative

## Response Scenarios

### 1. Recipe Results (Most Common)
When recipe search results are available:
- Generate 2-3 lines (40-50 words) that add value
- Describe what makes these recipes worth trying
- Highlight flavors, cooking techniques, or health benefits
- DO NOT ask questions - just provide informative context
- DO NOT list recipes - the frontend handles that

Examples:
- "Pasta is always a crowd-pleaser! These 5 recipes range from quick weeknight options to impressive dinner party dishes, all packed with authentic Italian flavors and fresh ingredients."
- "Chickpeas are incredibly versatile and protein-rich. These 4 vegetarian recipes showcase their nutty flavor and creamy texture in everything from hearty curries to fresh Mediterranean salads."
- "Healthy eating doesn't mean sacrificing flavor. These 6 recipes are designed to nourish your body while satisfying your taste buds, featuring wholesome ingredients and balanced nutrition."
- "Italian cuisine celebrates simplicity and quality ingredients. These 3 authentic recipes bring the warmth of a Roman trattoria to your kitchen, using traditional techniques passed down through generations."

### 2. No Results
When no recipes match:
- Acknowledge what they were looking for
- Explain why results might be limited
- Suggest related alternatives they might enjoy
- Keep it informative, not apologetic
- Display the filters applied that are limiting the results.

Example:
"I couldn't find an exact match for that combination, but don't worry - similar ingredients like mushrooms or cashews can create equally delicious creamy textures in vegan dishes."

### 3. Error Messages
When there's a technical issue:
- Apologize briefly
- Reassure them
- Be helpful

Example:
"I'm having a little trouble searching right now, but please try again in a moment - your perfect recipe is waiting to be found!"

## Tone and Style

- **Knowledgeable and confident** - Like a food expert
- **Value-adding** - Describe flavors, benefits, techniques
- **2-3 sentences** for recipe results (40-50 words)
- **No questions** - Just informative, descriptive statements
- **No recipe listings** - frontend handles that from metadata

Remember: Your goal is to add context and value that makes the user excited about the recipes. The frontend will display the actual recipe cards!""",

    handoff_description="Specialist for generating value-adding responses that describe recipe benefits and flavors",
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
    Generate an engaging 2-3 line response that adds value to recipe search results.

    IMPORTANT: The frontend renders recipe cards from metadata.
    This function should generate a response that describes what makes these
    recipes special - flavors, techniques, benefits - NOT list recipes and NEVER ask questions.

    Args:
        query: Original user query
        recipes: List of recipe dictionaries with details
        user_context: Optional user context (liked recipes, preferences)

    Returns:
        Engaging 2-3 line natural language response (40-50 words)
    """
    from agents import Runner

    recipe_count = len(recipes)

    prompt = f"""User query: "{query}"

Found {recipe_count} recipe(s) matching their search.

Generate a value-adding response (2-3 lines, 40-50 words) that:
1. Describes what makes these recipes special or worth trying
2. Highlights flavors, cooking techniques, or health benefits
3. Adds context that enhances the recipe cards

DO NOT:
- Ask questions
- List recipes or include recipe details
- Ask the user to narrow down or make choices

Examples:
- "Pasta is always a crowd-pleaser! These {recipe_count} recipes range from quick weeknight options to impressive dinner party dishes, all packed with authentic Italian flavors."
- "Chickpeas are incredibly versatile and protein-rich. These {recipe_count} recipes showcase their nutty flavor and creamy texture in everything from hearty curries to fresh salads."
- "Healthy eating doesn't mean sacrificing flavor. These {recipe_count} recipes nourish your body while satisfying your taste buds with wholesome ingredients."

Be confident, informative, and add value!"""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_no_results_response(
    query: str,
    intent: str,
    entities: Dict[str, Any],
    filters: Dict[str, Any]
) -> str:
    """
    Generate an informative response when no recipes match.

    Args:
        query: Original user query
        intent: Detected intent
        entities: Extracted entities
        filters: Applied filters

    Returns:
        Informative natural language response with alternatives
    """
    from agents import Runner

    prompt = f"""User query: "{query}"

No recipes matched their search.

Analysis:
- Intent: {intent}
- Entities they mentioned: {entities}
- Filters applied: {filters}

Generate an informative response (2-3 sentences) that:
1. Acknowledges what they were looking for
2. Explains why results might be limited
3. Suggests related alternatives they might enjoy

DO NOT ask questions. Just provide helpful information.

Example: "I couldn't find an exact match for that combination, but similar ingredients like mushrooms or cashews can create equally delicious creamy textures in vegan dishes."

Be helpful and informative!"""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_error_response(
    query: str,
    error_message: str
) -> str:
    """
    Generate a warm, reassuring response for technical errors.

    Args:
        query: Original user query
        error_message: Error details (for context, don't show to user)

    Returns:
        Warm, reassuring natural language response
    """
    from agents import Runner

    prompt = f"""User query: "{query}"

A technical error occurred while processing their request.

Generate a warm, reassuring response (1-2 sentences) that:
1. Apologizes briefly
2. Reassures them to try again

DO NOT ask questions or mention technical details.

Example: "I'm having a little trouble searching right now, but please try again in a moment - your perfect recipe is waiting to be found!"

Keep it light and friendly!"""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output
