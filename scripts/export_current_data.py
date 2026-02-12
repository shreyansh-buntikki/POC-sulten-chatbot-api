#!/usr/bin/env python3
"""
Export current nutrition and pricing data for all ingredients in the JSON file
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from database import SessionLocal
from sqlalchemy import text

def decimal_to_float(obj):
    """Convert Decimal to float for JSON serialization"""
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

def export_current_data():
    # Load ingredient IDs from the JSON file
    json_file = "/home/kevit/Downloads/enriched_ingredients/enriched_ingredients_partial_0_10.json"

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    ingredient_ids = [ingredient['id'] for ingredient in data['ingredients']]

    print(f"Found {len(ingredient_ids)} ingredients to export:")
    for ingredient in data['ingredients']:
        print(f"  - {ingredient['name']} ({ingredient['id']})")

    db = SessionLocal()
    try:
        # Export data for each ingredient
        results = []

        for ingredient_id in ingredient_ids:
            ingredient_data = {}

            # Get ingredient name
            result = db.execute(text("""
                SELECT name FROM ingredient WHERE id = :ingredient_id
            """), {'ingredient_id': ingredient_id})
            row = result.fetchone()
            ingredient_data['name'] = row.name if row else 'Unknown'

            # Get macros
            result = db.execute(text("""
                SELECT "servingSize", "energyKcal", "energyKj", protein, carbohydrates,
                       "totalFiber", "solubleFiber", "insolubleFiber", "totalSugars",
                       "addedSugar", starch, "totalFat", "saturatedFat", "transFat",
                       "monounsaturatedFat", "polyunsaturatedFat", "cholesterol"
                FROM ingredient_macros
                WHERE "ingredientId" = :ingredient_id
            """), {'ingredient_id': ingredient_id})

            macro_row = result.fetchone()
            if macro_row:
                ingredient_data['macros'] = dict(macro_row._mapping)
            else:
                ingredient_data['macros'] = None

            # Get micros
            result = db.execute(text("""
                SELECT "servingSize", "vitaminA", "vitaminC", "vitaminD", "vitaminE", "vitaminK",
                       "thiamineB1", "riboflavinB2", "niacinB3", "pantothenicAcidB5", "vitaminB6",
                       "biotinB7", "folateB9", "vitaminB12", choline, calcium, iron, magnesium,
                       phosphorus, potassium, sodium, zinc, copper, manganese, selenium, fluoride
                FROM ingredient_micros
                WHERE "ingredientId" = :ingredient_id
            """), {'ingredient_id': ingredient_id})

            micro_row = result.fetchone()
            if micro_row:
                ingredient_data['micros'] = dict(micro_row._mapping)
            else:
                ingredient_data['micros'] = None

            # Get pricing (simplified)
            result = db.execute(text("""
                SELECT p."countryId", c.code as currency_code, p."pricePerUnit", p.quantity
                FROM ingredient_pricing p
                JOIN country co ON co.id = p."countryId"
                JOIN currency c ON c.id = p."currencyId"
                WHERE p."ingredientId" = :ingredient_id
                ORDER BY p."countryId"
            """), {'ingredient_id': ingredient_id})

            pricing_rows = result.fetchall()
            if pricing_rows:
                pricing_data = {}
                for row in pricing_rows:
                    # Get country name in lowercase to match JSON format
                    result_country = db.execute(text("""
                        SELECT LOWER(name) FROM country WHERE id = :country_id
                    """), {'country_id': row.countryId})
                    country_row = result_country.fetchone()
                    country_name = country_row[0] if country_row else 'unknown'

                    pricing_data[country_name] = {
                        'currency': row.currency_code,
                        'pricePerUnit': float(row.pricePerUnit),
                        'quantity': row.quantity
                    }
                ingredient_data['pricing'] = pricing_data
            else:
                ingredient_data['pricing'] = None

            ingredient_data['id'] = ingredient_id
            results.append(ingredient_data)

        # Export to JSON file
        export_data = {
            'exported_at': datetime.now().isoformat(),
            'total_ingredients': len(results),
            'ingredients': results
        }

        export_filename = f"current_data_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(export_filename, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False, default=decimal_to_float)

        print(f"\n✅ Data exported to: {export_filename}")

        # Also display a summary
        print("\n=== SUMMARY ===")
        for ingredient in results:
            print(f"\n{ingredient['name']} ({ingredient['id']}):")
            if ingredient['macros']:
                print(f"  Macros: {ingredient['macros']['energyKcal']} kcal, {ingredient['macros']['protein']}g protein")
            else:
                print("  Macros: No data")

            if ingredient['pricing']:
                print(f"  Pricing: {len(ingredient['pricing'])} countries")
                for country, pricing in ingredient['pricing'].items():
                    print(f"    {country}: {pricing['pricePerUnit']} {pricing['currency']}")
            else:
                print("  Pricing: No data")

    except Exception as e:
        print(f"Error: {e}")
        raise

    finally:
        db.close()

if __name__ == '__main__':
    export_current_data()