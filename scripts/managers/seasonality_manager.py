#!/usr/bin/env python3
"""
Recipe Seasonality Manager
Create, view, update, and manage seasonalities mapped to recipes.

Usage:
    python recipe_seasonality_manager.py list-seasonalities [type]
    python recipe_seasonality_manager.py list-recipes [filter]
    python recipe_seasonality_manager.py view <recipe_name>
    python recipe_seasonality_manager.py add <recipe_name>
    python recipe_seasonality_manager.py remove <recipe_name> <seasonality>
    python recipe_seasonality_manager.py auto-map [limit]
    python recipe_seasonality_manager.py stats

Interactive mode:
    python recipe_seasonality_manager.py
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


class SeasonalityManager:
    """Manages recipe seasonalities"""

    def __init__(self):
        self.db = SessionLocal()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db.close()

    def list_seasonalities(self, type_filter: Optional[str] = None) -> List[Dict]:
        """List all seasonalities, optionally filtered by type"""
        query = text("""
            SELECT
                s.id,
                s.type,
                s."isActive",
                st.name,
                st.description
            FROM seasonality s
            JOIN seasonality_translation st ON st."seasonalityId" = s.id AND st."languageId" = 'en'
            WHERE (:filter IS NULL OR s.type = :filter)
            ORDER BY s.type, st.name
        """)
        result = self.db.execute(query, {"filter": type_filter})
        return [{
            'id': row[0],
            'type': row[1],
            'is_active': row[2],
            'name': row[3],
            'description': row[4],
        } for row in result]

    def list_recipes(self, filter_name: Optional[str] = None) -> List[Dict]:
        """List recipes, optionally filtered by name"""
        query = text("""
            SELECT id, name, status
            FROM recipe
            WHERE (:filter IS NULL OR name ILIKE '%' || :filter || '%')
                AND status = 'published'
            ORDER BY name
            LIMIT 50
        """)
        result = self.db.execute(query, {"filter": filter_name})
        return [{"id": row[0], "name": row[1], "status": row[2]} for row in result]

    def get_recipe_by_name(self, name: str) -> Optional[Dict]:
        """Find a recipe by name"""
        query = text("""
            SELECT id, name, status
            FROM recipe
            WHERE name ILIKE :name AND status = 'published'
            LIMIT 1
        """)
        result = self.db.execute(query, {"name": name}).fetchone()
        if result:
            return {"id": result[0], "name": result[1], "status": result[2]}
        return None

    def get_seasonality_by_name(self, name: str) -> Optional[Dict]:
        """Find a seasonality by name"""
        query = text("""
            SELECT s.id, s.type, st.name, st."languageId"
            FROM seasonality s
            JOIN seasonality_translation st ON st."seasonalityId" = s.id
            WHERE st.name ILIKE :name
            LIMIT 1
        """)
        result = self.db.execute(query, {"name": name}).fetchone()
        if result:
            return {"id": result[0], "type": result[1], "name": result[2]}
        return None

    def get_recipe_seasonalities(self, recipe_id: str) -> List[Dict]:
        """Get all seasonalities for a recipe"""
        query = text("""
            SELECT
                s.id,
                s.type,
                st.name,
                st.description
            FROM recipe_seasonality rs
            JOIN seasonality s ON s.id = rs."seasonalityId"
            JOIN seasonality_translation st ON st."seasonalityId" = s.id AND st."languageId" = 'en'
            WHERE rs."recipeId" = :recipeId
            ORDER BY s.type, st.name
        """)
        result = self.db.execute(query, {"recipeId": recipe_id})
        return [{
            'id': row[0],
            'type': row[1],
            'name': row[2],
            'description': row[3],
        } for row in result]

    def add_seasonality_to_recipe(self, recipe_id: str, seasonality_id: str) -> bool:
        """Add a seasonality to a recipe"""
        try:
            self.db.execute(text("""
                INSERT INTO recipe_seasonality ("recipeId", "seasonalityId", "createdAt")
                VALUES (:recipeId, :seasonalityId, now())
                ON CONFLICT ("recipeId", "seasonalityId") DO NOTHING
            """), {
                "recipeId": recipe_id,
                "seasonalityId": seasonality_id
            })
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error adding seasonality: {e}")
            return False

    def remove_seasonality_from_recipe(self, recipe_id: str, seasonality_id: str) -> bool:
        """Remove a seasonality from a recipe"""
        try:
            result = self.db.execute(text("""
                DELETE FROM recipe_seasonality
                WHERE "recipeId" = :recipeId AND "seasonalityId" = :seasonalityId
            """), {
                "recipeId": recipe_id,
                "seasonalityId": seasonality_id
            })
            self.db.commit()
            return result.rowcount > 0
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error removing seasonality: {e}")
            return False

    def get_stats(self) -> Dict:
        """Get seasonality statistics"""
        stats = self.db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM recipe WHERE status = 'published') as total_recipes,
                (SELECT COUNT(DISTINCT "recipeId") FROM recipe_seasonality) as recipes_with_seasonality,
                (SELECT COUNT(*) FROM recipe_seasonality) as total_mappings,
                (SELECT COUNT(*) FROM seasonality) as total_seasonalities
        """)).fetchone()

        # Count by seasonality type
        by_type = self.db.execute(text("""
            SELECT
                s.type,
                COUNT(*) as count
            FROM recipe_seasonality rs
            JOIN seasonality s ON s.id = rs."seasonalityId"
            GROUP BY s.type
            ORDER BY s.type
        """)).fetchall()

        # Top seasonalities
        top_seasonalities = self.db.execute(text("""
            SELECT
                s.type,
                st.name,
                COUNT(*) as recipe_count
            FROM recipe_seasonality rs
            JOIN seasonality s ON s.id = rs."seasonalityId"
            JOIN seasonality_translation st ON st."seasonalityId" = s.id AND st."languageId" = 'en'
            GROUP BY s.type, st.name
            ORDER BY recipe_count DESC
            LIMIT 10
        """)).fetchall()

        return {
            "total_recipes": stats[0],
            "with_seasonality": stats[1],
            "total_mappings": stats[2],
            "total_seasonalities": stats[3],
            "coverage": round((stats[1] / stats[0] * 100) if stats[0] > 0 else 0, 1),
            "by_type": {row[0]: row[1] for row in by_type},
            "top_seasonalities": [(row[0], row[1], row[2]) for row in top_seasonalities]
        }


def print_seasonalities(seasonalities: List[Dict]):
    """Pretty print seasonalities"""
    if not seasonalities:
        logger.info("\n  No seasonalities found.")
        return

    current_type = None
    for s in seasonalities:
        if s['type'] != current_type:
            if current_type is not None:
                logger.info("")
            logger.info(f"\n  📂 {s['type']}")
            logger.info("  " + "-" * 50)
            current_type = s['type']
        logger.info(f"     • {s['name']}")


def print_recipe_seasonalities(recipe_name: str, seasonalities: List[Dict]):
    """Pretty print recipe seasonalities"""
    if not seasonalities:
        logger.info(f"\n  '{recipe_name}' has no seasonalities assigned.")
        logger.info("  Use 'add' command to add seasonalities.")
        return

    logger.info(f"\n  📋 {recipe_name}")
    logger.info("  " + "=" * 60)

    current_type = None
    for s in seasonalities:
        if s['type'] != current_type:
            if current_type is not None:
                logger.info("")
            logger.info(f"\n  [{s['type']}]")
            current_type = s['type']
        logger.info(f"     • {s['name']}")


def interactive_add():
    """Interactive mode for adding seasonalities to recipes"""
    manager = SeasonalityManager()

    logger.info("\n🍂 Add Seasonality to Recipe")
    logger.info("=" * 50)

    # Get recipe name
    recipe_name = input("\nEnter recipe name (or 'search' to search): ").strip()

    if recipe_name.lower() == 'search':
        search_term = input("Search term: ").strip()
        recipes = manager.list_recipes(search_term)
        if recipes:
            logger.info(f"\nFound {len(recipes)} recipes:")
            for i, recipe in enumerate(recipes, 1):
                logger.info(f"  {i}. {recipe['name']}")
            choice = input(f"\nSelect number (1-{len(recipes)}): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(recipes):
                recipe_name = recipes[int(choice) - 1]['name']
            else:
                logger.error("Invalid selection")
                return
        else:
            logger.error("No recipes found")
            return

    recipe = manager.get_recipe_by_name(recipe_name)
    if not recipe:
        logger.error(f"Recipe '{recipe_name}' not found")
        return

    logger.info(f"\n✓ Found recipe: {recipe['name']}")

    # Show existing seasonalities
    existing = manager.get_recipe_seasonalities(recipe['id'])
    if existing:
        logger.info("\n⚠️  This recipe already has seasonalities:")
        print_recipe_seasonalities(recipe['name'], existing)
        cont = input("\nAdd more seasonalities? (y/n): ").strip().lower()
        if cont != 'y':
            return

    # Show available seasonalities by type
    all_seasonalities = manager.list_seasonalities()

    logger.info("\n--- Available Seasonalities ---")
    print_seasonalities(all_seasonalities)

    # Get seasonality to add
    logger.info("\nYou can add multiple seasonalities. Enter 'done' when finished.")
    logger.info("You can enter seasonality name or type (e.g., 'Summer' or 'WEATHER')")

    while True:
        seasonality_name = input("\nEnter seasonality name (or 'done'): ").strip()
        if not seasonality_name or seasonality_name.lower() == 'done':
            break

        # Try to find by name first
        seasonality = manager.get_seasonality_by_name(seasonality_name)

        if not seasonality:
            # Try to find by type - show options and let user select
            matching = [s for s in all_seasonalities if s['type'].lower() == seasonality_name.lower()]
            if matching:
                logger.info(f"\nFound {len(matching)} seasonalities of type '{seasonality_name}':")
                for i, s in enumerate(matching, 1):
                    logger.info(f"  {i}. {s['name']}")
                choice = input(f"\nSelect number (1-{len(matching)}), or 'done' to skip: ").strip()
                if choice.lower() == 'done':
                    continue
                if choice.isdigit() and 1 <= int(choice) <= len(matching):
                    seasonality = matching[int(choice) - 1]

        if not seasonality:
            logger.error(f"Seasonality '{seasonality_name}' not found")
            continue

        if manager.add_seasonality_to_recipe(recipe['id'], seasonality['id']):
            logger.info(f"  ✓ Added '{seasonality['name']}' ({seasonality['type']})")

    logger.info("\n✅ Seasonalities updated successfully!")


def cmd_list_seasonalities(args):
    """List all seasonalities"""
    with SeasonalityManager() as manager:
        type_filter = args[0] if len(args) > 0 else None
        seasonalities = manager.list_seasonalities(type_filter)
        print_seasonalities(seasonalities)


def cmd_list_recipes(args):
    """List recipes"""
    with SeasonalityManager() as manager:
        filter_name = args[0] if len(args) > 0 else None
        recipes = manager.list_recipes(filter_name)

        if recipes:
            logger.info(f"\n{'Recipe Name':<50} {'Status'}")
            logger.info("-" * 60)
            for recipe in recipes:
                logger.info(f"{recipe['name']:<50} {recipe['status']}")
            logger.info(f"\nTotal: {len(recipes)} recipes")
        else:
            logger.info("No recipes found")


def cmd_view(args):
    """View recipe seasonalities"""
    if len(args) < 1:
        logger.error("Usage: view <recipe_name>")
        return

    with SeasonalityManager() as manager:
        recipe = manager.get_recipe_by_name(args[0])
        if not recipe:
            logger.error(f"Recipe '{args[0]}' not found")
            return

        seasonalities = manager.get_recipe_seasonalities(recipe['id'])
        print_recipe_seasonalities(recipe['name'], seasonalities)


def cmd_remove(args):
    """Remove seasonality from recipe"""
    if len(args) < 2:
        logger.error("Usage: remove <recipe_name> <seasonality_name>")
        return

    with SeasonalityManager() as manager:
        recipe = manager.get_recipe_by_name(args[0])
        if not recipe:
            logger.error(f"Recipe '{args[0]}' not found")
            return

        seasonality = manager.get_seasonality_by_name(args[1])
        if not seasonality:
            logger.error(f"Seasonality '{args[1]}' not found")
            return

        if manager.remove_seasonality_from_recipe(recipe['id'], seasonality['id']):
            logger.info(f"\n✅ Removed '{seasonality['name']}' from '{recipe['name']}'")


def cmd_stats(args):
    """Show seasonality statistics"""
    with SeasonalityManager() as manager:
        stats = manager.get_stats()

        logger.info("\n📊 Seasonality Statistics")
        logger.info("=" * 50)
        logger.info(f"Total Recipes:            {stats['total_recipes']}")
        logger.info(f"With Seasonalities:       {stats['with_seasonality']} ({stats['coverage']}%)")
        logger.info(f"Total Mappings:           {stats['total_mappings']}")
        logger.info(f"Total Seasonalities:      {stats['total_seasonalities']}")

        logger.info("\nBy Type:")
        for type_name, count in stats['by_type'].items():
            logger.info(f"  {type_name:20} {count:4} mappings")

        logger.info("\nTop Seasonalities:")
        for type_name, name, count in stats['top_seasonalities']:
            logger.info(f"  [{type_name}] {name:30} {count:4} recipes")


def show_help():
    """Show help message"""
    logger.info("""
╔════════════════════════════════════════════════════════════════╗
║              Recipe Seasonality Manager                       ║
╠════════════════════════════════════════════════════════════════╣
║  Commands:                                                      ║
║    list-seasonalities [type]                                   ║
║                       List all seasonalities                    ║
║    list-recipes [filter]        List recipes (optionally filter) ║
║    view <recipe_name>         View seasonalities for recipe      ║
║    add                        Interactive mode to add             ║
║    remove <recipe> <seasonality>                               ║
║                               Remove seasonality from recipe     ║
║    stats                      Show coverage statistics          ║
║    help                       Show this help message              ║
╠════════════════════════════════════════════════════════════════╣
║  Seasonality Types:                                             ║
║    WEATHER, FESTIVAL, INGREDIENT_AVAILABILITY,                  ║
║    CULTURAL_OCCASION, DIETARY_PRACTICE, MEAL_TIMING,            ║
║    LIFESTYLE, REGIONAL, HEALTH_CYCLE                            ║
╠════════════════════════════════════════════════════════════════╣
║  Examples:                                                      ║
║    python recipe_seasonality_manager.py list-seasonalities      ║
║    python recipe_seasonality_manager.py list-seasonalities WEATHER║
║    python recipe_seasonality_manager.py view "Pumpkin Soup"     ║
║    python recipe_seasonality_manager.py add                    ║
║    python recipe_seasonality_manager.py remove "Pizza" Winter   ║
║    python recipe_seasonality_manager.py stats                  ║
╚════════════════════════════════════════════════════════════════╝
    """)


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()
    args = sys.argv[2:]

    if command == 'list-seasonalities':
        cmd_list_seasonalities(args)
    elif command == 'list-recipes':
        cmd_list_recipes(args)
    elif command == 'view':
        cmd_view(args)
    elif command == 'add':
        interactive_add()
    elif command == 'remove':
        cmd_remove(args)
    elif command == 'stats':
        cmd_stats(args)
    elif command == 'help' or command == '--help' or command == '-h':
        show_help()
    else:
        logger.error(f"Unknown command: {command}")
        show_help()


if __name__ == "__main__":
    main()
