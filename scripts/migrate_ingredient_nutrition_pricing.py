#!/usr/bin/env python3
"""
Migration script to replace ingredient nutrition and pricing data from JSON file.

This script processes ingredient data from a JSON file and updates the database tables:
- ingredient_macros
- ingredient_micros
- ingredient_pricing

Usage:
    python scripts/migrate_ingredient_nutrition_pricing.py /path/to/enriched_ingredients.json [--limit N]
"""

import argparse
import json
import logging
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from database import SessionLocal, DATABASE_URL
from models import IngredientMacros, IngredientMicros, IngredientPricing

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f'migration_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    ]
)
logger = logging.getLogger(__name__)

# Test ingredient (for initial testing)
TEST_INGREDIENT_ID = "036d22a6-fcc3-4f32-8650-4808957ade9a"
TEST_INGREDIENT_NAME = "sukkerfri mørk sjokolade"


class IngredientDataMigration:
    """Handles migration of ingredient nutrition and pricing data."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.unit_to_id: Dict[str, str] = {}
        self.country_to_id: Dict[str, str] = {}
        self.currency_to_id: Dict[str, str] = {}

    def build_lookup_mappings(self, db):
        """Build lookup dictionaries for units, countries, and currencies."""
        logger.info("Building lookup mappings...")

        # Unit mappings from measuring_unit_translation table
        # Note: This table is not in models.py, so we use raw SQL
        result = db.execute(text("""
            SELECT mut.name, mu.id as unit_id
            FROM measuring_unit_translation mut
            JOIN measuring_unit mu ON mu.id = mut."measuringUnitId"
            WHERE mut.name IN ('kg', 'stk', 'pk', 'boks', 'can', 'pack', 'x', 'g')
        """))
        for row in result:
            self.unit_to_id[row.name] = row.unit_id

        logger.info(f"Found units: {list(self.unit_to_id.keys())}")

        # Country mappings
        countries = ['norway', 'india', 'usa']
        # Query with both name and code matching
        countries_str = ','.join([f"'{c}'" for c in countries])
        result = db.execute(text(f"""
            SELECT LOWER(name) as name_lower, id
            FROM country
            WHERE LOWER(name) IN ({countries_str})
               OR LOWER(code) IN ({countries_str})
        """))
        for row in result:
            self.country_to_id[row.name_lower] = row.id

        logger.info(f"Found countries: {list(self.country_to_id.keys())}")

        # Currency mappings
        currencies = ['NOK', 'INR', 'USD']
        currencies_str = ','.join([f"'{c}'" for c in currencies])
        result = db.execute(text(f"""
            SELECT code, id
            FROM currency
            WHERE code IN ({currencies_str})
        """))
        for row in result:
            self.currency_to_id[row.code] = row.id

        logger.info(f"Found currencies: {list(self.currency_to_id.keys())}")

        # Verify all mappings are found
        missing_units = [u for u in ['kg', 'stk', 'pk', 'boks', 'can', 'pack', 'x'] if u not in self.unit_to_id]
        if missing_units:
            raise ValueError(f"Missing unit mappings: {missing_units}")

        # Check countries - map JSON keys to database keys
        country_json_keys = ['norway', 'india', 'usa']
        country_db_keys = []
        for json_key in country_json_keys:
            if json_key == 'usa':
                country_db_keys.append('united states')
            else:
                country_db_keys.append(json_key)

        missing_countries = [json_key for json_key, db_key in zip(country_json_keys, country_db_keys)
                           if db_key not in self.country_to_id]
        if missing_countries:
            raise ValueError(f"Missing country mappings: {missing_countries}")

        missing_currencies = [c for c in ['NOK', 'INR', 'USD'] if c not in self.currency_to_id]
        if missing_currencies:
            raise ValueError(f"Missing currency mappings: {missing_currencies}")

    def parse_serving_size(self, serving_size_str: str) -> int:
        """Parse serving size from string format (e.g., "100g" -> 100)."""
        match = re.search(r'(\d+)', serving_size_str)
        if match:
            return int(match.group(1))
        return 100  # Default serving size

    def upsert_macros(self, db, ingredient_id: str, data: Dict[str, Any]) -> bool:
        """Upsert macro data for an ingredient."""
        try:
            serving_size = self.parse_serving_size(data.get('servingSize', '100g'))

            # Prepare data for ingredient_macros
            macros_data = {
                'id': uuid.uuid4(),
                'ingredientId': ingredient_id,
                'servingSize': serving_size,
                'energyKcal': data.get('energyKcal'),
                'energyKj': data.get('energyKj'),
                'protein': data.get('protein'),
                'carbohydrates': data.get('carbohydrates'),
                'totalFiber': data.get('totalFiber'),
                'solubleFiber': data.get('solubleFiber'),
                'insolubleFiber': data.get('insolubleFiber'),
                'totalSugars': data.get('totalSugars'),
                'addedSugar': data.get('addedSugar'),
                'starch': data.get('starch'),
                'totalFat': data.get('totalFat'),
                'saturatedFat': data.get('saturatedFat'),
                'transFat': data.get('transFat'),
                'monounsaturatedFat': data.get('monounsaturatedFat'),
                'polyunsaturatedFat': data.get('polyunsaturatedFat'),
                'cholesterol': data.get('cholesterol'),
            }

            # Use ON CONFLICT for upsert
            stmt = insert(IngredientMacros.__table__).values(macros_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=['ingredientId'],
                set_=macros_data
            )

            db.execute(stmt)
            return True

        except Exception as e:
            logger.error(f"Error upserting macros for ingredient {ingredient_id}: {e}")
            return False

    def upsert_micros(self, db, ingredient_id: str, data: Dict[str, Any]) -> bool:
        """Upsert micro data for an ingredient."""
        try:
            serving_size = self.parse_serving_size(data.get('servingSize', '100g'))

            # Prepare data for ingredient_micros
            micros_data = {
                'id': uuid.uuid4(),
                'ingredientId': ingredient_id,
                'servingSize': serving_size,
                'vitaminA': data.get('vitaminA'),
                'vitaminC': data.get('vitaminC'),
                'vitaminD': data.get('vitaminD'),
                'vitaminE': data.get('vitaminE'),
                'vitaminK': data.get('vitaminK'),
                'thiamineB1': data.get('thiamineB1'),
                'riboflavinB2': data.get('riboflavinB2'),
                'niacinB3': data.get('niacinB3'),
                'pantothenicAcidB5': data.get('pantothenicAcidB5'),
                'vitaminB6': data.get('vitaminB6'),
                'biotinB7': data.get('biotinB7'),
                'folateB9': data.get('folateB9'),
                'vitaminB12': data.get('vitaminB12'),
                'choline': data.get('choline'),
                'calcium': data.get('calcium'),
                'iron': data.get('iron'),
                'magnesium': data.get('magnesium'),
                'phosphorus': data.get('phosphorus'),
                'potassium': data.get('potassium'),
                'sodium': data.get('sodium'),
                'zinc': data.get('zinc'),
                'copper': data.get('copper'),
                'manganese': data.get('manganese'),
                'selenium': data.get('selenium'),
                'fluoride': data.get('fluoride'),
            }

            # Use ON CONFLICT for upsert
            stmt = insert(IngredientMicros.__table__).values(micros_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=['ingredientId'],
                set_=micros_data
            )

            db.execute(stmt)
            return True

        except Exception as e:
            logger.error(f"Error upserting micros for ingredient {ingredient_id}: {e}")
            return False

    def update_pricing(self, db, ingredient_id: str, pricing_data: Dict[str, Any]) -> bool:
        """Update pricing data for an ingredient (delete old, insert new)."""
        try:
            # First, delete existing pricing for this ingredient
            db.execute(IngredientPricing.__table__.delete().where(
                IngredientPricing.ingredientId == ingredient_id
            ))

            # Insert new pricing records
            logger.info(f"  Processing pricing data with keys: {list(pricing_data.keys())}")
            for country_key, pricing in pricing_data.items():
                logger.info(f"    Processing country: {country_key}")

                # Map country keys from JSON to database keys
                db_country_key = country_key
                if country_key == 'usa':
                    db_country_key = 'united states'  # Map to what's in database

                if db_country_key not in self.country_to_id:
                    logger.warning(f"Unknown country: {country_key} (mapped to: {db_country_key})")
                    continue

                logger.info(f"    Successfully mapped: {country_key} -> {db_country_key}")

                try:
                    pricing_record = {
                        'id': uuid.uuid4(),
                        'ingredientId': ingredient_id,
                        'countryId': self.country_to_id[db_country_key],
                        'currencyId': self.currency_to_id[pricing['currency']],
                        'pricePerUnit': pricing['pricePerUnit'],
                        'quantity': 1,  # Default quantity
                    }

                    # Get measuring unit ID
                    unit_name = pricing['unit']
                    if unit_name in self.unit_to_id:
                        pricing_record['measuringUnitId'] = self.unit_to_id[unit_name]
                    else:
                        logger.warning(f"Unknown unit '{unit_name}' for ingredient {ingredient_id}")
                        # Use a default unit
                        pricing_record['measuringUnitId'] = self.unit_to_id.get('pk')

                    logger.info(f"      Inserting pricing record: {pricing_record}")
                    db.execute(IngredientPricing.__table__.insert(), pricing_record)

                except KeyError as e:
                    logger.error(f"    Missing key in pricing data for {country_key}: {e}")
                    raise
                except Exception as e:
                    logger.error(f"    Error inserting pricing record for {country_key}: {e}")
                    raise

            return True

        except Exception as e:
            logger.error(f"Error updating pricing for ingredient {ingredient_id}: {e}")
            return False

    def process_ingredient(self, db, ingredient_data: Dict[str, Any]) -> bool:
        """Process a single ingredient (macros, micros, pricing)."""
        ingredient_id = ingredient_data['id']
        ingredient_name = ingredient_data.get('name', 'Unknown')

        logger.info(f"Processing ingredient: {ingredient_name} (ID: {ingredient_id})")

        # Check if ingredient exists
        result = db.execute(text("""
            SELECT id FROM ingredient WHERE id = :ingredient_id
        """), {'ingredient_id': ingredient_id})

        if not result.fetchone():
            logger.error(f"Ingredient {ingredient_id} not found in database")
            return False

        success = True

        # Process macros
        if 'macros' in ingredient_data:
            logger.info(f"  Processing macros...")
            if not self.upsert_macros(db, ingredient_id, ingredient_data['macros']):
                success = False

        # Process micros
        if 'micros' in ingredient_data:
            logger.info(f"  Processing micros...")
            if not self.upsert_micros(db, ingredient_id, ingredient_data['micros']):
                success = False

        # Process pricing
        if 'pricing' in ingredient_data:
            logger.info(f"  Processing pricing...")
            if not self.update_pricing(db, ingredient_id, ingredient_data['pricing']):
                success = False

        if success:
            logger.info(f"Successfully processed: {ingredient_name}")
        else:
            logger.error(f"Failed to process: {ingredient_name}")

        return success


def main():
    parser = argparse.ArgumentParser(description='Migrate ingredient nutrition and pricing data')
    parser.add_argument('json_file', help='Path to JSON file with ingredient data')
    parser.add_argument('--limit', type=int, default=None,
                        help='Limit number of ingredients to process (for testing)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    # Check if JSON file exists
    if not Path(args.json_file).exists():
        logger.error(f"JSON file not found: {args.json_file}")
        sys.exit(1)

    # Load JSON data
    logger.info(f"Loading JSON data from: {args.json_file}")
    try:
        with open(args.json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
    except Exception as e:
        logger.error(f"Error loading JSON file: {e}")
        sys.exit(1)

    # Get ingredients array
    if 'ingredients' not in json_data:
        logger.error("JSON file must contain 'ingredients' array")
        sys.exit(1)

    ingredients = json_data['ingredients']
    logger.info(f"Loaded {len(ingredients)} ingredients from JSON")

    # Limit ingredients for testing
    if args.limit:
        ingredients = ingredients[:args.limit]
        logger.info(f"Limited to processing {len(ingredients)} ingredients")

    # Initialize migration
    db_url = DATABASE_URL
    migration = IngredientDataMigration(db_url)

    # Process ingredients
    success_count = 0
    error_count = 0

    for i, ingredient in enumerate(ingredients, 1):
        ingredient_name = ingredient.get('name', 'Unknown')
        logger.info(f"\n=== Processing ingredient {i}/{len(ingredients)}: {ingredient_name} ===")

        # Process the ingredient
        db = SessionLocal()
        try:
            # Build lookup mappings on first iteration
            if i == 1:
                migration.build_lookup_mappings(db)

            if migration.process_ingredient(db, ingredient):
                success_count += 1
                db.commit()
            else:
                error_count += 1
                db.rollback()

        except Exception as e:
            logger.error(f"Unexpected error processing ingredient {ingredient_name}: {e}")
            error_count += 1
            db.rollback()
        finally:
            db.close()

    # Summary
    logger.info("\n" + "="*50)
    logger.info("MIGRATION SUMMARY")
    logger.info("="*50)
    logger.info(f"Total ingredients processed: {len(ingredients)}")
    logger.info(f"Success: {success_count}")
    logger.info(f"Errors: {error_count}")

    if error_count == 0:
        logger.info("✅ All ingredients processed successfully!")
    else:
        logger.warning(f"⚠️  {error_count} ingredients failed to process")


if __name__ == '__main__':
    main()