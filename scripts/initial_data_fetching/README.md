# Initial Data Fetching Scripts

These scripts are used for **one-time initial population** of the database with nutrition, pricing, and seasonality data.

## 📁 Files

### `nutrition_api_client.py`
**Purpose:** API client library for fetching nutrition data from external sources

**Features:**
- `USDAFoodDataClient` - Queries USDA FoodData Central API
- `OpenFoodFactsClient` - Queries Open Food Facts API (fallback)
- `IngredientCategorizer` - Categorizes ingredients by type
- `PricingEstimator` - Estimates pricing for 3 countries

**Usage:** Not run directly - imported by other scripts

---

### `populate_ingredients_with_nutrition_and_pricing.py`
**Purpose:** Main script to populate ALL ingredients with nutrition & pricing data

**What it does:**
- Processes all 2,841 ingredients
- Fetches macro nutrients (protein, carbs, fat, fiber, etc.)
- Fetches micro nutrients (vitamins, minerals)
- Generates pricing for India, Norway, USA
- Handles Norwegian → English translations

**Usage:**
```bash
# From project root
python scripts/initial_data_fetching/populate_ingredients_with_nutrition_and_pricing.py
```

**Requirements:**
- USDA API key in environment variables
- Internet connection for API calls
- Takes ~30-60 minutes to process all ingredients

**Results:**
- ~1,700 ingredients with macro data
- ~1,700 ingredients with micro data
- 8,523 pricing records (2,841 × 3 countries)

---

### `create_seasonalities.py`
**Purpose:** Creates seasonality categories in the database

**What it does:**
- Creates 48 seasonality entries across 9 categories
- Adds English and Norwegian translations

**Seasonality Categories:**
- Weather (Winter, Summer, Spring, Autumn, Monsoon)
- Festivals (Christmas, Diwali, Easter, Thanksgiving, etc.)
- Ingredient Availability (Strawberry Season, Mango Season, etc.)
- Cultural Occasions (Wedding, Birthday, Potluck, Picnic)
- Dietary Practices (Vegan, Ramadan, Lent, Navratri)
- Meal Timing (Breakfast, Brunch, Lunch, Dinner, Snack)
- Lifestyle (Comfort Food, Party Food, Detox, Cozy, Outdoor)
- Regional (Italian, Mexican, Indian, Asian, etc.)
- Health Cycle (Immunity Boosting, Gut Health, etc.)

**Usage:**
```bash
python scripts/initial_data_fetching/create_seasonalities.py
```

**Results:** 48 seasonalities with 96 translations

---

### `map_recipes_to_seasonalities.py`
**Purpose:** Automatically maps recipes to seasonalities using keyword matching

**What it does:**
- Analyzes 3,298 published recipe names
- Matches keywords to assign seasonalities
- Creates recipe-seasonality mappings

**Usage:**
```bash
python scripts/initial_data_fetching/map_recipes_to_seasonalities.py
```

**Results:**
- 1,750 recipes mapped (53%)
- 3,135 total mappings created
- Average 1.8 seasonalities per mapped recipe

---

## 🚀 Quick Start

To populate all initial data:

```bash
# 1. Create seasonalities first
python scripts/initial_data_fetching/create_seasonalities.py

# 2. Populate ingredients with nutrition and pricing (takes time)
python scripts/initial_data_fetching/populate_ingredients_with_nutrition_and_pricing.py

# 3. Map recipes to seasonalities
python scripts/initial_data_fetching/map_recipes_to_seasonalities.py
```

---

## ⚠️ Notes

- These scripts are **one-time use** for initial data population
- They fetch data from external APIs (USDA, Open Food Facts)
- Run them in order as shown above
- For ongoing data management, use the scripts in `../managers/`
