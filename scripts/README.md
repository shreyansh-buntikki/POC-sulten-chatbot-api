# Scripts Directory

This directory contains all scripts for data population, management, and updates for the Sulten database.

## 📂 Directory Structure

```
scripts/
├── initial_data_fetching/    # One-time scripts for initial data population
├── managers/                   # CLI tools for ongoing data management
├── updates/                    # One-time scripts for data corrections/updates
└── README.md                   # This file
```

---

## 📁 Quick Reference

### `initial_data_fetching/`
Use these scripts for **one-time initial setup** of the database.

| Script | Purpose | Run Once? |
|--------|---------|-----------|
| `create_seasonalities.py` | Create seasonality categories | ✅ |
| `populate_ingredients_with_nutrition_and_pricing.py` | Populate all ingredients with nutrition & pricing data | ✅ |
| `map_recipes_to_seasonalities.py` | Auto-map recipes to seasonalities | ✅ |
| `nutrition_api_client.py` | API client library (helper) | - |

**When to use:**
- Initial database setup
- After database migration
- Re-populating all data from scratch

---

### `managers/`
Use these CLI tools for **ongoing daily management** of data.

| Script | Purpose | Run Regularly? |
|--------|---------|----------------|
| `nutrition_manager.py` | Manage ingredient nutrition data | ✅ |
| `pricing_manager.py` | Manage ingredient pricing (3 countries) | ✅ |
| `seasonality_manager.py` | Manage recipe seasonalities | ✅ |

**When to use:**
- Adding new ingredient nutrition data
- Updating ingredient prices
- Adding/removing seasonalities from recipes
- Checking data coverage statistics

---

### `updates/`
Use these scripts for **one-time data corrections**.

| Script | Purpose | Run Once? |
|--------|---------|-----------|
| `update_pricing_units.py` | Fix measuring units for pricing | ✅ |
| `update_country_pricing.py` | Fix country-specific currency conversion | ✅ |
| `remove_seasonality_types.py` | Remove unwanted seasonality types | ✅ |

**When to use:**
- After initial data import to fix units
- When pricing strategy changes
- To correct currency conversion issues
- To remove unwanted seasonality categories

---

## 🚀 Getting Started

### First Time Setup (New Database)

```bash
# 1. Create seasonalities (WEATHER and FESTIVAL types)
python scripts/initial_data_fetching/create_seasonalities.py

# 2. Populate ingredients with nutrition & pricing (takes 30-60 min)
python scripts/initial_data_fetching/populate_ingredients_with_nutrition_and_pricing.py

# 3. Fix pricing units
python scripts/updates/update_pricing_units.py

# 4. Fix country pricing
python scripts/updates/update_country_pricing.py

# 5. Map recipes to seasonalities
python scripts/initial_data_fetching/map_recipes_to_seasonalities.py

# Optional: Remove unwanted seasonality types
python scripts/updates/remove_seasonality_types.py
```

### Daily Operations

```bash
# Add nutrition data for new ingredient
python scripts/managers/nutrition_manager.py add

# Update pricing for an ingredient
python scripts/managers/pricing_manager.py set-price Tomato india 30

# Add seasonalities to a recipe
python scripts/managers/seasonality_manager.py add

# Check coverage statistics
python scripts/managers/nutrition_manager.py stats
python scripts/managers/pricing_manager.py stats
python scripts/managers/seasonality_manager.py stats
```

---

## 📖 Detailed Documentation

See individual README files in each subdirectory:
- [Initial Data Fetching](initial_data_fetching/README.md)
- [Managers](managers/README.md)
- [Updates](updates/README.md)

---

## ⚙️ Requirements

All scripts require:
- Python 3.8+
- Database connection (configured in `.env`)
- Dependencies installed: `pip install -r requirements.txt`

Initial data fetch scripts additionally require:
- USDA API key (for nutrition data)
- Internet connection

---

## 🛠️ Troubleshooting

### Script not found
```bash
# Make sure you're in the project root directory
cd /path/to/SultenChatbot
python scripts/...
```

### Database connection error
```bash
# Check your .env file has correct credentials
cat example.env
```

### Import errors
```bash
# Ensure you're running from project root
# NOT from inside the scripts/ directory
```
