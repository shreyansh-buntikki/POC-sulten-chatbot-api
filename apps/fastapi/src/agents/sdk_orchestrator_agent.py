"""
Orchestrator Agent - Main coordinator using OpenAI Agents SDK with handoffs
Routes user queries to the appropriate specialist agent or handles directly
"""
import os
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from agents import Agent, InputGuardrail, GuardrailFunctionOutput, Runner, AgentOutputSchema

load_dotenv()

# Model configuration from environment
ORCHESTRATOR_AGENT_MODEL = os.getenv('ORCHESTRATOR_AGENT_MODEL')
COOKING_GUARDRAIL_MODEL = os.getenv('COOKING_GUARDRAIL_MODEL')

# Agent keys for database lookup
ORCHESTRATOR_AGENT_KEY = "orchestrator_agent"
COOKING_GUARDRAIL_KEY = "cooking_guardrail"

# Import specialist agents
from apps.fastapi.src.agents.sdk_nlid_agent import nlid_agent, IntentOutput
from apps.fastapi.src.agents.sdk_recipe_agent import recipe_agent
from apps.fastapi.src.agents.sdk_nutritional_agent import nutritional_agent


# =====================================================
# Guardrail Models
# =====================================================

class CookingRelatedOutput(BaseModel):
    """Output from cooking-related guardrail check"""
    is_cooking_related: bool = Field(
        description="Whether the query is related to cooking, recipes, food, or kitchen activities"
    )
    reasoning: str = Field(
        description="Brief explanation of why this is or isn't cooking-related"
    )


# =====================================================
# Default Prompts (fallback if DB not available)
# =====================================================

DEFAULT_COOKING_GUARDRAIL_PROMPT = """You are a guardrail that checks if a user query is related to cooking, recipes, food, or kitchen activities.

A query is cooking-related if it mentions:
- Food, recipes, cooking, baking, kitchen
- Ingredients, dishes, meals, cuisines
- Nutrition, diets, allergies, substitutions
- Kitchen tools, techniques, methods
- Meal planning, food preparation
- **Price, cost, budget of ingredients or food items** (e.g., "cost of wheat flour", "price of tomatoes")

Examples of cooking-related queries:
- "Find chicken recipes"
- "How do I make pasta?"
- "What's for dinner?"
- "Is this healthy?"
- "Substitute for eggs"
- "What is the cost of wheat flour?"
- "Price of tomatoes"

Examples of NON-cooking queries:
- "What's the weather?"
- "Tell me about sports"
- "Help with my computer"
- "Latest news headlines"
- "Who won the game?"

Return your assessment as a JSON object with:
- is_cooking_related: true/false
- reasoning: brief explanation"""


DEFAULT_ORCHESTRATOR_PROMPT = """You are the main cooking assistant coordinator for a recipe and food platform.

Your role is to help users with their cooking-related questions by either:
1. Routing them to the appropriate specialist agent
2. Handling general cooking chat yourself

## Your Specialist Agents

You have access to these specialist agents via handoffs:

### 🍽️ RecipeRetrievalAgent
Use this agent for:
- Recipe searches ("find chicken recipes", "pasta dishes")
- Recipe recommendations ("what should I make for dinner?")
- Recipe details ("tell me more about this recipe")
- Meal planning ("breakfast ideas", "quick lunches")
- Ingredient-based searches ("recipes with tomatoes and basil")

### 🥗 NutritionalAgent
Use this agent for:
- Nutritional information ("how many calories in this?", "protein content")
- Dietary analysis ("is this healthy?", "fit for keto diet?")
- Ingredient nutrition ("what nutrients in spinach?")
- Health-related food questions

### 🔍 NLIDAgent (Intent Detection)
Use this agent to:
- Analyze user intent and extract entities
- Parse complex queries with multiple constraints
- Understand what the user is really looking for

## Routing Logic

When a user query arrives:

1. **For recipe searches and meal ideas** → Route to RecipeRetrievalAgent
   - Keywords: find, search, recipe, dinner, lunch, breakfast, make, cook, dish

2. **For nutritional questions** → Route to NutritionalAgent
   - Keywords: calories, protein, carbs, fat, healthy, nutrition, vitamins, diet

3. **For general cooking chat** → Handle yourself
   - Greetings, thanks, general conversation
   - Simple cooking tips and advice
   - Explanations of cooking techniques

## Handling the Conversation

### Initial Response Style:
- Be warm and welcoming
- Acknowledge what they're looking for
- Either route to specialist or provide helpful response
- Always offer further assistance

### After Specialist Agent Returns:
- Present the specialist's findings in a clear, friendly way
- Add any helpful context or suggestions
- Ask if they need more details or have other questions

### For Non-Cooking Queries:
- Politely explain you specialize in cooking and food
- Offer to help with cooking-related questions instead
- Be friendly but clear about your scope

## Tone and Style

- Warm and friendly (like a knowledgeable cooking friend)
- Enthusiastic about food and cooking
- Clear and concise in responses
- Helpful and supportive
- Not overly formal, but professional

## Your Goals

1. Help users find what they're looking for quickly
2. Route to the right specialist when needed
3. Provide helpful responses for general cooking questions
4. Maintain a friendly, supportive conversation
5. Always offer further assistance

Remember: You're the face of the cooking assistant - make every interaction helpful and pleasant!"""


# =====================================================
# Agent Factory Functions
# =====================================================

def create_cooking_guardrail_agent(prompt: Optional[str] = None) -> Agent:
    """
    Create a Cooking Guardrail agent with the given prompt.
    """
    instructions = prompt if prompt else DEFAULT_COOKING_GUARDRAIL_PROMPT

    return Agent(
        name="CookingGuardrail",
        model=COOKING_GUARDRAIL_MODEL,
        instructions=instructions,
        output_type=AgentOutputSchema(CookingRelatedOutput, strict_json_schema=False),
    )


def create_orchestrator_agent(prompt: Optional[str] = None, guardrail_agent: Optional[Agent] = None) -> Agent:
    """
    Create an Orchestrator agent with the given prompt.
    Uses default handoff agents (recipe_agent, nutritional_agent, nlid_agent).
    """
    instructions = prompt if prompt else DEFAULT_ORCHESTRATOR_PROMPT

    # Use provided guardrail agent or create default
    guardrail = guardrail_agent if guardrail_agent else create_cooking_guardrail_agent()

    async def cooking_guardrail_func(ctx, agent, input_data):
        result = await Runner.run(guardrail, input_data, context=ctx.context)
        output = result.final_output
        final_output = output if isinstance(output, CookingRelatedOutput) else CookingRelatedOutput(**output)
        return GuardrailFunctionOutput(
            output_info=final_output,
            tripwire_triggered=not final_output.is_cooking_related,
        )

    return Agent(
        name="OrchestratorAgent",
        model=ORCHESTRATOR_AGENT_MODEL,
        instructions=instructions,
        handoffs=[
            recipe_agent,
            nutritional_agent,
            nlid_agent,
        ],
        input_guardrails=[
            InputGuardrail(guardrail_function=cooking_guardrail_func),
        ],
        handoff_description="Main cooking assistant coordinator that routes queries to specialist agents",
    )


def create_orchestrator_with_custom_agents(
    prompt: Optional[str] = None,
    guardrail_agent: Optional[Agent] = None,
    custom_recipe_agent: Optional[Agent] = None,
    custom_nutritional_agent: Optional[Agent] = None,
    custom_nlid_agent: Optional[Agent] = None
) -> Agent:
    """
    Create an Orchestrator agent with custom handoff agents.

    This allows using custom prompts for all agents in the handoff chain.

    Args:
        prompt: Custom orchestrator prompt (uses default if None)
        guardrail_agent: Custom guardrail agent (creates default if None)
        custom_recipe_agent: Custom recipe agent (uses default if None)
        custom_nutritional_agent: Custom nutritional agent (uses default if None)
        custom_nlid_agent: Custom NLID agent (uses default if None)

    Returns:
        Configured Orchestrator Agent with custom handoffs
    """
    instructions = prompt if prompt else DEFAULT_ORCHESTRATOR_PROMPT

    # Use provided guardrail agent or create default
    guardrail = guardrail_agent if guardrail_agent else create_cooking_guardrail_agent()

    # Use provided custom agents or fall back to defaults
    handoff_recipe = custom_recipe_agent if custom_recipe_agent else recipe_agent
    handoff_nutritional = custom_nutritional_agent if custom_nutritional_agent else nutritional_agent
    handoff_nlid = custom_nlid_agent if custom_nlid_agent else nlid_agent

    async def cooking_guardrail_func(ctx, agent, input_data):
        result = await Runner.run(guardrail, input_data, context=ctx.context)
        output = result.final_output
        final_output = output if isinstance(output, CookingRelatedOutput) else CookingRelatedOutput(**output)
        return GuardrailFunctionOutput(
            output_info=final_output,
            tripwire_triggered=not final_output.is_cooking_related,
        )

    return Agent(
        name="OrchestratorAgent",
        model=ORCHESTRATOR_AGENT_MODEL,
        instructions=instructions,
        handoffs=[
            handoff_recipe,
            handoff_nutritional,
            handoff_nlid,
        ],
        input_guardrails=[
            InputGuardrail(guardrail_function=cooking_guardrail_func),
        ],
        handoff_description="Main cooking assistant coordinator that routes queries to specialist agents",
    )


def get_orchestrator_agents_with_db_prompts(db):
    """
    Get Orchestrator and Guardrail agents with prompts loaded from database.
    Falls back to default prompts if DB lookup fails.

    Args:
        db: Database session

    Returns:
        Tuple of (orchestrator_agent, cooking_guardrail_agent)
    """
    guardrail_prompt = DEFAULT_COOKING_GUARDRAIL_PROMPT
    orchestrator_prompt = DEFAULT_ORCHESTRATOR_PROMPT

    try:
        from models import AgentPrompt

        # Load guardrail prompt
        guardrail_record = db.query(AgentPrompt).filter(
            AgentPrompt.agent_key == COOKING_GUARDRAIL_KEY,
            AgentPrompt.is_active == True
        ).first()
        if guardrail_record:
            guardrail_prompt = guardrail_record.current_prompt

        # Load orchestrator prompt
        orchestrator_record = db.query(AgentPrompt).filter(
            AgentPrompt.agent_key == ORCHESTRATOR_AGENT_KEY,
            AgentPrompt.is_active == True
        ).first()
        if orchestrator_record:
            orchestrator_prompt = orchestrator_record.current_prompt

    except Exception as e:
        pass  # Use defaults

    guardrail_agent = create_cooking_guardrail_agent(guardrail_prompt)
    orch_agent = create_orchestrator_agent(orchestrator_prompt, guardrail_agent)

    return orch_agent, guardrail_agent


# =====================================================
# Default agent instances for backward compatibility
# =====================================================

cooking_guardrail_agent = create_cooking_guardrail_agent(DEFAULT_COOKING_GUARDRAIL_PROMPT)
orchestrator_agent = create_orchestrator_agent(DEFAULT_ORCHESTRATOR_PROMPT, cooking_guardrail_agent)


# =====================================================
# Helper function for backward compatibility
# =====================================================

async def process_query(
    query: str,
    context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Process a user query through the orchestrator.

    This is a convenience function that can be used instead of
    directly calling the agent with Runner.run().

    Args:
        query: User's text query
        context: Optional context dictionary (session_id, user_uid, db, etc.)

    Returns:
        The agent's response as a string
    """
    result = await Runner.run(
        orchestrator_agent,
        query,
        context=context or {}
    )

    return result.final_output
