# Query-Specific Context Management & SQL Filters

This document analyzes specific user queries and explains the exact flow of context management, SQL filter handling, and retrieval strategy for each scenario.

---

## Table of Contents

1. [Query Type Legend](#query-type-legend)
2. [Individual Queries](#individual-queries)
3. [Multi-Turn Conversations](#multi-turn-conversations)
4. [Edge Cases](#edge-cases)

---

## Query Type Legend

| Symbol | Meaning |
|--------|---------|
| `Q` | Individual/standalone query (new session) |
| `Q1, Q2, ...` | Consecutive queries in the same session |

---

## Individual Queries

### Q: "Can you suggest me something that I can cook under 500"

**NLID Detection:**
```
intent: price_filter
filters.cost: {operator: "<=", value: 500, country: "Norway", sort_order: "DESC"}
requires_embedding: false
```

**Context Management:**
```
Session Before: (empty - new session)
Session After:
  - filters.cost_filter: {operator: "<=", value: 500, country: "Norway", sort_order: "DESC"}
  - last_intent: price_filter
```

**SQL Filters Applied:**
```sql
WHERE r."status" = 'published'
  AND r."languageId" = 'en'
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 500
ORDER BY CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) DESC
-- Shows recipes nearest to 500 kr first
LIMIT 20
```

**Retrieval Strategy:** `SQL_ONLY` (direct SQL builder, no embedding)

**Result:** Recipes priced ≤ 500 NOK, sorted by price descending (nearest to budget first)

---

### Q: "Tell me something easy and quick to cook, remember i am a vegetarian and im allergic to dairy"

**NLID Detection:**
```
intent: recipe_search
entities: {}
parameters:
  difficulty: "easy"
  time_constraints: ["quick"]
filters:
  tags: ["vegetarian"]
  excluded_ingredients: ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]
requires_embedding: true
```

**Context Management:**
```
Session Before: (empty - new session)
Session After:
  - filters.tags: ["vegetarian"]
  - filters.difficulty: "easy"
  - filters.time_filter: {sort_order: "ASC"}
  - excluded_ingredients: ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]
  - allergies: {"dairy": ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]}
  - last_intent: recipe_search
  - last_vector_query: "vegetarian easy quick"
```

**SQL Filters Applied:**
```sql
WHERE r."status" = 'published'
  AND r."languageId" = 'en'
  AND r."difficulty" = 'easy'
  -- Vegetarian tag
  AND EXISTS (
    SELECT 1 FROM recipe_tags_tag rtt
    JOIN tag t ON rtt."tagId" = t.id
    WHERE rtt."recipeId" = r."id"
    AND t.name ILIKE '%vegetarian%'
  )
  -- Dairy allergy (expanded)
  AND NOT EXISTS (
    SELECT 1 FROM recipe_ingredient ri
    JOIN ingredient i ON ri."ingredientId" = i."id"
    WHERE ri."recipeId" = r."id"
    AND (i."name" ILIKE '%dairy%' OR i."name" ILIKE '%milk%'
         OR i."name" ILIKE '%cheese%' OR i."name" ILIKE '%cream%'
         OR i."name" ILIKE '%butter%' OR i."name" ILIKE '%yogurt%')
  )
ORDER BY (r."prepTime" + r."cookTime") ASC
LIMIT 20
```

**Retrieval Strategy:** `HYBRID_VECTOR_TO_SQL`
- Vector query: "vegetarian easy quick"
- SQL filters: difficulty, tag, allergy exclusion, time sort

**Result:** Easy, quick vegetarian recipes without dairy

---

### Q: "I dont like potatoes"

**NLID Detection:**
```
intent: recipe_search
filters:
  excluded_ingredients: ["potatoes", "potato"]
requires_embedding: true
```

**Context Management:**
```
Session Before: (empty - new session)
Session After:
  - excluded_ingredients: ["potatoes", "potato"]
  - last_intent: recipe_search
```

**Special Handling:**
- Detected as **pure exclusion query** (no positive context)
- Routes to `SQL_ONLY` to avoid embedding bias toward potato recipes

**SQL Filters Applied:**
```sql
WHERE r."status" = 'published'
  AND r."languageId" = 'en'
  -- Potato exclusion
  AND (
    r."name" NOT ILIKE '%potato%'
    AND r."ingress" NOT ILIKE '%potato%'
    AND NOT EXISTS (
      SELECT 1 FROM recipe_ingredient ri
      JOIN ingredient i ON ri."ingredientId" = i."id"
      WHERE ri."recipeId" = r."id"
      AND i."name" ILIKE '%potato%'
    )
  )
ORDER BY (r."prepTime" + r."cookTime") ASC
LIMIT 20
```

**Retrieval Strategy:** `SQL_ONLY`
- No embedding search (would bias toward potato recipes)
- Direct SQL with exclusion filter

**Result:** All recipes excluding potatoes

---

### Q: "what is the cost of banana"

**NLID Detection:**
```
intent: pricing_info
entities:
  ingredients: ["banana"]
parameters:
  country: "Norway" (default)
requires_embedding: true
```

**Context Management:**
```
Session Before: (empty)
Session After:
  - last_intent: pricing_info
  - context_entities.last_pricing_item: "banana"
  - context_entities.last_pricing_item_type: "ingredient"
```

**Flow:** Routes to `_handle_special_query` (not recipe search)

**SQL Executed:**
```sql
SELECT i."name", ip."price", ip."currency", c."name" as country
FROM ingredient i
LEFT JOIN ingredient_pricing ip ON i."id" = ip."ingredientId"
LEFT JOIN country c ON ip."countryId" = c."id"
WHERE i."name" ILIKE '%banana%'
AND c."name" = 'Norway'
```

**Result:** Price of banana in Norway

---

### Q: "What is the cost of sugar"

**NLID Detection:**
```
intent: pricing_info
entities:
  ingredients: ["sugar"]
parameters:
  country: "Norway" (default)
```

Same flow as banana query above.

---

### Q: "suggest me low budget queries"

**NLID Detection:**
```
intent: price_filter
filters.cost: {sort_order: "ASC", level: "low"}
requires_embedding: false
```

**SQL Filters Applied:**
```sql
WHERE r."status" = 'published'
ORDER BY CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) ASC
-- Cheapest first
LIMIT 20
```

**Retrieval Strategy:** `SQL_ONLY`

**Result:** Recipes sorted by price ascending (cheapest first)

---

### Q: "I want to make something quick"

**NLID Detection:**
```
intent: time_filter
filters.time: {sort_order: "ASC"}
requires_embedding: false
```

**Context Management:**
```
Session After:
  - filters.time_filter: {sort_order: "ASC"}
  - last_intent: time_filter
```

**SQL Filters Applied:**
```sql
WHERE r."status" = 'published'
ORDER BY (r."prepTime" + r."cookTime") ASC
-- Quickest first
LIMIT 20
```

**Retrieval Strategy:** `SQL_ONLY`

**Result:** Recipes sorted by total time ascending

---

### Q: "I dont have a lot of time on me and need to cook a good meal, can you suggest me somethin"

**NLID Detection:**
```
intent: time_filter
filters.time: {sort_order: "ASC"}
requires_embedding: false
```

**Trigger Phrases Detected:** "dont have a lot of time", "need to cook"

Same flow as "quick" query above.

---

### Q: "I am allergic to dairy, can you tell me some options please"

**NLID Detection:**
```
intent: recipe_search
filters:
  excluded_ingredients: ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]
requires_embedding: true
```

**Special Handling:**
- Detected as **pure exclusion query** (no positive search context)
- Allergy expansion: "dairy" → ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]

**Context Management:**
```
Session After:
  - excluded_ingredients: ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]
  - allergies: {"dairy": ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]}
  - last_intent: recipe_search
```

**Retrieval Strategy:** `SQL_ONLY` (exclusion-only query)

**Result:** All recipes excluding dairy products

---

### Q: "Can you tell me something vegetarian that I can cook quickly"

**NLID Detection:**
```
intent: recipe_search
filters:
  tags: ["vegetarian"]
parameters:
  time_constraints: ["quick"]
requires_embedding: true
```

**Context Management:**
```
Session After:
  - filters.tags: ["vegetarian"]
  - filters.time_filter: {sort_order: "ASC"}
  - last_vector_query: "vegetarian quick"
```

**SQL Filters Applied:**
```sql
WHERE r."status" = 'published'
  AND EXISTS (
    SELECT 1 FROM recipe_tags_tag rtt
    JOIN tag t ON rtt."tagId" = t.id
    WHERE rtt."recipeId" = r."id"
    AND t.name ILIKE '%vegetarian%'
  )
ORDER BY (r."prepTime" + r."cookTime") ASC
LIMIT 20
```

**Retrieval Strategy:** `HYBRID_VECTOR_TO_SQL`
- Vector query: "vegetarian quick"
- SQL: tag filter + time sort

---

## Multi-Turn Conversations

---

### Scenario 1: Creator + Budget

```
Q1: suggest me recipes of @mammapia
Q2: My budget is 200$
```

#### Q1: "suggest me recipes of @mammapia"

**NLID Detection:**
```
intent: recipe_search
filters:
  creator_username: "mammapia"
```

**Creator Resolution:**
```
@username lookup → UserService.get_user_by_username("mammapia")
Result: user found
  → filters.creator_uid: "uuid-of-mammapia"
  → filters.creator_username: "mammapia"
```

**Context After Q1:**
```
session.filters.creator_uid: "uuid-of-mammapia"
session.filters.creator_username: "mammapia"
session.last_vector_query: "recipes"
session.last_intent: recipe_search
```

**SQL for Q1:**
```sql
WHERE r."status" = 'published'
  AND r."userUid" = 'uuid-of-mammapia'
LIMIT 20
```

**Result:** All recipes by @mammapia

---

#### Q2: "My budget is 200$"

**NLID Detection:**
```
intent: price_filter
filters.cost: {operator: "<=", value: 200, country: "US", sort_order: "DESC"}
```

**Context Merge:**
```
Before Q2:
  - creator_uid: "uuid-of-mammapia"
  - last_vector_query: "recipes"

After Q2 (merge):
  - creator_uid: "uuid-of-mammapia" (preserved)
  - cost_filter: {operator: "<=", value: 200, country: "US"}
  - last_vector_query: "recipes" (preserved)
```

**Detection:** Has previous search context → Refinement mode

**Retrieval Strategy:** `_handle_filter_refinement`
1. Embedding search: vector_query="recipes" scoped to creator
2. SQL filter: cost ≤ $200 + creator filter

**SQL for Q2:**
```sql
WHERE r."status" = 'published'
  AND r."userUid" = 'uuid-of-mammapia'
  AND CAST(r.recipe_metadata->'pricing'->>'usa' AS FLOAT) <= 200
ORDER BY CAST(r.recipe_metadata->'pricing'->>'usa' AS FLOAT) DESC
LIMIT 20
```

**Result:** @mammapia's recipes under $200

---

### Scenario 2: Dessert + Allergy + Budget

```
Q1: suggest me dessert recipes
Q2: I am allergic to nuts
Q3: My budget is 200$
```

#### Q1: "suggest me dessert recipes"

**NLID Detection:**
```
intent: recipe_search
entities:
  meal_types: ["dessert"]
filters:
  tags: ["dessert"]
```

**Context After Q1:**
```
session.filters.tags: ["dessert"]
session.last_vector_query: "dessert"
session.last_intent: recipe_search
```

**SQL for Q1:**
```sql
WHERE r."status" = 'published'
  AND EXISTS (
    SELECT 1 FROM recipe_tags_tag rtt
    JOIN tag t ON rtt."tagId" = t.id
    WHERE rtt."recipeId" = r."id"
    AND t.name ILIKE '%dessert%'
  )
LIMIT 20
```

---

#### Q2: "I am allergic to nuts"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["nuts"]
```

**Allergy Expansion:**
```
"nuts" → ["nuts", "walnut", "almond", "pecan", "hazelnut", "cashew", "pistachio", "peanut"]
```

**Context After Q2:**
```
session.filters.tags: ["dessert"] (preserved)
session.excluded_ingredients: ["nuts", "walnut", "almond", "pecan", "hazelnut", ...]
session.allergies: {"nuts": ["nuts", "walnut", "almond", ...]}
session.last_vector_query: "dessert" (preserved)
session.last_intent: recipe_search
```

**Detection:** Refinement with previous search

**SQL for Q2:**
```sql
WHERE r."status" = 'published'
  -- Dessert tag (preserved)
  AND EXISTS (
    SELECT 1 FROM recipe_tags_tag rtt
    JOIN tag t ON rtt."tagId" = t.id
    WHERE rtt."recipeId" = r."id"
    AND t.name ILIKE '%dessert%'
  )
  -- Nut allergy (expanded)
  AND NOT EXISTS (
    SELECT 1 FROM recipe_ingredient ri
    JOIN ingredient i ON ri."ingredientId" = i."id"
    WHERE ri."recipeId" = r."id"
    AND (i."name" ILIKE '%nuts%' OR i."name" ILIKE '%walnut%'
         OR i."name" ILIKE '%almond%' OR i."name" ILIKE '%pecan%' ...)
  )
LIMIT 20
```

**Result:** Dessert recipes without nuts

---

#### Q3: "My budget is 200$"

**NLID Detection:**
```
intent: price_filter
filters.cost: {operator: "<=", value: 200, country: "US"}
```

**Context After Q3 (merged):**
```
session.filters.tags: ["dessert"] (preserved)
session.filters.cost_filter: {operator: "<=", value: 200, country: "US"}
session.excluded_ingredients: ["nuts", "walnut", ...] (preserved)
session.last_vector_query: "dessert" (preserved)
```

**SQL for Q3:**
```sql
WHERE r."status" = 'published'
  -- Dessert tag
  AND EXISTS (...dessert tag...)
  -- Nut allergy
  AND NOT EXISTS (...nut exclusion...)
  -- Budget filter
  AND CAST(r.recipe_metadata->'pricing'->>'usa' AS FLOAT) <= 200
ORDER BY CAST(r.recipe_metadata->'pricing'->>'usa' AS FLOAT) DESC
LIMIT 20
```

**Result:** Dessert recipes without nuts, under $200

---

### Scenario 3: Pricing Follow-up

```
Q1: Whats the cost of clove
Q2: And in India
```

#### Q1: "Whats the cost of clove"

**NLID Detection:**
```
intent: pricing_info
entities:
  ingredients: ["clove"]
parameters:
  country: "Norway" (default)
```

**Context After Q1:**
```
session.last_intent: pricing_info
session.context_entities.last_pricing_item: "clove"
session.context_entities.last_pricing_item_type: "ingredient"
```

**Result:** Price of clove in Norway

---

#### Q2: "And in India"

**NLID Detection (Initial):**
```
intent: general_chat (or similar - query is just "And in India")
```

**Special Handling (Pricing Follow-up Detection):**
```python
# Check: last_intent == "pricing_info" AND last_pricing_item exists
if session.last_intent == "pricing_info" and session.context_entities.last_pricing_item:
    detected_country = _extract_country_from_query("And in India")  # → "India"
    # Override NLID result
    nlid_result["intent"] = "pricing_info"
    nlid_result["entities"]["ingredients"] = ["clove"]  # From context
    nlid_result["parameters"]["country"] = "India"
```

**SQL for Q2:**
```sql
WHERE i."name" ILIKE '%clove%'
AND c."name" = 'India'
```

**Result:** Price of clove in India

---

### Scenario 4: Cumulative Exclusions

```
Q1: Suggest me pasta recipes
Q2: I am allergic to garlic
Q3: I ran out of tomatoes
Q4: I dont like lemon
```

#### Q1: "Suggest me pasta recipes"

**NLID Detection:**
```
intent: recipe_search
entities:
  ingredients: ["pasta"]
```

**Context After Q1:**
```
session.included_ingredients: ["pasta"]
session.last_vector_query: "pasta"
session.last_intent: recipe_search
```

---

#### Q2: "I am allergic to garlic"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["garlic"]
```

**Context After Q2:**
```
session.included_ingredients: ["pasta"] (preserved)
session.excluded_ingredients: ["garlic"]
session.last_vector_query: "pasta" (preserved)
```

**SQL for Q2:**
```sql
WHERE ... AND NOT EXISTS (...i."name" ILIKE '%garlic%'...)
```

---

#### Q3: "I ran out of tomatoes"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["tomatoes", "tomato"]
```

**Context After Q3 (cumulative):**
```
session.excluded_ingredients: ["garlic", "tomatoes", "tomato"]  # Accumulated!
session.last_vector_query: "pasta"
```

**SQL for Q3:**
```sql
WHERE ...
  AND NOT EXISTS (...i."name" ILIKE '%garlic%'...)
  AND NOT EXISTS (...i."name" ILIKE '%tomato%'...)
```

---

#### Q4: "I dont like lemon"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["lemon", "lemons"]
```

**Context After Q4 (cumulative):**
```
session.excluded_ingredients: ["garlic", "tomatoes", "tomato", "lemon", "lemons"]
session.last_vector_query: "pasta"
```

**SQL for Q4:**
```sql
WHERE ...
  AND NOT EXISTS (...i."name" ILIKE '%garlic%'...)
  AND NOT EXISTS (...i."name" ILIKE '%tomato%'...)
  AND NOT EXISTS (...i."name" ILIKE '%lemon%'...)
```

**Result:** Pasta recipes without garlic, tomatoes, or lemon

---

### Scenario 5: Ingredient + Budget + Allergy

```
Q1: suggest me something. I only have banana
Q2: My budget is 200
Q3: I am allergic to dairy
```

#### Q1: "suggest me something. I only have banana"

**NLID Detection:**
```
intent: recipe_search
entities:
  ingredients: ["banana"]
filters:
  included_ingredients: ["banana"]
```

**Context After Q1:**
```
session.included_ingredients: ["banana"]
session.last_vector_query: "banana"
```

**SQL for Q1:**
```sql
WHERE ... AND EXISTS (...i."name" ILIKE '%banana%'...)
```

---

#### Q2: "My budget is 200"

**NLID Detection:**
```
intent: price_filter
filters.cost: {operator: "<=", value: 200, country: "Norway"}
```

**Context After Q2:**
```
session.included_ingredients: ["banana"] (preserved)
session.filters.cost_filter: {operator: "<=", value: 200, country: "Norway"}
session.last_vector_query: "banana" (preserved)
```

**SQL for Q2:**
```sql
WHERE ...
  AND EXISTS (...i."name" ILIKE '%banana%'...)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 200
```

---

#### Q3: "I am allergic to dairy"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["dairy", "milk", "cheese", "cream", "butter", "yogurt"]
```

**Context After Q3:**
```
session.included_ingredients: ["banana"] (preserved)
session.excluded_ingredients: ["dairy", "milk", "cheese", ...] (expanded)
session.filters.cost_filter: {...} (preserved)
session.last_vector_query: "banana" (preserved)
```

**SQL for Q3:**
```sql
WHERE ...
  AND EXISTS (...i."name" ILIKE '%banana%'...)
  AND NOT EXISTS (...i."name" ILIKE '%dairy%' OR i."name" ILIKE '%milk%'...)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 200
```

**Result:** Banana recipes, no dairy, under 200 kr

---

### Scenario 6: Cuisine + Allergy + Vegetarian

```
Q1: Suggest me italian recipes
Q2: I am allergic to pasta
Q3: I dont eat non veg
```

#### Q1: "Suggest me italian recipes"

**NLID Detection:**
```
intent: recipe_search
filters:
  cuisines: ["italian"]
```

**Context After Q1:**
```
session.filters.cuisines: ["italian"]
session.last_vector_query: "italian"
```

---

#### Q2: "I am allergic to pasta"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["pasta"]
```

**Context After Q2:**
```
session.filters.cuisines: ["italian"] (preserved)
session.excluded_ingredients: ["pasta"]
session.last_vector_query: "italian" (preserved)
```

**SQL for Q2:**
```sql
WHERE ...
  AND EXISTS (...cuisine = 'italian'...)
  AND NOT EXISTS (...i."name" ILIKE '%pasta%'...)
```

---

#### Q3: "I dont eat non veg"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  tags: ["vegetarian"]
```

**Context After Q3:**
```
session.filters.cuisines: ["italian"] (preserved)
session.filters.tags: ["vegetarian"]
session.excluded_ingredients: ["pasta"] (preserved)
session.last_vector_query: "italian" (preserved)
```

**SQL for Q3:**
```sql
WHERE ...
  AND EXISTS (...cuisine = 'italian'...)
  AND EXISTS (...tag = 'vegetarian'...)
  AND NOT EXISTS (...i."name" ILIKE '%pasta%'...)
```

**Result:** Italian vegetarian recipes without pasta

---

### Scenario 7: Time + Multiple Exclusions + Budget

```
Q1: I am in a hurry, can you please tell me something to make
Q2: I dont like potato, ewww
Q3: I want without garlic
Q4: I dont like egg
Q5: My budget is 100
```

#### Q1: "I am in a hurry..."

**NLID Detection:**
```
intent: time_filter
filters.time: {sort_order: "ASC"}
```

**Context After Q1:**
```
session.filters.time_filter: {sort_order: "ASC"}
session.last_intent: time_filter
```

**SQL for Q1:**
```sql
ORDER BY (r."prepTime" + r."cookTime") ASC
```

---

#### Q2: "I dont like potato, ewww"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["potato", "potatoes"]
```

**Context After Q2:**
```
session.filters.time_filter: {sort_order: "ASC"} (preserved)
session.excluded_ingredients: ["potato", "potatoes"]
session.last_vector_query: null (time_filter doesn't set this)
```

**Detection:** No previous vector_query, but has time_filter → Routes to `_handle_filter_query`

**SQL for Q2:**
```sql
WHERE ...
  AND NOT EXISTS (...i."name" ILIKE '%potato%'...)
ORDER BY (r."prepTime" + r."cookTime") ASC
```

---

#### Q3: "I want without garlic"

**Context After Q3:**
```
session.filters.time_filter: {sort_order: "ASC"} (preserved)
session.excluded_ingredients: ["potato", "potatoes", "garlic"]  # Cumulative
```

**SQL for Q3:**
```sql
WHERE ...
  AND NOT EXISTS (...i."name" ILIKE '%potato%'...)
  AND NOT EXISTS (...i."name" ILIKE '%garlic%'...)
ORDER BY (r."prepTime" + r."cookTime") ASC
```

---

#### Q4: "I dont like egg"

**Context After Q4:**
```
session.excluded_ingredients: ["potato", "potatoes", "garlic", "egg", "eggs"]  # Cumulative
```

**SQL for Q4:**
```sql
WHERE ...
  AND NOT EXISTS (...i."name" ILIKE '%potato%'...)
  AND NOT EXISTS (...i."name" ILIKE '%garlic%'...)
  AND NOT EXISTS (...i."name" ILIKE '%egg%'...)
ORDER BY (r."prepTime" + r."cookTime") ASC
```

---

#### Q5: "My budget is 100"

**NLID Detection:**
```
intent: price_filter
filters.cost: {operator: "<=", value: 100, country: "Norway"}
```

**Context After Q5:**
```
session.filters.time_filter: {sort_order: "ASC"} (preserved)
session.filters.cost_filter: {operator: "<=", value: 100, country: "Norway"}
session.excluded_ingredients: ["potato", "potatoes", "garlic", "egg", "eggs"] (preserved)
```

**SQL for Q5:**
```sql
WHERE ...
  AND NOT EXISTS (...potato...)
  AND NOT EXISTS (...garlic...)
  AND NOT EXISTS (...egg...)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 100
ORDER BY (r."prepTime" + r."cookTime") ASC,
         CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) DESC
LIMIT 20
```

**Result:** Quick recipes, no potato/garlic/egg, under 100 kr

---

### Scenario 8: Creator + Exclusion + Budget

```
Q1: suggest me recipes by @mammapia
Q2: I dont have pasta
Q3: My budget is 200
```

#### Q1: "suggest me recipes by @mammapia"

**Context After Q1:**
```
session.filters.creator_uid: "uuid-of-mammapia"
session.filters.creator_username: "mammapia"
session.last_vector_query: "recipes"
```

---

#### Q2: "I dont have pasta"

**Context After Q2:**
```
session.filters.creator_uid: "uuid-of-mammapia" (preserved)
session.excluded_ingredients: ["pasta"]
session.last_vector_query: "recipes" (preserved)
```

**SQL for Q2:**
```sql
WHERE ...
  AND r."userUid" = 'uuid-of-mammapia'
  AND NOT EXISTS (...i."name" ILIKE '%pasta%'...)
```

---

#### Q3: "My budget is 200"

**Context After Q3:**
```
session.filters.creator_uid: "uuid-of-mammapia" (preserved)
session.filters.cost_filter: {operator: "<=", value: 200, country: "Norway"}
session.excluded_ingredients: ["pasta"] (preserved)
```

**SQL for Q3:**
```sql
WHERE ...
  AND r."userUid" = 'uuid-of-mammapia'
  AND NOT EXISTS (...i."name" ILIKE '%pasta%'...)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 200
```

**Result:** @mammapia's recipes, no pasta, under 200 kr

---

### Scenario 9: Combined @username + Budget + Time

```
Q1: Can you tell me something under 200 created by @mammapia
Q2: I want something quick
```

#### Q1: "Can you tell me something under 200 created by @mammapia"

**NLID Detection (Rule 4 - Combined Query):**
```
intent: recipe_search (because @username present)
filters:
  creator_username: "mammapia"
  cost: {operator: "<=", value: 200, country: "Norway", sort_order: "DESC"}
```

**Key:** Both @username AND cost extracted in single query

**Context After Q1:**
```
session.filters.creator_uid: "uuid-of-mammapia"
session.filters.cost_filter: {operator: "<=", value: 200, country: "Norway"}
session.last_vector_query: "recipes"
```

**SQL for Q1:**
```sql
WHERE ...
  AND r."userUid" = 'uuid-of-mammapia'
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 200
ORDER BY CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) DESC
```

---

#### Q2: "I want something quick"

**NLID Detection:**
```
intent: time_filter
filters.time: {sort_order: "ASC"}
```

**Context After Q2:**
```
session.filters.creator_uid: "uuid-of-mammapia" (preserved)
session.filters.cost_filter: {...} (preserved)
session.filters.time_filter: {sort_order: "ASC"}
```

**SQL for Q2:**
```sql
WHERE ...
  AND r."userUid" = 'uuid-of-mammapia'
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 200
ORDER BY (r."prepTime" + r."cookTime") ASC,
         CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) DESC
```

**Result:** @mammapia's quick recipes under 200 kr

---

### Scenario 10: Budget + Dessert + Exclusion

```
Q1: My budget is 400. Suggest me some recipes
Q2: I want desserts
Q3: I dont like egg
```

#### Q1: "My budget is 400. Suggest me some recipes"

**NLID Detection:**
```
intent: price_filter (budget mentioned first)
filters.cost: {operator: "<=", value: 400, country: "Norway"}
```

**Context After Q1:**
```
session.filters.cost_filter: {operator: "<=", value: 400, country: "Norway"}
session.last_intent: price_filter
```

**Note:** No `last_vector_query` set (standalone filter query)

---

#### Q2: "I want desserts"

**NLID Detection:**
```
intent: recipe_search
filters:
  tags: ["dessert"]
```

**Context After Q2:**
```
session.filters.cost_filter: {...} (preserved)
session.filters.tags: ["dessert"]
session.last_vector_query: "dessert"
session.last_intent: recipe_search
```

**Detection:** Has session cost_filter → Routes to `_handle_filter_query` with merged filters

**SQL for Q2:**
```sql
WHERE ...
  AND EXISTS (...tag = 'dessert'...)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 400
ORDER BY CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) DESC
```

---

#### Q3: "I dont like egg"

**NLID Detection:**
```
intent: recipe_search (refinement)
filters:
  excluded_ingredients: ["egg", "eggs"]
```

**Context After Q3:**
```
session.filters.cost_filter: {...} (preserved)
session.filters.tags: ["dessert"] (preserved)
session.excluded_ingredients: ["egg", "eggs"]
session.last_vector_query: "dessert" (preserved)
```

**SQL for Q3:**
```sql
WHERE ...
  AND EXISTS (...tag = 'dessert'...)
  AND NOT EXISTS (...i."name" ILIKE '%egg%'...)
  AND CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) <= 400
ORDER BY CAST(r.recipe_metadata->'pricing'->>'norway' AS FLOAT) DESC
```

**Result:** Dessert recipes, no egg, under 400 kr

---

### Scenario 11: Sweet + Allergy

```
Q1: Can you suggest me something sweet
Q2: I am allergic to almonds
```

#### Q1: "Can you suggest me something sweet"

**NLID Detection:**
```
intent: recipe_search
filters:
  tags: ["dessert"]
entities:
  meal_types: ["dessert"]
```

**Context After Q1:**
```
session.filters.tags: ["dessert"]
session.last_vector_query: "sweet dessert"
```

---

#### Q2: "I am allergic to almonds"

**Allergy Expansion:**
```
"almonds" → ["almond", "almonds", "marzipan"] (limited expansion for specific nut)
```

**Context After Q2:**
```
session.filters.tags: ["dessert"] (preserved)
session.excluded_ingredients: ["almond", "almonds", "marzipan"]
session.last_vector_query: "sweet dessert" (preserved)
```

**SQL for Q2:**
```sql
WHERE ...
  AND EXISTS (...tag = 'dessert'...)
  AND NOT EXISTS (...i."name" ILIKE '%almond%'...)
```

**Result:** Sweet/dessert recipes without almonds

---

## Edge Cases

### Pure Exclusion Query

```
Q: "I dont like potatoes"
```

**Problem:** If we run embedding search with "potatoes", we get potato recipes!

**Solution:** Detect pure exclusion → Route to `SQL_ONLY`

```python
has_positive_context = bool(ingredients or cuisines or tags or ...)
if excluded_ingredients and not has_positive_context:
    strategy = RetrievalStrategy.SQL_ONLY
```

---

### Pronoun Resolution

```
Q1: "recipes by @mammapia"
Q2: "show me more of her recipes"
```

**NLID Rule 5:** "her" → resolve to last mentioned creator

```python
if "her" in query:
    last_creator = conversation_history.get_last("creator_username")
    filters.creator_username = last_creator  # "mammapia"
```

---

### Budget Default Country

```
Q: "My budget is 500"  (no currency symbol)
```

**Default:** Norway (kr)

```
filters.cost: {operator: "<=", value: 500, country: "Norway"}
```

---

### Time vs Cost Ambiguity

```
Q: "under 30"  (ambiguous - could be time or cost)
```

**Resolution:**
- "under 30 minutes" → time_filter
- "under 30" (no unit) → cost_filter (default)

---

## Summary: Key Context Management Rules

| Rule | Description |
|------|-------------|
| **Accumulation** | Exclusions accumulate across turns (never removed) |
| **Preservation** | Previous search context preserved when adding constraints |
| **Priority** | Current query filters override session filters |
| **Expansion** | Allergies expanded to include variants |
| **Refinement Detection** | "I am allergic", "I don't like", "without X" → refinement |
| **Pure Exclusion** | No positive context → SQL_ONLY (no embedding bias) |
| **Filter Merging** | Session filters + current filters combined in SQL |
| **Pronoun Resolution** | "her", "his" → last mentioned creator |
| **Default Country** | No currency → Norway (kr) |

---

## Issues Found & Fixes Applied

### Issue 1: SQL Column Case Sensitivity (FIXED)

**Problem:** The LLM was generating incorrect SQL for the `bundle` table:

```sql
-- WRONG: PostgreSQL interprets as lowercase "deletedat"
LEFT JOIN bundle b ON br."bundleId" = b.id AND b.deletedAt IS NULL
```

**Error:**
```
psycopg2.errors.UndefinedColumn: column b.deletedat does not exist
HINT: Perhaps you meant to reference the column "b.deletedAt".
```

**Fix Applied:** Updated SQL generator system prompt to include explicit rule:
- Added Rule 12: `BUNDLE TABLE: When joining bundle table, ALWAYS add: AND b."deletedAt" IS NULL. The column is mixed-case so MUST be quoted.`
- Updated all SQL templates to use `b."deletedAt" IS NULL` with proper quoting

**File:** `apps/fastapi/src/services/sql_generator.py`

---

### Issue 2: Context Not Saved on SQL Failure (FIXED)

**Problem:** When SQL execution failed, the search context was never saved:

```python
# OLD CODE: Only saved on success
if execution_result.get("row_count", 0) > 0:
    self.session_manager.update_search_context(...)

# If SQL fails, this returns early WITHOUT saving context
if not execution_result["success"]:
    return {...}  # last_vector_query remains None!
```

**Impact:** Multi-turn queries like "dessert recipes" → "I am allergic to nuts" would fail because `last_vector_query` was `None`.

**Fix Applied:** Save search context BEFORE checking for SQL failure:

```python
# NEW CODE: Always save context for recipe_search intents
if retrieval_plan.vector_query and nlid_result_dict["intent"] == "recipe_search":
    self.session_manager.update_search_context(
        session, query, retrieval_plan.vector_query,
        retrieval_plan.sql_filters, nlid_result_dict["intent"]
    )
    logger.info(f"[STAGE 8] Saved search context: vector_query='{retrieval_plan.vector_query}'")

if not execution_result["success"]:
    # Now context is already saved before this return
    return {...}
```

**File:** `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

---

## Conversation History Management

The system maintains the last 10 conversations (1 conversation = 1 pair of user message + AI response).

**Storage Location:**
- `session.conversation_history` - In-memory list of message dicts
- Database: `Message` table with `session_id`, `role`, `content`, `meta`

**Context Extraction on Session Load:**
```python
def _extract_context_from_conversation(session, messages):
    # 1. Try to restore from message metadata (meta field)
    for msg in reversed(messages):
        if msg.meta:
            vector_query = meta.get('vector_query')
            filters = meta.get('filters')
            # Restore to session

    # 2. Fallback: keyword-based extraction from message content
    if not session.context_entities.last_vector_query:
        # Extract from message text using patterns
```

**History Limit:** Last 10 messages kept in `conversation_history`

---

## Verification Checklist

After the fixes, verify:

- [ ] `last_vector_query` is saved even when SQL fails
- [ ] SQL templates use `b."deletedAt"` with quotes
- [ ] Multi-turn queries work after SQL errors
- [ ] Budget filters persist across turns
- [ ] Allergy exclusions accumulate correctly
- [ ] Creator filters persist when adding other constraints
