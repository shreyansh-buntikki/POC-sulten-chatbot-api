# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

### Running the Application

```bash
# Using PM2 (recommended for production)
pm2 start ecosystem.config.js
pm2 logs fastapi-app
pm2 stop fastapi-app
pm2 restart fastapi-app

# Using Python directly (development)
python3.12 apps/fastapi/app.py

# Using Docker
docker build -t fastapi-app .
docker run -p 5000:5000 fastapi-app
```

### Testing

```bash
# Run all tests
pytest tests/fastapi

# Run specific test file
pytest tests/fastapi/test_health_check.py

# Run with verbose output
pytest tests/fastapi -v
```

### Code Quality

```bash
# Run pre-commit hooks manually
pre-commit run --all-files

# Install pre-commit hooks (first time setup)
pip install pre-commit
pre-commit install
```

### Database Scripts

```bash
# Generate embeddings for ingredients/recipes
python scripts/generate_embeddings.py

# Regenerate recipe embeddings
python scripts/regenerate_recipe_embeddings.py

# Manage ingredient nutrition data
python scripts/managers/nutrition_manager.py add
python scripts/managers/nutrition_manager.py stats

# Manage ingredient pricing
python scripts/managers/pricing_manager.py set-price <ingredient> <country> <price>
python scripts/managers/pricing_manager.py stats

# Manage recipe seasonalities
python scripts/managers/seasonality_manager.py add
```

## Architecture Overview

### 10-Stage Pipeline

The chatbot uses a sophisticated 10-stage processing pipeline orchestrated by `PipelineOrchestratorSDK`:

1. **Session Memory** - Conversation state tracking via `SessionMemoryManager`
2. **Intent & Entity Detection** - NLID Agent using OpenAI Agents SDK
3. **Retrieval Strategy** - Decision between vector/SQL/hybrid search
4. **Retrieval Execution** - Recipe candidate fetching via embedding search
5. **Schema Understanding** - Database context for SQL generation
6. **SQL Generation** - LLM-powered query generation via `SQLGenerator`
7. **SQL Validation** - Safety checks on generated SQL
8. **SQL Execution** - Database query with fallback mechanisms
9. **Post-Processing** - Recipe ranking and personalization
10. **Natural Language Generation** - Response synthesis via NLG Agent

### Service Layer Architecture

```
apps/fastapi/src/routes/     → Thin API handlers
apps/fastapi/src/services/   → Business logic
libs/                        → Shared utilities
models.py                    → SQLAlchemy ORM models
database.py                  → Database configuration
```

**Key Services:**
- `ChatService` - Main chat endpoint orchestration
- `PipelineOrchestratorSDK` - 10-stage pipeline execution
- `EmbeddingService` - Vector operations using pgvector
- `SQLGenerator` - LLM-powered SQL query generation
- `UserContextService` - User preferences and personalization

### OpenAI Agents SDK Integration

The system uses OpenAI Agents SDK with three specialized agents:
- **NLID Agent** (`sdk_nlid_agent.py`) - Detects cooking intent and extracts entities
- **Orchestrator Agent** (`sdk_orchestrator_agent.py`) - Manages workflow and guardrails
- **NLG Agent** (`sdk_nlg_agent.py`) - Generates natural language responses

SDK tracing is enabled automatically if `OPENAI_API_KEY` is set in environment.

### Database Models (`models.py`)

Key tables:
- `Recipe` - Main recipe table with vector embeddings
- `Ingredient` - Ingredients with nutrition and pricing
- `User`, `UserLikesRecipe` - User preferences and likes
- `IngredientPricing` - Multi-country pricing data
- `RecipeSeasonality` - Seasonal recipe categorization
- `Conversation`, `Message` - Chat history storage

### Recipe Access Control

The system implements tiered recipe access:
- **Paid recipes** - Only show name, hide details from non-payers
- **Private recipes** - Never appear in search results
- **Bundle recipes** - Special access handling

### Personalization Ranking

Recipe results are ranked by:
1. User-liked recipes (highest priority)
2. User-created recipes
3. Public recipes

## Environment Configuration

Required environment variables (see `example.env`):
```bash
HOST                    # Server host (default: localhost)
FASTAPI_PORT           # FastAPI port (default: 5000)
ENVIRONMENT            # development|production
DB_HOST                # PostgreSQL host
DB_PORT                # PostgreSQL port (default: 5432)
DB_NAME                # Database name
DB_USER                # Database user
DB_PASSWORD            # Database password
OPENAI_API_KEY         # OpenAI API key for LLM features
```

## Code Style

- **Line length**: 80 characters (Black + isort)
- **Python version**: 3.12
- **Type hints**: Required for all function signatures
- **Import order**: Standard → Third-party → Internal
- **Async**: Use async/await for I/O operations

## Development Workflow

1. All route handlers in `apps/fastapi/src/routes/` must be thin - business logic goes in services
2. Use Pydantic models for all API request/response validation
3. Raise `AppException` for domain-specific errors
4. Use `get_db()` dependency for database sessions
5. Avoid N+1 queries - use batch operations or in-memory processing
6. Keep cognitive complexity below 15 per function

## Key Files Reference

- `apps/fastapi/app.py` - Main application entry point
- `database.py` - PostgreSQL connection setup
- `models.py` - All SQLAlchemy ORM models
- `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` - 10-stage pipeline
- `apps/fastapi/src/services/chat_service.py` - Chat endpoint logic
- `apps/fastapi/src/services/embedding_service.py` - Vector search
- `apps/fastapi/src/services/sql_generator.py` - LLM SQL generation
