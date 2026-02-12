"""
Seed Seasonality Data
This script populates the seasonality tables with initial data
"""
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import uuid
from database import SessionLocal
from models import Seasonality, SeasonalityTranslation
from sqlalchemy import text
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Seasonality data with translations
SEASONALITY_DATA = {
    # WEATHER SEASONS
    "Winter": {
        "type": "WEATHER",
        "translations": {
            "en": {"name": "Winter", "description": "Cold season recipes, comfort food"},
            "no": {"name": "Vinter", "description": "Oppskrifter for kaldt vær, komfortmat"}
        }
    },
    "Summer": {
        "type": "WEATHER",
        "translations": {
            "en": {"name": "Summer", "description": "Light, refreshing recipes for hot weather"},
            "no": {"name": "Sommer", "description": "Lette, forfriskende oppskrifter for varmt vær"}
        }
    },
    "Spring": {
        "type": "WEATHER",
        "translations": {
            "en": {"name": "Spring", "description": "Fresh spring recipes with new produce"},
            "no": {"name": "Vår", "description": "Ferske våroppskrifter med nye råvarer"}
        }
    },
    "Autumn": {
        "type": "WEATHER",
        "translations": {
            "en": {"name": "Autumn", "description": "Cozy autumn recipes with harvest produce"},
            "no": {"name": "Høst", "description": "Koselige høstoppskrifter med høstvarer"}
        }
    },
    "Monsoon": {
        "type": "WEATHER",
        "translations": {
            "en": {"name": "Monsoon", "description": "Rainy season comfort food"},
            "no": {"name": "Monsun", "description": "Regntidskomfortmat"}
        }
    },

    # FESTIVALS
    "Christmas": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "Christmas", "description": "Traditional Christmas recipes"},
            "no": {"name": "Jul", "description": "Tradisjonelle juleoppskrifter"}
        }
    },
    "Diwali": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "Diwali", "description": "Indian festival of lights sweets and snacks"},
            "no": {"name": "Diwali", "description": "Indisk lysfest-søtsaker og snacks"}
        }
    },
    "Easter": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "Easter", "description": "Traditional Easter recipes"},
            "no": {"name": "Påske", "description": "Tradisjonelle påskeoppskrifter"}
        }
    },
    "Thanksgiving": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "Thanksgiving", "description": "American Thanksgiving feast recipes"},
            "no": {"name": "Thanksgiving", "description": "Amerikansk Thanksgiving-middag"}
        }
    },
    "New Year": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "New Year", "description": "New Year celebration recipes"},
            "no": {"name": "Nyttår", "description": "Nyttårsfeiringsoppskrifter"}
        }
    },
    "Eid": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "Eid", "description": "Eid celebration recipes"},
            "no": {"name": "Id", "description": "Id-feiringsoppskrifter"}
        }
    },
    "Holi": {
        "type": "FESTIVAL",
        "translations": {
            "en": {"name": "Holi", "description": "Indian festival of colors recipes"},
            "no": {"name": "Holi", "description": "Indisk fargefestival-oppskrifter"}
        }
    },

    # INGREDIENT AVAILABILITY
    "Strawberry Season": {
        "type": "INGREDIENT_AVAILABILITY",
        "translations": {
            "en": {"name": "Strawberry Season", "description": "Fresh strawberry recipes"},
            "no": {"name": "Jordbærsesong", "description": "Ferske jorbæroppskrifter"}
        }
    },
    "Asparagus Season": {
        "type": "INGREDIENT_AVAILABILITY",
        "translations": {
            "en": {"name": "Asparagus Season", "description": "Fresh asparagus recipes"},
            "no": {"name": "Aspargessesong", "description": "Ferske aspargesoppskrifter"}
        }
    },
    "Mango Season": {
        "type": "INGREDIENT_AVAILABILITY",
        "translations": {
            "en": {"name": "Mango Season", "description": "Fresh mango recipes"},
            "no": {"name": "Mangosesong", "description": "Ferske mangooppskrifter"}
        }
    },
    "Apple Season": {
        "type": "INGREDIENT_AVAILABILITY",
        "translations": {
            "en": {"name": "Apple Season", "description": "Fresh apple recipes"},
            "no": {"name": "Eplesesong", "description": "Ferske epleoppskrifter"}
        }
    },
    "Pumpkin Season": {
        "type": "INGREDIENT_AVAILABILITY",
        "translations": {
            "en": {"name": "Pumpkin Season", "description": "Fresh pumpkin recipes"},
            "no": {"name": "Gresskarsesong", "description": "Ferske gresskaroppskrifter"}
        }
    },

    # CULTURAL OCCASIONS
    "Wedding": {
        "type": "CULTURAL_OCCASION",
        "translations": {
            "en": {"name": "Wedding", "description": "Wedding feast recipes"},
            "no": {"name": "Bryllup", "description": "Bryllupsmenyoppskrifter"}
        }
    },
    "Birthday": {
        "type": "CULTURAL_OCCASION",
        "translations": {
            "en": {"name": "Birthday", "description": "Birthday celebration recipes"},
            "no": {"name": "Bursdag", "description": "Bursdagsfeiringsoppskrifter"}
        }
    },
    "Potluck": {
        "type": "CULTURAL_OCCASION",
        "translations": {
            "en": {"name": "Potluck", "description": "Dishes perfect for sharing"},
            "no": {"name": "Potluck", "description": "Retter perfekt for deling"}
        }
    },
    "Picnic": {
        "type": "CULTURAL_OCCASION",
        "translations": {
            "en": {"name": "Picnic", "description": "Portable outdoor meals"},
            "no": {"name": "Picnic", "description": "Portable utendørs måltider"}
        }
    },

    # DIETARY PRACTICES
    "Ramadan": {
        "type": "DIETARY_PRACTICE",
        "translations": {
            "en": {"name": "Ramadan", "description": "Recipes for Iftar and Suhoor"},
            "no": {"name": "Ramadan", "description": "Oppskrifter for Iftar og Suhoor"}
        }
    },
    "Lent": {
        "type": "DIETARY_PRACTICE",
        "translations": {
            "en": {"name": "Lent", "description": "Meat-free recipes for Lent"},
            "no": {"name": "Fastelavn", "description": "Kjøtfrie oppskrifter for fasten"}
        }
    },
    "Navratri Fasting": {
        "type": "DIETARY_PRACTICE",
        "translations": {
            "en": {"name": "Navratri Fasting", "description": "Indian fasting food recipes"},
            "no": {"name": "Navratri-fasting", "description": "Indiske fastematoppskrifter"}
        }
    },
    "Vegan": {
        "type": "DIETARY_PRACTICE",
        "translations": {
            "en": {"name": "Vegan", "description": "Plant-based recipes"},
            "no": {"name": "Vegansk", "description": "Plantebaserte oppskrifter"}
        }
    },

    # MEAL TIMING
    "Breakfast": {
        "type": "MEAL_TIMING",
        "translations": {
            "en": {"name": "Breakfast", "description": "Morning meal recipes"},
            "no": {"name": "Frokost", "description": "Frokostoppskrifter"}
        }
    },
    "Brunch": {
        "type": "MEAL_TIMING",
        "translations": {
            "en": {"name": "Brunch", "description": "Late morning meal recipes"},
            "no": {"name": "Brunsj", "description": "Sene frokostoppskrifter"}
        }
    },
    "Lunch": {
        "type": "MEAL_TIMING",
        "translations": {
            "en": {"name": "Lunch", "description": "Midday meal recipes"},
            "no": {"name": "Lunsj", "description": "Lunsjoppskrifter"}
        }
    },
    "Dinner": {
        "type": "MEAL_TIMING",
        "translations": {
            "en": {"name": "Dinner", "description": "Evening meal recipes"},
            "no": {"name": "Middag", "description": "Middagsoppskrifter"}
        }
    },
    "Late Night": {
        "type": "MEAL_TIMING",
        "translations": {
            "en": {"name": "Late Night", "description": "Late night snack recipes"},
            "no": {"name": "Kvelds", "description": "Kveldsmatoppskrifter"}
        }
    },
    "Snack": {
        "type": "MEAL_TIMING",
        "translations": {
            "en": {"name": "Snack", "description": "Quick snack recipes"},
            "no": {"name": "Snacks", "description": "Raske snackoppskrifter"}
        }
    },

    # LIFESTYLE
    "Comfort Food": {
        "type": "LIFESTYLE",
        "translations": {
            "en": {"name": "Comfort Food", "description": "Hearty, comforting recipes"},
            "no": {"name": "Komfortmat", "description": "Hjertevarme, trøstende oppskrifter"}
        }
    },
    "Party Food": {
        "type": "LIFESTYLE",
        "translations": {
            "en": {"name": "Party Food", "description": "Crowd-pleasing party recipes"},
            "no": {"name": "Partymat", "description": "Festlige oppskrifter for selskaper"}
        }
    },
    "Detox": {
        "type": "LIFESTYLE",
        "translations": {
            "en": {"name": "Detox", "description": "Healthy cleansing recipes"},
            "no": {"name": "Detox", "description": "Sunn rensende oppskrifter"}
        }
    },
    "Cozy": {
        "type": "LIFESTYLE",
        "translations": {
            "en": {"name": "Cozy", "description": "Warm and cozy recipes"},
            "no": {"name": "Koselig", "description": "Varme og koselige oppskrifter"}
        }
    },
    "Outdoor": {
        "type": "LIFESTYLE",
        "translations": {
            "en": {"name": "Outdoor", "description": "Outdoor cooking and BBQ"},
            "no": {"name": "Utendørs", "description": "Utendørs matlaging og grill"}
        }
    },
    "Quick & Easy": {
        "type": "LIFESTYLE",
        "translations": {
            "en": {"name": "Quick & Easy", "description": "Fast and simple recipes"},
            "no": {"name": "Raskt og Enkelt", "description": "Raske og enkle oppskrifter"}
        }
    },

    # REGIONAL
    "Nordic": {
        "type": "REGIONAL",
        "translations": {
            "en": {"name": "Nordic", "description": "Scandinavian recipes"},
            "no": {"name": "Nordisk", "description": "Skandinaviske oppskrifter"}
        }
    },
    "Indian": {
        "type": "REGIONAL",
        "translations": {
            "en": {"name": "Indian", "description": "Indian cuisine recipes"},
            "no": {"name": "Indisk", "description": "Indiske matoppskrifter"}
        }
    },
    "Italian": {
        "type": "REGIONAL",
        "translations": {
            "en": {"name": "Italian", "description": "Italian cuisine recipes"},
            "no": {"name": "Italiensk", "description": "Italienske matoppskrifter"}
        }
    },
    "Asian": {
        "type": "REGIONAL",
        "translations": {
            "en": {"name": "Asian", "description": "Asian cuisine recipes"},
            "no": {"name": "Asiatisk", "description": "Asiatiske matoppskrifter"}
        }
    },
    "Mexican": {
        "type": "REGIONAL",
        "translations": {
            "en": {"name": "Mexican", "description": "Mexican cuisine recipes"},
            "no": {"name": "Meksikansk", "description": "Meksikanske matoppskrifter"}
        }
    },
    "Middle Eastern": {
        "type": "REGIONAL",
        "translations": {
            "en": {"name": "Middle Eastern", "description": "Middle Eastern cuisine recipes"},
            "no": {"name": "Midtøsten", "description": "Midtøstlige matoppskrifter"}
        }
    },

    # HEALTH CYCLE
    "Immunity Boosting": {
        "type": "HEALTH_CYCLE",
        "translations": {
            "en": {"name": "Immunity Boosting", "description": "Recipes to strengthen immunity"},
            "no": {"name": "Immunstyrkende", "description": "Oppskrifter for å styrke immunitet"}
        }
    },
    "Summer Hydration": {
        "type": "HEALTH_CYCLE",
        "translations": {
            "en": {"name": "Summer Hydration", "description": "Refreshing hydrating recipes"},
            "no": {"name": "Sommerhydrering", "description": "Forfriskende hydrerende oppskrifter"}
        }
    },
    "Winter Nourishment": {
        "type": "HEALTH_CYCLE",
        "translations": {
            "en": {"name": "Winter Nourishment", "description": "Nourishing winter recipes"},
            "no": {"name": "Vinternæring", "description": "Nærende vinteroppskrifter"}
        }
    },
    "Post-Workout": {
        "type": "HEALTH_CYCLE",
        "translations": {
            "en": {"name": "Post-Workout", "description": "Recovery recipes after exercise"},
            "no": {"name": "Etter Trening", "description": "Restitusjonsoppskrifter etter trening"}
        }
    },
    "Gut Health": {
        "type": "HEALTH_CYCLE",
        "translations": {
            "en": {"name": "Gut Health", "description": "Probiotic and digestive-friendly recipes"},
            "no": {"name": "Tarmhelse", "description": "Probiotiske og fordøyelsesvennlige oppskrifter"}
        }
    },
}


def get_translation_sequence_start():
    """Get the next available ID for translation table"""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT COALESCE(MAX(id), 0) + 1 FROM seasonality_translation
        """))
        return result.scalar()
    finally:
        db.close()


def seed_seasonalities():
    """Seed seasonality data into the database"""
    db = SessionLocal()

    try:
        # Get current translation sequence start
        translation_id = get_translation_sequence_start()
        logger.info(f"Starting translation ID from: {translation_id}")

        created_count = 0
        translation_count = 0

        for key, data in SEASONALITY_DATA.items():
            # Check if seasonality already exists
            existing = db.execute(text("""
                SELECT s.id FROM seasonality s
                JOIN seasonality_translation st ON st."seasonalityId" = s.id
                WHERE st.name = :name AND st."languageId" = 'en'
                LIMIT 1
            """), {"name": data["translations"]["en"]["name"]}).fetchone()

            if existing:
                logger.info(f"Skipping '{key}' - already exists")
                continue

            # Create seasonality
            seasonality_id = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO seasonality (id, type, "isActive", "createdAt", "updatedAt")
                VALUES (:id, :type, true, now(), now())
            """), {
                "id": seasonality_id,
                "type": data["type"]
            })

            logger.info(f"Created seasonality: {key} ({data['type']})")

            # Create translations
            for lang_id, trans_data in data["translations"].items():
                db.execute(text("""
                    INSERT INTO seasonality_translation
                    (id, name, description, "languageId", "seasonalityId", "createdAt", "updatedAt")
                    VALUES (:id, :name, :description, :languageId, :seasonalityId, now(), now())
                """), {
                    "id": translation_id,
                    "name": trans_data["name"],
                    "description": trans_data.get("description"),
                    "languageId": lang_id,
                    "seasonalityId": seasonality_id
                })
                translation_id += 1
                translation_count += 1

            created_count += 1

        db.commit()

        logger.info(f"\n=== SEEDING COMPLETE ===")
        logger.info(f"Created {created_count} seasonalities")
        logger.info(f"Created {translation_count} translations")

        # Show summary by type
        logger.info(f"\n=== SUMMARY BY TYPE ===")
        result = db.execute(text("""
            SELECT type, COUNT(*) as count
            FROM seasonality
            GROUP BY type
            ORDER BY type
        """))
        for row in result:
            logger.info(f"  {row[0]}: {row[1]} entries")

    except Exception as e:
        db.rollback()
        logger.error(f"Error during seeding: {e}")
        raise
    finally:
        db.close()


def main():
    """Main function to run the seeding"""
    logger.info("=== SEASONALITY DATA SEEDING ===")
    seed_seasonalities()
    logger.info("\nDone!")


if __name__ == "__main__":
    main()
