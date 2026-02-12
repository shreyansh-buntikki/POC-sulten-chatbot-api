"""
Fix Pricing and Units Migration Script
This script updates ingredient pricing with appropriate units and realistic quantities
"""
import sys
import os

from apps.fastapi import logger

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from sqlalchemy.orm import Session
from database import SessionLocal
from models import Ingredient, IngredientPricing, MeasuringUnit
from sqlalchemy import text

# Unit IDs from the database
UNIT_GRAM = '396cab8c-5d3b-49b0-b946-b96a26f84af1'
UNIT_KILO = '45da9c48-2a89-47a9-8d7c-6146a9dde4d9'
UNIT_LITER = '3db0f7a7-1b23-4528-be58-9c13b50d5629'
UNIT_PIECE = '7d25ed2b-9f5f-4d95-8ee8-4617e756ff68'
UNIT_SLICE = '2ccd0924-2772-487e-a908-e4f4ceb2507e'
UNIT_TEASPOON = '2ca8464a-d6eb-428b-a18a-9169c3350146'
UNIT_SCOOP = '3b99944d-2703-4d73-bf42-b997dc2c263f'

# Country and Currency IDs
COUNTRY_IDS = {
    'india': '11111111-1111-1111-1111-111111111111',
    'norway': '22222222-2222-2222-2222-222222222222',
    'us': '33333333-3333-3333-3333-333333333333',
}

CURRENCY_IDS = {
    'india': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',  # INR
    'norway': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',  # NOK
    'us': 'cccccccc-cccc-cccc-cccc-cccccccccccc',  # USD
}


class IngredientCategorizer:
    """Categorizes ingredients and assigns appropriate units"""

    # LIQUID ingredients - priced per liter
    LIQUID_INGREDIENTS = [
        'milk', 'cream', 'yoghurt', 'yogurt', 'kefir', 'buttermilk',
        'water', 'juice', 'oil', 'olive oil', 'sunflower oil', 'coconut oil',
        'sauce', 'tomato sauce', 'pasta sauce', 'soy sauce', 'fish sauce',
        'vinegar', 'balsamic vinegar', 'apple cider vinegar',
        'syrup', 'maple syrup', 'honey', 'agave', 'molasses',
        'extract', 'vanilla extract', 'almond extract', 'lemon juice',
        'lime juice', 'essence', 'cream', 'half and half',
        'melk', 'krem', 'juice', 'olje', 'saus', 'eddik', 'sirup',
        'honning', 'ekstrakt', 'kulturmelk', 'matfløte', 'rømme',
        'vanilje', 'vann', 'like', 'smør', 'børje', 'sodd', 'kraft',
    ]

    # PIECE ingredients - priced per piece
    PIECE_INGREDIENTS = [
        'egg', 'eggs', 'boiled egg', 'fried egg',
        'fruit', 'vegetable', 'onion', 'garlic', 'ginger',
        'lime', 'lemon', 'orange', 'apple', 'banana',
        'tomato', 'potato', 'avocado', 'mango', 'papaya',
        'bread', 'bun', 'roll', 'bagel', 'tortilla', 'naan',
        'pita', 'roti', 'chapati', 'paratha',
        'egg', 'frukt', 'grønnsak', 'løk', 'hvitløk', 'ingefær',
        'lime', 'sitron', 'appelsin', 'eple', 'banan', 'tomat',
        'potet', 'avokado', 'brød', 'brødskive', 'rundstykke',
        'pulled pork', 'pulled chicken',
    ]

    # SLICE ingredients - priced per slice
    SLICE_INGREDIENTS = [
        'bread slice', 'cheese slice', 'ham slice', 'turkey slice',
        'toast', 'sandwich', 'bagel', 'bun', 'brødskive',
    ]

    # BULK/HEAVY ingredients - priced per kg
    BULK_INGREDIENTS = [
        'flour', 'wheat flour', 'all-purpose flour', 'bread flour',
        'rice', 'basmati rice', 'brown rice', 'jasmine rice',
        'sugar', 'white sugar', 'brown sugar', 'powdered sugar',
        'salt', 'sea salt', 'kosher salt',
        'grain', 'oats', 'oatmeal', 'rolled oats',
        'pasta', 'spaghetti', 'penne', 'macaroni', 'noodles',
        'nuts', 'almonds', 'cashews', 'walnuts', 'pecans',
        'beans', 'kidney beans', 'black beans', 'chickpeas',
        'lentils', 'split peas',
        'flour', 'hvetemel', 'mel', 'sukker', 'salt',
        'ris', 'korn', 'gryn', 'nøtter', 'frø', 'bønner',
        'linser', 'pasta', 'nudler',
    ]

    # SMALL QUANTITY ingredients - priced per 100g (spices)
    SPICE_INGREDIENTS = [
        'spice', 'powder', 'cumin', 'coriander', 'turmeric', 'paprika',
        'chili powder', 'cinnamon', 'nutmeg', 'clove', 'ginger',
        'garlic powder', 'onion powder', 'pepper', 'black pepper',
        'oregano', 'basil', 'thyme', 'rosemary',
        'vanilla', 'baking powder', 'baking soda', 'yeast',
        'krydder', 'pulver', 'kanel', 'krydder',
    ]

    @classmethod
    def categorize_ingredient(cls, ingredient_name: str) -> tuple:
        """
        Categorize ingredient and return (unit_id, quantity_multiplier, description)

        Returns: (unit_id, quantity, description)
        """
        name_lower = ingredient_name.lower().strip()

        # Check for liquid ingredients (price per liter)
        for keyword in cls.LIQUID_INGREDIENTS:
            if keyword in name_lower:
                return (UNIT_LITER, 1, 'liter')

        # Check for piece-based ingredients (price per piece)
        for keyword in cls.PIECE_INGREDIENTS:
            if keyword in name_lower:
                return (UNIT_PIECE, 1, 'piece')

        # Check for slice-based ingredients (price per slice)
        for keyword in cls.SLICE_INGREDIENTS:
            if keyword in name_lower:
                return (UNIT_SLICE, 1, 'slice')

        # Check for bulk ingredients (price per kg - 10x current quantity)
        for keyword in cls.BULK_INGREDIENTS:
            if keyword in name_lower:
                return (UNIT_KILO, 10, 'kilogram')

        # Default: price per 100g for spices and small quantities
        return (UNIT_GRAM, 100, '100g')


class PricingFixer:
    """Fixes pricing data with appropriate units and quantities"""

    def __init__(self, db: Session):
        self.db = db
        self.categorizer = IngredientCategorizer()

    def get_current_pricing(self) -> dict:
        """Get current pricing data grouped by ingredient"""
        query = text("""
            SELECT
                i.id as ingredient_id,
                i.name as ingredient_name,
                ip.id as pricing_id,
                c.code as country_code,
                ip."pricePerUnit",
                ip.quantity,
                ip."measuringUnitId"
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN country c ON c.id = ip."countryId"
            ORDER BY i.name, c.code
        """)

        result = self.db.execute(query)
        return result.fetchall()

    def fix_pricing(self):
        """Update pricing data with appropriate units and quantities"""

        # Get all current pricing data
        current_data = self.get_current_pricing()

        updated_count = 0
        skipped_count = 0

        logger.info(f"Processing {len(current_data)} pricing records...")

        # Group by ingredient
        ingredient_pricing = {}
        for row in current_data:
            ingredient_id = row[0]
            if ingredient_id not in ingredient_pricing:
                ingredient_pricing[ingredient_id] = {
                    'name': row[1],
                    'countries': {}
                }
            ingredient_pricing[ingredient_id]['countries'][row[3]] = {
                'pricing_id': row[2],
                'pricePerUnit': float(row[4]),
                'quantity': int(row[5]),
                'measuringUnitId': row[6]
            }

        # Process each ingredient
        for ingredient_id, data in ingredient_pricing.items():
            ingredient_name = data['name']

            # Determine appropriate unit and quantity
            unit_id, quantity, description = self.categorizer.categorize_ingredient(ingredient_name)

            logger.info(f"Processing '{ingredient_name}': {description}")

            # Get the base price from India (or first available)
            countries_data = data['countries']
            base_price = None
            base_currency = None

            if 'IND' in countries_data:
                base_price = countries_data['IND']['pricePerUnit']
                base_currency = 'IND'
            elif 'NOR' in countries_data:
                base_price = countries_data['NOR']['pricePerUnit']
                base_currency = 'NOR'
            elif 'USA' in countries_data:
                base_price = countries_data['USA']['pricePerUnit']
                base_currency = 'USA'

            if base_price is None:
                logger.warning(f"  Skipping '{ingredient_name}' - no pricing data found")
                skipped_count += 1
                continue

            # Calculate price multipliers for different units
            # Current data is all per 100g
            # - liter: 1 liter = 1000g (water/milk density) -> 10x quantity
            # - piece: 1 piece varies (egg ~50g) -> 0.5x quantity for pricing
            # - kilogram: 1kg = 1000g -> 10x quantity
            # - 100g: stays the same

            current_quantity = countries_data[base_currency]['quantity']

            if unit_id == UNIT_LITER:
                # Convert from per 100g to per liter (for liquids with ~1g/ml density)
                quantity_multiplier = 10
                price_multiplier = 10
            elif unit_id == UNIT_KILO:
                # Convert from per 100g to per kilogram
                quantity_multiplier = 10
                price_multiplier = 10
            elif unit_id == UNIT_PIECE:
                # Convert from per 100g to per piece (average egg ~50g)
                quantity_multiplier = 1
                price_multiplier = 0.5
            elif unit_id == UNIT_SLICE:
                # Convert from per 100g to per slice (average slice ~30g)
                quantity_multiplier = 1
                price_multiplier = 0.3
            elif unit_id == UNIT_GRAM:
                # Keep as is (per 100g)
                quantity_multiplier = 100
                price_multiplier = 1
            else:
                quantity_multiplier = 100
                price_multiplier = 1

            # Update pricing for each country
            for country_code, pricing_info in countries_data.items():
                # Calculate new price
                new_price = round(base_price * price_multiplier, 2)

                # Update the database
                update_query = text("""
                    UPDATE ingredient_pricing
                    SET "pricePerUnit" = :pricePerUnit,
                        quantity = :quantity,
                        "measuringUnitId" = :measuringUnitId
                    WHERE id = :id
                """)

                self.db.execute(update_query, {
                    'pricePerUnit': new_price,
                    'quantity': quantity,
                    'measuringUnitId': unit_id,
                    'id': pricing_info['pricing_id']
                })

                updated_count += 1

            # Commit changes for this ingredient
            self.db.commit()

        logger.info(f"Update complete: {updated_count} records updated, {skipped_count} skipped")

        return updated_count, skipped_count


def main():
    """Main function to run the migration"""
    db = SessionLocal()

    try:
        fixer = PricingFixer(db)

        # Show current state
        logger.info("=== CURRENT STATE ===")
        current_data = fixer.get_current_pricing()

        # Count unique ingredients
        unique_ingredients = set()
        for row in current_data:
            unique_ingredients.add(row[0])

        logger.info(f"Total ingredients with pricing: {len(unique_ingredients)}")
        logger.info(f"Total pricing records: {len(current_data)}")

        # Show sample of current state
        logger.info("\nSample current pricing (first 5 ingredients):")
        sample_count = 0
        for row in current_data:
            if row[3] == 'IND':  # Only show India to avoid duplicates
                logger.info(f"  {row[1]}: {row[5]} {row[6]} = {row[4]}")
                sample_count += 1
                if sample_count >= 5:
                    break

        # Ask for confirmation
        logger.info("\n=== MIGRATION PLAN ===")
        logger.info("This script will:")
        logger.info("1. Assign appropriate units (liter/piece/kilogram/gram) to ingredients")
        logger.info("2. Update quantities and prices accordingly")
        logger.info("3. Update measuringUnitId for all pricing records")

        # Run the migration
        logger.info("\n=== RUNNING MIGRATION ===")
        updated, skipped = fixer.fix_pricing()

        # Show final state
        logger.info("\n=== FINAL STATE ===")

        # Verify the updates
        verify_query = text("""
            SELECT
                i.name as ingredient,
                c.code as country,
                ip."pricePerUnit",
                ip.quantity,
                mut.name as unit_name
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN country c ON c.id = ip."countryId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            WHERE c.code = 'IND'
            ORDER BY i.name
            LIMIT 20
        """)

        result = db.execute(verify_query)
        logger.info("\nSample updated pricing (India only, first 20):")
        for row in result:
            logger.info(f"  {row[0]}: {row[2]} {row[4]} per {row[3]}")

        # Count units used
        unit_count_query = text("""
            SELECT
                mut.name as unit_name,
                COUNT(DISTINCT i.id) as ingredient_count
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            GROUP BY mut.name
            ORDER BY ingredient_count DESC
        """)

        result = db.execute(unit_count_query)
        logger.info("\nUnits distribution:")
        for row in result:
            logger.info(f"  {row[0]}: {row[1]} ingredients")

    finally:
        db.close()


if __name__ == "__main__":
    main()
