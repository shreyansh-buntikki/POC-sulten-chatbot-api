#!/usr/bin/env python
"""
Regenerate Recipe Embeddings

Regenerates ALL recipe embeddings with the fixed seasonality format.
This ensures seasonality names (like "Summer", "Christmas") are included
in embeddings, not just enum types (like "WEATHER", "FESTIVAL").

IMPORTANT: This will regenerate embeddings for ALL 3,506 recipes.
The existing embeddings will be overwritten with the new format.

Old format: "Seasonality: WEATHER, FESTIVAL"
New format: "Seasonality: Summer(WEATHER), Christmas(FESTIVE)"

This is required for queries like "summer recipes" to work correctly.
"""
import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from apps.fastapi.src.services.embedding_service import EmbeddingService
from models import Recipe

load_dotenv()


def regenerate_all_recipe_embeddings():
    """Regenerate embeddings for ALL recipes (overwrite existing)"""
    db = SessionLocal()

    try:
        print("=" * 60)
        print("Regenerating ALL Recipe Embeddings")
        print("=" * 60)

        # Get all recipes
        total_recipes = db.query(Recipe).count()
        print(f"\nTotal recipes in database: {total_recipes}")

        # Check for OpenAI API key
        if not os.getenv('OPENAI_API_KEY'):
            print("\n✗ Error: OPENAI_API_KEY not found in environment variables")
            print("Please set OPENAI_API_KEY in your .env file")
            return False

        service = EmbeddingService(db)

        # Get all recipes (not just ones without embeddings)
        recipes = db.query(Recipe).all()

        stats = {"regenerated": 0, "failed": 0}

        print(f"\nRegenerating embeddings for {len(recipes)} recipes...")
        print("This will take approximately 5-10 minutes...\n")

        for i, recipe in enumerate(recipes, 1):
            try:
                # Generate new embedding
                text = service._recipe_to_text(recipe)
                if not text:
                    print(f"  [{i}/{len(recipes)}] ⚠ Skipped recipe {recipe.id} (no text)")
                    stats["failed"] += 1
                    continue

                embedding = service._generate_embedding(text)
                if embedding:
                    recipe.embedding = embedding
                    db.commit()
                    stats["regenerated"] += 1

                    if i % 100 == 0 or i == len(recipes):
                        print(f"  [{i}/{len(recipes)}] ✓ Regenerated {stats['regenerated']} so far...")
                else:
                    stats["failed"] += 1
                    print(f"  [{i}/{len(recipes)}] ✗ Failed to generate embedding for recipe {recipe.id}")

            except Exception as e:
                db.rollback()
                stats["failed"] += 1
                print(f"  [{i}/{len(recipes)}] ✗ Error for recipe {recipe.id}: {e}")

        print("\n" + "=" * 60)
        print("Regeneration Complete!")
        print(f"  ✓ Regenerated: {stats['regenerated']}")
        print(f"  ✗ Failed: {stats['failed']}")
        print("=" * 60)

        return stats['failed'] == 0

    except Exception as e:
        db.rollback()
        print(f"\n✗ Error: {e}")
        return False

    finally:
        db.close()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("⚠ WARNING: This will regenerate embeddings for ALL recipes")
    print("=" * 60)

    response = input("\nDo you want to continue? (yes/no): ")

    if response.lower() in ['yes', 'y']:
        success = regenerate_all_recipe_embeddings()
        sys.exit(0 if success else 1)
    else:
        print("\n✗ Cancelled")
        sys.exit(1)
