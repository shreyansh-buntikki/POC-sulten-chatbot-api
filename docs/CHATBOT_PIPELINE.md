# Chatbot Pipeline Documentation

## Overview

The Sulten Chatbot processes user queries through a sophisticated **10-stage pipeline** orchestrated by `RecipeSearchPipelineSDK`. The system combines OpenAI Agents SDK for intent detection and response generation, with vector embeddings for semantic search and SQL for structured filtering.

---

## Endpoint

```
POST /chat/message
```

**Location:** `apps/fastapi/src/routes/chat.py`

### Request Schema

```json
{
  "session_id": "uuid (optional) - Continue existing session",
  "user_uid": "string (optional) - User identifier",
  "message": "string (required) - User's message",
  "new_session": "boolean (default: false) - Force new session creation"
}
```

### Response Schema

```json
{
  "session_id": "uuid",
  "user_message": {
    "id": "uuid",
    "content": "string",
    "created_at": "ISO timestamp"
  },
  "assistant_message": {
    "id": "uuid",
    "content": "string",
    "created_at": "ISO timestamp"
  },
  "metadata": {
    "intent": "string",
    "is_cooking_related": "boolean",
    "retrieval_strategy": "string",
    "num_results": "integer",
    "recipes": [...]
  }
}
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API LAYER                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  POST /chat/message                                                  │   │
│  │  - Request validation (Pydantic)                                     │   │
│  │  - Response caching (5 min TTL)                                      │   │
│  │  - Language header support                                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SERVICE LAYER                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  ChatService (chat_service.py)                                       │   │
│  │  - Session management (create/retrieve/delete)                       │   │
│  │  - Message persistence                                               │   │
│  │  - Pipeline orchestration                                            │   │
│  │  - Error handling & rollback                                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PIPELINE LAYER                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  RecipeSearchPipelineSDK (pipeline_orchestrator_sdk.py)              │   │
│  │                                                                       │   │
│  │   Stage 1  ──► Stage 2  ──► Stage 3  ──► Stages 4-8  ──► Stages 9-10│   │
│  │   Session    NLID       Strategy    Retrieval      Post-Process     │   │
│  │   Memory     Agent      Decider     + SQL          + NLG            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The 10-Stage Pipeline

### Stage 1: Session Memory

**Component:** `SessionMemoryManager`
**File:** `apps/fastapi/src/services/session_memory_manager.py`

**Purpose:** Load and manage conversation state across turns.

**How it works:**
1. Retrieves existing session from database or creates new one
2. Loads conversation history (last 10 messages for context)
3. Tracks session-level preferences:
   - `excluded_ingredients` - User allergies
   - `included_ingredients` - Preferred ingredients
   - `filters.tags` - Dietary preferences (vegetarian, vegan, etc.)
   - `last_vector_query` - Previous search query for refinements
   - `last_search_filters` - Previous filters applied

**Code Reference:**
```python
session = self.session_manager.get_or_create_session(
    session_id, user_uid, language, self.conversation_store
)
session.add_to_history("user", query)
```

---

### Stage 2: Intent & Entity Detection (NLID)

**Component:** OpenAI NLID Agent (SDK)
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

**Purpose:** Detect cooking intent and extract structured entities from user query.

**How it works:**
1. Sends query to OpenAI agent with conversation history context
2. Returns structured `IntentOutput`:
   - `is_cooking_related` - Whether query is food-related
   - `intent` - Type of query (recipe_search, pricing_info, etc.)
   - `entities` - Extracted entities (ingredients, cuisines, etc.)
   - `filters` - Structured filters (max_time, difficulty, tags, etc.)
   - `confidence` - Detection confidence score

**Supported Intents:**
| Intent | Description |
|--------|-------------|
| `recipe_search` | Search for recipes |
| `pricing_info` | Get ingredient pricing |
| `nutritional_info` | Get nutritional information |
| `price_filter` | Filter recipes by cost |
| `nutrition_filter` | Filter by nutrition (protein, carbs, etc.) |
| `general_chat` | General conversation |
| `not_supported` | Non-cooking queries |

**Code Reference:**
```python
nlid_result = await Runner.run(nlid_agent, query, context=enhanced_context)
output = nlid_result.final_output
nlid_data = output if isinstance(output, IntentOutput) else IntentOutput(**output)
```

**Special Handling:** If `is_cooking_related=False`, returns scope message immediately without further processing.

---

### Stage 3: Retrieval Strategy Decision

**Component:** `RetrievalStrategyDecider`
**File:** `apps/fastapi/src/services/retrieval_strategy.py`

**Purpose:** Decide the optimal retrieval approach based on query type.

**Strategies:**

| Strategy | When Used | Description |
|----------|-----------|-------------|
| `SQL_ONLY` | Price/nutrition filters | Direct SQL on recipe_metadata, no embeddings |
| `EMBEDDINGS_ONLY` | Pure semantic search | Vector search only |
| `HYBRID_VECTOR_TO_SQL` | Search with filters | Embedding search first, then SQL filter |
| `HYBRID_SQL_TO_VECTOR` | Filter then rank | SQL filter first, then vector re-rank |

**Code Reference:**
```python
retrieval_plan = self.strategy_decider.decide_strategy(
    query, nlid_result_dict, session_context
)
```

**RetrievalPlan Output:**
```python
@dataclass
class RetrievalPlan:
    strategy: RetrievalStrategy
    reasoning: str
    vector_query: Optional[str]
    sql_filters: Dict[str, Any]
    top_k: int
```

---

### Stage 4: Retrieval Execution (Embedding Search)

**Component:** `EmbeddingService`, `search_recipes_by_embedding`
**Files:**
- `apps/fastapi/src/services/embedding_service.py`
- `apps/fastapi/src/agents/agent_tools.py`

**Purpose:** Perform vector similarity search to find candidate recipes.

**How it works:**
1. Generate embedding for query text using OpenAI embeddings
2. Search PostgreSQL with pgvector extension
3. Return top-k candidates with similarity scores
4. Apply language filter for multi-language support

**Code Reference:**
```python
embedding_results = search_recipes_by_embedding(
    self.db,
    query_text=retrieval_plan.vector_query or query,
    limit=embedding_limit,
    threshold=0.4,
    language_id=language
)
candidate_ids = [str(r.id) for r, _ in embedding_results]
similarity_scores = {str(r.id): s for r, s in embedding_results}
```

---

### Stage 5: Schema Understanding

**Component:** `SchemaUnderstandingService`
**File:** `apps/fastapi/src/services/schema_understanding.py`

**Purpose:** Fetch database schema context for SQL generation.

**How it works:**
1. Retrieves table schemas relevant to the intent
2. Includes column types, relationships, constraints
3. Provides context for LLM to generate correct SQL

**Code Reference:**
```python
relevant_schema = self.schema_understanding.get_relevant_schema(
    nlid_result_dict["intent"],
    retrieval_plan.sql_filters,
    session_context
)
```

---

### Stage 6: SQL Generation

**Component:** `SQLGenerator`
**File:** `apps/fastapi/src/services/sql_generator.py`

**Purpose:** Generate SQL query using LLM based on schema and filters.

**How it works:**
1. Constructs prompt with schema + filters + user context
2. Calls OpenAI to generate SQL
3. Applies standard filters automatically:
   - Language filter
   - Access control (private recipes)
   - Soft delete filter
4. For hybrid strategies, filters by `candidate_ids` from embedding search

**Code Reference:**
```python
sql_result = self.sql_generator.generate_sql(
    query,
    nlid_result_dict,
    retrieval_plan.sql_filters,
    session_context,
    candidate_ids  # Optional: limit to embedding candidates
)
```

**SQLGenerationResult:**
```python
@dataclass
class SQLGenerationResult:
    sql: str
    is_safe: bool
    estimated_rows: int
    reasoning: str
```

---

### Stage 7: SQL Validation

**Component:** Built-in validation in `SQLGenerator`

**Purpose:** Ensure generated SQL is safe and valid.

**Validation Rules:**
- Only SELECT statements allowed
- No DROP, DELETE, INSERT, UPDATE
- No SQL injection patterns
- Reasonable row estimate

**Code Reference:**
```python
if not sql_result.is_safe:
    # Reject query, return error response
    error_response = await self._generate_error_response(query, execution_result)
```

---

### Stage 8: SQL Execution

**Component:** `SQLExecutionService`
**File:** `apps/fastapi/src/services/sql_generator.py`

**Purpose:** Execute validated SQL query against database.

**How it works:**
1. Executes SQL with parameterized queries
2. Handles connection pooling
3. Returns structured results with row count

**Code Reference:**
```python
execution_result = self.sql_executor.execute_sql(sql_result.sql, params)
# params = {"user_uid": user_uid, "language_id": language}
```

**Execution Result:**
```python
{
    "success": True/False,
    "rows": [...],
    "row_count": int,
    "error": Optional[str]
}
```

---

### Stage 9: Post-Processing & Ranking

**Component:** `_post_process_and_rank` method
**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

**Purpose:** Enhance, filter, and rank results.

**How it works:**

1. **Batch Loading (Performance Optimization):**
   - Load all recipes in ONE query (not N queries)
   - Load all bundle info in ONE query
   - Load all ingredients in ONE query
   - Load all instructions in ONE query

2. **Access Control:**
   - Exclude private recipes (unless owner)
   - Exclude deleted recipes
   - Handle bundle access:
     - Free bundle recipes: Full access
     - Paid bundle (user owns): Full access
     - Paid bundle (user doesn't own): Name only

3. **Priority Ranking:**
   ```
   Priority = user_liked (highest) > user_created > public
   ```

4. **Enrichment:**
   - Add similarity scores
   - Add ingredients list
   - Add instructions list
   - Add nutrition/cost info

**Code Reference:**
```python
processed_recipes = self._post_process_and_rank(
    execution_result["rows"],
    similarity_scores,
    session_context,
    retrieval_plan
)
final_recipes = processed_recipes[:self.MAX_RECIPES]  # Max 5 recipes
```

---

### Stage 10: Natural Language Generation (NLG)

**Component:** OpenAI NLG Agent (SDK)
**File:** `apps/fastapi/src/agents/sdk_nlg_agent.py`

**Purpose:** Generate human-like response from structured results.

**How it works:**
1. Formats recipe data for display
2. Generates contextual response using OpenAI
3. Includes cooking tips when relevant
4. Handles no-results case with helpful suggestions

**Code Reference:**
```python
response = await self._generate_natural_language_response(
    query,
    execution_result["rows"][:self.MAX_RECIPES],
    nlid_result_dict
)
```

---

## Data Flow Diagram

```
                    ┌──────────────────┐
                    │   User Message   │
                    └────────┬─────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 1: Session Memory                                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  • Load/create session                                   │ │
│  │  • Load conversation history (last 10 messages)          │ │
│  │  • Load user preferences (allergies, dietary tags)       │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 2: NLID (Parallel with schema pre-fetch)               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  • Send query + context to OpenAI Agent                  │ │
│  │  • Extract: intent, entities, filters                    │ │
│  │  • Check: is_cooking_related?                            │ │
│  │    └── NO → Return scope message, END                    │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 3: Retrieval Strategy Decision                         │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Decide strategy based on query type:                    │ │
│  │  • Price/Nutrition filter → SQL_ONLY (skip embeddings)   │ │
│  │  • Semantic search → EMBEDDINGS_ONLY                     │ │
│  │  • Search + filters → HYBRID_VECTOR_TO_SQL               │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
     │  SQL_ONLY   │ │  HYBRID     │ │  EMBED_ONLY │
     │             │ │             │ │             │
     │ Skip Stage 4│ │ Stage 4     │ │ Stage 4     │
     │ Go to 5+6   │ │ Then 5+6    │ │ Then 5+6    │
     └─────────────┘ └─────────────┘ └─────────────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGES 4-6: Retrieval + Schema + SQL (Optimized execution)   │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  HYBRID (sequential):                                    │ │
│  │    1. Embedding search → candidate_ids                   │ │
│  │    2. Schema fetch                                       │ │
│  │    3. SQL generation with candidate_ids filter           │ │
│  │                                                           │ │
│  │  OTHER (parallel):                                       │ │
│  │    1. Embedding search (if needed)                       │ │
│  │    2. Schema + SQL generation                            │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 7: SQL Validation                                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  • Check: Only SELECT statements                         │ │
│  │  • Check: No dangerous operations                        │ │
│  │  • Check: Reasonable row estimate                        │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 8: SQL Execution                                       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  • Execute parameterized SQL                             │ │
│  │  • Return rows + row_count                               │ │
│  │  • Handle errors gracefully                              │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGES 9-10: Post-Processing + NLG (Parallel)                │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  STAGE 9 (Post-Processing):                              │ │
│  │    • Batch load all recipe details                       │ │
│  │    • Apply access control (private, bundle)              │ │
│  │    • Rank by priority (liked > created > public)         │ │
│  │    • Enrich with ingredients, instructions               │ │
│  │                                                           │ │
│  │  STAGE 10 (NLG):                                         │ │
│  │    • Generate natural language response                   │ │
│  │    • Format recipes for display                          │ │
│  │    • Include contextual tips                              │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Final Response  │
                    │  + Recipes       │
                    │  + Metadata      │
                    └──────────────────┘
```

---

## Special Query Handling

### 1. Dietary Preferences & Allergies

**Detection Patterns:**
```
"I am allergic to [ingredient]"
"I am vegetarian/vegan/gluten-free"
"I'm lactose intolerant"
```

**Two Scenarios:**

| Scenario | Behavior |
|----------|----------|
| **Standalone** (no previous search) | Save preference, search with constraints |
| **Refinement** (after previous search) | Run NEW search with combined constraints |

**Code Reference:**
```python
# Refinement: Use original search + add allergy filter
retrieval_plan = RetrievalPlan(
    strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
    vector_query=original_vector_query,  # e.g., "orange recipes"
    sql_filters={"excluded_ingredients": ["tomato"]},
    top_k=20
)
```

### 2. Price & Nutrition Filters

**Detection:** Queries like "recipes under $10" or "high protein low carb"

**Optimization:** Skip embedding search entirely, use `SQL_ONLY` strategy.

**Code Reference:**
```python
if nlid_result_dict["intent"] in ["price_filter", "nutrition_filter"]:
    # Direct SQL on recipe_metadata - no embeddings needed
    return await self._handle_cost_nutrition_filter_query(...)
```

### 3. Non-Cooking Queries

**Detection:** `is_cooking_related=False` from NLID

**Response:**
```
I'm Sulten Chatbot, your cooking and recipe assistant!
I can help you with:
- Finding recipes and meal ideas
- Nutritional information about foods
- Cooking tips and techniques
- Ingredient substitutions

I'm not able to help with non-cooking topics...
```

---

## Performance Optimizations

### 1. Parallel Execution

The pipeline uses `asyncio.gather()` for parallel execution:

| Phase | Parallel Operations |
|-------|---------------------|
| Phase 1 | NLID + Schema pre-fetch |
| Phase 2 | Embedding search + Schema + SQL |
| Phase 3 | Post-processing + NLG |

### 2. Response Caching

- **TTL:** 5 minutes
- **Scope:** New sessions only (identical query + user + language)
- **Storage:** In-memory dictionary with auto-cleanup

**Code Reference:**
```python
cache_key = _get_cache_key(message, user_uid, language)
cached_response = _get_cached_response(cache_key)
```

### 3. Batch Database Queries

Post-processing uses batch queries instead of N+1:

| Before | After |
|--------|-------|
| 40-50 queries | 5-6 queries |

**Batch Queries:**
1. Load all recipes in ONE query
2. Load all bundle info in ONE query
3. Load all ingredients in ONE query
4. Load all instructions in ONE query
5. Load user context ONCE

---

## Session Management

### Session Lifecycle

```
Create Session → Add Messages → Close/Delete Session
       │              │               │
       └──────────────┴───────────────┘
                      │
            Max 5 sessions per user
            (old sessions auto-deleted)
```

### Session Storage

**Database Tables:**
- `chat_session` - Session metadata
- `chat_message` - Individual messages

**Message Metadata:**
```json
{
  "intent": "recipe_search",
  "retrieval_strategy": "hybrid_vector_to_sql",
  "num_results": 3,
  "recipes": [...],
  "vector_query": "chicken recipes",
  "filters": {...}
}
```

---

## Error Handling

### Pipeline Error Recovery

```python
try:
    result = await self.pipeline.process_query(...)
except Exception as e:
    self.db.rollback()  # Ensure clean state
    result = {
        "response": "I apologize, but I encountered an error...",
        "metadata": {"error": str(e)}
    }
```

### Message Pairing

If assistant message save fails, user message is deleted to prevent incomplete sessions:

```python
# If we can't save assistant message, delete user message
self.db.query(ChatMessage).filter(ChatMessage.id == user_msg.id).delete()
self.db.commit()
```

---

## Key Files Reference

| Component | File Path |
|-----------|-----------|
| Route Handler | `apps/fastapi/src/routes/chat.py` |
| Chat Service | `apps/fastapi/src/services/chat_service.py` |
| Pipeline Orchestrator | `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` |
| NLID Agent | `apps/fastapi/src/agents/sdk_nlid_agent.py` |
| NLG Agent | `apps/fastapi/src/agents/sdk_nlg_agent.py` |
| Agent Tools | `apps/fastapi/src/agents/agent_tools.py` |
| Session Memory | `apps/fastapi/src/services/session_memory_manager.py` |
| Retrieval Strategy | `apps/fastapi/src/services/retrieval_strategy.py` |
| Embedding Service | `apps/fastapi/src/services/embedding_service.py` |
| SQL Generator | `apps/fastapi/src/services/sql_generator.py` |
| Schema Understanding | `apps/fastapi/src/services/schema_understanding.py` |
| User Context | `apps/fastapi/src/services/user_context_service.py` |
| Conversation Store | `apps/fastapi/src/services/conversation_store.py` |

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_CONTEXT_MESSAGES` | Messages to keep in context | 10 |
| `OPENAI_API_KEY` | OpenAI API key | Required |

### Pipeline Constants

```python
MAX_RECIPES = 5  # Maximum recipes in response
EMBEDDING_THRESHOLD = 0.4  # Minimum similarity score
CACHE_TTL = 300  # 5 minutes
```

---

## Monitoring & Logging

### Log Format

```
[STAGE N] Description - details
[STAGE N] ✓ Completed in X.XXXs
[PIPELINE COMPLETE] Total time: X.XXXs | Results: N recipes
```

### Key Metrics

- Stage duration (each stage logged)
- Total pipeline duration
- Number of results returned
- Retrieval strategy used
- Cache hits/misses

---

## Future Enhancements

1. **Streaming Responses** - Stream NLG output for perceived performance
2. **Query Suggestion** - Suggest follow-up queries based on context
3. **A/B Testing** - Test different retrieval strategies
4. **Analytics** - Track query patterns and success rates
5. **Caching v2** - Redis-based distributed caching
