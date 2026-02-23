# Context Management & SQL Filters Documentation

This document describes how the chatbot maintains conversational context and manages SQL filters across multi-turn queries.

---

## Table of Contents

1. [Overview](#overview)
2. [Session State Structure](#session-state-structure)
3. [Constraint Types Managed](#constraint-types-managed)
4. [Query Retrieval Flow](#query-retrieval-flow)
5. [Filter Merging Logic](#filter-merging-logic)
6. [Multiple Constraint Handling](#multiple-constraint-handling)
7. [Retrieval Strategy Decision](#retrieval-strategy-decision)
8. [Session Persistence](#session-persistence)
9. [NLID Agent Context Rules](#nlid-agent-context-rules)
10. [Examples](#examples)

---

## Overview

The chatbot uses a sophisticated context management system that enables:

- **Multi-turn conversations** where constraints accumulate naturally
- **Constraint persistence** across queries in the same session
- **Intelligent refinement detection** to understand follow-up queries
- **Pronoun resolution** for creator references ("her recipes")
- **Allergen expansion** to comprehensively exclude related ingredients

---

## Session State Structure

The `SessionMemoryManager` maintains a `SessionState` object with several key components.

### SessionFilters

**File:** `apps/fastapi/src/services/session_memory_manager.py:31-46`

```python
@dataclass
class SessionFilters:
    tags: List[str]               # Dietary: vegetarian, vegan, dessert, etc.
    cuisines: List[str]           # Italian, Mexican, Indian, etc.
    categories: List[str]         # Recipe categories
    max_time: Optional[int]       # Cooking time limit (minutes)
    difficulty: Optional[str]     # easy, medium, hard
    season: Optional[str]         # summer, winter, etc.
    region: Optional[str]         # Regional preference
    creator_uid: Optional[str]    # Filter by recipe creator (UUID)
    creator_username: Optional[str]  # Resolved username for NLG context

    # Multi-turn persisted filters
    cost_filter: Optional[Dict]       # Budget constraints
    time_filter: Optional[Dict]       # Quick/slow sorting
    nutrition_filter: Optional[Dict]  # High protein, low carb, etc.
```

### Core Session State

```python
@dataclass
class SessionState:
    session_id: str
    user_uid: Optional[str]
    language: str = "en"

    # Ingredient constraints
    excluded_ingredients: List[str]   # Allergies, dislikes
    included_ingredients: List[str]   # Preferences
    excluded_recipe_ids: List[str]    # Negative feedback ("I don't like these")

    # Allergies with expanded variants
    allergies: Dict[str, List[str]]   # {"chocolate": ["chocolate", "cocoa", "cacao"]}

    filters: SessionFilters
    user_context: UserContext
    context_entities: ContextEntities
    conversation_history: List[Dict[str, str]]
    last_intent: Optional[str]        # Last detected intent
```

### ContextEntities (Reference Tracking)

```python
@dataclass
class ContextEntities:
    last_vector_query: Optional[str]      # Last search query for embedding
    last_search_filters: Optional[Dict]   # Last applied SQL filters
    last_recipe_results: List[Dict]       # Last shown recipes (id, name)
    last_user_query: Optional[str]        # Original user message
    search_cache: Dict[str, Any]          # For "show more" pagination
    last_pricing_item: Optional[str]      # For multi-turn pricing queries
    last_pricing_item_type: Optional[str] # "ingredient" or "recipe"
```

---

## Constraint Types Managed

### 1. Allergy/Dietary Constraints

| Property | Description |
|----------|-------------|
| **Storage** | `session.excluded_ingredients` + `session.allergies` |
| **Expansion** | Smart ingredient matching expands "nuts" to ["walnut", "almond", "pecan", ...] |
| **Multi-turn** | Constraints accumulate: Q1: "allergic to garlic" + Q2: "no tomatoes" = both persist |

**Example Expansion:**
```
Input: "I'm allergic to dairy"
Expanded: ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]
```

### 2. Budget/Cost Constraints

| Property | Description |
|----------|-------------|
| **Storage** | `session.filters.cost_filter` |
| **Format** | `{"operator": "<=", "value": 100, "country": "Norway", "sort_order": "DESC"}` |
| **Currency Mapping** | `$` → US, `₹` → India, `kr` → Norway (default) |

**Sort Order Logic:**
- `sort_order: "DESC"` → Shows recipes nearest to budget first (not cheapest)
- `sort_order: "ASC"` → Shows cheapest first (for "cheap", "affordable" queries)

### 3. Nutrition Constraints

| Property | Description |
|----------|-------------|
| **Storage** | `session.filters.nutrition_filter` |
| **Format** | `{"sort_by": "protein", "order": "DESC", "level": "high"}` |
| **Supported Nutrients** | protein, carbohydrates, totalFat, energyKcal, totalFiber, totalSugars |

**Order Logic:**
- `order: "DESC"` → "high protein", "high calorie"
- `order: "ASC"` → "low carb", "low calorie"

### 4. Time Constraints

| Property | Description |
|----------|-------------|
| **Storage** | `session.filters.time_filter` |
| **Format** | `{"sort_order": "ASC"}` (quick) or `{"sort_order": "DESC"}` (slow) |
| **Qualitative Only** | "quick", "fast", "slow", "elaborate" — no specific minute values |

**Trigger Phrases:**
- ASC sort: "quick", "fast", "short time", "in a hurry", "no time"
- DESC sort: "long", "slow", "elaborate", "takes time"

### 5. Creator/Author Constraints

| Property | Description |
|----------|-------------|
| **Storage** | `session.filters.creator_uid`, `session.filters.creator_username` |
| **Resolution** | `@username` → exact match, "by Name" → fuzzy match |
| **Pronoun Resolution** | "her recipes" → resolves to last mentioned creator |

**Patterns Detected:**
- `@username` → `filters.creator_username`
- "recipes by X", "X's recipes" → `filters.creator_name`
- "by her", "by him", "their recipes" → resolves from conversation history

### 6. Dietary Tags

| Property | Description |
|----------|-------------|
| **Storage** | `session.filters.tags` |
| **Examples** | vegetarian, vegan, gluten-free, dairy-free, keto, dessert |
| **Multi-turn** | Tags accumulate across queries |

### 7. Cuisine Constraints

| Property | Description |
|----------|-------------|
| **Storage** | `session.filters.cuisines` |
| **Examples** | italian, mexican, indian, chinese, thai |
| **Detection** | "italian recipes", "something mexican" |

---

## Query Retrieval Flow

### Direct Query (Single Turn)

```
User: "chicken pasta recipes under 200 kr"
```

**Pipeline Flow:**

1. **Stage 1: Session Memory** - Create/load session
2. **Stage 2: NLID Agent** - Detects:
   - `intent`: recipe_search
   - `entities.ingredients`: ["chicken", "pasta"]
   - `filters.cost`: {operator: "<=", value: 200, country: "Norway"}
3. **Stage 3: Retrieval Strategy** - HYBRID_VECTOR_TO_SQL
4. **Stage 4: Embedding Search** - Semantic matches for "chicken pasta"
5. **Stage 5-6: Schema + SQL Generation** - Applies cost filter + access control
6. **Stage 7-8: SQL Validation + Execution** - Returns filtered results
7. **Stage 9-10: Post-processing + NLG** - Formats response

### Consecutive Query (Multi-Turn Context)

```
Q1: "dessert recipes"
Q2: "I'm allergic to nuts"
Q3: "budget under 100 kr"
```

**Q1 Flow:**
```
NLID → intent: recipe_search
Embedding → vector_query: "dessert"
Session → stores last_vector_query = "dessert"
         stores last_intent = "recipe_search"
```

**Q2 Flow:**
```
NLID → detects refinement (allergy statement)
Session → preserves last_vector_query = "dessert"
        adds "nuts" → expanded variants to excluded_ingredients
Retrieval → HYBRID: vector_query="dessert" + SQL excludes nuts
```

**Q3 Flow:**
```
NLID → detects filter refinement
Session → merges: existing exclusions + new cost_filter
Retrieval → HYBRID: vector_query="dessert" + cost filter + nut exclusion
```

---

## Filter Merging Logic

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py:2437-2459`

When processing a filter query, the system merges current query filters with session-persisted filters:

```python
# Get session-persisted filters
session_cost = session.filters.cost_filter
session_time = session.filters.time_filter
session_nutrition = session.filters.nutrition_filter

# Current query's filter takes priority, fall back to session
if not cost_filter and session_cost:
    cost_filter = session_cost
if not time_filter and session_time:
    time_filter = session_time
if not nutrition_filter and session_nutrition:
    nutrition_filter = session_nutrition

# Persist back to session for future turns
if cost_filter:
    session.filters.cost_filter = cost_filter
if time_filter:
    session.filters.time_filter = time_filter
if nutrition_filter:
    session.filters.nutrition_filter = nutrition_filter
```

**Key Principle:** Current query filters override session filters, but session filters persist when not overridden.

---

## Multiple Constraint Handling

The system supports combining multiple constraints in a single query:

### Example Query

```
"quick vegetarian recipes under 100 kr by @mammapia, I'm allergic to dairy"
```

### Extracted Constraints

| Constraint Type | Value |
|-----------------|-------|
| `time_filter` | `{sort_order: "ASC"}` |
| `tags` | `["vegetarian"]` |
| `cost_filter` | `{operator: "<=", value: 100, country: "Norway"}` |
| `creator_username` | `"mammapia"` |
| `excluded_ingredients` | `["dairy", "milk", "cheese", "cream", "butter", "yogurt"]` |

### Generated SQL

```sql
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId"
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = 'en'
  AND (r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL)

  -- Creator filter
  AND r."userUid" = 'creator-uuid'

  -- Dietary tag filter
  AND EXISTS (
    SELECT 1 FROM recipe_tags_tag rtt
    JOIN tag t ON rtt."tagId" = t.id
    WHERE rtt."recipeId" = r."id"
    AND t.name ILIKE '%vegetarian%'
  )

  -- Allergy exclusion
  AND NOT EXISTS (
    SELECT 1 FROM recipe_ingredient ri
    JOIN ingredient i ON ri."ingredientId" = i."id"
    WHERE ri."recipeId" = r."id"
    AND (i."name" ILIKE '%dairy%' OR i."name" ILIKE '%milk%' OR i."name" ILIKE '%cheese%')
  )

  -- Budget filter (from recipe_metadata)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 100

ORDER BY (r."prepTime" + r."cookTime") ASC
LIMIT 20
```

---

## Retrieval Strategy Decision

The system chooses a retrieval strategy based on query type and context:

| Scenario | Strategy | Description |
|----------|----------|-------------|
| Positive search context | `HYBRID_VECTOR_TO_SQL` | Embedding search + SQL filters |
| Pure exclusion (no positive context) | `SQL_ONLY` | Direct SQL without embedding bias |
| Filter-only (price/nutrition/time) | `Direct SQL` | Deterministic SQL builders |
| Refinement with previous search | `Refinement Mode` | Re-uses previous vector_query |

### Strategy Decision Logic

```python
# Check for positive entities to drive embedding
has_positive_entities = bool(
    positive_ingredients
    or entities.get("cuisines")
    or entities.get("meal_types")
    or filters.get("cuisines")
    or filters.get("tags")
)

# Pure exclusion query → SQL_ONLY
if excluded_ingredients and not has_positive_entities:
    strategy = RetrievalStrategy.SQL_ONLY
    reasoning = "Exclusion-only query – SQL filters full recipe table"

# Normal search → HYBRID
else:
    strategy = RetrievalStrategy.HYBRID_VECTOR_TO_SQL
    reasoning = "Semantic search with SQL filtering"
```

---

## Session Persistence

### Storage Mechanisms

| Mechanism | Description |
|-----------|-------------|
| **In-memory cache** | `_MODULE_MEMORY_CACHE` - persists across requests |
| **Database** | Conversation history stored in `Message` table with metadata |
| **Metadata** | Each message stores `vector_query`, `intent`, `filters` for context restoration |

### Cache Management

| Setting | Value | Description |
|---------|-------|-------------|
| `SEARCH_CACHE_TTL_SECONDS` | 1800 (30 min) | Search cache expiration |
| `EMBEDDING_BATCH_SIZE` | 20 | Results per embedding batch |
| `MAX_EMBEDDING_OFFSET` | 100 | Maximum pagination offset |
| History limit | 10 messages | Context window for LLM |

### Session Restoration

When a session is loaded, the system:

1. Loads conversation history from database
2. Extracts context from message metadata (`meta` field)
3. Restores `last_vector_query`, `last_intent`, `filters`
4. Rebuilds `excluded_ingredients` from stored allergies

---

## NLID Agent Context Rules

**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

### Rule 3: Multi-Turn Context & Refinement

The NLID agent detects refinements and adds constraints without changing intent:

| User Pattern | Detected As | Action |
|--------------|-------------|--------|
| "I am allergic to X" | Refinement | Add X to `excluded_ingredients`, keep previous intent |
| "without X" / "no X" | Refinement | Add X to `excluded_ingredients` |
| "I don't like X" | Refinement | Add X to `excluded_ingredients` |
| "my budget is $200" | Refinement | Add `cost_filter`, keep previous search |
| "I'm vegetarian" | Refinement | Add dietary tag, keep previous search |

### Rule 4: Combined @username + Budget Queries

When a SINGLE query contains BOTH `@username` AND a budget constraint:

```
"something under 200 by @user"
```

- `intent`: recipe_search (because @username is present)
- `filters.creator_username`: "user"
- `filters.cost`: {operator: "<=", value: 200, country: "Norway"}

**Important:** "under X" without time units = COST, not time. Only "under 30 minutes" is treated as time.

### Rule 5: Pronoun Resolution for Creators

When user says "her", "his", "him", "them", "their", "by her":

1. Look at `conversation_history` and `previous_search_context`
2. Find most recent `creator_username` or `creator_name`
3. Resolve pronoun to that creator

**Example:**
```
Q1: "recipes by @mammapia" → filters.creator_username: "mammapia"
Q2: "show me recipes under 100 by her" → filters.creator_username: "mammapia" (resolved from Q1)
```

---

## Examples

### Example 1: Simple Search + Refinement

```
Q1: "pasta recipes"
Q2: "I'm allergic to garlic"
Q3: "something quick"
```

**Q1 State:**
- `last_vector_query`: "pasta"
- `excluded_ingredients`: []
- `time_filter`: null

**Q2 State:**
- `last_vector_query`: "pasta"
- `excluded_ingredients`: ["garlic"]
- `time_filter`: null

**Q3 State:**
- `last_vector_query`: "pasta"
- `excluded_ingredients`: ["garlic"]
- `time_filter`: {sort_order: "ASC"}

**SQL for Q3:**
```sql
SELECT ... WHERE ...
  AND NOT EXISTS (... i."name" ILIKE '%garlic%')
ORDER BY (r."prepTime" + r."cookTime") ASC
```

### Example 2: Creator + Budget

```
Q1: "recipes by @mammapia"
Q2: "my budget is 200 kr"
```

**Q1 State:**
- `last_vector_query`: "recipes"
- `filters.creator_uid`: "uuid-of-mammapia"
- `filters.creator_username`: "mammapia"

**Q2 State:**
- `last_vector_query`: "recipes"
- `filters.creator_uid`: "uuid-of-mammapia"
- `filters.cost_filter`: {operator: "<=", value: 200, country: "Norway"}

### Example 3: Multi-Constraint Single Query

```
"quick vegetarian dinner recipes under 150 kr by @chef, I'm allergic to nuts"
```

**Extracted:**
- `intent`: recipe_search
- `vector_query`: "vegetarian dinner"
- `time_filter`: {sort_order: "ASC"}
- `tags`: ["vegetarian"]
- `meal_types`: ["dinner"]
- `cost_filter`: {operator: "<=", value: 150, country: "Norway"}
- `creator_username`: "chef"
- `excluded_ingredients`: ["nuts", "walnut", "almond", "pecan", "hazelnut", ...]

### Example 4: Pricing Follow-up

```
Q1: "what's the cost of banana"
Q2: "and in India?"
```

**Q1:**
- `intent`: pricing_info
- `entities.ingredients`: ["banana"]
- `parameters.country`: "Norway" (default)
- `last_pricing_item`: "banana"
- `last_pricing_item_type`: "ingredient"

**Q2:**
- `intent`: pricing_info (detected as follow-up)
- `entities.ingredients`: ["banana"] (from context)
- `parameters.country`: "India"

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `apps/fastapi/src/services/session_memory_manager.py` | Session state management |
| `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` | 10-stage pipeline orchestration |
| `apps/fastapi/src/services/sql_generator.py` | LLM-powered SQL generation |
| `apps/fastapi/src/agents/sdk_nlid_agent.py` | Intent detection & entity extraction |
| `apps/fastapi/src/utils/sql_builders.py` | Deterministic SQL builders for filters |
| `apps/fastapi/src/utils/cost_nutrition_filters.py` | Filter extraction utilities |
| `apps/fastapi/src/services/ingredient_matcher.py` | Smart allergen expansion |
