"""
Fix Country-Specific Pricing Migration Script
This script updates ingredient pricing with realistic country-specific prices
"""
import sys
import os

from apps.fastapi import logger

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from sqlalchemy.orm import Session
from database import SessionLocal
from models import Ingredient, IngredientPricing
from sqlalchemy import text

class CountryPricingFixer:
    """
    Fixes pricing data with realistic country-specific prices

    Pricing Strategy:
    - India (INR): Base pricing (affordable for Indian market)
    - Norway (NOK): ~2-3x higher than India (high cost of living, strong currency)
    - USA (USD): ~10-15x higher than India in absolute terms (stronger currency)

    Exchange Rate Approximations (2024):
    - 1 USD ≈ 83 INR
    - 1 USD ≈ 10.5 NOK
    - 1 NOK ≈ 8 INR
    """

    # Country-specific pricing multipliers (relative to India base price)
    # These are adjusted for local purchasing power and market prices
    PRICING_MULTIPLIERS = {
        'IND': 1.0,      # Base pricing in INR
        'NOR': 0.125,    # NOK is ~8x stronger than INR, but local prices are higher
        'USA': 0.012,    # USD is ~83x stronger than INR
    }

    # Additional adjustments for specific categories to make prices more realistic
    CATEGORY_ADJUSTMENTS = {
        'IND': 1.0,
        'NOR': 1.5,      # Norway has higher costs for imported goods
        'USA': 1.2,      # US has moderate markups
    }

    def __init__(self, db: Session):
        self.db = db

    def get_current_pricing(self) -> dict:
        """Get current pricing data grouped by ingredient and country"""
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

    def fix_country_pricing(self):
        """Update pricing data with country-specific prices"""

        # Get all current pricing data
        current_data = self.get_current_pricing()

        updated_count = 0

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

            # Get the India price as base
            countries_data = data['countries']

            if 'IND' not in countries_data:
                logger.warning(f"  Skipping '{ingredient_name}' - no India pricing found")
                continue

            base_price_india = countries_data['IND']['pricePerUnit']

            # Update pricing for each country
            for country_code, pricing_info in countries_data.items():
                # Get the multiplier for this country
                multiplier = self.PRICING_MULTIPLIERS.get(country_code, 1.0)

                # Apply category adjustment for more realistic pricing
                category_adj = self.CATEGORY_ADJUSTMENTS.get(country_code, 1.0)

                # Calculate new price
                if country_code == 'IND':
                    # Keep India price as is (base)
                    new_price = base_price_india
                elif country_code == 'NOR':
                    # For Norway: convert from INR to NOK with adjustment
                    # 1 NOK ≈ 8 INR, so divide by ~8, but Norway has higher costs
                    new_price = round(base_price_india * multiplier * category_adj, 2)
                    # Ensure minimum price
                    if new_price < 1:
                        new_price = round(base_price_india / 8, 2)
                elif country_code == 'USA':
                    # For USA: convert from INR to USD
                    # 1 USD ≈ 83 INR, so divide by ~83
                    new_price = round(base_price_india * multiplier * category_adj, 2)
                    # Ensure reasonable price
                    if new_price < 0.1:
                        new_price = round(base_price_india / 83, 2)
                else:
                    new_price = round(base_price_india * multiplier, 2)

                # Update the database
                update_query = text("""
                    UPDATE ingredient_pricing
                    SET "pricePerUnit" = :pricePerUnit
                    WHERE id = :id
                """)

                self.db.execute(update_query, {
                    'pricePerUnit': new_price,
                    'id': pricing_info['pricing_id']
                })

                updated_count += 1

            # Commit changes for this ingredient
            self.db.commit()

        logger.info(f"Update complete: {updated_count} records updated")

        return updated_count


def main():
    """Main function to run the migration"""
    db = SessionLocal()

    try:
        fixer = CountryPricingFixer(db)

        # Show current state
        logger.info("=== CURRENT STATE ===")
        current_query = text("""
            SELECT
                i.name as ingredient,
                c.code as country,
                cur.code as currency,
                ip."pricePerUnit",
                ip.quantity,
                mut.name as unit_name
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN country c ON c.id = ip."countryId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            JOIN currency cur ON cur.id = ip."currencyId"
            WHERE i.name IN ('egg', 'milk', 'flour', 'rice', 'sugar', 'butter', 'chicken')
            ORDER BY i.name, c.code
        """)

        result = db.execute(current_query)
        logger.info("\nSample current pricing (before fix):")
        for row in result:
            logger.info(f"  {row[0]:15} {row[1]:4} {row[2]:6} {row[4]:4} {row[5]:8} = {row[3]:8.2f}")

        # Run the migration
        logger.info("\n=== RUNNING MIGRATION ===")
        updated = fixer.fix_country_pricing()

        # Show final state
        logger.info("\n=== FINAL STATE ===")
        result = db.execute(current_query)
        logger.info("\nSample updated pricing (after fix):")
        for row in result:
            logger.info(f"  {row[0]:15} {row[1]:4} {row[2]:6} {row[4]:4} {row[5]:8} = {row[3]:8.2f}")

        # Show more examples
        logger.info("\n=== MORE EXAMPLES ===")
        more_query = text("""
            SELECT
                i.name as ingredient,
                c.code as country,
                cur.code as currency,
                ip."pricePerUnit",
                ip.quantity,
                mut.name as unit_name
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN country c ON c.id = ip."countryId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            JOIN currency cur ON cur.id = ip."currencyId"
            WHERE c.code = 'IND'
            ORDER BY i.name
            LIMIT 20
        """)

        result = db.execute(more_query)
        logger.info("\nIndia pricing sample (first 20):")
        for row in result:
            logger.info(f"  {row[0]:30} {row[4]:4} {row[5]:8} = {row[3]:8.2f} {row[2]}")

        # Show Norway pricing
        norway_query = text("""
            SELECT
                i.name as ingredient,
                c.code as country,
                cur.code as currency,
                ip."pricePerUnit",
                ip.quantity,
                mut.name as unit_name
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN country c ON c.id = ip."countryId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            JOIN currency cur ON cur.id = ip."currencyId"
            WHERE c.code = 'NOR'
            ORDER BY i.name
            LIMIT 20
        """)

        result = db.execute(norway_query)
        logger.info("\nNorway pricing sample (first 20):")
        for row in result:
            logger.info(f"  {row[0]:30} {row[4]:4} {row[5]:8} = {row[3]:8.2f} {row[2]}")

        # Show US pricing
        us_query = text("""
            SELECT
                i.name as ingredient,
                c.code as country,
                cur.code as currency,
                ip."pricePerUnit",
                ip.quantity,
                mut.name as unit_name
            FROM ingredient_pricing ip
            JOIN ingredient i ON i.id = ip."ingredientId"
            JOIN country c ON c.id = ip."countryId"
            JOIN measuring_unit mu ON mu.id = ip."measuringUnitId"
            JOIN measuring_unit_translation mut ON mut."measuringUnitId" = mu.id AND mut."languageId" = 'en'
            JOIN currency cur ON cur.id = ip."currencyId"
            WHERE c.code = 'USA'
            ORDER BY i.name
            LIMIT 20
        """)

        result = db.execute(us_query)
        logger.info("\nUSA pricing sample (first 20):")
        for row in result:
            logger.info(f"  {row[0]:30} {row[4]:4} {row[5]:8} = {row[3]:8.2f} {row[2]}")

        logger.info(f"\n=== MIGRATION COMPLETE ===")
        logger.info(f"Total records updated: {updated}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
