#!/usr/bin/env python
"""
Drop Unused Embedding Columns

Drops the embedding columns from tag and seasonality tables.
These embeddings are no longer used as the data is already included in recipe embeddings.

Run this before regenerating recipe embeddings.
"""
import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from sqlalchemy import text

load_dotenv()


def drop_unused_embedding_columns():
    """Drop embedding columns from tag and seasonality tables"""
    db = SessionLocal()

    try:
        logger.info("=" * 60)
        logger.info("Dropping Unused Embedding Columns")
        logger.info("=" * 60)

        # Drop tag embedding column
        logger.info("\nDropping embedding column from tag table...")
        db.execute(text("ALTER TABLE tag DROP COLUMN IF EXISTS embedding"))
        db.commit()
        logger.info("  ✓ Tag embedding column dropped")

        # Drop seasonality embedding column
        logger.info("Dropping embedding column from seasonality table...")
        db.execute(text("ALTER TABLE seasonality DROP COLUMN IF EXISTS embedding"))
        db.commit()
        logger.info("  ✓ Seasonality embedding column dropped")

        logger.info("\n" + "=" * 60)
        logger.info("✓ Unused embedding columns dropped successfully!")
        logger.info("=" * 60)

        return True

    except Exception as e:
        db.rollback()
        logger.error(f"\n✗ Error: {e}")
        return False

    finally:
        db.close()


if __name__ == "__main__":
    success = drop_unused_embedding_columns()
    sys.exit(0 if success else 1)
