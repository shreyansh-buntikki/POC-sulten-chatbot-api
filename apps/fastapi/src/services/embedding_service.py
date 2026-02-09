"""
Embedding Service - Handles vector embeddings for semantic search
Uses OpenAI's text-embedding-3-small model (1536 dimensions)
Same-table approach: embedding column is on each entity's table
"""
import os
from typing import List, Optional, Tuple
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from openai import OpenAI
from dotenv import load_dotenv

from models import (
    Recipe, Ingredient, Seasonality, SeasonalityTranslation,
    SeasonalityTypeEnum, RecipeIngredient, RecipeSeasonality
)
from database import get_db

# Load environment variables
load_dotenv()

# OpenAI configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_EMBEDDING_MODEL = os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')
EMBEDDING_DIMENSION = int(os.getenv('EMBEDDING_DIMENSION', '1536'))

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


class EmbeddingService:
    """
    Service for generating and searching vector embeddings
    Same-table approach: embeddings are columns on entity tables
    """

    def __init__(self, db: Session):
        self.db = db
        self.client = client
        self.model = OPENAI_EMBEDDING_MODEL
        self.dimension = EMBEDDING_DIMENSION

    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text using OpenAI API

        Args:
            text: Text to generate embedding for

        Returns:
            List of floats representing the embedding, or None if failed
        """
        if not self.client:
            raise ValueError("OpenAI API key not configured. Please set OPENAI_API_KEY in .env")

        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"Error generating embedding: {e}")
            return None

    def generate_batch_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts in batch

        Args:
            texts: List of texts to generate embeddings for

        Returns:
            List of embeddings (same order as input)
        """
        if not self.client:
            raise ValueError("OpenAI API key not configured. Please set OPENAI_API_KEY in .env")

        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            print(f"Error generating batch embeddings: {e}")
            return [None] * len(texts)

    # =====================================================
    # Recipe Embeddings
    # =====================================================

    def generate_recipe_embeddings(self, limit: Optional[int] = None) -> dict:
        """
        Generate embeddings for all recipes that don't have one.
        Recipe embeddings include: name, description, ingredients, tags, seasonality.

        Args:
            limit: Maximum number of recipes to process (None for all)

        Returns:
            Dictionary with stats: processed, failed, skipped
        """
        # Get recipes without embeddings
        query = self.db.query(Recipe).filter(Recipe.embedding == None)
        if limit:
            query = query.limit(limit)

        recipes = query.all()

        stats = {"processed": 0, "failed": 0, "skipped": 0}

        for recipe in recipes:
            # Create rich text representation for embedding
            text = self._recipe_to_text(recipe)
            if not text:
                stats["skipped"] += 1
                continue

            embedding = self._generate_embedding(text)
            if embedding:
                try:
                    recipe.embedding = embedding
                    self.db.commit()
                    stats["processed"] += 1
                except Exception as e:
                    self.db.rollback()
                    print(f"Error saving embedding for recipe {recipe.id}: {e}")
                    stats["failed"] += 1
            else:
                stats["failed"] += 1

        return stats

    def _recipe_to_text(self, recipe: Recipe) -> Optional[str]:
        """
        Convert recipe to rich text representation for embedding.
        Includes: recipe info + ingredients + tags + seasonality

        Args:
            recipe: Recipe object

        Returns:
            Text representation suitable for embedding
        """
        parts = []

        # Basic recipe info
        if recipe.name:
            parts.append(f"Recipe: {recipe.name}")

        if recipe.ingress:
            parts.append(f"Description: {recipe.ingress}")

        if recipe.difficulty:
            parts.append(f"Difficulty: {recipe.difficulty}")

        if recipe.prepTime:
            parts.append(f"Prep time: {recipe.prepTime} minutes")

        if recipe.cookTime:
            parts.append(f"Cook time: {recipe.cookTime} minutes")

        # Get ingredients
        ingredients = self.db.query(RecipeIngredient).filter(
            RecipeIngredient.recipeId == recipe.id
        ).all()

        ingredient_names = []
        for ri in ingredients:
            if ri.ingredientId:
                ingr = self.db.query(Ingredient).filter(Ingredient.id == ri.ingredientId).first()
                if ingr and ingr.name:
                    ingredient_names.append(ingr.name)

        if ingredient_names:
            parts.append(f"Ingredients: {', '.join(ingredient_names)}")

        # Get tags
        tags = self.db.execute(
            text("""
                SELECT t.name FROM tag t
                JOIN recipe_tags_tag rt ON t.id = rt."tagId"
                WHERE rt."recipeId" = :recipe_id
            """),
            {"recipe_id": str(recipe.id)}
        ).fetchall()

        tag_names = [row[0] for row in tags if row[0]]
        if tag_names:
            parts.append(f"Tags: {', '.join(tag_names)}")

        # Get seasonality IDs for this recipe
        seasonality_ids = self.db.execute(
            text("SELECT \"seasonalityId\" FROM recipe_seasonality WHERE \"recipeId\" = :recipe_id"),
            {"recipe_id": str(recipe.id)}
        ).fetchall()

        if seasonality_ids:
            season_info = []
            for row in seasonality_ids:
                sid = row[0]  # Extract UUID from Row

                # Get the seasonality and its translation
                seasonality = self.db.query(Seasonality).filter(
                    Seasonality.id == sid,
                    Seasonality.isActive == True
                ).first()

                if seasonality:
                    # Get English translation name
                    translation = self.db.query(SeasonalityTranslation).filter(
                        SeasonalityTranslation.seasonalityId == sid,
                        SeasonalityTranslation.languageId == 'en'
                    ).first()

                    if translation and translation.name:
                        season_info.append(f"{translation.name}({seasonality.type})")
                    else:
                        season_info.append(seasonality.type)

            if season_info:
                parts.append(f"Seasonality: {', '.join(season_info)}")

        return " | ".join(parts) if parts else None

    def search_recipes_by_embedding(
        self,
        query_text: str,
        limit: int = 10,
        threshold: float = 0.7,
        language_id: Optional[str] = None
    ) -> List[Tuple[Recipe, float]]:
        """
        Search recipes by semantic similarity using cosine similarity

        Args:
            query_text: Search query text
            limit: Maximum number of results
            threshold: Minimum similarity score (0-1)
            language_id: Optional language ID filter (e.g., 'en', 'no')

        Returns:
            List of (Recipe, similarity_score) tuples
        """
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"[EMBEDDING SEARCH] START - query_text={query_text[:50]}, language_id={language_id}, threshold={threshold}")

        # Generate embedding for query
        query_embedding = self._generate_embedding(query_text)
        if not query_embedding:
            return []

        # Convert to pgvector format for PostgreSQL
        embedding_array = f"[{','.join(map(str, query_embedding))}]"

        # Build WHERE clause conditions
        where_conditions = [
            "embedding IS NOT NULL",
            "1 - (embedding <=> CAST(:query_embedding AS vector)) >= :threshold",
            '"deletedAt" IS NULL',
            '"status" = :status'  #-- Only published recipes
        ]
        params = {
            "query_embedding": embedding_array,
            "threshold": threshold,
            "limit": limit,
            "status": "published"
        }

        # Add language filter if provided
        if language_id:
            where_conditions.append('"languageId" = :language_id')
            params["language_id"] = language_id
            logger.warning(f"[EMBEDDING SEARCH] Language filter added: languageId={language_id}")
        else:
            logger.warning(f"[EMBEDDING SEARCH] NO language filter - language_id is None or empty!")

        logger.warning(f"[EMBEDDING SEARCH] WHERE conditions: {where_conditions}")

        # Use cosine similarity search (1 - cosine_distance)
        sql_query = text(f"""
            SELECT id,
                   name,
                   slug,
                   ingress,
                   image,
                   difficulty,
                   "prepTime",
                   "cookTime",
                   servings,
                   "languageId",
                   1 - (embedding <=> CAST(:query_embedding AS vector)) as similarity
            FROM recipe
            WHERE {' AND '.join(where_conditions)}
            ORDER BY embedding <=> CAST(:query_embedding AS vector)
            LIMIT :limit
        """)

        result = self.db.execute(sql_query, params)

        recipes = []
        for row in result:
            recipe = Recipe(
                id=row[0],
                name=row[1],
                slug=row[2],
                ingress=row[3],
                image=row[4],
                difficulty=row[5],
                prepTime=row[6],
                cookTime=row[7],
                servings=row[8],
                languageId=row[9]
            )
            similarity = float(row[10])
            recipes.append((recipe, similarity))

        logger.warning(f"[EMBEDDING SEARCH] Query executed, returning {len(recipes)} results")
        for i, (recipe, score) in enumerate(recipes[:5]):
            logger.warning(
                f"[EMBEDDING SEARCH]   Result {i+1}: {recipe.name} "
                f"(languageId={recipe.languageId}, score={score:.3f})"
            )

        return recipes

    # =====================================================
    # Ingredient Embeddings
    # =====================================================

    def generate_ingredient_embeddings(self, limit: Optional[int] = None) -> dict:
        """
        Generate embeddings for all ingredients that don't have one

        Args:
            limit: Maximum number of ingredients to process (None for all)

        Returns:
            Dictionary with stats: processed, failed, skipped
        """
        # Get ingredients without embeddings
        query = self.db.query(Ingredient).filter(Ingredient.embedding == None)
        if limit:
            query = query.limit(limit)

        ingredients = query.all()

        stats = {"processed": 0, "failed": 0, "skipped": 0}

        for ingredient in ingredients:
            text = self._ingredient_to_text(ingredient)
            if not text:
                stats["skipped"] += 1
                continue

            embedding = self._generate_embedding(text)
            if embedding:
                try:
                    ingredient.embedding = embedding
                    self.db.commit()
                    stats["processed"] += 1
                except Exception as e:
                    self.db.rollback()
                    print(f"Error saving embedding for ingredient {ingredient.id}: {e}")
                    stats["failed"] += 1
            else:
                stats["failed"] += 1

        return stats

    def _ingredient_to_text(self, ingredient: Ingredient) -> Optional[str]:
        """
        Convert ingredient to enhanced text representation for embedding.
        Includes: name + recipe context (tags, usage patterns)
        """
        if not ingredient.name:
            return None

        parts = [f"Ingredient: {ingredient.name}"]

        # Get recipes that use this ingredient to learn context
        # Sample up to 50 recipes for efficiency
        recipe_data = self.db.execute(
            text("""
                SELECT DISTINCT t.name as tag_name
                FROM recipe_ingredient ri
                JOIN recipe_tags_tag rt ON rt.\"recipeId\" = ri.\"recipeId\"
                JOIN tag t ON t.id = rt.\"tagId\"
                WHERE ri.\"ingredientId\" = :ingredient_id
                LIMIT 50
            """),
            {"ingredient_id": str(ingredient.id)}
        ).fetchall()

        # Extract unique tags to understand culinary context
        tags = list(set([row[0] for row in recipe_data if row[0]]))

        if tags:
            parts.append(f"Used in: {', '.join(sorted(tags)[:15])}")

        return " | ".join(parts) if parts else f"Ingredient: {ingredient.name}"

    def search_ingredients_by_embedding(
        self,
        query_text: str,
        limit: int = 10,
        threshold: float = 0.6
    ) -> List[Tuple[Ingredient, float]]:
        """
        Search ingredients by semantic similarity

        Args:
            query_text: Search query text
            limit: Maximum number of results
            threshold: Minimum similarity score (0-1)

        Returns:
            List of (Ingredient, similarity_score) tuples
        """
        # Generate embedding for query
        query_embedding = self._generate_embedding(query_text)
        if not query_embedding:
            return []

        # Convert to pgvector format
        embedding_array = f"[{','.join(map(str, query_embedding))}]"

        sql_query = text("""
            SELECT id,
                   name,
                   1 - (embedding <=> CAST(:query_embedding AS vector)) as similarity
            FROM ingredient
            WHERE embedding IS NOT NULL
              AND 1 - (embedding <=> CAST(:query_embedding AS vector)) >= :threshold
            ORDER BY embedding <=> CAST(:query_embedding AS vector)
            LIMIT :limit
        """)

        result = self.db.execute(
            sql_query,
            {
                "query_embedding": embedding_array,
                "threshold": threshold,
                "limit": limit
            }
        )

        ingredients = []
        for row in result:
            ingredient = Ingredient(id=row[0], name=row[1])
            similarity = float(row[2])
            ingredients.append((ingredient, similarity))

        return ingredients

    # =====================================================
    # Single Entity Update
    # =====================================================

    def update_recipe_embedding(self, recipe_id: str) -> bool:
        """
        Generate or update embedding for a specific recipe

        Args:
            recipe_id: Recipe UUID

        Returns:
            True if successful, False otherwise
        """
        recipe = self.db.query(Recipe).filter(Recipe.id == recipe_id).first()
        if not recipe:
            return False

        # Generate new embedding
        text = self._recipe_to_text(recipe)
        if not text:
            return False

        embedding = self._generate_embedding(text)
        if not embedding:
            return False

        try:
            recipe.embedding = embedding
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            print(f"Error updating embedding for recipe {recipe_id}: {e}")
            return False
