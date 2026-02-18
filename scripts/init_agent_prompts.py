"""
Script to initialize agent prompts in the database with default values.
Run this after migration to populate the agent_prompt table.
"""
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from models import AgentPrompt

# Default prompts for each agent
DEFAULT_PROMPTS = [
    {
        "agent_key": "nlid_agent",
        "agent_name": "NLIDAgent",
        "description": "Detects user intent and extracts entities from cooking-related queries",
        "current_prompt": "You are an expert Natural Language Intent Detection system for a recipe and cooking platform.\n\nYour task is to analyze user queries and extract structured information including:\n1. Intent classification\n2. Entity extraction (ingredients, recipes, quantities, units)\n3. User preferences and parameters\n4. Filters for result refinement\n...",
        "model_name": "gpt-4o-mini"
    },
    {
        "agent_key": "nlg_agent",
        "agent_name": "NLGAgent",
        "description": "Generates value-adding responses that describe recipe benefits and flavors",
        "current_prompt": "You are a specialist for generating value-adding responses that describe recipe benefits and flavors.\n...",
        "model_name": "gpt-4o-mini"
    },
    {
        "agent_key": "nutritional_agent",
        "agent_name": "NutritionalAgent",
        "description": "Provides nutritional information, ingredient substitutions, and dietary analysis",
        "current_prompt": "You are a nutritional specialist.\n...",
        "model_name": "gpt-4o-mini"
    },
    {
        "agent_key": "recipe_agent",
        "agent_name": "RecipeRetrievalAgent",
        "description": "Searches, retrieves, and recommends recipes",
        "current_prompt": "You are a recipe search specialist helping users discover delicious recipes that match their needs.\n...",
        "model_name": "gpt-4o-mini"
    },
    {
        "agent_key": "orchestrator_agent",
        "agent_name": "OrchestratorAgent",
        "description": "Main cooking assistant coordinator that routes queries to specialist agents",
        "current_prompt": "You are the main cooking assistant coordinator for a recipe and food platform.\n...",
        "model_name": "gpt-4o-mini"
    },
    {
        "agent_key": "cooking_guardrail",
        "agent_name": "CookingGuardrail",
        "description": "Checks if a user query is related to cooking, recipes, food, or kitchen activities",
        "current_prompt": "You are a guardrail that checks if a user query is related to cooking, recipes, food, or kitchen activities.\n...",
        "model_name": "gpt-4o-mini"
    }
]

# Update with your actual database URL
DATABASE_URL = "postgresql://postgres:postgres@localhost/sulten-db"

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

for prompt in DEFAULT_PROMPTS:
    existing = session.query(AgentPrompt).filter(AgentPrompt.agent_key == prompt["agent_key"]).first()
    if not existing:
        ap = AgentPrompt(
            agent_key=prompt["agent_key"],
            agent_name=prompt["agent_name"],
            description=prompt["description"],
            current_prompt=prompt["current_prompt"],
            model_name=prompt["model_name"],
            is_active=True
        )
        session.add(ap)
        print(f"Inserted prompt for {prompt['agent_key']}")
    else:
        print(f"Prompt for {prompt['agent_key']} already exists")

session.commit()
session.close()
print("Agent prompts initialization complete.")
