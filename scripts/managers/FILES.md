# File Descriptions

This document describes each file in the `managers/` folder.

---

## 📄 Files

### `nutrition_manager.py`
**Type:** CLI Tool (Interactive)
**Purpose:** Manage ingredient nutrition data (macros and micros)

**What it does:**
- Provides interactive command-line interface for managing nutrition data
- View, add, and update macronutrients (protein, carbs, fat, fiber, energy, etc.)
- View, add, and update micronutrients (vitamins A, C, D, E, K, B-complex, minerals)
- List and search ingredients
- View nutrition statistics and coverage

**Key Commands:**
- `list [filter]` - List ingredients (optionally filter by name)
- `view <name>` - View nutrition data for an ingredient
- `add` - Interactive mode to add/update nutrition data
- `stats` - Show nutrition coverage statistics

**Key Classes:**
- `NutritionManager` - Main manager class with database operations
  - `list_ingredients(filter_name)` - Get list of ingredients
  - `get_ingredient_by_name(name)` - Find ingredient by name
  - `get_nutrition(ingredient_id)` - Get macros and micros
  - `create_macros(...)` - Create/update macronutrients
  - `create_micros(...)` - Create/update micronutrients
  - `get_nutrition_stats()` - Get coverage statistics

**Usage:**
```bash
# Show help
python scripts/managers/nutrition_manager.py help

# List ingredients
python scripts/managers/nutrition_manager.py list tomato

# View nutrition for an ingredient
python scripts/managers/nutrition_manager.py view "Almond Flour"

# Interactive add mode
python scripts/managers/nutrition_manager.py add

# View statistics
python scripts/managers/nutrition_manager.py stats
```

**Interactive Mode:**
The `add` command provides an interactive prompt where you can:
- Search and select ingredients
- Input macro and micro nutrient values
- Update existing nutrition data
- Press Enter to skip optional fields

**Requirements:**
- Database connection (configured in `.env`)
- Tables `ingredient`, `ingredient_macros`, `ingredient_micros` must exist

---

### `pricing_manager.py`
**Type:** CLI Tool (Interactive)
**Purpose:** Manage ingredient pricing across 3 countries (India, Norway, USA)

**What it does:**
- Provides interactive command-line interface for managing pricing data
- View pricing across all countries with currency conversion
- Add or update pricing for specific countries
- Set prices with different units (gram, kg, liter, ml, piece, etc.)
- View pricing statistics by country

**Supported Countries:**
- **India (IND)** - INR (₹)
- **Norway (NOR)** - NOK (kr)
- **USA (USA)** - USD ($)

**Supported Units:**
- gram, kg, liter, ml, piece, slice, tablespoon, teaspoon, cup

**Key Commands:**
- `list [filter]` - List ingredients (optionally filter by name)
- `view <name>` - View pricing for ingredient across all countries
- `add` - Interactive mode to add/update pricing
- `set-price <name> <country> <price>` - Quick price update
- `stats` - Show pricing coverage statistics

**Key Classes:**
- `PricingManager` - Main manager class with database operations
  - `list_ingredients(filter_name)` - Get list of ingredients
  - `get_ingredient_by_name(name)` - Find ingredient by name
  - `get_pricing(ingredient_id)` - Get all pricing by country
  - `create_or_update_pricing(...)` - Create/update pricing
  - `delete_pricing(ingredient_id, country_code)` - Remove pricing
  - `get_pricing_stats()` - Get coverage statistics

**Usage:**
```bash
# Show help
python scripts/managers/pricing_manager.py help

# View pricing for an ingredient
python scripts/managers/pricing_manager.py view "Rice"

# Set price for specific country
python scripts/managers/pricing_manager.py set-price "Rice" india 50

# Interactive add mode
python scripts/managers/pricing_manager.py add

# View statistics
python scripts/managers/pricing_manager.py stats
```

**Interactive Mode:**
The `add` command provides an interactive prompt where you can:
- Search and select ingredients
- Set pricing for each country individually
- Choose appropriate units for each ingredient
- Press Enter to skip a country

**Requirements:**
- Database connection (configured in `.env`)
- Tables `ingredient`, `ingredient_pricing`, `country`, `currency`, `measuring_unit` must exist
- Country and currency records for India, Norway, USA

---

### `seasonality_manager.py`
**Type:** CLI Tool (Interactive)
**Purpose:** Manage recipe seasonalities (map recipes to seasonal categories)

**What it does:**
- Provides interactive command-line interface for managing seasonalities
- View all available seasonalities by type
- Add or remove seasonalities from recipes
- View which seasonalities are assigned to a recipe
- View seasonality coverage statistics

**Seasonality Types:**
- `WEATHER` - Winter, Summer, Spring, Autumn, Monsoon
- `FESTIVAL` - Christmas, Diwali, Easter, Thanksgiving, New Year, Eid, Holi
- `INGREDIENT_AVAILABILITY` - Strawberry Season, Asparagus Season, Mango Season, etc.
- `CULTURAL_OCCASION` - Wedding, Birthday, Potluck, Picnic
- `DIETARY_PRACTICE` - Ramadan, Lent, Navratri Fasting, Vegan
- `MEAL_TIMING` - Breakfast, Brunch, Lunch, Dinner, Late Night, Snack
- `LIFESTYLE` - Comfort Food, Party Food, Detox, Cozy, Outdoor, Quick & Easy
- `REGIONAL` - Nordic, Indian, Italian, Asian, Mexican, Middle Eastern
- `HEALTH_CYCLE` - Immunity Boosting, Summer Hydration, Winter Nourishment, etc.

**Key Commands:**
- `list-seasonalities [type]` - List all seasonalities (optionally filter by type)
- `list-recipes [filter]` - List recipes (optionally filter by name)
- `view <recipe_name>` - View seasonalities assigned to a recipe
- `add` - Interactive mode to add seasonalities to recipes
- `remove <recipe> <seasonality>` - Remove a seasonality from a recipe
- `stats` - Show seasonality coverage statistics

**Key Classes:**
- `SeasonalityManager` - Main manager class with database operations
  - `list_seasonalities(type_filter)` - Get all seasonalities
  - `list_recipes(filter_name)` - Get list of recipes
  - `get_recipe_by_name(name)` - Find recipe by name
  - `get_seasonality_by_name(name)` - Find seasonality by name
  - `get_recipe_seasonalities(recipe_id)` - Get all seasonalities for a recipe
  - `add_seasonality_to_recipe(recipe_id, seasonality_id)` - Add mapping
  - `remove_seasonality_from_recipe(recipe_id, seasonality_id)` - Remove mapping
  - `get_stats()` - Get coverage statistics

**Usage:**
```bash
# Show help
python scripts/managers/seasonality_manager.py help

# List all seasonalities
python scripts/managers/seasonality_manager.py list-seasonalities

# List seasonalities of a specific type
python scripts/managers/seasonality_manager.py list-seasonalities WEATHER

# View seasonalities for a recipe
python scripts/managers/seasonality_manager.py view "Pumpkin Soup"

# Interactive add mode
python scripts/managers/seasonality_manager.py add

# Remove a seasonality
python scripts/managers/seasonality_manager.py remove "Pizza" Winter

# View statistics
python scripts/managers/seasonality_manager.py stats
```

**Interactive Mode:**
The `add` command provides an interactive prompt where you can:
- Search and select recipes
- View existing seasonalities
- Add multiple seasonalities in one session
- Enter seasonality by name or type
- Type 'done' to finish

**Requirements:**
- Database connection (configured in `.env`)
- Tables `recipe`, `seasonality`, `seasonality_translation`, `recipe_seasonality` must exist
- Seasonalities must be created first (run `create_seasonalities.py` from initial_data_fetching/)

---

## 🔄 Comparison Table

| File | Purpose | Data Type | Countries | Interactive |
|------|---------|-----------|-----------|-------------|
| `nutrition_manager.py` | Nutrition data | Macros + Micros | - | ✅ |
| `pricing_manager.py` | Pricing data | Prices | India, Norway, USA | ✅ |
| `seasonality_manager.py` | Seasonality mapping | Categories | - | ✅ |

---

## 📊 Summary Table

| File | Command-based | Interactive | CRUD | Stats | Search |
|------|---------------|-------------|------|-------|--------|
| `nutrition_manager.py` | ✅ | ✅ | Create, Read, Update | ✅ | ✅ |
| `pricing_manager.py` | ✅ | ✅ | Create, Read, Update, Delete | ✅ | ✅ |
| `seasonality_manager.py` | ✅ | ✅ | Create, Read, Delete | ✅ | ✅ |

---

## 🎯 Common Features

All manager scripts include:
- **Help command** - Type `help` or `--help` to see usage
- **List command** - Browse items with optional filtering
- **View command** - See detailed data for a specific item
- **Stats command** - View coverage and usage statistics
- **Interactive mode** - User-friendly prompts for data entry
- **Search functionality** - Find items by name
- **Pretty printing** - Formatted output with tables and borders
