#!/usr/bin/env python
"""
Generate Embeddings Script

Generates vector embeddings for recipes and ingredients using OpenAI's API.
Run this script after setting up the database to enable semantic search.

Note: Tag and seasonality data is already included in recipe embeddings.

Usage:
    python scripts/generate_embeddings.py [--recipes] [--ingredients] [--limit N]

Examples:
    python scripts/generate_embeddings.py --recipes --limit 100
    python scripts/generate_embeddings.py --ingredients
    python scripts/generate_embeddings.py  # Generate all
"""
import os
import sys
import argparse
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from apps.fastapi.src.services.embedding_service import EmbeddingService
from libs.utils.logger import setup_logger

logger = setup_logger("generate_embeddings", True, False, False, False)


def generate_recipe_embeddings(limit: int = None):
    """Generate embeddings for recipes"""
    logger.info("=" * 60)
    logger.info("Generating Recipe Embeddings")
    logger.info("=" * 60)

    db = SessionLocal()
    try:
        service = EmbeddingService(db)

        limit_msg = f" (limit: {limit})" if limit else " (all)"
        logger.info(f"Starting recipe embedding generation{limit_msg}...")

        stats = service.generate_recipe_embeddings(limit=limit)

        logger.info(f"\nResults:")
        logger.info(f"  ✓ Processed: {stats['processed']}")
        logger.error(f"  ✗ Failed: {stats['failed']}")
        logger.info(f"  ⊘ Skipped: {stats['skipped']}")

        return stats['failed'] == 0

    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    finally:
        db.close()


def generate_ingredient_embeddings(limit: int = None):
    """Generate embeddings for ingredients"""
    logger.info("\n" + "=" * 60)
    logger.info("Generating Ingredient Embeddings")
    logger.info("=" * 60)

    db = SessionLocal()
    try:
        service = EmbeddingService(db)

        limit_msg = f" (limit: {limit})" if limit else " (all)"
        logger.info(f"Starting ingredient embedding generation{limit_msg}...")

        stats = service.generate_ingredient_embeddings(limit=limit)

        logger.info(f"\nResults:")
        logger.info(f"  ✓ Processed: {stats['processed']}")
        logger.error(f"  ✗ Failed: {stats['failed']}")
        logger.info(f"  ⊘ Skipped: {stats['skipped']}")

        return stats['failed'] == 0

    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate embeddings for recipes and ingredients"
    )
    parser.add_argument(
        '--recipes',
        action='store_true',
        help='Generate embeddings for recipes'
    )
    parser.add_argument(
        '--ingredients',
        action='store_true',
        help='Generate embeddings for ingredients'
    )
    parser.add_argument(
        '--limit', '-l',
        type=int,
        default=None,
        help='Limit number of items to process (for testing)'
    )

    args = parser.parse_args()

    # If none specified, do all
    if not args.recipes and not args.ingredients:
        args.recipes = True
        args.ingredients = True

    # Check for OpenAI API key
    if not os.getenv('OPENAI_API_KEY'):
        logger.error("OPENAI_API_KEY not found in environment variables")
        logger.error("Please set OPENAI_API_KEY in your .env file")
        sys.exit(1)

    success = True

    if args.recipes:
        if not generate_recipe_embeddings(args.limit):
            success = False

    if args.ingredients:
        if not generate_ingredient_embeddings(args.limit):
            success = False

    logger.info("=" * 60)
    if success:
        logger.info("Embedding generation completed successfully!")
    else:
        logger.error("Embedding generation completed with some errors")
    logger.info("=" * 60)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
