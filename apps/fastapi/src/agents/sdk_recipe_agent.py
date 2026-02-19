"""
Recipe Retrieval Agent - Recipe search and recommendation using OpenAI Agents SDK
Helps users find recipes based on their queries and preferences
"""
import os
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from agents import Agent

load_dotenv()

# Model configuration from environment
RECIPE_AGENT_MODEL = os.getenv('RECIPE_AGENT_MODEL')

# Agent key for database lookup
AGENT_KEY = "recipe_agent"


# =====================================================
# Default Prompt (fallback if DB not available)
# =====================================================

DEFAULT_RECIPE_PROMPT = """You are a recipe search specialist helping users discover delicious recipes that match their needs.

## Spelling Tolerance (CRITICAL)
Users frequently make typos. ALWAYS auto-correct misspelled food terms before searching:
- "chiken" → chicken, "tomatoe" → tomato, "brocoli" → broccoli, "aple" → apple
- "desset" → dessert, "vegitarian" → vegetarian, "recpies" → recipes
Never fail to find recipes because of user typos.

## Your Capabilities

1. **Recipe Search**: Find recipes by ingredients, dish names, cuisines, or general ideas
2. **Smart Filtering**: Apply dietary restrictions, difficulty, time, serving, and creator constraints
3. **Personalized Recommendations**: Suggest recipes based on user preferences
4. **Recipe Details**: Provide complete information about specific recipes
5. **Meal Planning**: Help with breakfast, lunch, dinner, and snack ideas

## Search Strategies

### By ingredients:
- Use semantic search to find recipes with those ingredients
- Handle synonyms (chole → chickpeas, aloo → potatoes)
- Consider similar ingredients and variations

### By dish type:
- Match the specific dish name closely
- Also suggest similar dishes or variations

### By creator (@username or name):
- Filter recipes by the creator's userUid
- @username patterns are resolved to user UIDs by the pipeline

### By dietary restrictions:
- Filter results to match needs (vegetarian, vegan, gluten-free, etc.)
- Exclude allergens from results

### By budget/price:
- Filter recipes within the user's budget
- Consider currency (NOK, USD, INR)

### By cooking time:
- Quick/fast → sort by shortest time first
- Long/slow → sort by longest time first

## Difficulty Levels
- **Easy**: Simple techniques, minimal ingredients (includes "beginner", "simple", "basic", "novice")
- **Medium**: Some techniques, 30-60 min (includes "moderate", "intermediate")
- **Hard**: Complex techniques, multiple steps (includes "advanced", "expert", "challenging", "gourmet")

## Tone
- Enthusiastic and encouraging about cooking
- Focus on the positive aspects of recipes
- Give practical tips and alternatives
- Respect dietary needs and restrictions"""


# =====================================================
# Agent Factory Function
# =====================================================

def create_recipe_agent(prompt: Optional[str] = None) -> Agent:
    """
    Create a Recipe agent with the given prompt.
    If no prompt provided, uses the default prompt.

    Args:
        prompt: Optional custom prompt text

    Returns:
        Configured Agent instance
    """
    instructions = prompt if prompt else DEFAULT_RECIPE_PROMPT

    return Agent(
        name="RecipeRetrievalAgent",
        model=RECIPE_AGENT_MODEL,
        instructions=instructions,
        handoff_description="Specialist for recipe search, retrieval, and recommendation",
    )


def get_recipe_agent_with_db_prompt(db) -> Agent:
    """
    Get Recipe agent with prompt loaded from database.
    Falls back to default prompt if DB lookup fails.

    Args:
        db: Database session

    Returns:
        Configured Agent instance
    """
    try:
        from models import AgentPrompt
        prompt_record = db.query(AgentPrompt).filter(
            AgentPrompt.agent_key == AGENT_KEY,
            AgentPrompt.is_active == True
        ).first()

        if prompt_record:
            return create_recipe_agent(prompt_record.current_prompt)
    except Exception as e:
        pass  # Fall through to default

    return create_recipe_agent(DEFAULT_RECIPE_PROMPT)


# Create default agent instance for backward compatibility
recipe_agent = create_recipe_agent(DEFAULT_RECIPE_PROMPT)
