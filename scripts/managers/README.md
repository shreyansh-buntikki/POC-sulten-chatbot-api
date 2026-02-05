# Manager CLI Tools

Interactive command-line tools for **ongoing management** of ingredient nutrition, pricing, and recipe seasonalities.

## 📁 Files

### `nutrition_manager.py`
**Purpose:** Manage ingredient nutrition data (macros & micros)

**Commands:**
```bash
# List all ingredients (optionally filter)
python scripts/managers/nutrition_manager.py list [filter]

# View nutrition data for an ingredient
python scripts/managers/nutrition_manager.py view "Tomato"

# Interactive mode to add/update nutrition
python scripts/managers/nutrition_manager.py add

# Show coverage statistics
python scripts/managers/nutrition_manager.py stats
```

**Example Output:**
```
INGREDIENT: Almond Flour
==================================================
  ╔══ MACRONUTRIENTS (per 100g serving)
  ║  Energy:     579 kcal / 2424 kJ
  ║  Protein:    21.2g
  ║  Carbs:      21.6g
  ║  Fat:        49.9g
  ╚════════════════════════════════════════
```

---

### `pricing_manager.py`
**Purpose:** Manage ingredient pricing across 3 countries (India, Norway, USA)

**Commands:**
```bash
# List ingredients
python scripts/managers/pricing_manager.py list [filter]

# View pricing across all countries
python scripts/managers/pricing_manager.py view "Almond Flour"

# Interactive mode to add pricing
python scripts/managers/pricing_manager.py add

# Quick price update for specific country
python scripts/managers/pricing_manager.py set-price rice india 50

# Show statistics
python scripts/managers/pricing_manager.py stats
```

**Supported Countries:** `india`, `norway`, `usa`

**Supported Units:** `gram`, `kg`, `liter`, `ml`, `piece`, `slice`, `tablespoon`, `teaspoon`, `cup`

**Example Output:**
```
INGREDIENT: Almond Flour
======================================================================
  ╔═══════════════════════════════════════════════════════════════╗
  ║  India       (IND): ₹500.00 per 10 kg                         ║
  ║  Norway      (NOR): kr 93.75 per 10 kg                         ║
  ║  USA         (USD): $7.20 per 10 kg                            ║
  ╚═══════════════════════════════════════════════════════════════╝
```

---

### `seasonality_manager.py`
**Purpose:** Manage seasonalities mapped to recipes

**Commands:**
```bash
# List all seasonalities
python scripts/managers/seasonality_manager.py list-seasonalities

# List seasonalities by type
python scripts/managers/seasonality_manager.py list-seasonalities WEATHER

# List recipes
python scripts/managers/seasonality_manager.py list-recipes [filter]

# View seasonalities for a recipe
python scripts/managers/seasonality_manager.py view "Pumpkin Soup"

# Interactive mode to add seasonalities
python scripts/managers/seasonality_manager.py add

# Remove a seasonality from a recipe
python scripts/managers/seasonality_manager.py remove "Pizza" Winter

# Show statistics
python scripts/managers/seasonality_manager.py stats
```

**Seasonality Types:** `WEATHER`, `FESTIVAL`, `INGREDIENT_AVAILABILITY`, `CULTURAL_OCCASION`, `DIETARY_PRACTICE`, `MEAL_TIMING`, `LIFESTYLE`, `REGIONAL`, `HEALTH_CYCLE`

**Example Output:**
```
📋 Pumpkin Soup
  ============================================================

  [WEATHER]
     • Autumn
     • Winter

  [INGREDIENT_AVAILABILITY]
     • Pumpkin Season

  [LIFESTYLE]
     • Comfort Food
```

---

## 🚀 Common Workflows

### Add a New Ingredient with Complete Data

```bash
# 1. Add/verify ingredient exists (handled by your system)

# 2. Add nutrition data
python scripts/managers/nutrition_manager.py add

# 3. Add pricing for 3 countries
python scripts/managers/pricing_manager.py add

# 4. For recipes using this ingredient, update seasonalities if needed
python scripts/managers/seasonality_manager.py add
```

### Update Pricing for an Ingredient

```bash
# View current pricing
python scripts/managers/pricing_manager.py view "Rice"

# Update specific country price
python scripts/managers/pricing_manager.py set-price Rice india 45
python scripts/managers/pricing_manager.py set-price Rice norway 85
python scripts/managers/pricing_manager.py set-price Rice usa 6.50
```

### Check Data Coverage

```bash
# Nutrition coverage
python scripts/managers/nutrition_manager.py stats

# Pricing coverage
python scripts/managers/pricing_manager.py stats

# Seasonality coverage
python scripts/managers/seasonality_manager.py stats
```

---

## 💡 Tips

1. **Interactive Mode** - Use the `add` command without arguments for guided input
2. **Search First** - Use `list` with a filter to find exact ingredient/recipe names
3. **Batch Updates** - Manager scripts handle one item at a time for precision
4. **Validation** - All scripts validate input and show clear error messages

---

## 📊 Current Statistics

```
Nutrition:  1,698 / 2,841 ingredients (59.8%)
Pricing:    2,841 / 2,841 ingredients (100%)
Seasonality:  1,750 / 3,298 recipes (53.1%)
```
