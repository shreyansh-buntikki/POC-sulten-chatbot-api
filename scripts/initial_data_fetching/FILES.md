# File Descriptions

This document describes each file in the `initial_data_fetching/` folder.

---

## 📄 Files

### `nutrition_api_client.py`
**Type:** Helper Module (Library)
**Purpose:** API client for fetching nutrition data from external sources

**What it does:**
- Provides `USDAFoodDataClient` class for querying USDA FoodData Central API
- Provides `OpenFoodFactsClient` class for querying Open Food Facts API (fallback)
- Provides `IngredientCategorizer` for categorizing ingredients into types (liquid, bulk, piece, spice)
- Provides `PricingEstimator` for estimating pricing in India, Norway, USA
- Contains `COUNTRY_IDS` and `CURRENCY_IDS` constants for database references

**Key Functions:**
- `search_food(query, session)` - Search for food items by name
- `get_nutrition_data(query, session)` - Get detailed nutrition info
- `estimate_pricing(ingredient_name, ingredient_id)` - Generate price estimates
- `get_pricing_data(ingredient_name, ingredient_id)` - Get all pricing data for storage

**Dependencies:**
- `aiohttp` - Async HTTP client for API calls
- `asyncio` - Async support
- USDA API key (environment variable)

**Usage:** Not run directly - imported by `populate_ingredients_with_nutrition_and_pricing.py`

---

### `populate_ingredients_with_nutrition_and_pricing.py`
**Type:** Data Population Script (One-time use)
**Purpose:** Main script to populate ALL ingredients with nutrition & pricing data

**What it does:**
- Processes all 2,841 ingredients from the database
- For each ingredient:
  1. Translates Norwegian name to English for API search
  2. Fetches macro nutrients (protein, carbs, fat, fiber, energy) from APIs
  3. Fetches micro nutrients (vitamins A, C, D, E, K, B-complex, minerals)
  4. Generates pricing for India, Norway, USA based on category
  5. Creates `IngredientMacros`, `IngredientMicros`, and `IngredientPricing` records

**Key Features:**
- Batch processing (30 ingredients at a time)
- Progress bar with `tqdm`
- Error handling and logging
- Norwegian to English translation mapping (400+ entries)
- Rate limiting between API calls (0.1-0.3s delay)
- Runs asynchronously for performance

**Usage:**
```bash
python scripts/initial_data_fetching/populate_ingredients_with_nutrition_and_pricing.py
```

**Time:** Takes ~30-60 minutes for all 2,841 ingredients

**Results:**
- ~1,698 ingredients with macro data
- ~1,698 ingredients with micro data
- 8,523 pricing records (2,841 × 3 countries)
- 160 ingredients skipped (no API data available)

**Requirements:**
- USDA API key in environment variable `USDA_API_KEY`
- Internet connection for API calls
- Database connection (configured in `.env`)

---

### `create_seasonalities.py`
**Type:** Data Population Script (One-time use)
**Purpose:** Creates seasonality categories with translations

**What it does:**
- Creates seasonality entries for WEATHER and FESTIVAL categories
- Adds English and Norwegian translations for each seasonality
- Stores in `seasonality` and `seasonality_translation` tables

**Seasonality Categories Created:**

| Type | Seasonalities |
|------|---------------|
| WEATHER | Winter, Summer, Spring, Autumn, Monsoon |
| FESTIVAL | Christmas, Diwali, Easter, Thanksgiving, New Year, Eid, Holi |

> **Note:** The original script created 9 categories (48 seasonalities). Additional categories have been removed, keeping only WEATHER and FESTIVAL.

**Usage:**
```bash
python scripts/initial_data_fetching/create_seasonalities.py
```

**Results:** 12 seasonalities with 24 translations (after removal)

**Requirements:**
- Database connection
- Tables `seasonality` and `seasonality_translation` must exist

---

### `map_recipes_to_seasonalities.py`
**Type:** Data Population Script (One-time use)
**Purpose:** Automatically maps recipes to seasonalities using intelligent keyword matching

**What it does:**
- Analyzes published recipe names
- Uses keyword matching to assign appropriate seasonalities
- Creates entries in `recipe_seasonality` table

**Keyword Matching Examples:**
- "Pumpkin" → Autumn
- "Christmas" → Christmas
- "Soup" → Winter
- "Grill" → Summer
- "Sun" → Summer

**Key Features:**
- `RecipeSeasonalityMapper` class with intelligent categorization
- Seasonality type detection (WEATHER, FESTIVAL)
- Keyword lists for each seasonality type
- Handles multi-language recipe names (Norwegian, English)

**Usage:**
```bash
python scripts/initial_data_fetching/map_recipes_to_seasonalities.py
```

**Results:** (After type reduction)
- ~579 recipes mapped to WEATHER seasonalities (775 mappings)
- ~163 recipes mapped to FESTIVAL seasonalities (166 mappings)
- Total: ~941 mappings across 579 recipes

**Requirements:**
- Database connection
- Recipes must exist in `recipe` table
- Seasonalities must exist (run `create_seasonalities.py` first)

---

## 🔄 Execution Order

Run these scripts in this order:

```bash
# 1. Create seasonalities first (required by mapping script)
python scripts/initial_data_fetching/create_seasonalities.py

# 2. Populate ingredients (can run independently)
python scripts/initial_data_fetching/populate_ingredients_with_nutrition_and_pricing.py

# 3. Map recipes (requires seasonalities to exist)
python scripts/initial_data_fetching/map_recipes_to_seasonalities.py
```

---

## 📊 Summary Table

| File | One-Time Use | Depends On | Produces | Time |
|------|--------------|------------|----------|------|
| `nutrition_api_client.py` | ❌ (library) | - | - | - |
| `populate_ingredients_with_nutrition_and_pricing.py` | ✅ | None | Nutrition + Pricing data | 30-60 min |
| `create_seasonalities.py` | ✅ | None | Seasonalities + Translations | <1 min |
| `map_recipes_to_seasonalities.py` | ✅ | Seasonalities | Recipe-Seasonality mappings | <1 min |
