#!/usr/bin/env python
"""
Regenerate Recipe Embeddings - No Confirmation

Regenerates ALL recipe embeddings with the fixed seasonality format.
Progress is shown every 10 recipes.
"""
import os
import sys
import time
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
        start_time = time.time()
        last_report_time = start_time

        print(f"\nRegenerating embeddings for {len(recipes)} recipes...")
        print("Progress shown every 10 recipes\n")

        for i, recipe in enumerate(recipes, 1):
            try:
                # Generate new embedding
                text = service._recipe_to_text(recipe)
                if not text:
                    stats["failed"] += 1
                    continue

                embedding = service._generate_embedding(text)
                if embedding:
                    recipe.embedding = embedding
                    db.commit()
                    stats["regenerated"] += 1

                    # Report progress every 10 recipes
                    if i % 10 == 0 or i == len(recipes):
                        elapsed = time.time() - start_time
                        rate = stats["regenerated"] / elapsed if elapsed > 0 else 0
                        eta = (len(recipes) - i) / rate if rate > 0 else 0

                        print(f"  [{i:4d}/{len(recipes)}] ✓ {stats['regenerated']:4d} regenerated | {stats['failed']:3d} failed | {rate:.1f} recipes/sec | ETA: {eta/60:.1f} min")
                else:
                    stats["failed"] += 1

            except Exception as e:
                db.rollback()
                stats["failed"] += 1
                if i % 10 == 0:
                    print(f"  [{i:4d}/{len(recipes)}] ✗ Error for recipe {recipe.id}: {str(e)[:50]}")

        elapsed = time.time() - start_time

        print("\n" + "=" * 60)
        print("Regeneration Complete!")
        print(f"  ✓ Regenerated: {stats['regenerated']}")
        print(f"  ✗ Failed: {stats['failed']}")
        print(f"  ⏱ Time elapsed: {elapsed/60:.1f} minutes")
        print(f"  📊 Average rate: {stats['regenerated']/elapsed:.1f} recipes/sec")
        print("=" * 60)

        return stats['failed'] == 0

    except Exception as e:
        db.rollback()
        print(f"\n✗ Error: {e}")
        return False

    finally:
        db.close()


if __name__ == "__main__":
    success = regenerate_all_recipe_embeddings()
    sys.exit(0 if success else 1)
