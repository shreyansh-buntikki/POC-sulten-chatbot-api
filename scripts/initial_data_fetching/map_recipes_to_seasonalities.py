"""
Recipe-Seasonality Mapping Script
Intelligently maps recipes to seasonalities based on keywords and patterns
"""
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from sqlalchemy.orm import Session
from database import SessionLocal
from sqlalchemy import text
import logging
from typing import Dict, List, Tuple

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RecipeSeasonalityMapper:
    """Maps recipes to seasonalities based on intelligent keyword matching"""

    def __init__(self, db: Session):
        self.db = db
        self.seasonality_cache = {}
        self._load_seasonalities()

    def _load_seasonalities(self):
        """Load all seasonalities into cache for quick lookup"""
        query = text("""
            SELECT s.id, s.type, st.name, st."languageId"
            FROM seasonality s
            JOIN seasonality_translation st ON st."seasonalityId" = s.id
            WHERE st."languageId" = 'en'
        """)

        result = self.db.execute(query)
        for row in result:
            seasonality_id, type_name, name, lang = row
            if type_name not in self.seasonality_cache:
                self.seasonality_cache[type_name] = []
            self.seasonality_cache[type_name].append({
                'id': str(seasonality_id),
                'name': name,
                'keywords': self._get_keywords_for_seasonality(name, type_name)
            })

        logger.info(f"Loaded {len(self.seasonality_cache)} seasonality types")

    def _get_keywords_for_seasonality(self, name: str, type_name: str) -> List[str]:
        """Generate keywords for matching recipes to seasonalities"""
        keywords = [name.lower()]

        # Add type-specific keywords
        if type_name == 'WEATHER':
            weather_keywords = {
                'Winter': ['winter', 'christmas', 'jul', 'warm', 'comfort', 'soup', 'stew', 'gløgg', 'hot'],
                'Summer': ['summer', 'grill', 'salad', 'cold', 'fresh', 'light', 'ice', 'cool'],
                'Spring': ['spring', 'fresh', 'green', 'asparagus', 'strawberry', 'rhubarb', 'new'],
                'Autumn': ['autumn', 'fall', 'høst', 'pumpkin', 'squash', 'mushroom', 'chanterelle', 'root'],
                'Monsoon': ['monsoon', 'rain', 'fried', 'spicy', 'soup', 'chaat', 'pakora']
            }
            if name in weather_keywords:
                keywords.extend(weather_keywords[name])

        elif type_name == 'FESTIVAL':
            festival_keywords = {
                'Christmas': ['christmas', 'jul', 'holiday', 'xmas', 'gløgg', ' pepperkake'],
                'Diwali': ['diwali', 'deepavali', 'sweets', 'mithai', 'laddu', 'barfi', 'indian festival'],
                'Easter': ['easter', 'påske', 'egg', 'lamb', 'spring'],
                'Thanksgiving': ['thanksgiving', 'turkey', 'cranberry', 'pumpkin pie'],
                'New Year': ['new year', 'nyttår', 'champagne', 'celebration'],
                'Eid': ['eid', 'biryani', 'kheer', 'seviyan', 'sheer khurma'],
                'Holi': ['holi', 'gujiya', 'thandai', 'color']
            }
            if name in festival_keywords:
                keywords.extend(festival_keywords[name])

        elif type_name == 'MEAL_TIMING':
            meal_keywords = {
                'Breakfast': ['breakfast', 'frokost', 'morning', 'egg', 'pancake', 'waffle', 'cereal', 'oatmeal', 'granola', 'yogurt'],
                'Brunch': ['brunch', 'brunsj'],
                'Lunch': ['lunch', 'lunsj', 'midday', 'sandwich', 'salad'],
                'Dinner': ['dinner', 'middag', 'supper', 'evening'],
                'Late Night': ['late night', 'kvelds', 'midnight', 'snack'],
                'Snack': ['snack', 'nibble', 'finger food', 'tapas', 'canapé']
            }
            if name in meal_keywords:
                keywords.extend(meal_keywords[name])

        elif type_name == 'LIFESTYLE':
            lifestyle_keywords = {
                'Comfort Food': ['comfort', 'warm', 'hearty', 'soup', 'stew', 'casserole', 'gratin', 'komfort'],
                'Party Food': ['party', 'finger food', 'appetizer', 'canapé', 'snacks', 'dip', 'picky'],
                'Detox': ['detox', 'healthy', 'clean', 'green', 'smoothie', 'juice'],
                'Cozy': ['cozy', 'koselig', 'warm', 'hygge', 'comfort'],
                'Outdoor': ['outdoor', 'grill', 'bbq', 'picnic', 'camping'],
                'Quick & Easy': ['quick', 'easy', 'simple', 'fast', 'rask', 'enkel', 'quick']
            }
            if name in lifestyle_keywords:
                keywords.extend(lifestyle_keywords[name])

        elif type_name == 'REGIONAL':
            regional_keywords = {
                'Nordic': ['nordic', 'scandinavian', 'norwegian', 'norsk', 'swedish', 'danish', 'nordisk', 'fiskesuppe', 'rakfisk', 'lutefisk'],
                'Indian': ['indian', 'indisk', 'curry', 'biryani', 'dal', 'tikka', 'masala', 'chutney', 'roti', 'naan', 'tandoori'],
                'Italian': ['italian', 'italiensk', 'pasta', 'pizza', 'risotto', 'tiramisu', 'bruschetta', 'lasagna'],
                'Asian': ['asian', 'asiatisk', 'thai', 'chinese', 'japanese', 'korean', 'vietnamese', 'noodle', 'ramen', 'stir fry', 'kimchi'],
                'Mexican': ['mexican', 'meksikansk', 'taco', 'burrito', 'quesadilla', 'guacamole', 'salsa', 'nachos'],
                'Middle Eastern': ['middle eastern', 'midtøsten', 'hummus', 'falafel', 'shawarma', 'tabbouleh', 'baba ganoush', 'pita']
            }
            if name in regional_keywords:
                keywords.extend(regional_keywords[name])

        elif type_name == 'DIETARY_PRACTICE':
            dietary_keywords = {
                'Vegan': ['vegan', 'vegansk', 'plant-based', 'dairy-free', 'egg-free', 'animal-free'],
                'Ramadan': ['iftar', 'suhoor', 'ramadan', 'dates', 'haleem'],
                'Lent': ['lent', 'fastelavn', 'meat-free', 'fish', 'seafood'],
                'Navratri Fasting': ['navratri', 'fasting', 'vrat', 'sabudana', 'farali']
            }
            if name in dietary_keywords:
                keywords.extend(dietary_keywords[name])

        elif type_name == 'HEALTH_CYCLE':
            health_keywords = {
                'Immunity Boosting': ['immunity', 'immune', 'vitamin c', 'ginger', 'turmeric', 'citrus', 'healthy'],
                'Summer Hydration': ['hydration', 'water', 'refreshing', 'cool', 'summer drink', 'smoothie'],
                'Winter Nourishment': ['nourishing', 'hearty', 'warming', 'winter', 'soup', 'stew'],
                'Post-Workout': ['protein', 'post-workout', 'energy', 'recovery', 'muscle'],
                'Gut Health': ['gut', 'probiotic', 'fermented', 'kimchi', 'sauerkraut', 'kombucha', 'yogurt']
            }
            if name in health_keywords:
                keywords.extend(health_keywords[name])

        elif type_name == 'INGREDIENT_AVAILABILITY':
            ingredient_keywords = {
                'Strawberry Season': ['strawberry', 'jordbær', 'syltetøy'],
                'Asparagus Season': ['asparagus', 'asparges'],
                'Mango Season': ['mango', 'mangosesong'],
                'Apple Season': ['apple', 'eple', 'apple pie', 'cider'],
                'Pumpkin Season': ['pumpkin', 'gresskar', 'squash', 'pumpkin spice']
            }
            if name in ingredient_keywords:
                keywords.extend(ingredient_keywords[name])

        elif type_name == 'CULTURAL_OCCASION':
            cultural_keywords = {
                'Wedding': ['wedding', 'bryllup', 'bridal', 'groom', 'reception'],
                'Birthday': ['birthday', 'bursdag', 'cake', 'bday'],
                'Potluck': ['potluck', 'share', 'bring a dish', 'selskap'],
                'Picnic': ['picnic', 'utendørs', 'outdoor meal', 'packed lunch']
            }
            if name in cultural_keywords:
                keywords.extend(cultural_keywords[name])

        return keywords

    def get_seasonalities_for_recipe(self, recipe_name: str) -> List[str]:
        """Find matching seasonalities for a recipe based on name"""
        recipe_name_lower = recipe_name.lower()
        matched_seasonalities = []

        for type_name, seasonalities in self.seasonality_cache.items():
            for seasonality in seasonalities:
                for keyword in seasonality['keywords']:
                    if keyword in recipe_name_lower:
                        matched_seasonalities.append(seasonality['id'])
                        break  # Don't add same seasonality multiple times

        return matched_seasonalities

    def map_all_recipes(self):
        """Map all published recipes to seasonalities"""
        # Get all published recipes
        query = text("""
            SELECT id, name
            FROM recipe
            WHERE status = 'published'
        """)

        result = self.db.execute(query)
        recipes = result.fetchall()

        logger.info(f"Processing {len(recipes)} published recipes...")

        mapped_count = 0
        total_mappings = 0
        skipped_count = 0

        for recipe_id, recipe_name in recipes:
            seasonality_ids = self.get_seasonalities_for_recipe(recipe_name)

            if not seasonality_ids:
                skipped_count += 1
                continue

            # Insert mappings
            for seasonality_id in seasonality_ids:
                insert_query = text("""
                    INSERT INTO recipe_seasonality ("recipeId", "seasonalityId", "createdAt")
                    VALUES (:recipeId, :seasonalityId, now())
                    ON CONFLICT ("recipeId", "seasonalityId") DO NOTHING
                """)

                self.db.execute(insert_query, {
                    'recipeId': str(recipe_id),
                    'seasonalityId': seasonality_id
                })

                total_mappings += 1

            mapped_count += 1

            if mapped_count % 100 == 0:
                logger.info(f"Processed {mapped_count} recipes, {total_mappings} mappings so far...")
                self.db.commit()

        self.db.commit()

        logger.info(f"\n=== MAPPING COMPLETE ===")
        logger.info(f"Total recipes processed: {len(recipes)}")
        logger.info(f"Recipes with seasonalities: {mapped_count}")
        logger.info(f"Recipes without matches: {skipped_count}")
        logger.info(f"Total mappings created: {total_mappings}")

        return mapped_count, total_mappings, skipped_count


def main():
    """Main function to run the mapping"""
    db = SessionLocal()

    try:
        mapper = RecipeSeasonalityMapper(db)

        # Show some example mappings before running
        logger.info("=== EXAMPLE MAPPINGS ===")
        sample_query = text("""
            SELECT name FROM recipe WHERE status = 'published' ORDER BY RANDOM() LIMIT 10
        """)
        result = db.execute(sample_query)
        for row in result:
            recipe_name = row[0]
            seasonalities = mapper.get_seasonalities_for_recipe(recipe_name)
            if seasonalities:
                logger.info(f"'{recipe_name}' -> {len(seasonalities)} seasonalities")

        # Run the full mapping
        logger.info("\n=== STARTING FULL MAPPING ===")
        mapper.map_all_recipes()

        # Show statistics by seasonality type
        logger.info("\n=== MAPPINGS BY SEASONALITY TYPE ===")
        stats_query = text("""
            SELECT s.type, st.name as seasonality_name, COUNT(*) as recipe_count
            FROM recipe_seasonality rs
            JOIN seasonality s ON s.id = rs."seasonalityId"
            JOIN seasonality_translation st ON st."seasonalityId" = s.id AND st."languageId" = 'en'
            GROUP BY s.type, st.name
            ORDER BY s.type, recipe_count DESC
        """)
        result = db.execute(stats_query)
        for row in result:
            logger.info(f"  {row[0]:20} {row[1]:30} {row[2]:4} recipes")

        # Show some example mapped recipes
        logger.info("\n=== EXAMPLE MAPPED RECIPES ===")
        examples_query = text("""
            SELECT
                r.name as recipe,
                s.type as type,
                st.name as seasonality
            FROM recipe_seasonality rs
            JOIN recipe r ON r.id = rs."recipeId"
            JOIN seasonality s ON s.id = rs."seasonalityId"
            JOIN seasonality_translation st ON st."seasonalityId" = s.id AND st."languageId" = 'en'
            ORDER BY RANDOM()
            LIMIT 30
        """)
        result = db.execute(examples_query)
        for row in result:
            logger.info(f"  [{row[1]}] {row[0]:50} -> {row[2]}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
