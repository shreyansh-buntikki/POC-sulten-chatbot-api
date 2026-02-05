#!/usr/bin/env python3
"""
Ingredient Nutrition Manager
Create, view, update, and map macros and micros to ingredients.

Usage:
    python ingredient_nutrition_manager.py list [ingredient_name]
    python ingredient_nutrition_manager.py view <ingredient_name>
    python ingredient_nutrition_manager.py add <ingredient_name>
    python ingredient_nutrition_manager.py update <ingredient_name>
    python ingredient_nutrition_manager.py fetch <ingredient_name>
    python ingredient_nutrition_manager.py import-ingredients <csv_file>

Interactive mode:
    python ingredient_nutrition_manager.py
"""
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import uuid
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from database import SessionLocal
from sqlalchemy import text
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class NutritionManager:
    """Manages ingredient nutrition data (macros and micros)"""

    def __init__(self):
        self.db = SessionLocal()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db.close()

    def list_ingredients(self, filter_name: Optional[str] = None) -> list:
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

    def get_nutrition(self, ingredient_id: str) -> Dict:
        """Get nutrition data for an ingredient"""
        # Get macros
        macros_query = text("""
            SELECT * FROM ingredient_macros
            WHERE "ingredientId" = :ingredientId
        """)
        macros = self.db.execute(macros_query, {"ingredientId": ingredient_id}).fetchone()

        # Get micros
        micros_query = text("""
            SELECT * FROM ingredient_micros
            WHERE "ingredientId" = :ingredientId
        """)
        micros = self.db.execute(micros_query, {"ingredientId": ingredient_id}).fetchone()

        return {
            "macros": macros,
            "micros": micros
        }

    def create_macros(
        self,
        ingredient_id: str,
        serving_size: int = 100,
        energy_kcal: Optional[float] = None,
        energy_kj: Optional[float] = None,
        protein: Optional[float] = None,
        carbohydrates: Optional[float] = None,
        total_fiber: Optional[float] = None,
        total_sugars: Optional[float] = None,
        total_fat: Optional[float] = None,
        saturated_fat: Optional[float] = None,
        trans_fat: Optional[float] = None,
        cholesterol: Optional[float] = None,
    ) -> bool:
        """Create or update macros for an ingredient"""
        try:
            # Check if exists
            existing = self.db.execute(
                text('SELECT id FROM ingredient_macros WHERE "ingredientId" = :ingredientId'),
                {"ingredientId": ingredient_id}
            ).fetchone()

            if existing:
                # Update
                self.db.execute(text("""
                    UPDATE ingredient_macros
                    SET "servingSize" = :serving_size,
                        "energyKcal" = :energy_kcal,
                        "energyKj" = :energy_kj,
                        protein = :protein,
                        carbohydrates = :carbohydrates,
                        "totalFiber" = :total_fiber,
                        "totalSugars" = :total_sugars,
                        "totalFat" = :total_fat,
                        "saturatedFat" = :saturated_fat,
                        "transFat" = :trans_fat,
                        cholesterol = :cholesterol,
                        "updatedAt" = now()
                    WHERE "ingredientId" = :ingredient_id
                """), {
                    "ingredient_id": ingredient_id,
                    "serving_size": serving_size,
                    "energy_kcal": energy_kcal,
                    "energy_kj": energy_kj,
                    "protein": protein,
                    "carbohydrates": carbohydrates,
                    "total_fiber": total_fiber,
                    "total_sugars": total_sugars,
                    "total_fat": total_fat,
                    "saturated_fat": saturated_fat,
                    "trans_fat": trans_fat,
                    "cholesterol": cholesterol,
                })
            else:
                # Create
                self.db.execute(text("""
                    INSERT INTO ingredient_macros
                    (id, "ingredientId", "servingSize", "energyKcal", "energyKj",
                     protein, carbohydrates, "totalFiber", "totalSugars", "totalFat",
                     "saturatedFat", "transFat", cholesterol, "createdAt", "updatedAt")
                    VALUES
                    (:id, :ingredient_id, :serving_size, :energy_kcal, :energy_kj,
                     :protein, :carbohydrates, :total_fiber, :total_sugars, :total_fat,
                     :saturated_fat, :trans_fat, :cholesterol, now(), now())
                """), {
                    "id": str(uuid.uuid4()),
                    "ingredient_id": ingredient_id,
                    "serving_size": serving_size,
                    "energy_kcal": energy_kcal,
                    "energy_kj": energy_kj,
                    "protein": protein,
                    "carbohydrates": carbohydrates,
                    "total_fiber": total_fiber,
                    "total_sugars": total_sugars,
                    "total_fat": total_fat,
                    "saturated_fat": saturated_fat,
                    "trans_fat": trans_fat,
                    "cholesterol": cholesterol,
                })

            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving macros: {e}")
            return False

    def create_micros(
        self,
        ingredient_id: str,
        serving_size: int = 100,
        vitamin_a: Optional[float] = None,
        vitamin_c: Optional[float] = None,
        vitamin_d: Optional[float] = None,
        vitamin_e: Optional[float] = None,
        vitamin_k: Optional[float] = None,
        thiamine_b1: Optional[float] = None,
        riboflavin_b2: Optional[float] = None,
        niacin_b3: Optional[float] = None,
        vitamin_b6: Optional[float] = None,
        folate_b9: Optional[float] = None,
        vitamin_b12: Optional[float] = None,
        calcium: Optional[float] = None,
        iron: Optional[float] = None,
        magnesium: Optional[float] = None,
        phosphorus: Optional[float] = None,
        potassium: Optional[float] = None,
        sodium: Optional[float] = None,
        zinc: Optional[float] = None,
    ) -> bool:
        """Create or update micros for an ingredient"""
        try:
            # Check if exists
            existing = self.db.execute(
                text('SELECT id FROM ingredient_micros WHERE "ingredientId" = :ingredientId'),
                {"ingredientId": ingredient_id}
            ).fetchone()

            if existing:
                # Update
                self.db.execute(text("""
                    UPDATE ingredient_micros
                    SET "servingSize" = :serving_size,
                        "vitaminA" = :vitamin_a,
                        "vitaminC" = :vitamin_c,
                        "vitaminD" = :vitamin_d,
                        "vitaminE" = :vitamin_e,
                        "vitaminK" = :vitamin_k,
                        "thiamineB1" = :thiamine_b1,
                        "riboflavinB2" = :riboflavin_b2,
                        "niacinB3" = :niacin_b3,
                        "vitaminB6" = :vitamin_b6,
                        "folateB9" = :folate_b9,
                        "vitaminB12" = :vitamin_b12,
                        calcium = :calcium,
                        iron = :iron,
                        magnesium = :magnesium,
                        phosphorus = :phosphorus,
                        potassium = :potassium,
                        sodium = :sodium,
                        zinc = :zinc,
                        "updatedAt" = now()
                    WHERE "ingredientId" = :ingredient_id
                """), {
                    "ingredient_id": ingredient_id,
                    "serving_size": serving_size,
                    "vitamin_a": vitamin_a,
                    "vitamin_c": vitamin_c,
                    "vitamin_d": vitamin_d,
                    "vitamin_e": vitamin_e,
                    "vitamin_k": vitamin_k,
                    "thiamine_b1": thiamine_b1,
                    "riboflavin_b2": riboflavin_b2,
                    "niacin_b3": niacin_b3,
                    "vitamin_b6": vitamin_b6,
                    "folate_b9": folate_b9,
                    "vitamin_b12": vitamin_b12,
                    "calcium": calcium,
                    "iron": iron,
                    "magnesium": magnesium,
                    "phosphorus": phosphorus,
                    "potassium": potassium,
                    "sodium": sodium,
                    "zinc": zinc,
                })
            else:
                # Create
                self.db.execute(text("""
                    INSERT INTO ingredient_micros
                    (id, "ingredientId", "servingSize",
                     "vitaminA", "vitaminC", "vitaminD", "vitaminE", "vitaminK",
                     "thiamineB1", "riboflavinB2", "niacinB3", "vitaminB6", "folateB9", "vitaminB12",
                     calcium, iron, magnesium, phosphorus, potassium, sodium, zinc,
                     "createdAt", "updatedAt")
                    VALUES
                    (:id, :ingredient_id, :serving_size,
                     :vitamin_a, :vitamin_c, :vitamin_d, :vitamin_e, :vitamin_k,
                     :thiamine_b1, :riboflavin_b2, :niacin_b3, :vitamin_b6, :folate_b9, :vitamin_b12,
                     :calcium, :iron, :magnesium, :phosphorus, :potassium, :sodium, :zinc,
                     now(), now())
                """), {
                    "id": str(uuid.uuid4()),
                    "ingredient_id": ingredient_id,
                    "serving_size": serving_size,
                    "vitamin_a": vitamin_a,
                    "vitamin_c": vitamin_c,
                    "vitamin_d": vitamin_d,
                    "vitamin_e": vitamin_e,
                    "vitamin_k": vitamin_k,
                    "thiamine_b1": thiamine_b1,
                    "riboflavin_b2": riboflavin_b2,
                    "niacin_b3": niacin_b3,
                    "vitamin_b6": vitamin_b6,
                    "folate_b9": folate_b9,
                    "vitamin_b12": vitamin_b12,
                    "calcium": calcium,
                    "iron": iron,
                    "magnesium": magnesium,
                    "phosphorus": phosphorus,
                    "potassium": potassium,
                    "sodium": sodium,
                    "zinc": zinc,
                })

            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving micros: {e}")
            return False

    def get_nutrition_stats(self) -> Dict:
        """Get statistics about nutrition coverage"""
        stats = self.db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM ingredient) as total_ingredients,
                (SELECT COUNT(*) FROM ingredient_macros) as ingredients_with_macros,
                (SELECT COUNT(*) FROM ingredient_micros) as ingredients_with_micros
        """)).fetchone()

        return {
            "total_ingredients": stats[0],
            "with_macros": stats[1],
            "with_micros": stats[2],
            "macros_coverage": round((stats[1] / stats[0] * 100) if stats[0] > 0 else 0, 1),
            "micros_coverage": round((stats[2] / stats[0] * 100) if stats[0] > 0 else 0, 1),
        }


def print_nutrition(nutrition: Dict):
    """Pretty print nutrition data"""
    if nutrition["macros"]:
        m = nutrition["macros"]
        logger.info(f"\n  ╔══ MACRONUTRIENTS (per {m[1]}g serving)")
        logger.info(f"  ║  Energy:     {m[2]:.0f} kcal / {m[3]:.0f} kJ")
        logger.info(f"  ║  Protein:    {m[4]:.1f}g")
        logger.info(f"  ║  Carbs:      {m[5]:.1f}g")
        logger.info(f"  ║    Fiber:    {m[6]:.1f}g")
        logger.info(f"  ║    Sugars:   {m[7]:.1f}g")
        logger.info(f"  ║  Fat:        {m[8]:.1f}g")
        logger.info(f"  ║    Saturated: {m[9]:.1f}g")
        logger.info(f"  ║    Trans:    {m[10]:.1f}g")
        logger.info(f"  ║  Cholesterol: {m[11]:.1f}mg")
        logger.info(f"  ╚════════════════════════════════════════")

    if nutrition["micros"]:
        mic = nutrition["micros"]
        logger.info(f"\n  ╔══ MICRONUTRIENTS (per {mic[1]}g serving)")
        logger.info(f"  ║  Vitamin A:  {mic[2]:.0f} mcg")
        logger.info(f"  ║  Vitamin C:  {mic[3]:.0f} mg")
        logger.info(f"  ║  Vitamin D:  {mic[4]:.0f} mcg")
        logger.info(f"  ║  Vitamin E:  {mic[5]:.0f} mg")
        logger.info(f"  ║  Vitamin K:  {mic[6]:.0f} mcg")
        logger.info(f"  ║  B1 (Thiamine): {mic[7]:.0f} mg")
        logger.info(f"  ║  B2 (Riboflavin): {mic[8]:.0f} mg")
        logger.info(f"  ║  B3 (Niacin):   {mic[9]:.0f} mg")
        logger.info(f"  ║  B6:           {mic[10]:.0f} mg")
        logger.info(f"  ║  B9 (Folate):  {mic[11]:.0f} mcg")
        logger.info(f"  ║  B12:          {mic[12]:.0f} mcg")
        logger.info(f"  ║  Calcium:      {mic[13]:.0f} mg")
        logger.info(f"  ║  Iron:         {mic[14]:.0f} mg")
        logger.info(f"  ║  Magnesium:    {mic[15]:.0f} mg")
        logger.info(f"  ║  Phosphorus:   {mic[16]:.0f} mg")
        logger.info(f"  ║  Potassium:    {mic[17]:.0f} mg")
        logger.info(f"  ║  Sodium:       {mic[18]:.0f} mg")
        logger.info(f"  ║  Zinc:         {mic[19]:.0f} mg")
        logger.info(f"  ╚══════════════════════════════════════════════════")


def interactive_add():
    """Interactive mode for adding nutrition data"""
    manager = NutritionManager()

    logger.info("\n🥗 Add Nutrition Data to Ingredient")
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

    # Get existing nutrition
    existing = manager.get_nutrition(ingredient['id'])
    if existing['macros'] or existing['micros']:
        logger.info("\n⚠️  This ingredient already has nutrition data:")
        print_nutrition(existing)
        update = input("\nUpdate existing data? (y/n): ").strip().lower()
        if update != 'y':
            return

    # Input macros
    logger.info("\n--- Macronutrients (per 100g) ---")
    logger.info("Press Enter to leave a field empty")

    try:
        serving_size = 100
        energy_kcal = float_input("Energy (kcal)") or None
        energy_kj = float_input("Energy (kJ)") or None
        protein = float_input("Protein (g)") or None
        carbs = float_input("Carbohydrates (g)") or None
        fiber = float_input("Fiber (g)") or None
        sugars = float_input("Sugars (g)") or None
        fat = float_input("Total Fat (g)") or None
        sat_fat = float_input("Saturated Fat (g)") or None
        trans_fat = float_input("Trans Fat (g)") or None
        cholesterol = float_input("Cholesterol (mg)") or None

        if manager.create_macros(
            ingredient['id'], serving_size, energy_kcal, energy_kj,
            protein, carbs, fiber, sugars, fat, sat_fat, trans_fat, cholesterol
        ):
            logger.info("✓ Macros saved")

        # Input micros
        logger.info("\n--- Micronutrients (per 100g) ---")
        logger.info("(Optional - press Enter to skip)")

        vit_a = float_input("Vitamin A (mcg)")
        vit_c = float_input("Vitamin C (mg)")
        vit_d = float_input("Vitamin D (mcg)")
        vit_e = float_input("Vitamin E (mg)")
        vit_k = float_input("Vitamin K (mcg)")
        thia_b1 = float_input("Vitamin B1/Thiamine (mg)")
        ribo_b2 = float_input("Vitamin B2/Riboflavin (mg)")
        nia_b3 = float_input("Vitamin B3/Niacin (mg)")
        vit_b6 = float_input("Vitamin B6 (mg)")
        fol_b9 = float_input("Vitamin B9/Folate (mcg)")
        vit_b12 = float_input("Vitamin B12 (mcg)")
        calc = float_input("Calcium (mg)")
        ir = float_input("Iron (mg)")
        magn = float_input("Magnesium (mg)")
        phos = float_input("Phosphorus (mg)")
        pot = float_input("Potassium (mg)")
        sod = float_input("Sodium (mg)")
        zn = float_input("Zinc (mg)")

        if manager.create_micros(
            ingredient['id'], serving_size,
            vit_a, vit_c, vit_d, vit_e, vit_k,
            thia_b1, ribo_b2, nia_b3, vit_b6, fol_b9, vit_b12,
            calc, ir, magn, phos, pot, sod, zn
        ):
            logger.info("✓ Micros saved")

        logger.info("\n✅ Nutrition data saved successfully!")

    except ValueError as e:
        logger.error(f"Invalid input: {e}")


def float_input(prompt: str) -> Optional[float]:
    """Get float input with optional empty value"""
    while True:
        value = input(f"  {prompt}: ").strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            logger.error("  Please enter a valid number or press Enter to skip")


def cmd_list(args):
    """List ingredients"""
    with NutritionManager() as manager:
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
    """View ingredient nutrition"""
    if len(args) < 1:
        logger.error("Usage: view <ingredient_name>")
        return

    with NutritionManager() as manager:
        ingredient = manager.get_ingredient_by_name(args[0])
        if not ingredient:
            logger.error(f"Ingredient '{args[0]}' not found")
            return

        nutrition = manager.get_nutrition(ingredient['id'])

        logger.info(f"\n{'='*60}")
        logger.info(f"INGREDIENT: {ingredient['name']}")
        logger.info(f"{'='*60}")

        if not nutrition['macros'] and not nutrition['micros']:
            logger.info("\n  No nutrition data found for this ingredient.")
            logger.info("  Use 'add' command to add nutrition data.")
        else:
            print_nutrition(nutrition)


def cmd_stats(args):
    """Show nutrition statistics"""
    with NutritionManager() as manager:
        stats = manager.get_nutrition_stats()

        logger.info("\n📊 Nutrition Data Statistics")
        logger.info("=" * 40)
        logger.info(f"Total Ingredients:     {stats['total_ingredients']}")
        logger.info(f"With Macros:           {stats['with_macros']} ({stats['macros_coverage']}%)")
        logger.info(f"With Micros:           {stats['with_micros']} ({stats['micros_coverage']}%)")


def show_help():
    """Show help message"""
    logger.info("""
╔════════════════════════════════════════════════════════════════╗
║        Ingredient Nutrition Manager                            ║
╠════════════════════════════════════════════════════════════════╣
║  Commands:                                                      ║
║    list [filter]      List ingredients (optionally filter)       ║
║    view <name>        View nutrition data for ingredient          ║
║    add               Interactive mode to add nutrition           ║
║    stats             Show nutrition coverage statistics          ║
║    help              Show this help message                      ║
╠════════════════════════════════════════════════════════════════╣
║  Examples:                                                      ║
║    python ingredient_nutrition_manager.py list tomato           ║
║    python ingredient_nutrition_manager.py view "Almond Flour"   ║
║    python ingredient_nutrition_manager.py add                   ║
║    python ingredient_nutrition_manager.py stats                 ║
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
    elif command == 'stats':
        cmd_stats(args)
    elif command == 'help' or command == '--help' or command == '-h':
        show_help()
    else:
        logger.error(f"Unknown command: {command}")
        show_help()


if __name__ == "__main__":
    main()
