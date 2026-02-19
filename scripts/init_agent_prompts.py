"""
Script to initialize agent prompts in the database with default values.
Run this after migration to populate the agent_prompt table.

Usage:
    python scripts/init_agent_prompts.py           # Insert new, skip existing
    python scripts/init_agent_prompts.py --update   # Insert new AND update existing
"""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

from libs.utils.logger import setup_logger

logger = setup_logger("init_agent_prompts", True, False, False, False)

from models import AgentPrompt

# Import actual prompts from agent files
from apps.fastapi.src.agents.sdk_nlid_agent import DEFAULT_NLID_PROMPT, NLID_AGENT_MODEL
from apps.fastapi.src.agents.sdk_nlg_agent import DEFAULT_NLG_PROMPT, NLG_AGENT_MODEL
from apps.fastapi.src.agents.sdk_nutritional_agent import DEFAULT_NUTRITIONAL_PROMPT, NUTRITIONAL_AGENT_MODEL
from apps.fastapi.src.agents.sdk_recipe_agent import DEFAULT_RECIPE_PROMPT, RECIPE_AGENT_MODEL
from apps.fastapi.src.agents.sdk_orchestrator_agent import (
    DEFAULT_ORCHESTRATOR_PROMPT,
    DEFAULT_COOKING_GUARDRAIL_PROMPT,
    ORCHESTRATOR_AGENT_MODEL
)

# Default prompts for each agent - using actual prompts from code
DEFAULT_PROMPTS = [
    {
        "agent_key": "nlid_agent",
        "agent_name": "NLIDAgent",
        "description": "Natural Language Intent Detection - Analyzes user queries to detect intent and extract entities for recipe and cooking platform",
        "current_prompt": DEFAULT_NLID_PROMPT,
        "model_name": NLID_AGENT_MODEL or "gpt-4o-mini"
    },
    {
        "agent_key": "nlg_agent",
        "agent_name": "NLGAgent",
        "description": "Natural Language Generation - Generates warm, friendly responses for recipe recommendations and cooking assistance",
        "current_prompt": DEFAULT_NLG_PROMPT,
        "model_name": NLG_AGENT_MODEL or "gpt-4o-mini"
    },
    {
        "agent_key": "nutritional_agent",
        "agent_name": "NutritionalAgent",
        "description": "Nutrition Expert - Helps users understand nutritional content of recipes and ingredients, suggests substitutions",
        "current_prompt": DEFAULT_NUTRITIONAL_PROMPT,
        "model_name": NUTRITIONAL_AGENT_MODEL or "gpt-4o-mini"
    },
    {
        "agent_key": "recipe_agent",
        "agent_name": "RecipeRetrievalAgent",
        "description": "Recipe Search Specialist - Helps users discover recipes that match their needs with smart filtering",
        "current_prompt": DEFAULT_RECIPE_PROMPT,
        "model_name": RECIPE_AGENT_MODEL or "gpt-4o-mini"
    },
    {
        "agent_key": "orchestrator_agent",
        "agent_name": "OrchestratorAgent",
        "description": "Main Cooking Assistant Coordinator - Routes user queries to appropriate specialist agents",
        "current_prompt": DEFAULT_ORCHESTRATOR_PROMPT,
        "model_name": ORCHESTRATOR_AGENT_MODEL or "gpt-4o-mini"
    },
    {
        "agent_key": "cooking_guardrail",
        "agent_name": "CookingGuardrail",
        "description": "Guardrail Agent - Checks if user queries are related to cooking, recipes, food, or kitchen activities",
        "current_prompt": DEFAULT_COOKING_GUARDRAIL_PROMPT,
        "model_name": ORCHESTRATOR_AGENT_MODEL or "gpt-4o-mini"
    }
]

# Build database URL from environment
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "sulten-db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Check for --update flag
force_update = "--update" in sys.argv

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

inserted = 0
updated = 0
skipped = 0

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
        logger.info(f"Inserted prompt for {prompt['agent_key']}")
        inserted += 1
    elif force_update:
        existing.current_prompt = prompt["current_prompt"]
        existing.description = prompt["description"]
        existing.model_name = prompt["model_name"]
        logger.info(f"Updated prompt for {prompt['agent_key']}")
        updated += 1
    else:
        logger.info(f"Skipped {prompt['agent_key']} (already exists, use --update to overwrite)")
        skipped += 1

session.commit()
session.close()
logger.info(f"Agent prompts initialization complete: {inserted} inserted, {updated} updated, {skipped} skipped.")
