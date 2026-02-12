"""
Remove Seasonality Types Script
Removes specific seasonality types and all related data.
Types to remove: health_cycle, meal_timing, dietary_practice, cultural_occasion,
                 ingredient_availability, lifestyle, regional

Types to keep: WEATHER, FESTIVAL
"""
import sys
import os

from apps.fastapi import logger

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from sqlalchemy.orm import Session
from database import SessionLocal
from sqlalchemy import text

# Types to remove
TYPES_TO_REMOVE = [
    'HEALTH_CYCLE',
    'MEAL_TIMING',
    'DIETARY_PRACTICE',
    'CULTURAL_OCCASION',
    'INGREDIENT_AVAILABILITY',
    'LIFESTYLE',
    'REGIONAL'
]


class SeasonalityTypeRemover:
    """Removes seasonality types and all related data"""

    def __init__(self, db: Session):
        self.db = db

    def show_current_state(self):
        """Show current state before deletion"""
        # Count by type
        query = text("""
            SELECT
                s.type,
                COUNT(DISTINCT s.id) as seasonality_count,
                COUNT(DISTINCT rs."recipeId") as recipe_count,
                COUNT(rs.*) as mapping_count
            FROM seasonality s
            LEFT JOIN recipe_seasonality rs ON rs."seasonalityId" = s.id
            GROUP BY s.type
            ORDER BY s.type
        """)
        result = self.db.execute(query)

        logger.info("\n=== CURRENT STATE ===")
        logger.info(f"{'Type':<25} {'Seasonalities':<15} {'Recipes':<10} {'Mappings':<10}")
        logger.info("-" * 60)

        for row in result:
            type_name = row[0]
            to_delete = " [TO DELETE]" if type_name in TYPES_TO_REMOVE else ""
            logger.info(f"{row[0]:<25} {row[1]:<15} {row[2]:<10} {row[3]:<10}{to_delete}")

    def get_seasonalities_to_remove(self):
        """Get all seasonality IDs that will be removed"""
        query = text("""
            SELECT id, type
            FROM seasonality
            WHERE type = ANY(:types)
        """)
        result = self.db.execute(query, {"types": TYPES_TO_REMOVE})
        return [(row[0], row[1]) for row in result]

    def remove_recipe_mappings(self, seasonality_ids):
        """Remove recipe_seasonality mappings for the seasonalities"""
        if not seasonality_ids:
            return 0

        query = text("""
            DELETE FROM recipe_seasonality
            WHERE "seasonalityId" = ANY(:seasonality_ids)
        """)

        result = self.db.execute(query, {"seasonality_ids": seasonality_ids})
        count = result.rowcount
        self.db.commit()
        return count

    def remove_translations(self, seasonality_ids):
        """Remove seasonality_translation records for the seasonalities"""
        if not seasonality_ids:
            return 0

        query = text("""
            DELETE FROM seasonality_translation
            WHERE "seasonalityId" = ANY(:seasonality_ids)
        """)

        result = self.db.execute(query, {"seasonality_ids": seasonality_ids})
        count = result.rowcount
        self.db.commit()
        return count

    def remove_seasonalities(self):
        """Remove seasonality entries"""
        query = text("""
            DELETE FROM seasonality
            WHERE type = ANY(:types)
        """)

        result = self.db.execute(query, {"types": TYPES_TO_REMOVE})
        count = result.rowcount
        self.db.commit()
        return count

    def remove(self):
        """Execute the complete removal process"""
        logger.info("\n=== REMOVING SEASONALITY TYPES ===")
        logger.info(f"Types to remove: {', '.join(TYPES_TO_REMOVE)}")

        # Get seasonalities to remove
        seasonalities = self.get_seasonalities_to_remove()
        seasonality_ids = [s[0] for s in seasonalities]

        if not seasonality_ids:
            logger.info("\nNo seasonalities found to remove.")
            return

        logger.info(f"\nFound {len(seasonality_ids)} seasonalities to remove")

        # Show breakdown by type
        for type_name in TYPES_TO_REMOVE:
            count = sum(1 for s in seasonalities if s[1] == type_name)
            if count > 0:
                logger.info(f"  - {type_name}: {count} seasonalities")

        # Confirm
        confirm = input("\nProceed with deletion? (yes/no): ").strip().lower()
        if confirm != 'yes':
            logger.info("Aborted.")
            return

        # Remove in correct order due to foreign keys
        logger.info("\nRemoving...")

        # 1. Remove recipe mappings
        mapping_count = self.remove_recipe_mappings(seasonality_ids)
        logger.info(f"  ✓ Removed {mapping_count} recipe-seasonality mappings")

        # 2. Remove translations
        translation_count = self.remove_translations(seasonality_ids)
        logger.info(f"  ✓ Removed {translation_count} translations")

        # 3. Remove seasonality entries
        seasonality_count = self.remove_seasonalities()
        logger.info(f"  ✓ Removed {seasonality_count} seasonality entries")

        logger.info("\n✅ Removal complete!")

    def show_final_state(self):
        """Show state after deletion"""
        query = text("""
            SELECT
                s.type,
                COUNT(DISTINCT s.id) as seasonality_count,
                COUNT(DISTINCT rs."recipeId") as recipe_count,
                COUNT(rs.*) as mapping_count
            FROM seasonality s
            LEFT JOIN recipe_seasonality rs ON rs."seasonalityId" = s.id
            GROUP BY s.type
            ORDER BY s.type
        """)
        result = self.db.execute(query)

        logger.info("\n=== FINAL STATE ===")
        logger.info(f"{'Type':<25} {'Seasonalities':<15} {'Recipes':<10} {'Mappings':<10}")
        logger.info("-" * 60)

        total_seasonalities = 0
        total_recipes = 0
        total_mappings = 0

        for row in result:
            logger.info(f"{row[0]:<25} {row[1]:<15} {row[2]:<10} {row[3]:<10}")
            total_seasonalities += row[1]
            total_recipes = max(total_recipes, row[2])
            total_mappings += row[3]

        logger.info("-" * 60)
        logger.info(f"{'TOTAL':<25} {total_seasonalities:<15} {total_recipes:<10} {total_mappings:<10}")


def main():
    """Main function"""
    db = SessionLocal()

    try:
        remover = SeasonalityTypeRemover(db)

        # Show current state
        remover.show_current_state()

        # Perform removal
        remover.remove()

        # Show final state
        remover.show_final_state()

        # Show remaining seasonalities
        logger.info("\n=== REMAINING SEASONALITIES ===")
        query = text("""
            SELECT s.type, st.name
            FROM seasonality s
            JOIN seasonality_translation st ON st."seasonalityId" = s.id
            WHERE st."languageId" = 'en'
            ORDER BY s.type, st.name
        """)
        result = db.execute(query)

        current_type = None
        for row in result:
            if row[0] != current_type:
                logger.info(f"\n[{row[0]}]")
                current_type = row[0]
            logger.info(f"  • {row[1]}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
