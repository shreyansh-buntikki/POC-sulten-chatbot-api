#!/usr/bin/env python3
"""
Ingredient Pricing Manager
Create, view, update, and manage ingredient pricing across 3 countries (India, Norway, USA).

Usage:
    python ingredient_pricing_manager.py list [ingredient_name]
    python ingredient_pricing_manager.py view <ingredient_name>
    python ingredient_pricing_manager.py add <ingredient_name>
    python ingredient_pricing_manager.py update <ingredient_name>
    python ingredient_pricing_manager.py set-price <ingredient_name> <country> <price>
    python ingredient_pricing_manager.py stats

Interactive mode:
    python ingredient_pricing_manager.py
"""
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import uuid
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from database import SessionLocal
from sqlalchemy import text
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Country and Currency mappings
COUNTRIES = {
    'india': {'id': '11111111-1111-1111-1111-111111111111', 'code': 'IND', 'currency_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'},
    'norway': {'id': '22222222-2222-2222-2222-222222222222', 'code': 'NOR', 'currency_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'},
    'usa': {'id': '33333333-3333-3333-3333-333333333333', 'code': 'USA', 'currency_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc'},
}

# Unit mappings
UNIT_IDS = {
    'gram': '396cab8c-5d3b-49b0-b946-b96a26f84af1',
    'kg': '45da9c48-2a89-47a9-8d7c-6146a9dde4d9',
    'liter': '3db0f7a7-1b23-4528-be58-9c13b50d5629',
    'ml': '72e3d5a6-1c84-4c1e-9a1a-f8c2e4a2f3e8',
    'piece': '7d25ed2b-9f5f-4d95-8ee8-4617e756ff68',
    'slice': '2ccd0924-2772-487e-a908-e4f4ceb2507e',
    'tablespoon': 'd4a8f4e3-5e3b-4a7e-9b5f-2c3e4d5a6b7c',
    'teaspoon': '2ca8464a-d6eb-428b-a18a-9169c3350146',
    'cup': 'e5b9c5f4-6f4c-4d8f-a5c6-3d4e5f6a7b8c',
}


class PricingManager:
    """Manages ingredient pricing across multiple countries"""

    def __init__(self):
        self.db = SessionLocal()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db.close()

    def list_ingredients(self, filter_name: Optional[str] = None) -> List[Dict]:
        """List all ingredients, optionally filtered by name"""
        query = text("""
            SELECT id, name
            FROM ingredient
            WHERE (:filter IS NULL OR name ILIKE '%' || :filter || '%')
            ORDER BY name
            LIMIT 50
        """)
        result = self.db.execute(query, {"filter": filter_name})
        return [{"id": row[0], "name": row[1]} for row in result]

    def get_ingredient_by_name(self, name: str) -> Optional[Dict]:
        """Find an ingredient by name"""
        query = text("""
            SELECT id, name
            FROM ingredient
            WHERE name ILIKE :name
            LIMIT 1
        """)
        result = self.db.execute(query, {"name": name}).fetchone()
        if result:
            return {"id": result[0], "name": result[1]}
        return None

    def get_pricing(self, ingredient_id: str) -> List[Dict]:
        """Get all pricing for an ingredient across countries"""
        query = text("""
            SELECT
                ip.id,
                c.code as country_code,
                c.name as country_name,
                cur.code as currency_code,
                cur.symbol as currency_symbol,
                ip."pricePerUnit",
                ip.quantity,
                mu.name as unit_name
            FROM ingredient_pricing ip
            JOIN country c ON c.id = ip."countryId"
            JOIN currency cur ON cur.id = ip."currencyId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            WHERE ip."ingredientId" = :ingredientId
            ORDER BY c.code
        """)
        result = self.db.execute(query, {"ingredientId": ingredient_id})
        return [{
            'id': row[0],
            'country_code': row[1],
            'country_name': row[2],
            'currency_code': row[3],
            'currency_symbol': row[4],
            'price': float(row[5]),
            'quantity': int(row[6]),
            'unit': row[7],
        } for row in result]

    def create_or_update_pricing(
        self,
        ingredient_id: str,
        country_code: str,
        price_per_unit: float,
        quantity: int,
        unit: str,
    ) -> bool:
        """Create or update pricing for an ingredient in a country"""
        try:
            country_code = country_code.upper()
            if country_code not in ['IND', 'NOR', 'USA']:
                logger.error(f"Invalid country code: {country_code}. Use IND, NOR, or USA")
                return False

            country_key = country_code.lower()
            if country_key not in COUNTRIES:
                logger.error(f"Country {country_code} not configured")
                return False

            country_id = COUNTRIES[country_key]['id']
            currency_id = COUNTRIES[country_key]['currency_id']

            if unit.lower() not in UNIT_IDS:
                logger.error(f"Invalid unit: {unit}. Use: {', '.join(UNIT_IDS.keys())}")
                return False

            unit_id = UNIT_IDS[unit.lower()]

            # Check if exists
            existing = self.db.execute(text("""
                SELECT id FROM ingredient_pricing
                WHERE "ingredientId" = :ingredientId AND "countryId" = :countryId
            """), {
                "ingredientId": ingredient_id,
                "countryId": country_id
            }).fetchone()

            if existing:
                # Update
                self.db.execute(text("""
                    UPDATE ingredient_pricing
                    SET "pricePerUnit" = :price,
                        quantity = :quantity,
                        "measuringUnitId" = :unit_id,
                        "updatedAt" = now()
                    WHERE id = :id
                """), {
                    "price": price_per_unit,
                    "quantity": quantity,
                    "unit_id": unit_id,
                    "id": existing[0]
                })
                logger.info(f"  ✓ Updated pricing for {country_code}")
            else:
                # Create
                self.db.execute(text("""
                    INSERT INTO ingredient_pricing
                    (id, "ingredientId", "countryId", "currencyId",
                     "pricePerUnit", quantity, "measuringUnitId", "createdAt", "updatedAt")
                    VALUES
                    (:id, :ingredient_id, :country_id, :currency_id,
                     :price, :quantity, :unit_id, now(), now())
                """), {
                    "id": str(uuid.uuid4()),
                    "ingredient_id": ingredient_id,
                    "country_id": country_id,
                    "currency_id": currency_id,
                    "price": price_per_unit,
                    "quantity": quantity,
                    "unit_id": unit_id,
                })
                logger.info(f"  ✓ Created pricing for {country_code}")

            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving pricing: {e}")
            return False

    def delete_pricing(self, ingredient_id: str, country_code: str) -> bool:
        """Delete pricing for an ingredient in a country"""
        try:
            country_key = country_code.lower()
            if country_key not in COUNTRIES:
                logger.error(f"Invalid country: {country_code}")
                return False

            country_id = COUNTRIES[country_key]['id']

            self.db.execute(text("""
                DELETE FROM ingredient_pricing
                WHERE "ingredientId" = :ingredientId AND "countryId" = :countryId
            """), {
                "ingredientId": ingredient_id,
                "countryId": country_id
            })

            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error deleting pricing: {e}")
            return False

    def get_pricing_stats(self) -> Dict:
        """Get pricing statistics"""
        stats = self.db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM ingredient) as total_ingredients,
                (SELECT COUNT(DISTINCT "ingredientId") FROM ingredient_pricing) as ingredients_with_pricing,
                (SELECT COUNT(*) FROM ingredient_pricing) as total_pricing_records
        """)).fetchone()

        # Count by country
        by_country = self.db.execute(text("""
            SELECT
                c.code,
                COUNT(*) as count
            FROM ingredient_pricing ip
            JOIN country c ON c.id = ip."countryId"
            GROUP BY c.code
            ORDER BY c.code
        """)).fetchall()

        return {
            "total_ingredients": stats[0],
            "with_pricing": stats[1],
            "total_records": stats[2],
            "coverage": round((stats[1] / stats[0] * 100) if stats[0] > 0 else 0, 1),
            "by_country": {row[0]: row[1] for row in by_country}
        }


def print_pricing(pricing_list: List[Dict]):
    """Pretty print pricing data"""
    if not pricing_list:
        logger.info("\n  No pricing data found for this ingredient.")
        logger.info("  Use 'add' command to add pricing.")
        return

    logger.info(f"\n  ╔═══════════════════════════════════════════════════════════════╗")
    for p in pricing_list:
        logger.info(f"  ║  {p['country_name']:12} ({p['country_code']}): "
                   f"{p['currency_symbol']}{p['price']:.2f} per {p['quantity']} {p['unit']:<15} ║")
    logger.info(f"  ╚═══════════════════════════════════════════════════════════════╝")


def interactive_add():
    """Interactive mode for adding pricing"""
    manager = PricingManager()

    logger.info("\n💰 Add Ingredient Pricing")
    logger.info("=" * 50)

    # Get ingredient name
    ingredient_name = input("\nEnter ingredient name (or 'search' to search): ").strip()

    if ingredient_name.lower() == 'search':
        search_term = input("Search term: ").strip()
        ingredients = manager.list_ingredients(search_term)
        if ingredients:
            logger.info(f"\nFound {len(ingredients)} ingredients:")
            for i, ing in enumerate(ingredients, 1):
                logger.info(f"  {i}. {ing['name']}")
            choice = input(f"\nSelect number (1-{len(ingredients)}): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(ingredients):
                ingredient_name = ingredients[int(choice) - 1]['name']
            else:
                logger.error("Invalid selection")
                return
        else:
            logger.error("No ingredients found")
            return

    ingredient = manager.get_ingredient_by_name(ingredient_name)
    if not ingredient:
        logger.error(f"Ingredient '{ingredient_name}' not found")
        return

    logger.info(f"\n✓ Found ingredient: {ingredient['name']}")

    # Get existing pricing
    existing = manager.get_pricing(ingredient['id'])
    if existing:
        logger.info("\n⚠️  This ingredient already has pricing:")
        print_pricing(existing)
        update = input("\nUpdate existing pricing? (y/n): ").strip().lower()
        if update != 'y':
            return

    # Add pricing for each country
    logger.info("\n--- Add Pricing for Each Country ---")
    logger.info("Countries: India (INR), Norway (NOK), USA (USD)")
    logger.info("Units: gram, kg, liter, ml, piece, slice, tablespoon, teaspoon, cup")
    logger.info("Press Enter to skip a country\n")

    for country_key, country_info in COUNTRIES.items():
        country_code = country_info['code']
        logger.info(f"\n{country_info['code']} - {country_info['code']}:")

        price_input = input(f"  Price per unit: ").strip()
        if not price_input:
            logger.info(f"  ⊘ Skipped {country_code}")
            continue

        try:
            price = float(price_input)
        except ValueError:
            logger.error(f"  ✗ Invalid price. Skipping {country_code}")
            continue

        quantity = input(f"  Quantity (default 100): ").strip()
        try:
            quantity = int(quantity) if quantity else 100
        except ValueError:
            quantity = 100

        unit = input(f"  Unit (default gram): ").strip().lower()
        if not unit or unit not in UNIT_IDS:
            unit = 'gram'

        if manager.create_or_update_pricing(ingredient['id'], country_code, price, quantity, unit):
            logger.info(f"  ✓ {country_code} pricing saved")

    logger.info("\n✅ Pricing data saved successfully!")


def cmd_list(args):
    """List ingredients"""
    with PricingManager() as manager:
        filter_name = args[0] if len(args) > 0 else None
        ingredients = manager.list_ingredients(filter_name)

        if ingredients:
            logger.info(f"\n{'Ingredient Name':<50} {'ID'}")
            logger.info("-" * 86)
            for ing in ingredients:
                logger.info(f"{ing['name']:<50} {ing['id']}")
            logger.info(f"\nTotal: {len(ingredients)} ingredients")
        else:
            logger.info("No ingredients found")


def cmd_view(args):
    """View ingredient pricing"""
    if len(args) < 1:
        logger.error("Usage: view <ingredient_name>")
        return

    with PricingManager() as manager:
        ingredient = manager.get_ingredient_by_name(args[0])
        if not ingredient:
            logger.error(f"Ingredient '{args[0]}' not found")
            return

        pricing = manager.get_pricing(ingredient['id'])

        logger.info(f"\n{'='*70}")
        logger.info(f"INGREDIENT: {ingredient['name']}")
        logger.info(f"{'='*70}")
        print_pricing(pricing)


def cmd_set_price(args):
    """Set price for an ingredient in a specific country"""
    if len(args) < 3:
        logger.error("Usage: set-price <ingredient_name> <country> <price>")
        logger.error("Countries: india, norway, usa")
        return

    with PricingManager() as manager:
        ingredient = manager.get_ingredient_by_name(args[0])
        if not ingredient:
            logger.error(f"Ingredient '{args[0]}' not found")
            return

        country = args[1].lower()
        if country not in COUNTRIES:
            logger.error(f"Invalid country: {args[1]}. Use: india, norway, usa")
            return

        try:
            price = float(args[2])
        except ValueError:
            logger.error(f"Invalid price: {args[2]}")
            return

        # Get defaults
        pricing_list = manager.get_pricing(ingredient['id'])
        if pricing_list:
            # Use existing quantity and unit
            existing = next((p for p in pricing_list if p['country_code'] == country.upper()), None)
            if existing:
                quantity = existing['quantity']
                unit = existing['unit'].lower()
            else:
                quantity = 100
                unit = 'gram'
        else:
            quantity = 100
            unit = 'gram'

        if manager.create_or_update_pricing(ingredient['id'], country.upper(), price, quantity, unit):
            logger.info(f"\n✅ Price updated: {ingredient['name']} in {country.upper()}: {price}")


def cmd_stats(args):
    """Show pricing statistics"""
    with PricingManager() as manager:
        stats = manager.get_pricing_stats()

        logger.info("\n💰 Pricing Data Statistics")
        logger.info("=" * 50)
        logger.info(f"Total Ingredients:       {stats['total_ingredients']}")
        logger.info(f"With Pricing:            {stats['with_pricing']} ({stats['coverage']}%)")
        logger.info(f"Total Pricing Records:   {stats['total_records']}")
        logger.info("\nBy Country:")
        for code, count in stats['by_country'].items():
            logger.info(f"  {code}:  {count} ingredients")


def show_help():
    """Show help message"""
    logger.info("""
╔════════════════════════════════════════════════════════════════╗
║        Ingredient Pricing Manager (3 Countries)              ║
╠════════════════════════════════════════════════════════════════╣
║  Commands:                                                      ║
║    list [filter]      List ingredients (optionally filter)       ║
║    view <name>        View pricing for ingredient across countries║
║    add               Interactive mode to add pricing            ║
║    set-price <name> <country> <price>                           ║
║                      Set price for specific country              ║
║    stats             Show pricing coverage statistics          ║
║    help              Show this help message                      ║
╠════════════════════════════════════════════════════════════════╣
║  Countries: india, norway, usa                                   ║
║  Units: gram, kg, liter, ml, piece, slice, tbsp, tsp, cup        ║
╠════════════════════════════════════════════════════════════════╣
║  Examples:                                                      ║
║    python ingredient_pricing_manager.py list tomato             ║
║    python ingredient_pricing_manager.py view "Almond Flour"     ║
║    python ingredient_pricing_manager.py set-price rice india 50 ║
║    python ingredient_pricing_manager.py add                    ║
║    python ingredient_pricing_manager.py stats                  ║
╚════════════════════════════════════════════════════════════════╝
    """)


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()
    args = sys.argv[2:]

    if command == 'list':
        cmd_list(args)
    elif command == 'view':
        cmd_view(args)
    elif command == 'add':
        interactive_add()
    elif command == 'set-price':
        cmd_set_price(args)
    elif command == 'stats':
        cmd_stats(args)
    elif command == 'help' or command == '--help' or command == '-h':
        show_help()
    else:
        logger.error(f"Unknown command: {command}")
        show_help()


if __name__ == "__main__":
    main()
