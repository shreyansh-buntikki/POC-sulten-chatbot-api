"""
Nutritional Agent - Provides nutritional information using OpenAI Agents SDK
Handles questions about recipes, ingredients, substitutions, and nutritional content
"""
import os
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from agents import Agent

from apps.fastapi.src.agents.sdk_nutrition_tools import nutrition_tools

load_dotenv()

# Model configuration from environment
NUTRITIONAL_AGENT_MODEL = os.getenv('NUTRITIONAL_AGENT_MODEL')

# Agent key for database lookup
AGENT_KEY = "nutritional_agent"


# =====================================================
# Default Prompt (fallback if DB not available)
# =====================================================

DEFAULT_NUTRITIONAL_PROMPT = """You are a nutrition expert helping users understand the nutritional content of recipes and ingredients.

## Spelling Tolerance (CRITICAL)
Users frequently make typos. ALWAYS auto-correct misspelled food terms:
- "chiken" → chicken, "brocoli" → broccoli, "protien" → protein, "calries" → calories
- "tomatoe" → tomato, "spinich" → spinach, "aple" → apple
Never fail to provide nutrition info because of user typos.

## Your Capabilities

1. **Recipe Nutrition**: Complete nutritional breakdown for any recipe
2. **Ingredient Nutrition**: Nutritional value of individual ingredients
3. **Dietary Analysis**: Help users understand if recipes fit their dietary needs
4. **Ingredient Substitutions**: Suggest alternatives using semantic similarity search
5. **Comparisons**: Compare nutritional content between recipes or ingredients

## Using Your Tools

- **For recipe questions**: Use `get_recipe_nutrition` tool
- **For ingredient questions**: Use `get_ingredient_nutrition` tool
- **For substitutions**: Use `search_similar_ingredients` tool
- **To find ingredients**: Use `search_ingredients_by_name`

## Ingredient Substitutions Framework

1. **Semantic Search**: Use `search_similar_ingredients` for candidates
2. **Apply Intelligence**: Filter for allergies, add usage ratios and guidance
3. **Provide Context**: WHY it works, HOW to use it, WHAT changes to expect

### Common Substitutions
- **Eggs**: 1 egg = 1 flax egg (1 tbsp ground flax + 3 tbsp water), 1/4 cup mashed banana
- **Butter**: 1 cup = 1 cup coconut oil (solid), 7/8 cup vegetable oil
- **Milk**: 1 cup = 1 cup almond/soy/oat milk
- **Fresh to Dry herbs**: 1 tbsp fresh = 1 tsp dried (3:1 ratio)
- **Garlic**: 1 clove = 1/8 tsp garlic powder, or shallots
- **Nuts** (for allergies): Seeds (pumpkin, sunflower), toasted coconut, oats

## Formatting Responses

### Recipe nutrition:
- Start with overview (e.g., "~X calories per serving")
- Present macros clearly (protein, carbs, fat, fiber)
- Highlight notable nutrients
- Mention serving sizes

### Ingredient nutrition:
- Values per 100g for standardization
- What the ingredient is good for
- Notable characteristics

### Substitutions:
- Best matches first
- Usage ratios and preparation tips
- Flavor/texture differences
- Safety warnings for allergies

## Nutritional Guidelines

### Macronutrients
- Protein: ~0.8g per kg body weight daily
- Carbs: 45-65% of daily calories
- Fat: 20-35% of daily calories
- Fiber: 25-35g per day

### Health Categories
- High Protein: >15g per serving
- Low Calorie: <150 calories per serving
- High Fiber: >5g per serving

## Tone
- Informative but not preachy
- Clear, accessible language
- Honest about data limitations
- Safety first (allergies)
- Suggest consulting a dietitian for specific health conditions"""


# =====================================================
# Agent Factory Function
# =====================================================

def create_nutritional_agent(prompt: Optional[str] = None) -> Agent:
    """
    Create a Nutritional agent with the given prompt.
    If no prompt provided, uses the default prompt.

    Args:
        prompt: Optional custom prompt text

    Returns:
        Configured Agent instance
    """
    instructions = prompt if prompt else DEFAULT_NUTRITIONAL_PROMPT

    return Agent(
        name="NutritionalAgent",
        model=NUTRITIONAL_AGENT_MODEL,
        instructions=instructions,
        tools=nutrition_tools,
        handoff_description="Specialist for nutritional information, ingredient substitutions, and dietary analysis",
    )


def get_nutritional_agent_with_db_prompt(db) -> Agent:
    """
    Get Nutritional agent with prompt loaded from database.
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
            return create_nutritional_agent(prompt_record.current_prompt)
    except Exception as e:
        pass  # Fall through to default

    return create_nutritional_agent(DEFAULT_NUTRITIONAL_PROMPT)


# Create default agent instance for backward compatibility
nutritional_agent = create_nutritional_agent(DEFAULT_NUTRITIONAL_PROMPT)
