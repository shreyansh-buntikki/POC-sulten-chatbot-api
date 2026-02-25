# Chatbot Context Management: Multi-Turn Filter Narrowing Approaches

## The Problem

When users interact with the chatbot across multiple turns, each new filter (budget, servings, tags, allergies) narrows the candidate pool. If the initial pool is small (e.g., 20 embedding results), adding 2-3 filters can quickly reduce results to zero.

### Example Scenarios

**Scenario A (Embedding → Filter):**
```
Q1: "Suggest me dessert recipes"
    → Embedding returns 20 dessert candidates
    → SQL filters from those 20 → shows 5 to user

Q2: "My budget is $200"
    → No new embedding (filter-only intent)
    → SQL applies budget filter on same 20 cached candidates
    → Only 2 match → could easily be 0
```

**Scenario B (Filter → Embedding):**
```
Q1: "My budget is $200"
    → SQL-only, random recipes under $200 → shows 5

Q2: "I want desserts"
    → Embedding search for "desserts" (new topic detected)
    → Returns 20 dessert candidates
    → Session carries forward $200 budget filter
    → SQL filters on 20 → some may exceed budget → fewer results
```

**Scenario C (Cascade Narrowing):**
```
Q1: "Christmas lunch recipes"     → 20 candidates
Q2: "For 2 people"                → filters to 8
Q3: "I'm allergic to nuts"        → filters to 4
Q4: "Under 100 kr"                → filters to 0 ← problem
```

---

## Approach 1: Larger Initial Candidate Pool

### Concept
Fetch a much larger pool from embedding search (e.g., `top_k=100`) upfront, but only **display** the top 5 to the user. The remaining 95 candidates stay in the session cache as a "reserve pool" for subsequent filter narrowing.

### How It Works
1. User asks "suggest me dessert recipes"
2. Embedding search retrieves **100** semantically similar recipes (instead of 20)
3. SQL filters those 100 → maybe 40 match the base criteria
4. Show top 5 to user
5. User says "my budget is $200" → SQL re-filters the same 100 cached candidates with budget constraint
6. 25 of the 100 match → plenty of results to show

### Who Does This
- **Spotify Discover Weekly:** Retrieves 500+ track candidates from collaborative filtering, then re-ranks and filters to 30.
- **Netflix:** ANN index returns ~1000 candidates per query; filters + business rules + re-ranking reduce to ~50 displayed.
- **YouTube Recommendations:** Two-phase system — candidate generation retrieves hundreds, ranking model selects tens.

### Pros
- Simplest change — just increase `top_k` and `EMBEDDING_BATCH_SIZE`
- No architectural changes needed
- Session cache already exists (used for "show more")
- pgvector performance difference between `top_k=20` and `top_k=100` is negligible (HNSW index)

### Cons
- Slightly more memory per session (~100 UUIDs ≈ 3.6KB, negligible)
- Candidates beyond the first 20-30 may have significantly lower similarity scores
- Doesn't help when the entire DB has fewer than 100 relevant results for a query

### Cost Impact
- Embedding API call cost: **unchanged** (one query embedding, same OpenAI call)
- pgvector query: **~same** (HNSW index scans ~same number of nodes)
- SQL generation: **unchanged** (just more IDs in the IN clause)
- Memory: **+4KB per session** (trivial)

---

## Approach 2: Waterfall / Fallback Retrieval

### Concept
When filtered results drop below a configurable threshold (e.g., < 3 results), **automatically expand the search** by either:
- Re-embedding with a larger `top_k`, OR
- Dropping the embedding restriction and doing SQL-only with all accumulated filters, OR
- Progressively relaxing soft filters (tags, servings) while keeping hard filters (allergies)

### How It Works
```
Tier 1: Filter cached candidates (100 from Approach 1)
        → Got 2 results (below threshold of 3)

Tier 2: Re-embed with top_k=200, apply all filters
        → Got 8 results ✓ (serve these)

Tier 3: (If Tier 2 also fails) SQL-only search with all filters
        (no embedding restriction, search full DB)
        → Got 15 results ✓

Tier 4: (If Tier 3 also fails) Relax soft filters (drop tags, keep allergies/budget)
        → Got 10 results, inform user: "I couldn't find exact matches for
          'christmas lunch', but here are some lunch recipes within your budget"
```

### Who Does This
- **Amazon Product Search:** Progressive query relaxation — starts with exact match, falls back to partial match, then category-level results with "Did you mean?" suggestions.
- **Google Shopping:** When strict filters yield zero, shows "Similar results" with relaxed constraints and highlights which filters were loosened.
- **Airbnb:** If exact dates/price/location yield nothing, shows "Flexible dates" or "Nearby locations" with explanatory messaging.
- **Elasticsearch:** Built-in `function_score` with `min_score` + fallback queries as a documented pattern.

### Filter Relaxation Priority
Not all filters should be treated equally when relaxing:

| Filter Type | Priority | Relax? | Reason |
|------------|----------|--------|--------|
| Allergies / `excluded_ingredients` | Hard | NEVER | Safety concern |
| Access control (private, deleted) | Hard | NEVER | Business rule |
| Budget / `cost` filter | Soft-Hard | Expand by 20% | User preference, slight flex OK |
| Servings | Soft | Drop or expand range | Recipes are scalable |
| Tags (christmas, dessert) | Soft | Drop → ranking boost | Tags are unreliable in DB |
| Cuisines | Soft | Drop → ranking boost | Preference, not requirement |
| Difficulty | Soft | Drop | Minor preference |
| Time constraint | Soft | Drop | Minor preference |

### Pros
- Guarantees results in almost all cases
- User experience is smooth — they don't see "no results found"
- Can provide transparency: "I relaxed the 'christmas' filter to find more options"
- Combines well with Approach 1 (larger pool = Tier 1, waterfall = Tier 2+)

### Cons
- More complex logic in the pipeline orchestrator
- Need to decide threshold values (what's "too few" results?)
- Relaxed results may not fully match user intent
- Need to communicate relaxations to user clearly via NLG

### Cost Impact
- Best case (Tier 1 succeeds): **zero additional cost**
- Worst case (reaches Tier 3): one additional SQL query (~10ms) or one additional embedding call (~200ms + API cost)
- Re-embedding at Tier 2: one OpenAI embedding API call (same cost as original)

---

## Approach 3: Pre-Filtered Vector Search

### Concept
Instead of the current "embed first, filter later" approach, apply SQL filters **during** the embedding search query itself. pgvector supports this natively by adding WHERE clauses to the vector similarity query.

### How It Works
```sql
-- Current approach: embed → get 20 → filter → might get 0
-- Pre-filtered approach: filter + embed in one query
SELECT r."id", r."name",
       r.embedding <=> $query_embedding AS distance
FROM recipe r
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND CAST(r."recipe_metadata"->'pricing'->>'KR' AS FLOAT) <= 200
  AND r."servings" = 2
ORDER BY r.embedding <=> $query_embedding
LIMIT 20;
```

Every returned result **already satisfies all filters**. No candidate waste.

### Who Does This
- **Pinecone:** Native metadata filtering during ANN search — filters are applied within the index traversal, not post-hoc.
- **Weaviate:** `where` filters in GraphQL queries run inside the HNSW index scan.
- **Qdrant:** Payload-based filtering integrated into vector search.
- **Milvus:** Attribute filtering combined with ANN search.

### How It Differs from Current Architecture
Currently:
```
Embedding(query, top_k=20) → 20 candidates → SQL(candidates + filters) → N results
```

Pre-filtered:
```
pgvector_search(query, filters, top_k=20) → 20 results (all pre-filtered)
```

### Pros
- Most efficient — zero wasted candidates
- Every result satisfies all constraints
- No need for large cache or fallback tiers
- Single query instead of embedding + SQL

### Cons
- Requires changes to `embedding_service.py` to accept and apply SQL filters
- Can't reuse cached embedding results when new filters arrive (must re-query)
- pgvector pre-filtering can be slower for complex filter combinations (though HNSW handles it well in practice)
- Tight coupling between embedding and SQL layers

### Cost Impact
- OpenAI embedding API: **same** (one call per query)
- pgvector query: **slightly slower** per query (filter + vector vs vector-only), but **eliminates** the second SQL query
- Net: **faster overall** (one query instead of two)

---

## Approach 4: Two-Phase Funnel with Soft Ranking

### Concept
Separate retrieval into two distinct phases:
1. **Recall Phase:** Broad retrieval — get 200+ candidates using embedding + SQL union (maximize recall)
2. **Ranking Phase:** Score all candidates by a weighted combination of filter match signals + semantic similarity (maximize precision)
3. **Display Phase:** Show top 5-10 to user

Hard filters (allergies, access control) are applied in Phase 1.
Soft filters (tags, budget, servings, cuisine) become **ranking signals** in Phase 2 — they boost scores but don't eliminate.

### How It Works
```python
# Phase 1: Broad recall
candidates = embedding_search(query, top_k=100)  # Semantic relevance
candidates += sql_search(filters, limit=100)       # Filter relevance
candidates = deduplicate(candidates)               # ~150 unique

# Phase 2: Weighted scoring
for recipe in candidates:
    score = 0.0
    score += 0.4 * semantic_similarity(recipe)     # How relevant semantically
    score += 0.2 * budget_match(recipe, budget)     # How close to budget
    score += 0.15 * tag_match(recipe, tags)         # Has matching tags
    score += 0.1 * servings_match(recipe, servings) # Servings match
    score += 0.1 * user_preference(recipe, user)    # User likes/created
    score += 0.05 * freshness(recipe)               # Recently created

# Phase 3: Display top 5
results = sorted(candidates, key=lambda r: r.score, reverse=True)[:5]
```

### Who Does This
- **LinkedIn Job Search:** Retrieves hundreds of candidates via inverted index, then a learned ranking model scores by relevance, recency, and applicant fit.
- **Airbnb Search:** Recall phase uses geographic + availability filters; ranking phase uses ML model with 100+ features (price sensitivity, host quality, listing freshness).
- **YouTube:** Two-tower model for candidate generation (hundreds), then a separate ranking model for final ordering.
- **Twitter/X Feed:** Candidate generation → heavy ranking model → diversity injection → display.

### Scoring Example
```
Recipe: "Chocolate Lava Cake"
  semantic_sim = 0.85 (query: "dessert recipes")
  budget: $180 (budget: $200) → 0.90
  tag_match: has "dessert" tag → 1.0
  servings: 4 (wanted: 2) → 0.5
  user_liked: no → 0.0
  TOTAL: 0.4*0.85 + 0.2*0.90 + 0.15*1.0 + 0.1*0.5 + 0.1*0 + 0.05*0.5
       = 0.34 + 0.18 + 0.15 + 0.05 + 0 + 0.025
       = 0.745

Recipe: "Quick Vanilla Pudding"
  semantic_sim = 0.72
  budget: $50 → 0.25 (far from budget)
  tag_match: has "dessert" → 1.0
  servings: 2 → 1.0
  TOTAL: 0.4*0.72 + 0.2*0.25 + 0.15*1.0 + 0.1*1.0 + 0.1*0 + 0.05*0.5
       = 0.288 + 0.05 + 0.15 + 0.1 + 0 + 0.025
       = 0.613
```

Chocolate Lava Cake ranks higher despite worse servings match, because of stronger semantic and budget fit.

### Pros
- Most sophisticated and flexible
- Soft filters never cause zero results
- Easy to tune ranking weights over time
- Can incorporate user preferences and A/B testing
- Industry-proven pattern

### Cons
- Most complex to implement
- Requires a scoring function that understands all filter types
- Need to fetch recipe metadata (price, servings, tags) for scoring — may need batch lookups
- Requires careful weight tuning

### Cost Impact
- Higher initial cost (broader retrieval)
- But eliminates "no results" scenarios entirely
- Long-term: most scalable approach

---

## Approach 5: Cumulative Session Pool (Growing Pool)

### Concept
Each new search **adds** to the session's candidate pool instead of replacing it. Over multiple turns, the pool grows, giving more candidates to filter from.

### How It Works
```
Q1: "desserts"         → embed → pool = {50 dessert recipes}
Q2: "under $200"       → SQL-only on pool → 12 results
                         ALSO: SQL-only for "under $200" (no embedding)
                         → 30 more cheap recipes → pool = {80 recipes total}
Q3: "for 2 people"     → filter pool of 80 → 15 results
Q4: "no nuts"           → filter pool of 80 → 12 results (hard filter applied)
```

Each step both filters AND expands. The pool is a union of all relevant retrievals.

### Who Does This
- **Shopify Chat Assistants:** Product pool grows as conversation continues — each mention of a product category adds to the consideration set.
- **E-commerce recommendation engines:** "Session-based recommendations" treat the whole browsing session as context, accumulating signals.
- **Conversational search engines (Perplexity, You.com):** Each follow-up adds documents to the context window rather than replacing.

### Pros
- Pool only grows, never shrinks below a useful size
- Natural accumulation of context
- Works well with "show more" (larger pool to draw from)
- Simple mental model

### Cons
- Pool can grow large if conversation is long (memory/performance)
- Need deduplication logic
- Older candidates may become less relevant as conversation evolves
- Need to decide: when does a query represent a "new topic" vs. "refinement"?

### Cost Impact
- May result in additional embedding/SQL queries per turn (to grow the pool)
- Memory grows linearly with conversation length
- Deduplication adds minor CPU overhead

---

## Approach 6: Re-Embed with Composite Query (Context-Aware Embedding)

### Concept
When filters accumulate, **rebuild the embedding query** from scratch to include the full accumulated context. Instead of embedding "desserts" and then filtering by budget, embed "budget-friendly dessert recipes for 2 people."

### How It Works
```
Q1: "desserts"
    → embed("dessert recipes")
    → 20 candidates, show 5

Q2: "my budget is $200"
    → Reconstruct full query: "dessert recipes under $200"
    → RE-embed("dessert recipes under $200")
    → 20 NEW candidates (all semantically aligned with both concepts)
    → SQL applies budget filter → most match

Q3: "for 2 people"
    → Reconstruct: "dessert recipes under $200 for 2 people"
    → RE-embed with full context
    → 20 candidates that semantically match all 3 concepts
```

### Who Does This
- **ChatGPT (retrieval-augmented generation):** Reformulates the full search query each turn based on conversation history — doesn't just pass the latest user message.
- **Perplexity AI:** Constructs a synthesized search query from multi-turn context before hitting their search index.
- **Google Bard/Gemini:** Query expansion and reformulation on each turn, incorporating prior context.
- **Microsoft Copilot:** Uses the full conversation context to generate retrieval queries for Bing.

### Query Reconstruction Examples
```
Turn 1: User: "desserts"           → Embed: "dessert recipes"
Turn 2: User: "under $200"         → Embed: "affordable dessert recipes under $200"
Turn 3: User: "no nuts"            → Embed: "affordable nut-free dessert recipes under $200"
Turn 4: User: "for 2 people"       → Embed: "affordable nut-free dessert recipes under $200 for 2 people"
Turn 5: User: "something Italian"  → Embed: "affordable Italian nut-free dessert recipes under $200 for 2 people"
```

### Pros
- Embedding itself captures nuanced intent (budget-friendly desserts surface naturally)
- Each turn gets fresh, contextually aligned candidates
- Works well when recipe descriptions mention pricing, portions, etc.
- Clean architecture — each turn is a self-contained search

### Cons
- One embedding API call per turn (higher cost)
- Loses the cached candidates from previous turns
- Query reconstruction logic can be complex (what to include, what to drop)
- Embedding quality degrades with very long composite queries
- Structural filters (exact price ≤ $200) may not be captured well by embeddings alone — still needs SQL

### Cost Impact
- **Highest embedding cost** — one API call per turn
- But often produces the best semantic alignment
- Still needs SQL for hard filters (price, servings are exact constraints)

---

## Comparison Matrix

| Approach | Implementation Effort | Result Quality | Cost | Eliminates Zero Results? |
|----------|----------------------|---------------|------|--------------------------|
| 1. Larger Pool | **Low** | Good | Minimal | Reduces significantly |
| 2. Waterfall Fallback | **Medium** | Very Good | Low (only on fallback) | Yes (with relaxation) |
| 3. Pre-Filtered Vector | **Medium** | Excellent | Same or lower | Yes |
| 4. Two-Phase Funnel | **High** | Excellent | Higher | Yes |
| 5. Cumulative Pool | **Medium** | Good | Medium | Reduces significantly |
| 6. Context-Aware Re-Embed | **Medium** | Very Good | Higher (per-turn API) | Reduces significantly |

### Recommended Combination

**Phase 1 (Quick Win):** Implement **Approach 1 + 2** together.
- Approach 1 gives a 5x larger candidate pool with essentially zero cost.
- Approach 2 catches the edge cases where even the larger pool is insufficient.
- Combined, they solve 90%+ of the narrowing problem with minimal architectural change.

**Phase 2 (Future):** Add **Approach 3** (pre-filtered vector search) for SQL-only filter intents that follow an embedding search. This eliminates the "two separate queries" overhead and guarantees all candidates satisfy filters.

---

## Implementation Plan: Approach 1 + 2

### Overview

Approach 1 (Larger Pool) and Approach 2 (Waterfall Fallback) work together as a layered defense:
- **Approach 1:** The "wider net" — fetch 100 candidates instead of 20, giving filters much more room to narrow.
- **Approach 2:** The "safety net" — when even 100 candidates aren't enough, automatically expand or relax filters.

### Current State (What Exists Today)

| Component | Current Value | Location |
|-----------|--------------|----------|
| Default `top_k` | 20 | `retrieval_strategy.py` line 47, and hardcoded in ~8 plan returns (lines 685-735) |
| Allergy `top_k` boost | 50 | `pipeline_orchestrator_sdk.py` lines 944, 1118, 1196-1203 |
| `EMBEDDING_BATCH_SIZE` | 20 | `session_memory_manager.py` line 19 |
| Embedding `limit` | `max(top_k, 10)` | `pipeline_orchestrator_sdk.py` lines 1324, 1403 |
| Cache stores all candidates | Yes | `session_memory_manager.py` `store_embedding_candidates()` |
| Candidate fallback (< 3) | Drops to SQL-only | `pipeline_orchestrator_sdk.py` line 1351 (MIN_CANDIDATES_FOR_RESTRICTION) |
| Show more / negative feedback | Uses cached candidates | `pipeline_orchestrator_sdk.py` lines 4415-4470 |
| No-results handling | Generates "sorry" message | `pipeline_orchestrator_sdk.py` line 2254 |

### Step-by-Step Implementation

---

#### Step 1: Increase Default `top_k` (Approach 1 — Core Change)

**Files to change:**
- `apps/fastapi/src/services/retrieval_strategy.py`
- `apps/fastapi/src/services/session_memory_manager.py`

**Changes:**

1. **`retrieval_strategy.py`** — Change `RetrievalPlan.top_k` default from `20` to `100`:
   - Line 47: `top_k: int = 100`
   - All hardcoded `top_k=20` in plan returns (lines 685, 696, 713, 725) → `top_k=100`
   - The `top_k=30` for re-ranking (line 735) → keep or increase to `100`
   - The allergy boost to `50` → increase to `150` (proportional)

2. **`session_memory_manager.py`** — `EMBEDDING_BATCH_SIZE` stays at `20` (this is the **display** batch size, not the retrieval size). No change needed here.

**Key principle:** Retrieve 100, **display 5**, cache 100. The display limit is controlled by the SQL `LIMIT 20` and the NLG which shows top 5.

---

#### Step 2: Ensure Cache Stores Full Pool (Approach 1 — Cache)

**Files to change:**
- `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

**Changes:**

1. In `HYBRID_VECTOR_TO_SQL` handler (around line 1362-1375):
   - The cache already stores all `candidate_ids` from embedding. With `top_k=100`, it will now cache 100 IDs automatically. **No code change needed** — just confirming the flow works.

2. In the SQL generation step (line 1386-1393):
   - SQL receives all 100 candidate IDs in the `IN (:recipe_ids)` clause
   - SQL applies filters (tags, budget, servings) on these 100
   - SQL's `LIMIT 20` caps the returned results
   - **No code change needed** — existing flow handles this correctly.

**Verification:** The SQL `IN` clause with 100 UUIDs is ~3.7KB — well within PostgreSQL's limits (max query length is ~1GB).

---

#### Step 3: Add Waterfall Fallback on Low Results (Approach 2 — Core)

**Files to change:**
- `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

**Where:** After Stage 8 (SQL Execution), around line 1500, where the code currently checks `execution_result["success"]`.

**New logic to add:**

```
After SQL execution:
  row_count = execution_result.get("row_count", 0)

  IF row_count < FALLBACK_THRESHOLD (e.g., 3):
    AND strategy was HYBRID_VECTOR_TO_SQL:
    AND candidate_ids existed (embedding was used):

    TIER 2: SQL-only fallback (drop embedding restriction)
      - Remove the r."id" IN (:recipe_ids) restriction
      - Keep ALL other filters (budget, servings, allergies)
      - Re-run SQL generation + execution without candidate_ids
      - If this returns results → use them

    TIER 3: Filter relaxation (if Tier 2 still < threshold)
      - Identify soft filters: tags, cuisines, difficulty, servings
      - Remove them from sql_filters (keep allergies, budget, access control)
      - Re-run SQL without soft filters
      - Track which filters were relaxed for NLG messaging
      - If results found → pass relaxation info to NLG so it can say:
        "I couldn't find christmas-tagged recipes in your budget,
         but here are some lunch recipes under $200:"
```

**Implementation detail — Fallback config:**

```python
# Constants for the fallback system
FALLBACK_THRESHOLD = 3           # Trigger fallback if fewer than 3 results
HARD_FILTERS = {"excluded_ingredients", "creator_uid"}  # Never relax these
SOFT_FILTERS_PRIORITY = [        # Relax in this order (least important first)
    "tags",                       # Tags are unreliable anyway
    "difficulty",                 # Minor preference
    "servings",                   # Recipes are scalable
    "cuisines",                   # Preference, not requirement
]
```

---

#### Step 4: Add Relaxation Info to NLG Context (Approach 2 — Messaging)

**Files to change:**
- `apps/fastapi/src/services/pipeline_orchestrator_sdk.py` (Stage 9+10 section)

**Changes:**
- When a fallback was triggered, pass a `relaxed_filters` dict to the NLG agent
- Example: `{"relaxed": ["tags:christmas"], "kept": ["cost:<=200", "allergies:nuts"]}`
- The NLG agent can then generate: "I couldn't find specific Christmas recipes in your budget, but here are some great options under $200 that would work well for a festive meal!"

---

#### Step 5: Update Allergy `top_k` Boost (Approach 1 — Proportional)

**Files to change:**
- `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

**Changes:**
- Lines 944, 1118: change `top_k = 50` → `top_k = 150` (with allergies, we lose many candidates to exclusion, so we need an even wider net)
- Lines 1196-1203: change the boost threshold from `< 50` → `< 150`

---

#### Step 6: Logging and Observability

**Files to change:**
- `apps/fastapi/src/services/pipeline_orchestrator_sdk.py`

**Add logging at key points:**
```
[STAGE 8] ✓ Execution: rows=2 (below threshold 3, triggering fallback)
[FALLBACK TIER 2] SQL-only search (no embedding restriction): rows=12
[FALLBACK TIER 3] Relaxed filters [tags, difficulty]: rows=8
[FALLBACK] Relaxed: tags=christmas, difficulty=easy | Kept: cost<=200, allergies=nuts
```

---

### Testing Scenarios

After implementation, test these multi-turn conversations:

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 1 | Q1: "desserts" → Q2: "under $200" | 100 cached dessert candidates → budget filter on 100 → should get results |
| 2 | Q1: "christmas lunch for 2" | 100 candidates → tags boost christmas → servings=2 filter → enough results |
| 3 | Q1: "pasta" → Q2: "no garlic" → Q3: "no tomato" → Q4: "under 100 kr" | 100 pasta candidates → progressive filtering → should survive 4 filters |
| 4 | Q1: "vegan christmas dessert for 2 under 50 kr" | If < 3 results from 100 candidates → Tier 2 (SQL-only) → Tier 3 (relax tags) |
| 5 | Q1: "recipes by @creator" → Q2: "my budget is 100" | Creator filter + budget on 100 candidates → should get results |
| 6 | Q1: "high protein" → Q2: "under $200" → Q3: "desserts" | SQL-only → SQL-only → embed + carry filters → 100 dessert candidates filtered by protein + budget |

### Rollout Order

1. **Step 1** (increase `top_k`) — do this first, deploy, observe logs for a day
2. **Step 2** (verify cache) — part of Step 1, no separate deploy needed
3. **Step 3** (waterfall fallback) — implement and deploy
4. **Step 4** (NLG messaging) — implement alongside Step 3
5. **Step 5** (allergy boost) — include with Step 1
6. **Step 6** (logging) — include with all steps

### Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Larger pool degrades SQL performance | 100 UUIDs in IN clause is trivial for PostgreSQL. Monitor query times. |
| Waterfall adds latency on fallback | Only triggers when results < 3. Most queries won't hit it. Log frequency. |
| Relaxed filters confuse users | NLG messaging explains what was relaxed. User can re-apply with "clear filters". |
| More embedding results = lower quality tail | Similarity threshold (0.4) already filters low-quality matches. The 100th result may score 0.41 vs 20th at 0.45 — minimal quality difference for recipe search. |
