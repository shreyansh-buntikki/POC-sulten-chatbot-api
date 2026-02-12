# File Descriptions

This document describes each file in the `updates/` folder.

---

## 📄 Files

### `update_pricing_units.py`
**Type:** Update Script (Re-runnable)
**Purpose:** Assign appropriate measuring units to ingredients based on their type

**What it does:**
- Categorizes ingredients by type (liquid, bulk, piece, slice, spice)
- Updates measuring units accordingly:
  - **Liquids** → liter (milk, cream, oil, water, juice, etc.)
  - **Bulk items** → kilogram (flour, rice, sugar, nuts, beans, pasta, etc.)
  - **Piece items** → piece (eggs, fruits, vegetables, bread, etc.)
  - **Spices** → 100g (spices, powders, extracts, etc.)
- Adjusts quantities and prices to match new units
- Updates all 8,523 pricing records across 3 countries

**Key Classes:**
- `IngredientCategorizer` - Categorizes ingredients by keyword matching
  - `LIQUID_INGREDIENTS` - List of liquid-based keywords
  - `PIECE_INGREDIENTS` - List of items sold by piece
  - `SLICE_INGREDIENTS` - List of items sold by slice
  - `BULK_INGREDIENTS` - List of heavy/bulk items
  - `SPICE_INGREDIENTS` - List of small quantity items
  - `categorize_ingredient(ingredient_name)` - Returns (unit_id, quantity, description)

- `PricingFixer` - Updates pricing data with proper units
  - `get_current_pricing()` - Fetch all pricing data
  - `fix_pricing()` - Update units, quantities, and prices

**Unit Mappings:**
| Category | Unit | Quantity | Examples |
|----------|------|----------|----------|
| Liquid | liter | 1 | milk, cream, oil, water, juice, syrup |
| Bulk | kilogram | 10 | flour, rice, sugar, nuts, beans, pasta |
| Piece | piece | 1 | eggs, fruits, vegetables, bread |
| Slice | slice | 1 | bread slices, cheese slices |
| Spice | gram | 100 | spices, powders, extracts |

**Price Calculation Logic:**
- **Liter**: 10x current price (converting from per 100g, assuming ~1g/ml density)
- **Kilogram**: 10x current price (converting from per 100g)
- **Piece**: 0.5x current price (assuming average piece ~50g)
- **Slice**: 0.3x current price (assuming average slice ~30g)
- **100g**: Same price (no change)

**Usage:**
```bash
python scripts/updates/update_pricing_units.py
```

**What it displays:**
- Current pricing state (before update)
- Sample of ingredients being processed
- Count of updated and skipped records
- Final pricing state (after update)
- Units distribution summary

**Results:**
- Updates all 8,523 pricing records
- Assigns appropriate units based on ingredient type
- Shows distribution of units used

**Requirements:**
- Database connection (configured in `.env`)
- Tables `ingredient`, `ingredient_pricing`, `measuring_unit` must exist
- Measuring units with IDs:
  - `396cab8c-5d3b-49b0-b946-b96a26f84af1` (gram)
  - `45da9c48-2a89-47a9-8d7c-6146a9dde4d9` (kilogram)
  - `3db0f7a7-1b23-4528-be58-9c13b50d5629` (liter)
  - `7d25ed2b-9f5f-4d95-8ee8-4617e756ff68` (piece)
  - `2ccd0924-2772-487e-a908-e4f4ceb2507e` (slice)

**When to Re-run:**
- After adding new ingredients that need proper unit assignment
- If units were incorrectly assigned
- When adding new measuring unit types

---

### `update_country_pricing.py`
**Type:** Update Script (Re-runnable)
**Purpose:** Convert pricing to realistic local currency values for each country

**What it does:**
- Converts pricing from base INR values to realistic local currency amounts
- Uses India pricing as base, calculates Norway and USA prices
- Applies currency conversion with local market adjustments
- Updates all 8,523 pricing records across 3 countries

**Pricing Strategy:**
- **India (INR)**: Base pricing (no change)
- **Norway (NOK)**: ~1/8 of INR with 1.5x cost adjustment for higher local costs
- **USA (USD)**: ~1/83 of INR with 1.2x market adjustment

**Exchange Rate Approximations (2024):**
- 1 USD ≈ 83 INR
- 1 USD ≈ 10.5 NOK
- 1 NOK ≈ 8 INR

**Key Classes:**
- `CountryPricingFixer` - Updates pricing with country-specific values
  - `PRICING_MULTIPLIERS` - Base conversion multipliers
  - `CATEGORY_ADJUSTMENTS` - Local market cost adjustments
  - `get_current_pricing()` - Fetch current pricing grouped by ingredient and country
  - `fix_country_pricing()` - Update prices for all countries

**Pricing Formula:**
```
India: new_price = base_price_india
Norway: new_price = base_price_india * 0.125 * 1.5
USA: new_price = base_price_india * 0.012 * 1.2
```

**Usage:**
```bash
python scripts/updates/update_country_pricing.py
```

**What it displays:**
- Sample pricing before update (for common ingredients)
- Processing progress for all ingredients
- Sample pricing after update (for common ingredients)
- Extended samples for each country (20 ingredients each)
- Total records updated

**Example Output:**
```
Sample updated pricing (after fix):
  egg           IND  INR    1  piece     =    6.00
  egg           NOR  NOK    1  piece     =    0.94
  egg           USA  USD    1  piece     =    0.09
```

**Results:**
- Updates all 8,523 pricing records
- Realistic pricing for each country's market
- Proper currency conversion

**Requirements:**
- Database connection (configured in `.env`)
- Tables `ingredient`, `ingredient_pricing`, `country`, `currency`, `measuring_unit` must exist
- Country records for India, Norway, USA
- Currency records for INR, NOK, USD

**When to Re-run:**
- After changing currency exchange rates
- If pricing needs market adjustment
- After adding new countries

---

### `remove_seasonality_types.py`
**Type:** Update Script (Re-runnable)
**Purpose:** Remove specific seasonality types and all related data

**What it does:**
- Removes seasonality types and all their related data
- Deletes in correct order due to foreign key constraints:
  1. Recipe-seasonality mappings
  2. Seasonality translations
  3. Seasonality entries
- Shows state before and after deletion
- Requires confirmation before proceeding

**Types Configured for Removal:**
- `HEALTH_CYCLE` - Immunity Boosting, Summer Hydration, Winter Nourishment, etc.
- `MEAL_TIMING` - Breakfast, Brunch, Lunch, Dinner, Late Night, Snack
- `DIETARY_PRACTICE` - Ramadan, Lent, Navratri Fasting, Vegan
- `CULTURAL_OCCASION` - Wedding, Birthday, Potluck, Picnic
- `INGREDIENT_AVAILABILITY` - Strawberry Season, Asparagus Season, etc.
- `LIFESTYLE` - Comfort Food, Party Food, Detox, Cozy, Outdoor, Quick & Easy
- `REGIONAL` - Nordic, Indian, Italian, Asian, Mexican, Middle Eastern

**Types Kept:**
- `WEATHER` - Winter, Summer, Spring, Autumn, Monsoon
- `FESTIVAL` - Christmas, Diwali, Easter, Thanksgiving, New Year, Eid, Holi

**Usage:**
```bash
python scripts/updates/remove_seasonality_types.py
```

**What it displays:**
- Current state before deletion (count by type)
- Number of seasonalities to be removed by type
- Confirmation prompt
- Progress of deletion (mappings, translations, entries)
- Final state after deletion
- List of remaining seasonalities

**Example Output:**
```
=== CURRENT STATE ===
Type                      Seasonalities   Recipes    Mappings
------------------------------------------------------------
CULTURAL_OCCASION         4               91         91         [TO DELETE]
DIETARY_PRACTICE          4               196        197        [TO DELETE]
FESTIVAL                  7               163        166
WEATHER                   5               579        775

=== REMOVING SEASONALITY TYPES ===
Found 36 seasonalities to remove
Removing...
  ✓ Removed 2194 recipe-seasonality mappings
  ✓ Removed 72 translations
  ✓ Removed 36 seasonality entries
```

**Results:**
- Removes 2,194 recipe-seasonality mappings
- Removes 72 translations (36 seasonalities × 2 languages)
- Removes 36 seasonality entries
- Leaves only WEATHER (5) and FESTIVAL (7) seasonalities

**Key Classes:**
- `SeasonalityTypeRemover` - Handles the removal process
  - `show_current_state()` - Shows state before deletion
  - `get_seasonalities_to_remove()` - Gets IDs of seasonalities to remove
  - `remove_recipe_mappings(seasonality_ids)` - Removes mappings
  - `remove_translations(seasonality_ids)` - Removes translations
  - `remove_seasonalities()` - Removes seasonality entries
  - `show_final_state()` - Shows state after deletion

**Requirements:**
- Database connection (configured in `.env`)
- Tables `seasonality`, `seasonality_translation`, `recipe_seasonality` must exist
- Seasonalities must exist (created by `create_seasonalities.py`)

**When to Re-run:**
- If you need to remove additional seasonality types (modify `TYPES_TO_REMOVE` in the script)
- After adding new seasonality types that need to be removed

---

## 🔄 Execution Order

Run these scripts in this order after initial data population:

```bash
# 1. First, assign proper units to ingredients
python scripts/updates/update_pricing_units.py

# 2. Then, convert to realistic country-specific pricing
python scripts/updates/update_country_pricing.py

# 3. Optionally, remove unwanted seasonality types (if needed)
python scripts/updates/remove_seasonality_types.py
```

**Note:** The order matters for pricing scripts because `update_country_pricing.py` assumes ingredients have proper units assigned by `update_pricing_units.py`. The seasonality removal script can be run independently at any time.

---

## 📊 Summary Table

| File | Re-runnable | Depends On | Updates | Time |
|------|-------------|------------|---------|------|
| `update_pricing_units.py` | ✅ | Pricing records exist | Units, quantities, prices | <5 min |
| `update_country_pricing.py` | ✅ | Units assigned | Country-specific prices | <5 min |
| `remove_seasonality_types.py` | ✅ | Seasonalities exist | Removes seasonality types | <1 min |

---

## 🔧 Technical Notes

### PostgreSQL Column Names
Both scripts use double quotes for mixed-case column names:
```sql
SELECT ip."pricePerUnit", ip.quantity, ip."measuringUnitId"
FROM ingredient_pricing ip
```

### Grouping by Ingredient
Both scripts group pricing data by ingredient to ensure:
- All countries for the same ingredient are processed together
- Pricing consistency across countries
- Efficient batch updates with commits per ingredient

### Error Handling
- Logs warnings for ingredients without India pricing (used as base)
- Skips ingredients with missing data
- Commits changes after each ingredient to avoid large rollbacks
- Handles invalid multipliers by applying minimum price logic

---

## 🎯 When to Use These Scripts

### Use `update_pricing_units.py` when:
- Initial data population used incorrect units (all 100g)
- New ingredients were added without unit assignment
- You want to standardize units across similar ingredients

### Use `update_country_pricing.py` when:
- Initial pricing was identical across all countries (e.g., 80 INR, 80 NOK, 80 USD)
- Currency exchange rates have changed significantly
- You want to adjust for local market conditions
- Adding new countries to the pricing system

---

## 📈 Expected Results

After running both scripts:
- All ingredients will have appropriate units (liter, kg, piece, gram)
- Pricing will be realistic for each country's market
- India prices in INR (₹) - typical range: ₹10-500 per unit
- Norway prices in NOK (kr) - typical range: kr1-100 per unit
- USA prices in USD ($) - typical range: $0.10-10 per unit
