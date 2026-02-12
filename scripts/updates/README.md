# Data Update Scripts

These scripts are used for **one-time corrections/updates** to existing data in the database.

## 📁 Files

### `update_pricing_units.py`
**Purpose:** Fix pricing units for all ingredients

**What it does:**
- Assigns appropriate measuring units based on ingredient type
- Updates quantities and prices accordingly

**Unit Mappings:**
| Ingredient Type | Unit | Example |
|-----------------|------|---------|
| Liquids (milk, oil) | liter | 1 L |
| Bulk (flour, rice, sugar) | kilogram | 10 kg |
| Pieces (eggs, fruits) | piece | 1 piece |
| Spices/Small items | gram | 100 g |
| Slices (bread, cheese) | slice | 1 slice |

**Usage:**
```bash
python scripts/updates/update_pricing_units.py
```

**Results:**
- Updates all 8,523 pricing records
- Categorizes ingredients by type
- Applies realistic quantities

---

### `update_country_pricing.py`
**Purpose:** Fix country-specific pricing with proper currency conversion

**What it does:**
- Converts prices to local currencies (INR, NOK, USD)
- Applies country-specific pricing multipliers
- Ensures realistic local market prices

**Pricing Strategy:**
- India (INR): Base pricing
- Norway (NOK): ~1/8 of INR price (with cost of living adjustment)
- USA (USD): ~1/83 of INR price (with market adjustment)

**Usage:**
```bash
python scripts/updates/update_country_pricing.py
```

**Results:**
- Updates all 8,523 pricing records
- Proper currency conversion
- Country-appropriate pricing

---

## ⚠️ Important Notes

1. **Run Order:** These update scripts should be run AFTER initial data population
   - First: `populate_ingredients_with_nutrition_and_pricing.py`
   - Then: `update_pricing_units.py`
   - Then: `update_country_pricing.py`

2. **One-Time Use:** These are migration/update scripts, not for daily use

3. **Backup First:** Always backup database before running update scripts

4. **Verify Results:** Check output logs to verify changes

---

## 🔄 When to Re-Run

Re-run these scripts if:
- Initial data import had incorrect units
- Currency conversion rates need updating
- Pricing strategy changes
- After database restore from backup
