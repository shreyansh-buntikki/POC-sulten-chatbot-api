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
NLG_AGENT_MODEL = os.getenv('NLG_AGENT_MODEL')

# Agent key for database lookup
AGENT_KEY = "nlg_agent"


# =====================================================
# Default Prompt (fallback if DB not available)
# =====================================================

DEFAULT_NLG_PROMPT = """You are a warm, knowledgeable cooking assistant for a recipe platform.

CRITICAL: The frontend renders recipe cards from structured metadata. Your response should add value and context to the recipes - NOT list recipes and NEVER ask questions.

## Spelling Tolerance
If the user's query contains typos (e.g., "chiken", "desset", "aple"), respond naturally using the corrected term without pointing out the mistake.

## Response Scenarios

### 1. Recipe Results (Most Common)
When recipe search results are available:
- Generate 2-3 lines (40-50 words) that add value
- Describe what makes these recipes special or worth trying
- Highlight flavors, cooking techniques, or health benefits
- DO NOT ask questions - just provide informative context
- DO NOT list recipes - the frontend handles that

Examples:
- "Pasta is always a crowd-pleaser! These 5 recipes range from quick weeknight options to impressive dinner party dishes, all packed with authentic Italian flavors and fresh ingredients."
- "Chickpeas are incredibly versatile and protein-rich. These 4 vegetarian recipes showcase their nutty flavor and creamy texture in everything from hearty curries to fresh Mediterranean salads."

### 2. Cost/Pricing Responses (CRITICAL FORMATTING)
When presenting pricing data, ALWAYS use the following format:

**For recipe cost (no country specified) - show ALL countries:**
Here's the pricing information for [Recipe Name]:

Norway: kr[TOTAL] per serving
  [Ingredient 1] ([amount] [unit]): kr[price]
  [Ingredient 2] ([amount] [unit]): kr[price]

India: ₹[TOTAL] per serving
  [Ingredient 1] ([amount] [unit]): ₹[price]
  [Ingredient 2] ([amount] [unit]): ₹[price]

United States: $[TOTAL] per serving
  [Ingredient 1] ([amount] [unit]): $[price]
  [Ingredient 2] ([amount] [unit]): $[price]

**For recipe cost (specific country):**
The price of [Recipe Name] in [Country] is [SYMBOL][TOTAL] per serving.
  [Ingredient 1] ([amount] [unit]): [SYMBOL][price]
  [Ingredient 2] ([amount] [unit]): [SYMBOL][price]

**For ingredient pricing (no country):**
Here's the pricing for [Ingredient]:
  Norway: kr[price] per [unit]
  India: ₹[price] per [unit]
  United States: $[price] per [unit]

**For ingredient pricing (specific country):**
The price of [Ingredient] in [Country] is [SYMBOL][price] per [unit].

Currency symbols: Norway → kr, India → ₹, United States → $

### 3. No Results
When no recipes match:
- Acknowledge what they were looking for
- Explain why results might be limited
- Suggest related alternatives they might enjoy
- Display the filters applied that are limiting the results

### 4. Error Messages
When there's a technical issue:
- Apologize briefly and reassure them to try again

## Tone and Style
- **Knowledgeable and confident** - Like a food expert
- **2-3 sentences** for recipe results (40-50 words)
- **No questions** - Just informative, descriptive statements
- **No recipe listings** - frontend handles that from metadata

Remember: Your goal is to add context and value that makes the user excited about the recipes!"""


# =====================================================
# Agent Factory Function
# =====================================================

def create_nlg_agent(prompt: Optional[str] = None) -> Agent:
    """
    Create an NLG agent with the given prompt.
    If no prompt provided, uses the default prompt.

    Args:
        prompt: Optional custom prompt text

    Returns:
        Configured Agent instance
    """
    instructions = prompt if prompt else DEFAULT_NLG_PROMPT

    return Agent(
        name="NLGAgent",
        model=NLG_AGENT_MODEL,
        instructions=instructions,
        handoff_description="Specialist for generating value-adding responses that describe recipe benefits and flavors",
    )


def get_nlg_agent_with_db_prompt(db) -> Agent:
    """
    Get NLG agent with prompt loaded from database.
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
            return create_nlg_agent(prompt_record.current_prompt)
    except Exception as e:
        pass  # Fall through to default

    return create_nlg_agent(DEFAULT_NLG_PROMPT)


# Create default agent instance for backward compatibility
nlg_agent = create_nlg_agent(DEFAULT_NLG_PROMPT)


# =====================================================
# Helper functions for specific scenarios
# =====================================================

async def generate_recipe_response(
    query: str,
    recipes: List[Dict[str, Any]],
    user_context: Optional[Dict[str, Any]] = None,
    agent: Optional[Agent] = None
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
        agent: Optional custom agent to use (if None, uses default nlg_agent)

    Returns:
        Engaging 2-3 line natural language response (40-50 words)
    """
    from agents import Runner

    recipe_count = len(recipes)
    use_agent = agent if agent else nlg_agent

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

    result = await Runner.run(use_agent, prompt)
    return result.final_output


async def generate_no_results_response(
    query: str,
    intent: str,
    entities: Dict[str, Any],
    filters: Dict[str, Any],
    agent: Optional[Agent] = None
) -> str:
    """
    Generate an informative response when no recipes match.

    Args:
        query: Original user query
        intent: Detected intent
        entities: Extracted entities
        filters: Applied filters
        agent: Optional custom agent to use (if None, uses default nlg_agent)

    Returns:
        Informative natural language response with alternatives
    """
    from agents import Runner

    use_agent = agent if agent else nlg_agent

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

    result = await Runner.run(use_agent, prompt)
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


async def generate_recipe_detail_response(
    recipe: Dict[str, Any],
    detail_type: str,
    query: str
) -> str:
    """
    Generate a detailed response for a specific recipe reference query.

    Args:
        recipe: Recipe dictionary with full details
        detail_type: Type of detail requested (full, ingredients, instructions, nutrition, cost)
        query: Original user query

    Returns:
        Detailed natural language response about the recipe
    """
    from agents import Runner

    recipe_name = recipe.get("name", "this recipe")

    # Build context based on detail type
    if detail_type == "ingredients":
        ingredients = recipe.get("ingredients", [])
        ingredients_text = "\n".join([
            f"- {ing.get('amount', '')} {ing.get('unit', '')} {ing.get('name', '')}".strip()
            for ing in ingredients[:15]
        ]) if ingredients else "No ingredients available"

        prompt = f"""User asked: "{query}"

They want to know the ingredients for "{recipe_name}".

Ingredients:
{ingredients_text}

Generate a friendly response that presents these ingredients clearly.
Include the recipe name at the start. Be concise and helpful."""

    elif detail_type == "instructions":
        instructions = recipe.get("instructions", [])
        instructions_text = "\n".join([
            f"{i+1}. {inst.get('description', inst.get('text', ''))}"
            for i, inst in enumerate(instructions[:10])
        ]) if instructions else "No instructions available"

        prompt = f"""User asked: "{query}"

They want to know the instructions for "{recipe_name}".

Instructions:
{instructions_text}

Generate a friendly response that presents these cooking steps clearly.
Include the recipe name at the start. Be concise and helpful."""

    elif detail_type == "nutrition":
        nutrition = recipe.get("nutrition") or {}
        if recipe.get("recipe_metadata"):
            import json
            metadata = recipe.get("recipe_metadata", {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            nutrition = metadata.get("totalNutrition", {}).get("macros", {})

        prompt = f"""User asked: "{query}"

They want to know the nutrition for "{recipe_name}".

Nutrition per serving:
- Calories: {nutrition.get('energyKcal', 'N/A')} kcal
- Protein: {nutrition.get('protein', 'N/A')}g
- Carbs: {nutrition.get('carbohydrates', 'N/A')}g
- Fat: {nutrition.get('totalFat', 'N/A')}g

Generate a friendly response that presents the nutrition information clearly.
Include the recipe name at the start. Be concise."""

    elif detail_type == "cost":
        cost = recipe.get("cost") or {}
        usa_cost = cost.get("usa", {}).get("total", "N/A")

        prompt = f"""User asked: "{query}"

They want to know the cost for "{recipe_name}".

Estimated cost: ${usa_cost}

Generate a brief response about the recipe cost.
Include the recipe name. Be concise."""

    else:  # full details
        prep_time = recipe.get("prepTime", 0) or 0
        cook_time = recipe.get("cookTime", 0) or 0
        total_time = prep_time + cook_time
        description = recipe.get("ingress") or recipe.get("description", "")

        prompt = f"""User asked: "{query}"

They want full details about "{recipe_name}".

Recipe Details:
- Description: {description[:300]}
- Total time: {total_time} minutes
- Servings: {recipe.get('servings', 'N/A')}
- Difficulty: {recipe.get('difficulty', 'N/A')}

Generate an engaging response (3-4 sentences) that:
1. Introduces the recipe with its name and description
2. Highlights the key details (time, servings, difficulty)
3. Makes it sound appealing

Be enthusiastic but concise!"""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_educational_response(
    query: str,
    concept: Optional[str],
    related_recipes: List[Dict[str, Any]]
) -> str:
    """
    Generate an educational response about cooking concepts or techniques.

    Args:
        query: Original user query
        concept: The cooking concept or technique being asked about
        related_recipes: List of related recipes (if any)

    Returns:
        Educational natural language response
    """
    from agents import Runner

    recipes_text = ""
    if related_recipes:
        recipes_text = f"\n\nRelated recipes:\n" + "\n".join([
            f"- {r.get('name', 'Recipe')}" for r in related_recipes[:5]
        ])

    prompt = f"""User asked: "{query}"

They want to learn about: {concept or 'a cooking topic'}

Provide an educational, helpful response (2-3 paragraphs) that:
1. Explains the concept or technique clearly
2. Includes practical tips where relevant
3. If about a dietary concept (vegan, keto, etc.), mention what foods are included/excluded
4. If about a technique, briefly explain how to do it
{recipes_text}

Be informative but conversational. End with a suggestion for recipes they might want to try."""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_combined_meal_response(
    query: str,
    meal_recipes: Dict[str, Dict[str, Any]],
    combined_budget: Optional[float] = None
) -> str:
    """
    Generate a response for combined meal search (multi-course meals).

    Args:
        query: Original user query
        meal_recipes: Dictionary mapping course types to recipe details
        combined_budget: Total budget for the meal (if specified)

    Returns:
        Natural language response suggesting the meal combination
    """
    from agents import Runner

    courses_text = []
    for course, recipe in meal_recipes.items():
        time_str = f"{recipe.get('total_time', 0)} mins" if recipe.get('total_time') else ""
        courses_text.append(f"- {course.title()}: {recipe.get('name', 'Recipe')} ({time_str})")

    budget_text = f"\nBudget: ${combined_budget}" if combined_budget else ""

    prompt = f"""User asked: "{query}"

I found a {len(meal_recipes)}-course meal suggestion:

{chr(10).join(courses_text)}
{budget_text}

Generate an engaging response (2-3 sentences) that:
1. Introduces the meal combination
2. Highlights what makes these courses work well together
3. Mentions the total number of courses

Be enthusiastic and make the meal sound delicious!"""

    result = await Runner.run(nlg_agent, prompt)
    return result.final_output


async def generate_no_results_with_context_response(
    query: str,
    active_filters: Dict[str, Any],
    excluded_recipe_count: int = 0,
    agent: Optional[Agent] = None
) -> str:
    """
    Generate an informative response when no recipes match, with filter context.

    Args:
        query: Original user query
        active_filters: Dictionary of active filters limiting results
        excluded_recipe_count: Number of recipes excluded from previous negative feedback
        agent: Optional custom agent to use (if None, uses default nlg_agent)

    Returns:
        Informative natural language response with suggestions
    """
    from agents import Runner

    use_agent = agent if agent else nlg_agent

    # Build filter description
    filter_items = []

    if active_filters.get("excluded_ingredients"):
        filter_items.append(f"excluding: {', '.join(active_filters['excluded_ingredients'][:5])}")
    if active_filters.get("tags"):
        filter_items.append(f"preferences: {', '.join(active_filters['tags'])}")
    if active_filters.get("cuisines"):
        filter_items.append(f"cuisines: {', '.join(active_filters['cuisines'])}")
    if active_filters.get("max_time"):
        filter_items.append(f"under {active_filters['max_time']} minutes")
    if active_filters.get("difficulty"):
        filter_items.append(f"difficulty: {active_filters['difficulty']}")
    if excluded_recipe_count > 0:
        filter_items.append(f"{excluded_recipe_count} recipes excluded")

    filters_text = "\n".join([f"- {item}" for item in filter_items]) if filter_items else "No specific filters"

    prompt = f"""User searched for: "{query}"

No recipes matched their search.

Active filters that limited results:
{filters_text}

Generate a helpful response (2-3 sentences) that:
1. Acknowledges what they were looking for
2. Mentions which filters are limiting results (if any)
3. Suggests they can say "clear filters" to start fresh or try different keywords

Be helpful, not apologetic. Focus on solutions!"""

    result = await Runner.run(use_agent, prompt)
    return result.final_output
