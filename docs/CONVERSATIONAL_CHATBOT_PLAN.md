# Conversational Chatbot Enhancement Plan

## Context

The current Sulten Chatbot is a recipe-focused assistant with a 10-stage pipeline. The user wants to enhance it to be more conversational with comprehensive capabilities.

### Original Requirements (8 features):
1. **Educational queries** - "What is vegan food?" → description of what is vegan food (what is included, why, etc) + 
   vegan recipe suggestions
2. **Festival/occasion queries** - "What is Significance of Christmas" → description + seasonal recipes
3. **Filter context on no results** - If no results found, generate a message saying I couldn't find any recipes 
   with filters X, Y, Z. Let me try removing these filters to generate results..
4. **Recipe reference queries** - "Explain the 1st recipe" → details of previously shown recipe
5. **Negative feedback** - "I don't like these" → exclude those recipes, search again
6. **User-created recipes** - "Recipes by user X" → filter by creator
7. **Price sorting improvement** - "Under $300" → show recipes nearest to budget (DESC sort)
8. **Additional recipe details** - Include nutrition, cost, seasonality in all responses

### Additional Requirements (21 use cases analyzed):

#### Already Handled (15 cases) - No changes needed:
| Query | How it's handled |
|-------|------------------|
| "What can I make without onion?" | `excluded_ingredients` filter |
| "What is the carb breakdown?" | `nutritional_info` intent |
| "Is almond milk healthy?" | `nutritional_info` intent |
| "Which ingredient has more iron?" | `nutritional_info` comparison |
| "Compare protein between recipes" | `nutritional_info` intent |
| "Compare rice vs quinoa" | `nutritional_info` intent |
| "Budget-friendly recipes" | `price_filter` intent |
| "Gluten-free recipes" | Tag filtering |
| "Vegan recipes" | Tag filtering |
| "Quick 10 minute snack" | Time filter (snack needs meal type) |
| "Show only easy ones" | Difficulty filter refinement |
| "Cheaper option?" | Price refinement |
| "Make it vegetarian" | Dietary tag refinement |
| "Replace chicken with tofu" | `ingredient_substitution` intent |
| "Similar recipes" | Vector similarity search |

#### Need New Features (10 cases):
| Query | Required Feature |
|-------|-----------------|
| "I only have eggs and potatoes" | New intent: `ingredients_on_hand` |
| "I have 500g chicken, 2 tomatoes, rice" | Enhanced ingredient matching with quantities |
| "Recipes with under 5 ingredients" | New filter: `ingredient_count` |
| "What does sauté mean?" | New intent: `cooking_technique` |
| "Kid-friendly recipes" | Tag system enhancement |
| "Increase serving to 6" | Serving scaling feature |
| "Clear all filters" or UI button | Filter management with re-search |
| No results found | Auto-clear filters & retry as new query |
| "Main course and dessert under $1000" | New intent: `combined_meal_search` with pair suggestions |
| "Recipes by user John" | Enhanced creator filter with user validation |

---

## Current Architecture Gaps

### Original Gaps:
| Gap | Current State | Required Change |
|-----|---------------|-----------------|
| No educational/festival intents | Falls through to general_chat | Add new intents |
| No recipe result storage | Cannot reference "1st recipe" | Store last_recipe_results in session |
| No negative feedback handling | Not supported | Add intent + exclusion logic |
| Price filtering uses ASC | Shows cheapest first | Change to DESC for nearest-to-budget |
| Missing nutrition/cost/seasonality | Not in recipe response | Add to post-processing |
| Filter context on no results | Generic message | Show specific filters to clear |

### Additional Gaps (from new use cases):
| Gap | Current State | Required Change |
|-----|---------------|-----------------|
| No "ingredients on hand" intent | Basic ingredient search | New intent with exact matching |
| No ingredient count filter | Not supported | Add `ingredient_count` filter |
| No cooking technique explanation | Falls to general_chat | New `cooking_technique` intent |
| No serving scaling | Static servings | Add scaling calculation |
| Limited meal types | Basic categories | Expand meal type recognition |
| No filter management | Filters only accumulate | Add clear filters + re-search capability |
| No structured filter storage | Ad-hoc session fields | `SessionFilters` dataclass |
| No last query storage | Cannot re-search after clear | Store `last_user_query` |
| No auto-clear on no results | Generic error message | Auto-clear filters & retry |
| No combined meal search | Single meal type only | Multi-course pair suggestions |
| Creator filter has no validation | Returns empty if user not found | Validate user + friendly error |

---

## Implementation Plan

### Phase 1: Foundation (Low Risk)

#### 1.1 Price Sorting Fix
**File:** `apps/fastapi/src/utils/sql_builders.py`

Change line 66 from `ASC` to `DESC`:
```python
# Before:
ORDER BY CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) ASC

# After:
ORDER BY CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) DESC
```

**Rationale:** When user says "under $300", they want recipes near $300 (nicer ingredients), not cheapest.

#### 1.2 Additional Recipe Details (Nutrition, Cost, Seasonality)
**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

In `_post_process_and_rank` method (~line 1100+), add batch queries and enrichment:

```python
# BATCH QUERY: Load seasonality for all recipes
seasonality_results = self.db.execute(text("""
    SELECT rs."recipeId", st.name as seasonality_name
    FROM recipe_seasonality rs
    JOIN seasonality s ON rs."seasonalityId" = s."id"
    JOIN seasonality_translation st ON s."id" = st."seasonalityId"
    WHERE rs."recipeId" = ANY(:recipe_ids)
"""), {"recipe_ids": unique_recipe_ids}).fetchall()

# In recipe processing loop, extract from recipe_metadata:
nutrition_data = None
if recipe.recipe_metadata:
    metadata = recipe.recipe_metadata
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    nutrition_data = metadata.get("totalNutrition", {}).get("macros")
    cost_data = metadata.get("pricing", {})

# Add to processed recipe dict:
"nutrition": nutrition_data,
"cost": cost_data,
"seasonality": seasonality_list
```

---

### Phase 2: Session Enhancements

#### 2.1 Store Last Recipe Results for Reference Queries
**File:** `apps/fastapi/src/services/session_memory_manager.py`

Add to `ContextEntities` dataclass (~line 38):
```python
@dataclass
class ContextEntities:
    # ... existing fields ...
    last_recipe_results: List[Dict[str, Any]] = field(default_factory=list)  # NEW
    last_recipe_count: int = 0  # NEW
```

Add to `SessionState` dataclass (~line 60):
```python
@dataclass
class SessionState:
    # ... existing fields ...
    excluded_recipe_ids: List[str] = field(default_factory=list)  # NEW
```

Update `update_search_context` method to accept and store recipe results.

#### 2.2 Enhanced No-Results Response
**File:** `apps/fastapi/src/agents/sdk_nlg_agent.py`

Modify `generate_no_results_response` to accept active filters:
```python
async def generate_no_results_response(
    query: str,
    intent: str,
    entities: Dict[str, Any],
    filters: Dict[str, Any],
    active_session_filters: Optional[Dict[str, Any]] = None  # NEW
) -> str:
    # If filters exist, suggest which ones to clear
    # Example: "No recipes found with vegan + Italian + under 30 min. Try removing the Italian filter?"
```

---

### Phase 3: New Intents

#### 3.1 Recipe Reference Intent
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

Add to agent instructions:
```
8. **recipe_reference**: User references a previously shown recipe by index
   - Examples: "Explain 1st recipe", "Ingredients of 3rd", "Tell me about the second one"
   - Entities:
     * reference_index: integer (1-5)
     * reference_type: "first", "second", "third", etc.
     * detail_requested: "ingredients", "instructions", "nutrition", "cost", "explain"
   - requires_embedding: false
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

Add handler (~line 265):
```python
if nlid_result_dict["intent"] == "recipe_reference":
    return await self._handle_recipe_reference_query(
        query, nlid_result_dict, session, user_uid, language
    )
```

Handler logic:
1. Parse reference index ("first" → 0, "second" → 1, etc.)
2. Get recipe ID from `session.context_entities.last_recipe_results[index]`
3. Fetch full details using `get_recipe_details()`
4. Return detailed response

#### 3.2 Negative Feedback Intent
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

```
9. **negative_feedback**: User expresses dissatisfaction with current results
   - Examples: "I don't like these", "Show me different ones", "Not what I'm looking for"
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

Handler logic:
1. Get recipe IDs from `session.context_entities.last_recipe_results`
2. Add them to `session.excluded_recipe_ids`
3. Re-run last search with exclusion filter
4. Return new results

#### 3.3 Educational Info Intent
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

```
10. **educational_info**: User asks for explanation of food/dietary concepts
    - Examples: "What is vegan food?", "Explain keto diet", "What does gluten-free mean?"
    - Entities: concept (vegan, keto, vegetarian, etc.)
    - Filters: May include related dietary tags for recipe suggestions
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

Handler logic:
1. Extract concept from entities
2. Generate 1-2 line description using LLM
3. Search recipes with related tag
4. Return description + recipes

#### 3.4 Festival/Occasion Intent
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

```
11. **festival_occasion**: User asks about festivals, holidays, or occasions
    - Examples: "Significance of Christmas", "Recipes for Diwali"
    - Entities: occasion name
    - Filters: Related seasonality tags
```

Handler logic:
1. Look up occasion in seasonality tables
2. Generate description
3. Find recipes with matching seasonality

#### 3.5 Creator Filter (Enhance recipe_search)
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

Add to recipe_search intent entities:
```
- creator_name: Username or display name of recipe creator
- creator_filter: True when user explicitly wants recipes by someone
```

**File:** `apps/fastapi/src/utils/sql_builders.py`

Add function:
```python
def build_creator_filter_condition(creator_name: str) -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM "user" u
            WHERE u."uid" = r."userUid"
            AND LOWER(u."username") LIKE LOWER('%{safe_name}%')
        )
    """
```

---

### Phase 4: NLG Enhancements

**File:** `apps/fastapi/src/agents/sdk_nlg_agent.py`

Add new response generators:

```python
async def generate_educational_response(
    concept: str,
    description: str,
    recipes: List[Dict[str, Any]]
) -> str:
    """Generate response for educational queries with recipe suggestions"""

async def generate_recipe_detail_response(
    recipe: Dict[str, Any],
    detail_type: str  # "ingredients", "instructions", "nutrition", "cost", "full"
) -> str:
    """Generate detailed response about a specific recipe"""

async def generate_feedback_response(
    query: str,
    new_recipes: List[Dict[str, Any]]
) -> str:
    """Generate response after negative feedback with new results"""
```

---

### Phase 4: Additional Use Cases (From Extended Requirements)

#### 4.1 Ingredients On Hand Intent
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

Add intent:
```
12. **ingredients_on_hand**: User lists ingredients they have and wants recipe suggestions
    - Examples: "I only have eggs and potatoes", "I have 500g chicken, 2 tomatoes, and rice"
    - Entities: ingredients (list with optional quantities)
    - requires_embedding: true (find recipes matching ingredients)
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

Handler logic:
1. Extract ingredients with quantities
2. Find recipes that use ALL specified ingredients
3. Prioritize recipes that use ONLY/MOSTLY these ingredients
4. Consider quantity matching for partial matches

#### 4.2 Ingredient Count Filter
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

Add to recipe_search filters:
```
- ingredient_count: {max: 5} for "under 5 ingredients"
```

**File:** `apps/fastapi/src/utils/sql_builders.py`

Add SQL condition:
```python
def build_ingredient_count_condition(max_count: int) -> str:
    return f"""
        (SELECT COUNT(*) FROM recipe_ingredient ri
         WHERE ri."recipeId" = r."id" AND ri."deletedAt" IS NULL) <= {max_count}
    """
```

#### 4.3 Cooking Technique Intent
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

Add intent:
```
13. **cooking_technique**: User asks about cooking techniques or terminology
    - Examples: "What does sauté mean?", "How do I blanch vegetables?"
    - Entities: technique_name (sauté, blanch, julienne, etc.)
    - requires_embedding: false
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

Handler logic:
1. Extract technique name
2. Generate explanation using LLM (or lookup from technique database)
3. Suggest recipes that use this technique

#### 4.4 Serving Size Scaling
**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

Enhance recipe_reference intent to include:
```
- scaling_factor: Target servings (e.g., 6)
```

**File:** `apps/fastapi/src/agents/agent_tools.py`

Add function:
```python
def scale_recipe_ingredients(recipe: Dict, target_servings: int) -> Dict:
    """Scale ingredient quantities based on target servings"""
    current_servings = recipe.get("servings", 1)
    scale_factor = target_servings / current_servings
    # Scale each ingredient amount
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `apps/fastapi/src/agents/sdk_nlid_agent.py` | Add 7 new intents, enhance recipe_search |
| `apps/fastapi/src/services/session_memory_manager.py` | Add last_recipe_results, excluded_recipe_ids |
| `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` | Add handlers, enhance post-processing |
| `apps/fastapi/src/agents/sdk_nlg_agent.py` | Add 4 new response generators |
| `apps/fastapi/src/utils/sql_builders.py` | Fix price sort, add creator/count filters |
| `apps/fastapi/src/agents/agent_tools.py` | Add serving scaling function |
| `docs/CHATBOT_PIPELINE.md` | Update documentation |

---

## Implementation Order (Dependencies)

```
PHASE 1: Foundation (No dependencies)
├── 1.1 Price sorting fix
├── 1.2 Add nutrition/cost/seasonality to recipes
├── 2.1 Session state for recipe results
└── 2.3 SessionFilters dataclass & last_user_query storage

PHASE 2: Depends on Phase 1
├── 3.1 Recipe reference queries (requires 2.1)
├── 3.2 Negative feedback handling (requires 2.1)
├── 2.2 Enhanced no-results response
├── 8.1 Auto-clear filters on no results (requires 2.3)
└── 7.1 Filter management API endpoints (requires 2.3)

PHASE 3: Depends on Phase 1 & 2
├── 3.3 Educational queries
├── 3.4 Festival/occasion queries
├── 3.5 Creator filter with validation (enhanced)
├── 10.1 User validation for creator filter
├── 4.1 Ingredients on hand intent
├── 4.2 Ingredient count filter
├── 4.3 Cooking technique intent
├── 4.4 Serving size scaling
├── 6.1 Dynamic ingredient category expansion
├── 7.2 Clear filters intent (requires 7.1)
└── 9.1 Combined meal search intent & pairing

PHASE 4: Final
├── NLG enhancements for all new intents
├── Integration testing
├── Documentation update
└── Frontend filter management UI
```

---

## All Use Cases & Test Scenarios

### Category 1: Recipe Search & Filtering

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 1.1 | "Show me chicken recipes" | Basic recipe search | Existing: `recipe_search` |
| 1.2 | "Gluten-free recipes" | Filter by dietary tag | Existing: tag filter |
| 1.3 | "Vegan recipes" | Filter by dietary tag | Existing: tag filter |
| 1.4 | "Kid-friendly recipes" | Filter by tag | **New**: Add tag to system |
| 1.5 | "Budget-friendly recipes" | Filter by low cost | Existing: `price_filter` |
| 1.6 | "Quick 10 minute snack" | Time + meal type filter | Existing: time filter, **Enhance**: meal type |
| 1.7 | "Recipes under $50" | Price filter sorted DESC | **Change**: ASC → DESC |
| 1.8 | "Recipes with under 5 ingredients" | Ingredient count filter | **New**: `ingredient_count` filter |
| 1.9 | "Recipes by John" | Creator filter | **New**: `creator_name` filter |
| 1.10 | "Easy Italian recipes under 30 min" | Multiple filters | Existing: combined filters |

### Category 2: Ingredient-Based Queries

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 2.1 | "I only have eggs and potatoes" | Find recipes using ALL ingredients | **New**: `ingredients_on_hand` intent |
| 2.2 | "I have 500g chicken, 2 tomatoes, rice" | Match with quantities | **New**: quantity matching |
| 2.3 | "What can I make without onion?" | Exclude ingredient | Existing: `excluded_ingredients` |
| 2.4 | "Replace chicken with tofu" | Substitution suggestion | Existing: `ingredient_substitution` |
| 2.5 | "I'm allergic to nuts" | Exclude ALL nuts (walnut, almond, cashew, etc.) | **New**: `category_expansion` |
| 2.6 | "No dairy please" | Exclude milk, cheese, butter, cream, etc. | **New**: `category_expansion` |
| 2.7 | "I can't have gluten" | Exclude wheat, barley, rye, etc. | **New**: `category_expansion` |
| 2.8 | "Allergic to shellfish" | Exclude shrimp, crab, lobster, etc. | **New**: `category_expansion` |
| 2.9 | "No citrus fruits" | Exclude orange, lemon, lime, etc. | **New**: `category_expansion` |
| 2.10 | "Avoid nightshades" | Exclude tomato, potato, eggplant, etc. | **New**: `category_expansion` |
| 2.11 | "No tropical fruits" | Exclude mango, pineapple, papaya, etc. | **New**: `category_expansion` |

### Category 3: Recipe Reference & Navigation

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 3.1 | "Explain the 1st recipe" | Full details of recipe #1 | **New**: `recipe_reference` intent |
| 3.2 | "Show ingredients of 3rd" | Ingredients of recipe #3 | **New**: `recipe_reference` intent |
| 3.3 | "Nutrition of the second one" | Nutrition of recipe #2 | **New**: `recipe_reference` intent |
| 3.4 | "Cost estimate of 4th" | Cost of recipe #4 | **New**: `recipe_reference` intent |
| 3.5 | "Similar recipes to 1st" | Vector similarity search | Existing: embedding search |

### Category 4: Negative Feedback & Refinement

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 4.1 | "I don't like these" | Exclude all shown, search again | **New**: `negative_feedback` intent |
| 4.2 | "Show only easy ones" | Add difficulty filter | Existing: refinement |
| 4.3 | "Cheaper option?" | Refine by price | Existing: refinement |
| 4.4 | "Make it vegetarian" | Add dietary tag | Existing: refinement |
| 4.5 | "Increase serving to 6" | Scale ingredients | **New**: serving scaling |

### Category 5: Educational & Knowledge

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 5.1 | "What is vegan food?" | Description + vegan recipes | **New**: `educational_info` intent |
| 5.2 | "What does sauté mean?" | Technique explanation | **New**: `cooking_technique` intent |
| 5.3 | "Significance of Christmas" | Festival info + recipes | **New**: `festival_occasion` intent |
| 5.4 | "Recipes for Diwali" | Seasonal recipes | **New**: `festival_occasion` intent |

### Category 6: Nutrition & Health

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 6.1 | "What is the carb breakdown?" | Recipe nutrition info | Existing: `nutritional_info` |
| 6.2 | "Is almond milk healthy?" | Ingredient nutrition | Existing: `nutritional_info` |
| 6.3 | "Which ingredient has more iron?" | Nutrition comparison | Existing: `nutritional_info` |
| 6.4 | "Compare protein between these two" | Recipe comparison | Existing: `nutritional_info` |
| 6.5 | "Compare rice vs quinoa" | Ingredient comparison | Existing: `nutritional_info` |
| 6.6 | "High protein low carb recipes" | Nutrition filter | Existing: `nutrition_filter` |

### Category 7: No Results Handling

| # | Scenario | Expected Behavior | Implementation |
|---|----------|-------------------|----------------|
| 7.1 | No results + filters active | Suggest which filters to clear | **Enhance**: no-results NLG |
| 7.2 | No results + no filters | Suggest alternatives | Existing |
| 7.3 | All recipes excluded | Message about clearing exclusions | **Enhance**: no-results NLG |

### Category 8: Recipe Details Enhancement

| # | Feature | Current | Required |
|---|---------|---------|----------|
| 8.1 | Nutrition in response | Not included | Add to all recipes |
| 8.2 | Cost in response | Not included | Add to all recipes |
| 8.3 | Seasonality in response | Not included | Add to all recipes |

### Category 9: Filter Management

| # | Query/Action | Expected Behavior | Implementation |
|---|--------------|-------------------|----------------|
| 9.1 | "Clear all filters" | Reset all filters, confirm | **New**: `clear_filters` intent |
| 9.2 | "Clear filters and search again" | Reset + re-search with last query | **New**: `clear_filters` with re-search |
| 9.3 | "Reset my exclusions" | Clear only ingredient exclusions | **New**: `clear_filters` scope=ingredients |
| 9.4 | UI: Clear Filters button | POST `/clear-filters?re_search=true` | **New**: API endpoint |
| 9.5 | UI: View active filters | GET `/filters` returns filter summary | **New**: API endpoint |
| 9.6 | Remove specific filter | DELETE `/filters/{filter_name}` | **New**: API endpoint |
| 9.7 | Remove one ingredient exclusion | POST `/remove-ingredient-exclusion` | **New**: API endpoint |

### Category 10: Auto-Clear on No Results

| # | Scenario | Expected Behavior | Implementation |
|---|----------|-------------------|----------------|
| 10.1 | No results + active filters | Auto-clear filters, retry search | **New**: Auto-clear logic |
| 10.2 | No results + no filters | "Try different keywords" message | **Enhance**: Error message |
| 10.3 | Results after auto-clear | "Couldn't find with filters, cleared them, found X" | **New**: Response format |
| 10.4 | Still no results after clear | Generic "no recipes" message | **Existing**: Fallback |

### Category 11: Combined Meal Search

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 11.1 | "Main course and dessert under $100" | 3 pairs of main + dessert within budget | **New**: `combined_meal_search` intent |
| 11.2 | "Appetizer and main under 60 min" | Pairs where total time <= 60 min | **New**: Time constraint pairing |
| 11.3 | "Breakfast and snack under 500 cal" | Pairs where total calories <= 500 | **New**: Calorie constraint pairing |
| 11.4 | No valid pairs found | "Couldn't find combinations, try adjusting" | **New**: No-results handling |
| 11.5 | "3 course meal under $200" | Multiple meal types in combination | **New**: Multi-course support |

### Category 12: Enhanced Creator Filter

| # | Query | Expected Behavior | Implementation |
|---|-------|-------------------|----------------|
| 12.1 | "Recipes by john_doe" (exact match) | Return recipes by that user | **Enhance**: Exact match first |
| 12.2 | "Recipes by NonExistent123" | "Couldn't find user, kindly confirm name" | **New**: User validation |
| 12.3 | "Recipes by John" (multiple users) | Use first user (most recipes), mention alternatives | **New**: Disambiguation |
| 12.4 | "Recipes by user with no recipes" | "'username' hasn't published any recipes" | **New**: Friendly message |
| 12.5 | "Show me my recipes" | Filter by current user's UID | **Existing**: User context |

---

## Detailed Implementation Specifications

### SPEC 1: New Intents for NLID Agent

**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

```python
# Add to agent instructions - Available Intents section:

8. **recipe_reference**: User references a previously shown recipe by position
   - Triggers: "first", "second", "third", "1st", "2nd", "3rd", "last"
   - Examples: "Explain the 1st recipe", "Ingredients of the third one"
   - Entities:
     * reference_position: integer (1-5) or "last"
     * detail_type: "full", "ingredients", "instructions", "nutrition", "cost"
   - requires_embedding: false

9. **negative_feedback**: User expresses dissatisfaction with results
   - Triggers: "don't like", "not what I want", "different", "other options"
   - Examples: "I don't like these", "Show me different ones"
   - Entities: none (uses session context)
   - requires_embedding: false (re-runs previous search)

10. **educational_info**: User asks about food/dietary concepts
    - Triggers: "what is", "explain", "what does X mean"
    - Examples: "What is vegan food?", "Explain keto diet"
    - Entities:
      * concept: string (vegan, keto, gluten-free, etc.)
      * concept_type: "diet", "ingredient", "technique"
    - Filters: May include related dietary tags
    - requires_embedding: true (for recipe suggestions)

11. **festival_occasion**: User asks about festivals/occasions
    - Triggers: Holiday names, occasions, seasons
    - Examples: "Significance of Christmas", "Recipes for Diwali"
    - Entities:
      * occasion_name: string
      * occasion_type: "holiday", "season", "event"
    - Filters: seasonality tags
    - requires_embedding: true

12. **ingredients_on_hand**: User lists available ingredients
    - Triggers: "I have", "I only have", "with X and Y"
    - Examples: "I only have eggs and potatoes", "I have 500g chicken, 2 tomatoes"
    - Entities:
      * ingredients: List[{name: string, quantity: optional, unit: optional}]
    - requires_embedding: true

13. **cooking_technique**: User asks about cooking methods
    - Triggers: "what does X mean", "how do I X"
    - Examples: "What does sauté mean?", "How do I blanch vegetables?"
    - Entities:
      * technique_name: string
    - requires_embedding: true (for technique-related recipes)
```

### SPEC 2: Session State Extensions

**File:** `apps/fastapi/src/services/session_memory_manager.py`

```python
@dataclass
class ContextEntities:
    """Extended context entities"""
    last_referenced_recipe_id: Optional[str] = None
    last_referenced_recipe_name: Optional[str] = None
    last_ingredient_list: List[str] = field(default_factory=list)
    active_timers: List[Dict[str, Any]] = field(default_factory=list)
    last_vector_query: Optional[str] = None
    last_search_filters: Optional[Dict[str, Any]] = None

    # NEW FIELDS:
    last_recipe_results: List[Dict[str, Any]] = field(default_factory=list)
    # Format: [{"id": "uuid", "name": "Recipe Name", "index": 1}, ...]
    last_recipe_count: int = 0


@dataclass
class SessionState:
    """Extended session state"""
    # ... existing fields ...

    # NEW FIELD:
    excluded_recipe_ids: List[str] = field(default_factory=list)
    # Recipes to exclude in future searches (from negative feedback)


# Add method to SessionMemoryManager:
def update_recipe_results(
    self,
    session: SessionState,
    recipes: List[Dict[str, Any]]
) -> SessionState:
    """Store recipe results for reference queries"""
    session.context_entities.last_recipe_results = [
        {"id": str(r.get("id")), "name": r.get("name"), "index": i + 1}
        for i, r in enumerate(recipes[:5])  # Store max 5
    ]
    session.context_entities.last_recipe_count = len(recipes)
    return session


def add_excluded_recipes(
    self,
    session: SessionState,
    recipe_ids: List[str]
) -> SessionState:
    """Add recipes to exclusion list (negative feedback)"""
    for rid in recipe_ids:
        if rid not in session.excluded_recipe_ids:
            session.excluded_recipe_ids.append(rid)
    return session
```

### SPEC 3: SQL Builder Extensions

**File:** `apps/fastapi/src/utils/sql_builders.py`

```python
def build_ingredient_count_condition(max_count: int) -> str:
    """
    Build SQL condition for maximum ingredient count.
    Usage: "Recipes with under 5 ingredients"
    """
    return f"""
        (SELECT COUNT(*)
         FROM recipe_ingredient ri
         WHERE ri."recipeId" = r."id"
         AND ri."deletedAt" IS NULL) <= {max_count}
    """


def build_creator_filter_condition(creator_name: str) -> str:
    """
    Build SQL condition for filtering by recipe creator.
    Usage: "Recipes by John"
    """
    safe_name = creator_name.replace("'", "''")
    return f"""
        EXISTS (
            SELECT 1 FROM "user" u
            WHERE u."uid" = r."userUid"
            AND (
                LOWER(u."username") LIKE LOWER('%{safe_name}%')
                OR LOWER(u."displayName") LIKE LOWER('%{safe_name}%')
            )
        )
    """


def build_excluded_recipes_condition(excluded_ids: List[str]) -> str:
    """
    Build SQL condition for excluding specific recipes.
    Usage: Negative feedback - "I don't like these"
    """
    if not excluded_ids:
        return ""
    ids_str = ", ".join(f"'{rid}'" for rid in excluded_ids)
    return f'r."id" NOT IN ({ids_str})'


def build_ingredients_on_hand_condition(
    ingredients: List[str],
    match_all: bool = True
) -> str:
    """
    Build SQL condition for recipes using specified ingredients.
    Usage: "I only have eggs and potatoes"

    Args:
        ingredients: List of ingredient names
        match_all: If True, recipe must contain ALL ingredients
    """
    conditions = []
    for ing in ingredients:
        safe_ing = ing.replace("'", "''").lower()
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM recipe_ingredient ri
                JOIN ingredient i ON ri."ingredientId" = i."id"
                WHERE ri."recipeId" = r."id"
                AND LOWER(i."name") LIKE '%{safe_ing}%'
            )
        """)

    operator = " AND " if match_all else " OR "
    return f"({operator.join(conditions)})"


# MODIFY EXISTING FUNCTION:
def build_recipe_cost_filter_sql(
    cost_filter: Dict[str, Any],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    # ... existing code ...

    # CHANGE: Sort DESC to show recipes closest to budget
    base_query += f"""
ORDER BY CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) DESC
LIMIT {limit}
"""
    return base_query
```

### SPEC 4: Pipeline Handler Methods

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

```python
async def _handle_recipe_reference_query(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle queries referencing previously shown recipes.
    Examples: "Explain the 1st recipe", "Ingredients of the third one"
    """
    # 1. Parse position from entities
    position = nlid_result.get("entities", {}).get("reference_position", 1)
    detail_type = nlid_result.get("entities", {}).get("detail_type", "full")

    # Handle "last" as the most recent
    if position == "last":
        position = len(session.context_entities.last_recipe_results)

    # 2. Get recipe from stored results
    recipe_results = session.context_entities.last_recipe_results
    if not recipe_results or position > len(recipe_results):
        return {
            "response": "I don't have any recipes to reference. Try searching for recipes first.",
            "metadata": {"error": "no_recipes_in_context"}
        }

    recipe_ref = recipe_results[position - 1]  # Convert 1-indexed to 0-indexed
    recipe_id = recipe_ref.get("id")

    # 3. Fetch full recipe details
    recipe_details = get_recipe_details(self.db, recipe_id, language)

    # 4. Add nutrition/cost if requested
    if detail_type in ["nutrition", "cost", "full"]:
        recipe_details["nutrition"] = get_recipe_nutrition(self.db, recipe_id)
        recipe_details["cost"] = get_recipe_cost(self.db, recipe_id, "usa")

    # 5. Generate response
    response = await generate_recipe_detail_response(recipe_details, detail_type)

    return {
        "response": response,
        "metadata": {
            "intent": "recipe_reference",
            "recipe": recipe_details
        }
    }


async def _handle_negative_feedback(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle negative feedback on shown recipes.
    Examples: "I don't like these", "Show me different ones"
    """
    # 1. Get IDs of shown recipes
    shown_ids = [r.get("id") for r in session.context_entities.last_recipe_results]

    if not shown_ids:
        return {
            "response": "I don't have any previous results to exclude. Let me search for recipes for you.",
            "metadata": {"error": "no_recipes_to_exclude"}
        }

    # 2. Add to excluded list
    session = self.session_manager.add_excluded_recipes(session, shown_ids)

    # 3. Re-run last search with exclusions
    last_query = session.context_entities.last_vector_query
    last_filters = session.context_entities.last_search_filters or {}

    # Add exclusion filter
    last_filters["excluded_recipe_ids"] = session.excluded_recipe_ids

    # 4. Execute new search
    result = await self.process_query(
        query=last_query,
        session_id=session.session_id,
        user_uid=user_uid,
        language=language
    )

    return result


async def _handle_educational_query(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle educational queries with recipe suggestions.
    Examples: "What is vegan food?", "Explain keto diet"
    """
    # 1. Extract concept
    concept = nlid_result.get("entities", {}).get("concept", "")

    # 2. Generate educational description using LLM
    description = await self._generate_concept_description(concept)

    # 3. Search for related recipes
    concept_to_tag = {
        "vegan": "vegan",
        "vegetarian": "vegetarian",
        "keto": "keto",
        "gluten-free": "gluten-free",
        "dairy-free": "dairy-free",
        "paleo": "paleo",
    }
    tag = concept_to_tag.get(concept.lower(), concept.lower())

    # 4. Search recipes with tag
    search_result = await self.process_query(
        query=f"{concept} recipes",
        session_id=session.session_id,
        user_uid=user_uid,
        language=language
    )

    # 5. Combine description with recipes
    response = f"{description}\n\n{search_result['response']}"

    return {
        "response": response,
        "metadata": {
            "intent": "educational_info",
            "concept": concept,
            "description": description,
            "recipes": search_result.get("metadata", {}).get("recipes", [])
        }
    }


async def _handle_ingredients_on_hand(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle queries about available ingredients.
    Examples: "I only have eggs and potatoes", "I have 500g chicken, 2 tomatoes"
    """
    # 1. Extract ingredients
    ingredients_data = nlid_result.get("entities", {}).get("ingredients", [])
    ingredient_names = [ing.get("name") for ing in ingredients_data]

    if not ingredient_names:
        return {
            "response": "Could you tell me which ingredients you have available?",
            "metadata": {"error": "no_ingredients_detected"}
        }

    # 2. Build SQL with ingredient matching
    # Recipes that use ALL specified ingredients
    condition = build_ingredients_on_hand_condition(ingredient_names, match_all=True)

    # 3. Execute search
    sql = f"""
    SELECT r."id", r."name", r."ingress", r."image",
           (r."prepTime" + r."cookTime") as total_time,
           r."difficulty", r."servings"
    FROM recipe r
    WHERE r."deletedAt" IS NULL
      AND r."status" = 'published'
      AND r."languageId" = '{language}'
      AND {condition}
    ORDER BY r."name"
    LIMIT 10
    """

    result = self.db.execute(text(sql)).fetchall()

    # 4. Process and return results
    recipes = [self._row_to_dict(row) for row in result]

    if not recipes:
        response = f"I couldn't find any recipes using all of: {', '.join(ingredient_names)}. "
        response += "Try removing some ingredients or searching with fewer constraints."
    else:
        response = f"Here are {len(recipes)} recipes you can make with {', '.join(ingredient_names)}:"

    return {
        "response": response,
        "metadata": {
            "intent": "ingredients_on_hand",
            "ingredients": ingredient_names,
            "recipes": recipes
        }
    }


def scale_recipe_ingredients(
    recipe: Dict[str, Any],
    target_servings: int
) -> Dict[str, Any]:
    """
    Scale recipe ingredients based on target servings.
    Usage: "Increase serving to 6"
    """
    current_servings = recipe.get("servings", 1)
    if current_servings <= 0:
        current_servings = 1

    scale_factor = target_servings / current_servings

    scaled_ingredients = []
    for ing in recipe.get("ingredients", []):
        scaled_ing = ing.copy()
        if ing.get("amount"):
            scaled_ing["amount"] = round(ing["amount"] * scale_factor, 2)
        scaled_ingredients.append(scaled_ing)

    recipe["ingredients"] = scaled_ingredients
    recipe["servings"] = target_servings
    recipe["scale_factor"] = scale_factor

    return recipe
```

### SPEC 5: NLG Response Generators

**File:** `apps/fastapi/src/agents/sdk_nlg_agent.py`

```python
async def generate_recipe_detail_response(
    recipe: Dict[str, Any],
    detail_type: str
) -> str:
    """
    Generate detailed response about a specific recipe.
    detail_type: "full", "ingredients", "instructions", "nutrition", "cost"
    """
    if detail_type == "ingredients":
        ingredients = recipe.get("ingredients", [])
        ing_list = "\n".join([
            f"• {ing.get('amount', '')} {ing.get('unit', '')} {ing.get('name', '')}"
            for ing in ingredients
        ])
        return f"**{recipe.get('name')}** ingredients:\n{ing_list}"

    elif detail_type == "instructions":
        instructions = recipe.get("instructions", [])
        inst_list = "\n".join([
            f"{i.get('order')}. {i.get('description')}"
            for i in instructions
        ])
        return f"**{recipe.get('name')}** instructions:\n{inst_list}"

    elif detail_type == "nutrition":
        nutrition = recipe.get("nutrition", {})
        return f"""**{recipe.get('name')}** nutrition per serving:
• Calories: {nutrition.get('energyKcal', 'N/A')} kcal
• Protein: {nutrition.get('protein', 'N/A')}g
• Carbs: {nutrition.get('carbohydrates', 'N/A')}g
• Fat: {nutrition.get('totalFat', 'N/A')}g"""

    elif detail_type == "cost":
        cost = recipe.get("cost", {})
        return f"**{recipe.get('name')}** estimated cost: ${cost.get('usa', {}).get('total', 'N/A')}"

    else:  # full
        return f"""**{recipe.get('name')}**
{recipe.get('description', recipe.get('ingress', ''))}

⏱️ Total time: {recipe.get('total_time', 'N/A')} min
👥 Servings: {recipe.get('servings', 'N/A')}
📊 Difficulty: {recipe.get('difficulty', 'N/A')}"""


async def generate_no_results_with_filters_response(
    query: str,
    active_filters: Dict[str, Any]
) -> str:
    """
    Generate helpful response when no results found with filters.
    Suggests which filters to clear.
    """
    filter_names = []

    if active_filters.get("tags"):
        filter_names.append(f"tags: {', '.join(active_filters['tags'])}")
    if active_filters.get("cuisines"):
        filter_names.append(f"cuisines: {', '.join(active_filters['cuisines'])}")
    if active_filters.get("excluded_ingredients"):
        filter_names.append(f"excluding: {', '.join(active_filters['excluded_ingredients'])}")
    if active_filters.get("max_time"):
        filter_names.append(f"under {active_filters['max_time']} min")
    if active_filters.get("difficulty"):
        filter_names.append(f"difficulty: {active_filters['difficulty']}")

    if filter_names:
        return f"""No recipes found with your current filters:
• {chr(10).join(['• ' + f for f in filter_names])}

Try removing one or more filters to see more results."""
    else:
        return f"No recipes found for '{query}'. Try different keywords or broaden your search."


async def generate_educational_response(
    concept: str,
    description: str,
    recipes: List[Dict[str, Any]]
) -> str:
    """
    Generate response for educational queries.
    """
    if recipes:
        return f"{description}\n\nHere are some {concept} recipes you might like:"
    else:
        return description
```

---

### SPEC 6: Intelligent Ingredient Matching System

**Problem:** Multiple challenges with ingredient handling:

1. **Category Expansion**: User says "I'm allergic to nuts" but database has specific ingredients like "walnut", "almond", "cashew"
2. **Database Mismatches**: LLM expands "nuts" → ["walnut", "almond"], but database has "Walnuts" (plural), "Almonds (raw)" (suffix), "Kaju" (Hindi for cashew)
3. **Spelling Mistakes**: User types "tomatos" instead of "tomatoes", "aple" instead of "apple"
4. **Synonyms & Regional Names**: "cilantro" vs "coriander", "eggplant" vs "aubergine", "kaju" vs "cashew"
5. **Plural Forms**: "onion" vs "onions", "berry" vs "berries"

**Solution:** Hybrid Approach (LLM + Embedding Similarity) - No database category dependency

---

#### Architecture Overview

```
User Input: "I'm allergic to nuts, no tomatos please"
                    ↓
┌─────────────────────────────────────────────────────────┐
│  STEP 1: NLID Extraction                                 │
│  Extract: excluded_ingredients: ["nuts", "tomatos"]      │
└─────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│  STEP 2: LLM Expansion & Normalization                   │
│  - Expand categories to all variations                   │
│  - Fix spelling mistakes                                 │
│  - Include synonyms, plurals, regional names             │
│  Output: ["walnut", "walnuts", "almond", "almonds",      │
│           "cashew", "kaju", "tomato", "tomatoes", ...]   │
└─────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│  STEP 3: Database Matching (Embedding + Text)            │
│  Match expanded items to actual database ingredients     │
│  - Substring matching (plurals, suffixes)               │
│  - Word-level matching (multi-word names)               │
│  - Embedding similarity (synonyms, regional names)      │
│  Output: [ingredient_id_1, ingredient_id_2, ...]        │
└─────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│  STEP 4: SQL Filter by IDs                               │
│  Exclude recipes containing ANY matched ingredient IDs   │
└─────────────────────────────────────────────────────────┘
```

---

#### Implementation

**File:** `apps/fastapi/src/utils/ingredient_matcher.py` (NEW FILE)

```python
"""
Intelligent Ingredient Matching System
Handles: Categories, Spelling Mistakes, Plurals, Synonyms, Regional Names
Uses: LLM + Embedding Similarity (No database category dependency)
"""
from typing import List, Dict, Any, Tuple, Optional
import json
import re
import time
import numpy as np
from openai import OpenAI
from sqlalchemy.orm import Session
from sqlalchemy import text
from apps.fastapi import logger


# ============================================================================
# CACHING
# ============================================================================

# In-memory cache for expansions and embeddings (TTL: 1 hour)
_EXPANSION_CACHE: Dict[str, Tuple[List[str], float]] = {}
_EMBEDDING_CACHE: Dict[str, Tuple[List[float], float]] = {}
_CACHE_TTL = 3600  # 1 hour

# Similarity threshold for embedding matching
SIMILARITY_THRESHOLD = 0.85


# ============================================================================
# MAIN CLASS
# ============================================================================

class IntelligentIngredientMatcher:
    """
    Handles all ingredient matching scenarios:
    - Category expansion (nuts → walnut, almond, etc.)
    - Spelling correction (tomatos → tomato)
    - Plural handling (onions → onion)
    - Synonym matching (cilantro ↔ coriander)
    - Regional names (kaju → cashew)
    """

    def __init__(self, openai_client: OpenAI, db: Session):
        self.client = openai_client
        self.db = db

    async def process_ingredients(
        self,
        ingredients: List[str],
        mode: str = "exclude"  # "exclude" or "include"
    ) -> List[str]:
        """
        Main entry point: Process ingredient list and return database IDs.

        Args:
            ingredients: List from NLID (e.g., ["nuts", "tomatos"])
            mode: "exclude" for allergies/dislikes, "include" for search

        Returns:
            List of database ingredient IDs to filter by
        """
        if not ingredients:
            return []

        logger.info(f"[INGREDIENT_MATCHER] Processing {len(ingredients)} ingredients: {ingredients}")

        # Step 1: LLM expansion and normalization
        expanded_items = await self._llm_expand_and_normalize(ingredients)

        # Step 2: Get database ingredients with embeddings
        db_ingredients = self._fetch_database_ingredients()

        # Step 3: Match expanded items to database
        matched_ids = self._match_to_database(expanded_items, db_ingredients)

        logger.info(
            f"[INGREDIENT_MATCHER] Matched {len(matched_ids)} database ingredients "
            f"from {len(ingredients)} input items"
        )

        return matched_ids

    # ========================================================================
    # STEP 1: LLM EXPANSION & NORMALIZATION
    # ========================================================================

    async def _llm_expand_and_normalize(self, ingredients: List[str]) -> List[str]:
        """
        Use LLM to:
        1. Expand categories to all specific items
        2. Fix spelling mistakes
        3. Include plurals, synonyms, regional names
        """
        cache_key = "|".join(sorted(ingredients)).lower()

        # Check cache
        if cache_key in _EXPANSION_CACHE:
            cached, timestamp = _EXPANSION_CACHE[cache_key]
            if time.time() - timestamp < _CACHE_TTL:
                logger.info(f"[INGREDIENT_MATCHER] Cache hit for expansion")
                return cached

        prompt = f"""You are a culinary expert. Process these ingredients for recipe search.

Input ingredients: {json.dumps(ingredients)}

For EACH ingredient, provide ALL variations that could match in a database:
1. Category expansion (if category like "nuts" → all specific nuts)
2. Spelling corrections (fix any typos)
3. Plural/singular forms (onion, onions)
4. Common synonyms (cilantro ↔ coriander)
5. Regional names (kaju = cashew, aubergine = eggplant)
6. Common variations (tomato, tomatoes, cherry tomato)

Return JSON object mapping each input to its variations:
{{
  "nuts": ["walnut", "walnuts", "almond", "almonds", "cashew", "cashews", "kaju",
           "pecan", "pecans", "hazelnut", "hazelnuts", "pistachio", "macadamia",
           "peanut", "peanuts", "groundnut", "pine nut", "brazil nut", "chestnut"],
  "tomatos": ["tomato", "tomatoes", "cherry tomato", "roma tomato"]
}}

Rules:
- Maximum 15 variations per ingredient
- Include ALL common names and spellings
- Always include both singular and plural
- Return ONLY valid JSON, no explanation

Process these ingredients: {json.dumps(ingredients)}
Output:"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.1
            )

            content = response.choices[0].message.content.strip()
            expansion_dict = self._parse_json_response(content)

            # Flatten to single list
            all_variations = []
            for variations in expansion_dict.values():
                all_variations.extend([v.lower().strip() for v in variations])

            # Remove duplicates
            expanded = list(set(all_variations))

            # Cache result
            _EXPANSION_CACHE[cache_key] = (expanded, time.time())

            logger.info(
                f"[INGREDIENT_MATCHER] LLM expanded {len(ingredients)} items "
                f"→ {len(expanded)} variations"
            )

            return expanded

        except Exception as e:
            logger.error(f"[INGREDIENT_MATCHER] LLM expansion failed: {e}")
            # Fallback: return original items with lowercase
            return [ing.lower() for ing in ingredients]

    # ========================================================================
    # STEP 2: FETCH DATABASE INGREDIENTS
    # ========================================================================

    def _fetch_database_ingredients(self) -> List[Dict[str, Any]]:
        """Fetch all ingredients from database with their embeddings."""
        sql = text("""
            SELECT id, name, embedding
            FROM ingredient
            WHERE "deletedAt" IS NULL
        """)
        result = self.db.execute(sql).fetchall()

        ingredients = []
        for row in result:
            embedding = row[2]
            # Parse embedding if stored as string
            if isinstance(embedding, str):
                try:
                    embedding = json.loads(embedding)
                except:
                    embedding = None

            ingredients.append({
                "id": str(row[0]),
                "name": row[1],
                "name_lower": row[1].lower(),
                "embedding": embedding
            })

        logger.info(f"[INGREDIENT_MATCHER] Fetched {len(ingredients)} ingredients from database")
        return ingredients

    # ========================================================================
    # STEP 3: MATCH TO DATABASE
    # ========================================================================

    def _match_to_database(
        self,
        expanded_items: List[str],
        db_ingredients: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Match expanded items to database ingredients using multiple strategies:
        1. Substring matching (handles plurals, suffixes)
        2. Word-level matching (handles multi-word names)
        3. Embedding similarity (handles synonyms, regional names)
        """
        matched_ids = set()

        # Get embeddings for expanded items (batch)
        expanded_embeddings = self._get_embeddings_batch(expanded_items)

        for i, expanded_item in enumerate(expanded_items):
            expanded_lower = expanded_item.lower()
            expanded_embedding = expanded_embeddings[i]
            expanded_words = set(expanded_lower.split())

            for db_ing in db_ingredients:
                db_name_lower = db_ing["name_lower"]
                db_words = set(db_name_lower.split())

                # Strategy 1: Exact match
                if expanded_lower == db_name_lower:
                    matched_ids.add(db_ing["id"])
                    continue

                # Strategy 2: Substring match (handles plurals, suffixes)
                # "walnut" matches "Walnuts", "walnut oil"
                if expanded_lower in db_name_lower or db_name_lower in expanded_lower:
                    # Avoid false positives like "nut" matching "coconut"
                    # Only match if one is substring of the other AND reasonable length
                    if len(expanded_lower) >= 4 or len(db_name_lower) >= 4:
                        matched_ids.add(db_ing["id"])
                        continue

                # Strategy 3: Word-level match
                # "cashew" matches "cashew nut", "roasted cashews"
                common_words = expanded_words & db_words
                if common_words:
                    # Check if common word is significant (not "oil", "powder", etc.)
                    significant_words = common_words - {"oil", "powder", "paste", "extract"}
                    if significant_words:
                        matched_ids.add(db_ing["id"])
                        continue

                # Strategy 4: Embedding similarity (handles synonyms, regional names)
                if expanded_embedding and db_ing["embedding"]:
                    similarity = self._cosine_similarity(
                        expanded_embedding,
                        db_ing["embedding"]
                    )
                    if similarity >= SIMILARITY_THRESHOLD:
                        matched_ids.add(db_ing["id"])
                        logger.debug(
                            f"[INGREDIENT_MATCHER] Embedding match: "
                            f"'{expanded_item}' ↔ '{db_ing['name']}' ({similarity:.2f})"
                        )

        return list(matched_ids)

    # ========================================================================
    # EMBEDDING UTILITIES
    # ========================================================================

    def _get_embeddings_batch(self, items: List[str]) -> List[Optional[List[float]]]:
        """Get embeddings for multiple items (with caching)."""
        if not items:
            return []

        embeddings = []
        items_to_fetch = []
        items_to_fetch_indices = []

        # Check cache first
        for i, item in enumerate(items):
            cache_key = item.lower()
            if cache_key in _EMBEDDING_CACHE:
                cached, timestamp = _EMBEDDING_CACHE[cache_key]
                if time.time() - timestamp < _CACHE_TTL:
                    embeddings.append((i, cached))
                    continue

            items_to_fetch.append(item)
            items_to_fetch_indices.append(i)

        # Fetch missing embeddings
        if items_to_fetch:
            try:
                response = self.client.embeddings.create(
                    model="text-embedding-3-small",
                    input=items_to_fetch
                )

                for j, item in enumerate(items_to_fetch):
                    embedding = response.data[j].embedding
                    embeddings.append((items_to_fetch_indices[j], embedding))

                    # Cache it
                    _EMBEDDING_CACHE[item.lower()] = (embedding, time.time())

            except Exception as e:
                logger.error(f"[INGREDIENT_MATCHER] Embedding fetch failed: {e}")
                for idx in items_to_fetch_indices:
                    embeddings.append((idx, None))

        # Sort by original index and extract embeddings
        embeddings.sort(key=lambda x: x[0])
        return [e[1] for e in embeddings]

    def _cosine_similarity(
        self,
        vec1: Optional[List[float]],
        vec2: Optional[List[float]]
    ) -> float:
        """Calculate cosine similarity between two vectors."""
        if not vec1 or not vec2:
            return 0.0

        v1 = np.array(vec1)
        v2 = np.array(vec2)

        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(v1, v2) / (norm1 * norm2))

    # ========================================================================
    # UTILITIES
    # ========================================================================

    def _parse_json_response(self, content: str) -> Dict[str, List[str]]:
        """Parse JSON from LLM response."""
        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON object
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Fallback: return empty dict
        return {}


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

async def process_excluded_ingredients(
    excluded: List[str],
    openai_client: OpenAI,
    db: Session
) -> List[str]:
    """
    Process excluded ingredients for allergy/dislike queries.

    Usage:
        excluded_ids = await process_excluded_ingredients(
            ["nuts", "tomatos"],
            openai_client,
            db
        )
        # Returns: ["uuid-walnut", "uuid-almond", "uuid-tomato", ...]
    """
    matcher = IntelligentIngredientMatcher(openai_client, db)
    return await matcher.process_ingredients(excluded, mode="exclude")


async def process_included_ingredients(
    included: List[str],
    openai_client: OpenAI,
    db: Session
) -> List[str]:
    """
    Process included ingredients for search queries.

    Usage:
        included_ids = await process_included_ingredients(
            ["chicken", "tomatos"],
            openai_client,
            db
        )
    """
    matcher = IntelligentIngredientMatcher(openai_client, db)
    return await matcher.process_ingredients(included, mode="include")


def clear_ingredient_matcher_cache():
    """Clear all caches (useful for testing or memory management)."""
    global _EXPANSION_CACHE, _EMBEDDING_CACHE
    _EXPANSION_CACHE.clear()
    _EMBEDDING_CACHE.clear()
```

---

#### SQL Builder Update

**File:** `apps/fastapi/src/utils/sql_builders.py` (MODIFY)

```python
def build_excluded_ingredients_by_id_condition(excluded_ids: List[str]) -> str:
    """
    Exclude recipes containing ANY of these ingredient IDs.
    More reliable than name matching.
    """
    if not excluded_ids:
        return ""

    ids_str = ", ".join(f"'{id}'" for id in excluded_ids)

    return f"""
        NOT EXISTS (
            SELECT 1 FROM recipe_ingredient ri
            WHERE ri."recipeId" = r."id"
            AND ri."deletedAt" IS NULL
            AND ri."ingredientId" IN ({ids_str})
        )
    """


def build_included_ingredients_by_id_condition(included_ids: List[str]) -> str:
    """
    Include recipes containing ALL of these ingredient IDs.
    """
    if not included_ids:
        return ""

    # Each ingredient must be present
    conditions = []
    for ing_id in included_ids:
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM recipe_ingredient ri
                WHERE ri."recipeId" = r."id"
                AND ri."deletedAt" IS NULL
                AND ri."ingredientId" = '{ing_id}'
            )
        """)

    return " AND ".join(conditions)
```

---

#### Pipeline Integration

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` (MODIFY)

```python
from apps.fastapi.src.utils.ingredient_matcher import (
    process_excluded_ingredients,
    process_included_ingredients
)

async def process_query(self, query: str, session_id: str, ...):
    # ... NLID detection ...

    filters = nlid_result_dict.get("filters", {})

    # ================================================================
    # PROCESS EXCLUDED INGREDIENTS (Allergies, Dislikes)
    # ================================================================
    excluded_ingredients = filters.get("excluded_ingredients", [])

    # Add session-level exclusions
    if session.excluded_ingredients:
        excluded_ingredients.extend(session.excluded_ingredients)

    if excluded_ingredients:
        # Intelligent matching: LLM + Embedding
        excluded_ids = await process_excluded_ingredients(
            excluded_ingredients,
            self.client,
            self.db
        )

        filters["excluded_ingredient_ids"] = excluded_ids

        logger.info(
            f"[INGREDIENT_MATCHER] Excluded: {excluded_ingredients} "
            f"→ {len(excluded_ids)} database IDs"
        )

    # ================================================================
    # PROCESS INCLUDED INGREDIENTS (Search queries)
    # ================================================================
    included_ingredients = filters.get("ingredients", [])

    if included_ingredients:
        # Intelligent matching: LLM + Embedding
        included_ids = await process_included_ingredients(
            included_ingredients,
            self.client,
            self.db
        )

        filters["included_ingredient_ids"] = included_ids

        logger.info(
            f"[INGREDIENT_MATCHER] Included: {included_ingredients} "
            f"→ {len(included_ids)} database IDs"
        )

    # Update filters
    nlid_result_dict["filters"] = filters

    # Continue with search...
```

---

#### Matching Strategies Summary

| Strategy | Handles | Example Match |
|----------|---------|---------------|
| **Exact Match** | Identical names | "chicken" = "chicken" |
| **Substring** | Plurals, suffixes | "walnut" → "Walnuts", "walnut oil" |
| **Word-level** | Multi-word names | "cashew" → "cashew nut", "roasted cashews" |
| **Embedding** | Synonyms, regional names, spelling | "kaju" → "cashew", "cilantro" → "coriander", "tomatos" → "tomato" |

---

#### Test Cases

| # | User Query | Input | Expected Database Matches |
|---|------------|-------|---------------------------|
| 1 | "I'm allergic to nuts" | ["nuts"] | All nut ingredients (walnut, almond, cashew, pecan, etc.) |
| 2 | "No dairy please" | ["dairy"] | milk, cheese, butter, cream, yogurt, etc. |
| 3 | "I don't like tomatos" | ["tomatos"] | tomato, tomatoes, cherry tomato (spelling fixed) |
| 4 | "No aple in my food" | ["aple"] | apple, apples (spelling fixed) |
| 5 | "Show me chicken recipes" | ["chicken"] | chicken, chicken breast, chicken thigh |
| 6 | "Allergic to kaju" | ["kaju"] | cashew, cashews, cashew nut (regional name) |
| 7 | "No cilantro" | ["cilantro"] | coriander, cilantro, fresh coriander (synonym) |
| 8 | "I hate onions" | ["onions"] | onion, onions, red onion, white onion (plural) |
| 9 | "No egplant" | ["egplant"] | eggplant, aubergine (spelling + synonym) |
| 10 | "Show me recipes with potatos" | ["potatos"] | potato, potatoes (spelling + plural) |

---

#### Performance Optimization

```python
# Caching is built-in:
# - Expansion cache: 1 hour TTL
# - Embedding cache: 1 hour TTL

# For high-traffic scenarios, consider:
# 1. Redis cache instead of in-memory
# 2. Pre-compute common category expansions
# 3. Batch embedding requests
```

---

### SPEC 7: Context Management & Filter Control

**Problem:** Users accumulate filters during a conversation (excluded ingredients, tags, cuisines, etc.) and need a way to:
1. See what filters are active
2. Clear all filters and start fresh
3. Clear specific filters without losing everything
4. Re-search with the last query after clearing filters

**Solution:** Three-tier context system with explicit filter management endpoints.

#### Three-Tier Context System

| Tier | Scope | Stored In | Examples | Lifecycle |
|------|-------|-----------|----------|-----------|
| **Persistent** | Cross-session | Database (User Preferences) | Allergies, dietary restrictions, disliked ingredients | Survives session end |
| **Search-Specific** | Current conversation | SessionState | Excluded ingredients, tags, cuisines, time filters | Cleared on new topic |
| **Conversational** | Current exchange | ContextEntities | Last shown recipes, last query, referenced recipe | Per-query context |

#### Session State Extensions

**File:** `apps/fastapi/src/services/session_memory_manager.py`

```python
@dataclass
class ContextEntities:
    """Extended context entities"""
    # ... existing fields ...

    # NEW FIELDS for filter control:
    last_user_query: Optional[str] = None  # The actual user message (not vector query)
    last_search_intent: Optional[str] = None  # recipe_search, nutritional_info, etc.


@dataclass
class SessionState:
    """Extended session state"""
    # ... existing fields ...

    # NEW FIELD for filter control:
    filters: SessionFilters = field(default_factory=SessionFilters)


@dataclass
class SessionFilters:
    """Structured filter storage for easy management"""
    # Ingredient filters
    excluded_ingredients: List[str] = field(default_factory=list)
    included_ingredients: List[str] = field(default_factory=list)

    # Recipe filters
    tags: List[str] = field(default_factory=list)
    cuisines: List[str] = field(default_factory=list)
    difficulty: Optional[str] = None
    max_time: Optional[int] = None
    meal_type: Optional[str] = None

    # Nutrition filters
    max_calories: Optional[int] = None
    min_protein: Optional[float] = None
    max_carbs: Optional[float] = None

    # Price filters
    max_price: Optional[float] = None
    price_country: Optional[str] = None

    # Exclusion from negative feedback
    excluded_recipe_ids: List[str] = field(default_factory=list)

    # Creator filter
    creator_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "excluded_ingredients": self.excluded_ingredients,
            "included_ingredients": self.included_ingredients,
            "tags": self.tags,
            "cuisines": self.cuisines,
            "difficulty": self.difficulty,
            "max_time": self.max_time,
            "meal_type": self.meal_type,
            "max_calories": self.max_calories,
            "min_protein": self.min_protein,
            "max_carbs": self.max_carbs,
            "max_price": self.max_price,
            "price_country": self.price_country,
            "excluded_recipe_ids": self.excluded_recipe_ids,
            "creator_name": self.creator_name,
        }

    def clear_all(self) -> None:
        """Reset all filters to default"""
        self.excluded_ingredients = []
        self.included_ingredients = []
        self.tags = []
        self.cuisines = []
        self.difficulty = None
        self.max_time = None
        self.meal_type = None
        self.max_calories = None
        self.min_protein = None
        self.max_carbs = None
        self.max_price = None
        self.price_country = None
        self.excluded_recipe_ids = []
        self.creator_name = None

    def clear_filter(self, filter_name: str) -> bool:
        """Clear a specific filter by name"""
        if hasattr(self, filter_name):
            attr = getattr(self, filter_name)
            if isinstance(attr, list):
                setattr(self, filter_name, [])
            else:
                setattr(self, filter_name, None)
            return True
        return False
```

#### New API Endpoints

**File:** `apps/fastapi/src/routes/chat.py` (NEW ENDPOINTS)

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional, List

chat_route = APIRouter()


@chat_route.get("/session/{session_id}/filters")
async def get_active_filters(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get all active filters for a session.

    Returns:
        - List of all active filters
        - Last user query (for re-search)
        - Filter summary for UI display
    """
    session = chat_service.session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    filters = session.filters.to_dict()

    # Build human-readable summary
    active_count = sum(1 for v in filters.values() if v)
    filter_summary = _build_filter_summary(filters)

    return {
        "session_id": session_id,
        "active_filters": filters,
        "active_count": active_count,
        "filter_summary": filter_summary,
        "last_user_query": session.context_entities.last_user_query
    }


@chat_route.post("/session/{session_id}/clear-filters")
async def clear_all_filters(
    session_id: str,
    re_search: bool = True,  # Auto re-search with last query
    db: Session = Depends(get_db)
):
    """
    Clear all filters and optionally re-search with the last user query.

    Args:
        session_id: Session identifier
        re_search: If True, automatically re-runs the last user query

    Returns:
        - Confirmation of cleared filters
        - New search results (if re_search=True and last query exists)
    """
    session = chat_service.session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Store last query before clearing
    last_query = session.context_entities.last_user_query

    # Clear all filters
    session.filters.clear_all()

    # Reset excluded recipe IDs (negative feedback)
    session.excluded_recipe_ids = []

    logger.info(f"[CLEAR_FILTERS] Session {session_id}: All filters cleared")

    response = {
        "session_id": session_id,
        "message": "All filters have been cleared",
        "cleared": True
    }

    # Auto re-search if requested and query exists
    if re_search and last_query:
        logger.info(f"[CLEAR_FILTERS] Re-searching with last query: {last_query}")

        # Re-run the pipeline with the last query
        search_result = await chat_service.pipeline.process_query(
            query=last_query,
            session_id=session_id,
            user_uid=session.user_uid,
            language=session.language
        )

        response["re_search"] = True
        response["last_query"] = last_query
        response["search_result"] = search_result
    else:
        response["re_search"] = False
        if not last_query:
            response["message"] += " (no previous query to re-search)"

    return response


@chat_route.delete("/session/{session_id}/filters/{filter_name}")
async def clear_specific_filter(
    session_id: str,
    filter_name: str,
    re_search: bool = False,
    db: Session = Depends(get_db)
):
    """
    Clear a specific filter by name.

    Args:
        session_id: Session identifier
        filter_name: Name of filter to clear (e.g., "excluded_ingredients", "tags")
        re_search: If True, re-run search after clearing

    Returns:
        - Confirmation of cleared filter
        - Updated filter state
    """
    session = chat_service.session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate filter name
    valid_filters = [
        "excluded_ingredients", "included_ingredients", "tags", "cuisines",
        "difficulty", "max_time", "meal_type", "max_calories", "min_protein",
        "max_carbs", "max_price", "price_country", "excluded_recipe_ids",
        "creator_name"
    ]

    if filter_name not in valid_filters:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filter name. Valid filters: {valid_filters}"
        )

    # Clear the specific filter
    cleared = session.filters.clear_filter(filter_name)

    logger.info(f"[CLEAR_FILTER] Session {session_id}: Cleared filter '{filter_name}'")

    response = {
        "session_id": session_id,
        "cleared_filter": filter_name,
        "success": cleared,
        "remaining_filters": session.filters.to_dict()
    }

    # Optional re-search
    if re_search and session.context_entities.last_user_query:
        search_result = await chat_service.pipeline.process_query(
            query=session.context_entities.last_user_query,
            session_id=session_id,
            user_uid=session.user_uid,
            language=session.language
        )
        response["search_result"] = search_result

    return response


@chat_route.post("/session/{session_id}/remove-ingredient-exclusion")
async def remove_ingredient_exclusion(
    session_id: str,
    ingredient: str,
    re_search: bool = False,
    db: Session = Depends(get_db)
):
    """
    Remove a specific ingredient from the exclusion list.

    Use case: User said "I'm allergic to nuts" but wants to allow one type.

    Args:
        session_id: Session identifier
        ingredient: Ingredient name to remove from exclusions
        re_search: If True, re-run search after removal
    """
    session = chat_service.session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    ingredient_lower = ingredient.lower()

    if ingredient_lower in session.filters.excluded_ingredients:
        session.filters.excluded_ingredients.remove(ingredient_lower)
        logger.info(f"[REMOVE_EXCLUSION] Session {session_id}: Removed '{ingredient}' from exclusions")

        response = {
            "session_id": session_id,
            "removed": ingredient,
            "remaining_exclusions": session.filters.excluded_ingredients
        }
    else:
        response = {
            "session_id": session_id,
            "removed": None,
            "message": f"'{ingredient}' was not in the exclusion list",
            "current_exclusions": session.filters.excluded_ingredients
        }

    if re_search and session.context_entities.last_user_query:
        search_result = await chat_service.pipeline.process_query(
            query=session.context_entities.last_user_query,
            session_id=session_id,
            user_uid=session.user_uid,
            language=session.language
        )
        response["search_result"] = search_result

    return response


def _build_filter_summary(filters: Dict[str, Any]) -> List[str]:
    """Build human-readable filter summary for UI display"""
    summary = []

    if filters.get("excluded_ingredients"):
        summary.append(f"Excluding: {', '.join(filters['excluded_ingredients'])}")

    if filters.get("included_ingredients"):
        summary.append(f"Including: {', '.join(filters['included_ingredients'])}")

    if filters.get("tags"):
        summary.append(f"Tags: {', '.join(filters['tags'])}")

    if filters.get("cuisines"):
        summary.append(f"Cuisines: {', '.join(filters['cuisines'])}")

    if filters.get("difficulty"):
        summary.append(f"Difficulty: {filters['difficulty']}")

    if filters.get("max_time"):
        summary.append(f"Under {filters['max_time']} min")

    if filters.get("max_price"):
        summary.append(f"Under ${filters['max_price']}")

    if filters.get("max_calories"):
        summary.append(f"Under {filters['max_calories']} calories")

    if filters.get("excluded_recipe_ids"):
        summary.append(f"{len(filters['excluded_recipe_ids'])} recipes excluded")

    return summary
```

#### Pipeline Integration

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

```python
# In process_query method, after receiving user message:

async def process_query(
    self,
    query: str,
    session_id: str,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    # ... existing code ...

    # STORE the original user query for re-search capability
    session.context_entities.last_user_query = query

    # ... rest of pipeline ...

    # After getting results, store the intent for context
    session.context_entities.last_search_intent = nlid_result_dict.get("intent")

    # ... continue with existing logic ...
```

#### UI Button Implementation (Frontend)

```typescript
// Example React component for filter management

interface FilterState {
  excluded_ingredients: string[];
  tags: string[];
  cuisines: string[];
  difficulty: string | null;
  max_time: number | null;
  // ... other filters
}

const FilterManager: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const [filters, setFilters] = useState<FilterState | null>(null);
  const [filterSummary, setFilterSummary] = useState<string[]>([]);

  // Fetch active filters
  const fetchFilters = async () => {
    const response = await fetch(`/api/session/${sessionId}/filters`);
    const data = await response.json();
    setFilters(data.active_filters);
    setFilterSummary(data.filter_summary);
  };

  // Clear all filters and re-search
  const clearAllFilters = async () => {
    const response = await fetch(
      `/api/session/${sessionId}/clear-filters?re_search=true`,
      { method: 'POST' }
    );
    const data = await response.json();

    if (data.search_result) {
      // Update UI with new search results
      updateSearchResults(data.search_result);
    }

    setFilters(null);
    setFilterSummary([]);
  };

  // Clear specific filter
  const clearFilter = async (filterName: string) => {
    await fetch(
      `/api/session/${sessionId}/filters/${filterName}?re_search=true`,
      { method: 'DELETE' }
    );
    fetchFilters();
  };

  return (
    <div className="filter-manager">
      {filterSummary.length > 0 && (
        <>
          <div className="active-filters">
            <h4>Active Filters</h4>
            <ul>
              {filterSummary.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>

          <button
            className="clear-filters-btn"
            onClick={clearAllFilters}
          >
            Clear All Filters
          </button>
        </>
      )}
    </div>
  );
};
```

#### Query-Based Clear Filters

Users can also clear filters via natural language:

**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

```python
# Add new intent:

14. **clear_filters**: User wants to reset filters
    - Triggers: "clear filters", "reset", "start over", "forget my preferences"
    - Examples: "Clear all my filters", "Reset search", "Start fresh"
    - Entities:
      * scope: "all" | "ingredients" | "tags" | "specific"
      * specific_filter: Optional filter name to clear
    - requires_embedding: false
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

```python
async def _handle_clear_filters(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle filter clearing requests.
    Examples: "Clear all filters", "Reset my exclusions"
    """
    scope = nlid_result.get("entities", {}).get("scope", "all")
    specific_filter = nlid_result.get("entities", {}).get("specific_filter")

    if scope == "all":
        # Clear everything
        session.filters.clear_all()
        message = "All filters have been cleared."

    elif scope == "specific" and specific_filter:
        # Clear specific filter
        session.filters.clear_filter(specific_filter)
        message = f"{specific_filter} filter has been cleared."

    elif scope == "ingredients":
        # Clear only ingredient-related filters
        session.filters.excluded_ingredients = []
        session.filters.included_ingredients = []
        message = "Ingredient filters have been cleared."

    else:
        # Default: clear all
        session.filters.clear_all()
        message = "All filters have been cleared."

    # Check if user wants to re-search
    last_query = session.context_entities.last_user_query
    if last_query and any(word in query.lower() for word in ["again", "new", "refresh", "re-search"]):
        search_result = await self.process_query(
            query=last_query,
            session_id=session.session_id,
            user_uid=user_uid,
            language=language
        )
        return search_result

    return {
        "response": message,
        "metadata": {
            "intent": "clear_filters",
            "scope": scope,
            "active_filters": session.filters.to_dict()
        }
    }
```

### Test Cases for Filter Management

| # | Action | Expected Result |
|---|--------|-----------------|
| 1 | GET `/session/{id}/filters` | Returns all active filters with summary |
| 2 | POST `/session/{id}/clear-filters` | Clears all, returns confirmation |
| 3 | POST `/session/{id}/clear-filters?re_search=true` | Clears all, re-searches with last query |
| 4 | DELETE `/session/{id}/filters/excluded_ingredients` | Clears only ingredient exclusions |
| 5 | "Clear all filters" (chat) | All filters reset, confirmation message |
| 6 | "Clear filters and search again" | Filters cleared + new results with last query |
| 7 | "Reset my ingredient exclusions" | Only ingredient filters cleared |
| 8 | Session with no filters → clear | Graceful message, no error |
| 9 | Invalid filter name → clear specific | 400 error with valid filter names |
| 10 | Clear filters → new search → filter persists? | No, filters stay cleared |

### Context Persistence Strategy

```python
# When to preserve vs clear filters:

PRESERVE_FILTERS_ON_INTENT = [
    "recipe_search",           # Continuing search
    "refine_search",           # Refining results
    "filter_addition",         # Adding filters
    "recipe_reference",        # Asking about shown recipes
    "negative_feedback",       # Excluding shown recipes
]

CLEAR_FILTERS_ON_INTENT = [
    "clear_filters",           # Explicit clear
    "new_topic",               # Completely different topic
    # Note: Most intents should preserve filters
]

# Example implementation:
def should_preserve_filters(intent: str, query: str) -> bool:
    """Determine if filters should be preserved for this query"""
    if intent in CLEAR_FILTERS_ON_INTENT:
        return False

    # Check for topic shift (optional advanced logic)
    # This could use embedding similarity to detect if query is completely unrelated

    return True  # Default: preserve filters
```

---

### SPEC 8: Auto-Clear Filters on No Results

**Problem:** When no results are found with active filters, users get a generic error. They have to manually clear filters or guess which filter is causing issues.

**Solution:** Automatically clear all filters and retry the query as a fresh search when no results are found.

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

```python
async def _handle_no_results_with_auto_clear(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle no-results scenario with automatic filter clearing.

    Flow:
    1. First attempt: Search with current filters
    2. If no results AND filters exist: Clear filters and retry
    3. If still no results: Return helpful message
    """
    # Check if there are active filters
    active_filters = session.filters.to_dict()
    has_active_filters = any(v for v in active_filters.values() if v)

    if has_active_filters:
        # Store current filter state for user feedback
        filter_summary = _build_filter_summary(active_filters)

        logger.info(f"[NO_RESULTS] Clearing filters and retrying: {filter_summary}")

        # Clear all filters
        session.filters.clear_all()
        session.excluded_recipe_ids = []

        # Retry the search without filters
        retry_result = await self._execute_recipe_search(
            query=query,
            nlid_result=nlid_result,
            session=session,
            user_uid=user_uid,
            language=language
        )

        retry_recipes = retry_result.get("recipes", [])

        if retry_recipes:
            # Success after clearing filters!
            response = f"I couldn't find any recipes with your previous filters ({', '.join(filter_summary)}). "
            response += f"I've cleared those filters and found {len(retry_recipes)} recipes for you:"

            return {
                "response": response,
                "recipes": retry_recipes,
                "metadata": {
                    "intent": nlid_result.get("intent"),
                    "filters_cleared": True,
                    "cleared_filters": filter_summary,
                    "retry_count": 1
                }
            }
        else:
            # Still no results even without filters
            return {
                "response": f"I couldn't find any recipes matching '{query}'. Try different keywords or be more specific.",
                "recipes": [],
                "metadata": {
                    "intent": nlid_result.get("intent"),
                    "filters_cleared": True,
                    "retry_count": 1
                }
            }
    else:
        # No filters were active, just no results
        return {
            "response": f"No recipes found for '{query}'. Try different keywords.",
            "recipes": [],
            "metadata": {
                "intent": nlid_result.get("intent"),
                "filters_cleared": False
            }
        }


# Integration in main pipeline:
async def process_query(self, query: str, session_id: str, ...):
    # ... existing pipeline logic ...

    # After executing search
    recipes = await self._execute_search(...)

    if not recipes or len(recipes) == 0:
        # Auto-clear filters and retry
        return await self._handle_no_results_with_auto_clear(
            query, nlid_result, session, user_uid, language
        )

    # ... continue with normal flow ...
```

**User Experience:**

| Scenario | Behavior |
|----------|----------|
| No results + active filters | Clear filters, retry, show results with explanation |
| No results + no filters | Show helpful "try different keywords" message |
| Results found after clearing | "I couldn't find recipes with your filters. I've cleared them and found X recipes" |
| Still no results after clearing | "I couldn't find any recipes matching 'query'. Try different keywords." |

---

### SPEC 9: Combined Meal Search (Multi-Course Pairing)

**Problem:** Users want meal combinations like "main course and dessert under $1000" but can only search for one meal type at a time.

**Solution:** New intent `combined_meal_search` that finds complementary meal pairs within budget constraints.

**File:** `apps/fastapi/src/agents/sdk_nlid_agent.py`

```python
15. **combined_meal_search**: User wants multiple meal types with combined constraints
    - Triggers: "X and Y", "pair of", "combination", "course + course"
    - Examples: "Main course and dessert under $1000", "Appetizer and main course under 30 min"
    - Entities:
      * meal_types: List of meal types ["main course", "dessert"]
      * combined_constraints: {max_price: 1000, max_time: 60, max_calories: 800}
      * pair_count: Number of pairs to suggest (default: 3)
    - requires_embedding: true
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

```python
async def _handle_combined_meal_search(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle multi-course meal combination queries.
    Example: "Main course and dessert under $1000"
    """
    meal_types = nlid_result.get("entities", {}).get("meal_types", [])
    constraints = nlid_result.get("entities", {}).get("combined_constraints", {})
    pair_count = nlid_result.get("entities", {}).get("pair_count", 3)

    if len(meal_types) < 2:
        return {
            "response": "Please specify at least two meal types to combine. For example: 'main course and dessert'",
            "metadata": {"error": "insufficient_meal_types"}
        }

    # Map meal types to database tags/categories
    meal_type_mapping = {
        "appetizer": ["appetizer", "starter", "snack"],
        "main course": ["main course", "main", "dinner", "lunch"],
        "dessert": ["dessert", "sweet", "pudding"],
        "breakfast": ["breakfast", "brunch"],
        "snack": ["snack", "appetizer"],
        "soup": ["soup", "starter"],
        "salad": ["salad", "starter", "side"]
    }

    # Search for each meal type independently
    meal_results = {}
    for meal_type in meal_types:
        tags = meal_type_mapping.get(meal_type.lower(), [meal_type.lower()])

        # Build search for this meal type
        search_result = await self._search_recipes_by_tags(
            tags=tags,
            constraints=constraints,  # Individual constraints (not combined)
            user_uid=user_uid,
            language=language,
            limit=20  # Get more candidates for better pairing
        )
        meal_results[meal_type] = search_result

    # Generate combinations
    pairs = self._generate_meal_pairs(
        meal_results=meal_results,
        meal_types=meal_types,
        constraints=constraints,
        max_pairs=pair_count
    )

    # Generate response
    if pairs:
        response = await self._generate_combined_meal_response(pairs, constraints)
    else:
        response = f"I couldn't find any {meal_types[0]} + {meal_types[1]} combinations "
        if constraints.get("max_price"):
            response += f"under ${constraints['max_price']}. "
        response += "Try adjusting your budget or constraints."

    return {
        "response": response,
        "pairs": pairs,
        "metadata": {
            "intent": "combined_meal_search",
            "meal_types": meal_types,
            "constraints": constraints
        }
    }


def _generate_meal_pairs(
    self,
    meal_results: Dict[str, List[Dict]],
    meal_types: List[str],
    constraints: Dict[str, Any],
    max_pairs: int = 3
) -> List[Dict[str, Any]]:
    """
    Generate meal pairings that satisfy combined constraints.

    Strategy:
    1. Sort each meal type by price (if budget constraint)
    2. Use greedy pairing to find combinations within budget
    3. Prioritize variety and balance
    """
    pairs = []

    # Extract constraints
    max_price = constraints.get("max_price")
    max_time = constraints.get("max_time")
    max_calories = constraints.get("max_calories")

    # Get recipes for each meal type
    first_meal_recipes = meal_results.get(meal_types[0], [])
    second_meal_recipes = meal_results.get(meal_types[1], [])

    if not first_meal_recipes or not second_meal_recipes:
        return []

    # Sort by price if budget constraint
    if max_price:
        first_meal_recipes = sorted(
            first_meal_recipes,
            key=lambda r: r.get("cost", {}).get("usa", {}).get("total", 999)
        )
        second_meal_recipes = sorted(
            second_meal_recipes,
            key=lambda r: r.get("cost", {}).get("usa", {}).get("total", 999)
        )

    # Find valid pairs
    for recipe1 in first_meal_recipes:
        for recipe2 in second_meal_recipes:
            # Check combined constraints
            if max_price:
                price1 = recipe1.get("cost", {}).get("usa", {}).get("total", 0)
                price2 = recipe2.get("cost", {}).get("usa", {}).get("total", 0)
                if price1 + price2 > max_price:
                    continue

            if max_time:
                time1 = recipe1.get("total_time", 0) or 0
                time2 = recipe2.get("total_time", 0) or 0
                if time1 + time2 > max_time:
                    continue

            if max_calories:
                cal1 = recipe1.get("nutrition", {}).get("energyKcal", 0) or 0
                cal2 = recipe2.get("nutrition", {}).get("energyKcal", 0) or 0
                if cal1 + cal2 > max_calories:
                    continue

            # Valid pair found!
            pairs.append({
                "meal_1": {
                    "type": meal_types[0],
                    "recipe": recipe1
                },
                "meal_2": {
                    "type": meal_types[1],
                    "recipe": recipe2
                },
                "combined": {
                    "total_price": price1 + price2 if max_price else None,
                    "total_time": time1 + time2 if max_time else None,
                    "total_calories": cal1 + cal2 if max_calories else None
                }
            })

            if len(pairs) >= max_pairs:
                return pairs

    return pairs
```

**File:** `apps/fastapi/src/agents/sdk_nlg_agent.py`

```python
async def generate_combined_meal_response(
    pairs: List[Dict[str, Any]],
    constraints: Dict[str, Any]
) -> str:
    """
    Generate response for combined meal search.
    Format: 3 pair suggestions with details
    """
    if not pairs:
        return "No suitable meal combinations found."

    lines = [f"Here are {len(pairs)} meal combinations for you:\n"]

    for i, pair in enumerate(pairs, 1):
        meal1 = pair["meal_1"]
        meal2 = pair["meal_2"]
        combined = pair["combined"]

        lines.append(f"**Option {i}:**")
        lines.append(f"  🍽️ {meal1['type'].title()}: {meal1['recipe'].get('name', 'Unknown')}")
        lines.append(f"  🍰 {meal2['type'].title()}: {meal2['recipe'].get('name', 'Unknown')}")

        # Show combined stats
        stats = []
        if combined.get("total_price"):
            stats.append(f"${combined['total_price']:.2f} total")
        if combined.get("total_time"):
            stats.append(f"{combined['total_time']} min")
        if combined.get("total_calories"):
            stats.append(f"{combined['total_calories']} cal")

        if stats:
            lines.append(f"  📊 {', '.join(stats)}")
        lines.append("")  # Blank line between pairs

    return "\n".join(lines)
```

**Example Response:**

```
Here are 3 meal combinations for you:

**Option 1:**
  🍽️ Main Course: Grilled Salmon with Vegetables
  🍰 Dessert: Chocolate Mousse
  📊 $45.50 total, 55 min

**Option 2:**
  🍽️ Main Course: Chicken Stir Fry
  🍰 Dessert: Apple Crumble
  📊 $38.00 total, 40 min

**Option 3:**
  🍽️ Main Course: Pasta Primavera
  🍰 Dessert: Tiramisu
  📊 $32.00 total, 45 min
```

---

### SPEC 10: Enhanced Creator Filter with User Validation

**Problem:** Creator filter returns empty results if user not found, without helpful feedback.

**Solution:** Validate user existence first, handle multiple users with same name, provide friendly error messages.

**File:** `apps/fastapi/src/utils/sql_builders.py`

```python
def build_creator_filter_condition(creator_name: str) -> str:
    """
    Build SQL condition for filtering by recipe creator.
    Usage: "Recipes by John"
    """
    safe_name = creator_name.replace("'", "''")
    return f"""
        EXISTS (
            SELECT 1 FROM "user" u
            WHERE u."uid" = r."userUid"
            AND (
                LOWER(u."username") LIKE LOWER('%{safe_name}%')
                OR LOWER(u."displayName") LIKE LOWER('%{safe_name}%')
            )
        )
    """


def find_users_by_name(db, name: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Find users matching a name (for validation and disambiguation).

    Returns list of users with their basic info.
    """
    safe_name = name.replace("'", "''")

    sql = f"""
        SELECT
            u."uid",
            u."username",
            u."displayName",
            u."email",
            (SELECT COUNT(*) FROM recipe r
             WHERE r."userUid" = u."uid"
             AND r."status" = 'published'
             AND r."deletedAt" IS NULL) as recipe_count
        FROM "user" u
        WHERE
            LOWER(u."username") LIKE LOWER('%{safe_name}%')
            OR LOWER(u."displayName") LIKE LOWER('%{safe_name}%')
        ORDER BY recipe_count DESC
        LIMIT {limit}
    """

    result = db.execute(text(sql)).fetchall()

    return [
        {
            "uid": row[0],
            "username": row[1],
            "display_name": row[2],
            "email": row[3],
            "recipe_count": row[4]
        }
        for row in result
    ]


def get_user_exact_match(db, name: str) -> Optional[Dict[str, Any]]:
    """
    Try to find exact match for user name.
    Returns first match if multiple users have same name.
    """
    safe_name = name.replace("'", "''")

    sql = f"""
        SELECT
            u."uid",
            u."username",
            u."displayName",
            (SELECT COUNT(*) FROM recipe r
             WHERE r."userUid" = u."uid"
             AND r."status" = 'published'
             AND r."deletedAt" IS NULL) as recipe_count
        FROM "user" u
        WHERE
            LOWER(u."username") = LOWER('{safe_name}')
            OR LOWER(u."displayName") = LOWER('{safe_name}')
        ORDER BY recipe_count DESC
        LIMIT 1
    """

    result = db.execute(text(sql)).fetchone()

    if result:
        return {
            "uid": result[0],
            "username": result[1],
            "display_name": result[2],
            "recipe_count": result[3]
        }
    return None
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

```python
async def _handle_creator_filter_query(
    self,
    query: str,
    nlid_result: Dict[str, Any],
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """
    Handle queries filtering by recipe creator.
    Example: "Recipes by John", "Show me user123's recipes"

    Flow:
    1. Validate user exists
    2. If exact match: Search recipes
    3. If no match: Ask for confirmation
    4. If multiple matches: Use first one (with most recipes)
    """
    from apps.fastapi.src.utils.sql_builders import (
        find_users_by_name,
        get_user_exact_match
    )

    creator_name = nlid_result.get("entities", {}).get("creator_name")

    if not creator_name:
        return {
            "response": "Which user's recipes would you like to see? Please specify a username.",
            "metadata": {"error": "no_creator_specified"}
        }

    # Step 1: Try exact match first
    exact_user = get_user_exact_match(self.db, creator_name)

    if exact_user:
        # Found exact match - search recipes
        return await self._search_recipes_by_creator(
            creator_uid=exact_user["uid"],
            creator_name=exact_user.get("display_name") or exact_user["username"],
            session=session,
            user_uid=user_uid,
            language=language
        )

    # Step 2: No exact match - try fuzzy search
    matching_users = find_users_by_name(self.db, creator_name)

    if not matching_users:
        # No users found with that name
        return {
            "response": f"I was not able to find a user with the name '{creator_name}'. "
                        f"Kindly confirm the name and try again.",
            "metadata": {
                "error": "user_not_found",
                "searched_name": creator_name
            }
        }

    # Step 3: Multiple users found - use first one (sorted by recipe count)
    first_user = matching_users[0]

    # Check if there are other users with similar names
    if len(matching_users) > 1:
        other_names = [u["username"] for u in matching_users[1:3]]
        clarification = f" (showing recipes by '{first_user['username']}'. "
        clarification += f"Other users found: {', '.join(other_names)})"
    else:
        clarification = ""

    # Search using first user
    result = await self._search_recipes_by_creator(
        creator_uid=first_user["uid"],
        creator_name=first_user.get("display_name") or first_user["username"],
        session=session,
        user_uid=user_uid,
        language=language
    )

    # Add clarification to response if multiple users
    if clarification and result.get("recipes"):
        result["response"] = result["response"].replace(
            f"by {first_user['username']}",
            f"by {first_user['username']}{clarification}"
        )

    return result


async def _search_recipes_by_creator(
    self,
    creator_uid: str,
    creator_name: str,
    session: SessionState,
    user_uid: Optional[str],
    language: str
) -> Dict[str, Any]:
    """Search for recipes by a specific creator."""

    sql = f"""
        SELECT r."id", r."name", r."ingress", r."image",
               (r."prepTime" + r."cookTime") as total_time,
               r."difficulty", r."servings"
        FROM recipe r
        WHERE r."deletedAt" IS NULL
          AND r."status" = 'published'
          AND r."languageId" = '{language}'
          AND r."userUid" = '{creator_uid}'
        ORDER BY r."createdAt" DESC
        LIMIT 20
    """

    result = self.db.execute(text(sql)).fetchall()
    recipes = [self._row_to_dict(row) for row in result]

    if not recipes:
        return {
            "response": f"'{creator_name}' hasn't published any recipes yet.",
            "recipes": [],
            "metadata": {
                "creator_uid": creator_uid,
                "creator_name": creator_name
            }
        }

    response = f"Here are {len(recipes)} recipes by {creator_name}:"

    return {
        "response": response,
        "recipes": recipes,
        "metadata": {
            "creator_uid": creator_uid,
            "creator_name": creator_name
        }
    }
```

**User Experience:**

| Scenario | Response |
|----------|----------|
| Exact user found | "Here are 5 recipes by John" |
| No user found | "I was not able to find a user with the name 'Jan'. Kindly confirm the name." |
| Multiple users with same name | "Here are 3 recipes by John (showing recipes by 'john_doe'. Other users found: john_smith, john_chef)" |
| User has no recipes | "'John' hasn't published any recipes yet." |

---

## Files to Modify Summary

| File | Changes | Lines (Est.) |
|------|---------|-------------|
| `apps/fastapi/src/agents/sdk_nlid_agent.py` | Add 9 new intents to instructions (including clear_filters, combined_meal_search) | ~150 |
| `apps/fastapi/src/services/session_memory_manager.py` | Add ContextEntities fields, SessionFilters dataclass, new methods | ~120 |
| `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` | Add 9 handler methods, auto-clear on no results, combined meals, creator validation, category expansion | ~550 |
| `apps/fastapi/src/agents/sdk_nlg_agent.py` | Add 5 new response generators (including combined_meal_response) | ~150 |
| `apps/fastapi/src/utils/sql_builders.py` | Add 7 new functions (creator validation, pair generation), modify 1 existing | ~120 |
| `apps/fastapi/src/agents/agent_tools.py` | Add scale_recipe_ingredients function | ~30 |
| `apps/fastapi/src/utils/ingredient_category_expander.py` | **NEW FILE** - Dynamic category expansion | ~150 |
| `apps/fastapi/src/routes/chat.py` | Add filter management endpoints (GET, POST, DELETE) | ~200 |
| `docs/CHATBOT_PIPELINE.md` | Update with new features | ~100 |

**Total Estimated Changes: ~1570 lines**

---

## Verification & Testing

### Unit Tests Required

| Test File | Tests |
|-----------|-------|
| `test_nlid_agent.py` | New intent detection, entity extraction |
| `test_session_manager.py` | Recipe result storage, exclusion list |
| `test_sql_builders.py` | New filter conditions, price sort order |
| `test_pipeline.py` | Handler methods for new intents |

### Integration Test Scenarios

```
1. Recipe Reference Flow
   - POST /chat/message {"message": "Show me pasta recipes"}
   - POST /chat/message {"message": "Explain the 1st recipe"}
   - Verify: Full details of first recipe returned

2. Negative Feedback Flow
   - POST /chat/message {"message": "Show me chicken recipes"}
   - POST /chat/message {"message": "I don't like these"}
   - Verify: New results exclude previous recipe IDs

3. Educational Query Flow
   - POST /chat/message {"message": "What is vegan food?"}
   - Verify: Description + vegan recipes returned

4. Ingredients On Hand Flow
   - POST /chat/message {"message": "I only have eggs and potatoes"}
   - Verify: Recipes using both ingredients

5. Filter Context on No Results
   - POST /chat/message {"message": "Vegan Italian recipes under 20 minutes"}
   - If no results: Verify filter names in error message

6. Category Expansion - Allergies
   - POST /chat/message {"message": "I'm allergic to nuts"}
   - Verify: Results exclude walnut, almond, cashew, pecan, hazelnut, etc.
   - POST /chat/message {"message": "Show me a cake recipe"}
   - Verify: No recipes containing any nut ingredients

7. Category Expansion - Dietary
   - POST /chat/message {"message": "No dairy for me"}
   - Verify: Results exclude milk, cheese, butter, cream, yogurt, etc.

8. Category Expansion - Complex Query
   - POST /chat/message {"message": "Show me pasta recipes, I'm allergic to nuts and shellfish"}
   - Verify: Results exclude all nuts AND all shellfish

9. Category Expansion - Follow-up
   - POST /chat/message {"message": "Show me dessert recipes"}
   - POST /chat/message {"message": "I can't have gluten"}
   - Verify: Results exclude wheat, barley, rye, etc.
   - Verify: Session persists gluten exclusion

10. Clear Filters - API Endpoint
    - POST /chat/message {"message": "Vegan Italian recipes under 30 min"}
    - GET /session/{id}/filters
    - Verify: Returns active filters (vegan, Italian, max_time=30)
    - POST /session/{id}/clear-filters?re_search=true
    - Verify: Filters cleared, re-searched with last query
    - GET /session/{id}/filters
    - Verify: No active filters

11. Clear Filters - Chat Query
    - POST /chat/message {"message": "Show me chicken recipes without onion"}
    - POST /chat/message {"message": "Clear all my filters"}
    - Verify: Confirmation message, filters cleared

12. Clear Filters and Re-search
    - POST /chat/message {"message": "Easy pasta recipes"}
    - POST /chat/message {"message": "Make it vegetarian"}
    - POST /chat/message {"message": "Clear filters and search again"}
    - Verify: Filters cleared, new pasta results (not filtered by vegetarian)

13. Clear Specific Filter
    - POST /chat/message {"message": "Vegan Italian recipes under 30 min"}
    - DELETE /session/{id}/filters/cuisines?re_search=true
    - Verify: Italian filter removed, vegan + time still active
    - Verify: New results (vegan + under 30 min, any cuisine)

14. Remove Ingredient Exclusion
    - POST /chat/message {"message": "I'm allergic to nuts and dairy"}
    - POST /session/{id}/remove-ingredient-exclusion {"ingredient": "dairy"}
    - Verify: Only nuts in exclusion list
    - POST /chat/message {"message": "Show me recipes"}
    - Verify: Results may contain dairy but not nuts

15. Filter Persistence Across Queries
    - POST /chat/message {"message": "Show me vegan recipes"}
    - POST /chat/message {"message": "Make it Italian"}
    - POST /chat/message {"message": "Under 30 minutes"}
    - GET /session/{id}/filters
    - Verify: All three filters active (vegan, Italian, max_time=30)

16. Topic Shift Handling
    - POST /chat/message {"message": "Show me vegan Italian recipes"}
    - POST /chat/message {"message": "What is keto diet?"}
    - Verify: Educational response returned
    - GET /session/{id}/filters
    - Verify: Vegan + Italian filters still active (preserved)

17. Auto-Clear Filters on No Results
    - POST /chat/message {"message": "Show me vegan recipes"}
    - POST /chat/message {"message": "Make it Italian"}
    - POST /chat/message {"message": "Under 5 minutes"}
    - Verify: No results → filters auto-cleared → retry search
    - Verify: Response mentions "I couldn't find recipes with your filters..."
    - GET /session/{id}/filters
    - Verify: All filters cleared

18. Auto-Clear - Filters vs No Filters
    - POST /chat/message {"message": "xyznonexistent123"}
    - Verify: No filters active → "Try different keywords" message (no auto-clear)

19. Combined Meal Search - Budget
    - POST /chat/message {"message": "Suggest main course and dessert under $100"}
    - Verify: Returns 3 pairs of main course + dessert
    - Verify: Each pair's combined price <= $100
    - Verify: Response format shows meal types, names, and combined stats

20. Combined Meal Search - Time Constraint
    - POST /chat/message {"message": "Appetizer and main course under 60 minutes total"}
    - Verify: Returns pairs where total_time <= 60 min
    - Verify: Response shows combined time for each pair

21. Combined Meal Search - No Valid Pairs
    - POST /chat/message {"message": "Main course and dessert under $1"}
    - Verify: "I couldn't find any combinations" message
    - Verify: Suggests adjusting constraints

22. Creator Filter - Exact Match
    - POST /chat/message {"message": "Recipes by john_doe"}
    - Verify: Returns recipes by user with exact username "john_doe"

23. Creator Filter - User Not Found
    - POST /chat/message {"message": "Recipes by nonexistentuser123"}
    - Verify: "I was not able to find a user with the name 'nonexistentuser123'. Kindly confirm the name."

24. Creator Filter - Multiple Users Same Name
    - POST /chat/message {"message": "Recipes by John"}
    - Verify: Uses first user (sorted by recipe count)
    - Verify: Response mentions other users found if applicable

25. Creator Filter - No Recipes
    - POST /chat/message {"message": "Recipes by new_user_with_no_recipes"}
    - Verify: "'username' hasn't published any recipes yet."
```

### Manual Testing Checklist

```
□ Price sorting DESC works for "under $X" queries
□ Nutrition/cost/seasonality appear in all recipe responses
□ "1st", "2nd", "3rd" references work correctly
□ "First", "second", "last" position words recognized
□ Negative feedback excludes all shown recipes
□ Educational queries return description + recipes
□ Festival queries return seasonal recipes
□ Creator filter matches username
□ Ingredient count filter works
□ Serving scaling calculates correctly
□ No-results message shows filter names
□ Session persists across multiple queries
□ Excluded recipes persist across session
□ "Allergic to nuts" excludes all nut types
□ "No dairy" excludes all dairy products
□ Category expansion works for unknown categories
□ Category expansion caching reduces LLM calls
□ GET /filters returns active filters with summary
□ POST /clear-filters clears all filters
□ POST /clear-filters?re_search=true re-runs last query
□ DELETE /filters/{name} clears specific filter
□ "Clear all filters" (chat) works correctly
□ "Clear filters and search again" re-searches
□ Filter removal updates search results
□ Filters persist across related queries
□ Empty filter state handled gracefully
□ Auto-clear filters on no results works
□ Auto-clear shows "couldn't find with filters" message
□ No auto-clear when no filters were active
□ Combined meal search returns pairs
□ Combined meal search respects budget constraint
□ Combined meal search respects time constraint
□ Combined meal search handles no valid pairs
□ Creator filter validates user existence
□ Creator filter handles user not found
□ Creator filter handles multiple users with same name
□ Creator filter handles user with no recipes
```

---

## Backward Compatibility

All changes are **additive** - no breaking changes:

1. **New intents** - Only triggered by new patterns
2. **New session fields** - Default values, existing sessions work
3. **New SQL functions** - Only used when new filters detected
4. **Price sort change** - Improves existing behavior
5. **Recipe detail additions** - Extra fields in response
6. **Category expansion** - Falls back to original term if LLM fails
7. **Auto-clear on no results** - Only activates when filters exist
8. **Combined meal search** - New intent, doesn't affect single meal searches
9. **Creator validation** - Enhanced error handling, existing searches still work

---

## Rollback Plan

| Issue | Rollback Action |
|-------|-----------------|
| Price sort problems | Revert DESC → ASC (1 line) |
| Intent detection issues | Remove from NLID instructions |
| Session serialization errors | Remove new fields from dataclass |
| Handler errors | Comment out intent routing in pipeline |
| SQL function issues | Remove unused functions |
| Category expansion issues | Remove expand_excluded_ingredients call |
| LLM expansion too slow | Enable caching or skip expansion |
| Filter management issues | Remove endpoints from chat.py routes |
| SessionFilters errors | Remove filters field from SessionState |
| Clear filters endpoint issues | Set re_search default to False |
| Specific filter removal fails | Remove endpoint, keep clear-all only |
| Auto-clear too aggressive | Add config flag to disable auto-clear |
| Auto-clear causes confusion | Remove _handle_no_results_with_auto_clear call |
| Combined meal search issues | Remove combined_meal_search intent |
| Pair generation slow | Reduce max_pairs or cache results |
| Creator validation slow | Cache user lookups or skip validation |
| Multiple users confusion | Show only first user without alternatives |
