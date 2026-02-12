"""
Admin Routes - Administrative endpoints for chatbot management
"""
from typing import Optional
from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from apps.fastapi import logger
from database import get_db
from apps.fastapi.src.services.embedding_service import EmbeddingService

admin_route = APIRouter(prefix="/admin", tags=["Admin"])


# =====================================================
# Request/Response Models
# =====================================================

class EmbeddingGenerationRequest(BaseModel):
    """Request model for embedding generation"""
    limit: Optional[int] = Query(None, description="Limit number of items to process")


class EmbeddingGenerationResponse(BaseModel):
    """Response model for embedding generation results"""
    success: bool
    processed: int
    failed: int
    skipped: int
    message: str


# =====================================================
# Routes
# =====================================================

class AdminRoutes:
    """Route handlers for administrative operations"""

    @staticmethod
    @admin_route.post(
        "/embeddings/recipes",
        status_code=status.HTTP_200_OK,
        response_model=EmbeddingGenerationResponse,
        summary="Generate recipe embeddings",
        description="Trigger embedding generation for recipes. Use limit parameter for testing."
    )
    def generate_recipe_embeddings(
        limit: Optional[int] = Query(None, ge=1, le=1000, description="Limit number of recipes to process"),
        db: Session = Depends(get_db)
    ):
        """
        Generate embeddings for recipes

        This endpoint triggers batch embedding generation for recipes
        that don't already have embeddings.
        """
        logger.info(f"Generating recipe embeddings (limit: {limit})")

        try:
            service = EmbeddingService(db)
            stats = service.generate_recipe_embeddings(limit=limit)

            return EmbeddingGenerationResponse(
                success=stats['failed'] == 0,
                processed=stats['processed'],
                failed=stats['failed'],
                skipped=stats['skipped'],
                message=f"Processed {stats['processed']} recipes, {stats['failed']} failed, {stats['skipped']} skipped"
            )

        except Exception as e:
            logger.error(f"Error generating recipe embeddings: {e}")
            return EmbeddingGenerationResponse(
                success=False,
                processed=0,
                failed=0,
                skipped=0,
                message=f"Error: {str(e)}"
            )

    @staticmethod
    @admin_route.post(
        "/embeddings/ingredients",
        status_code=status.HTTP_200_OK,
        response_model=EmbeddingGenerationResponse,
        summary="Generate ingredient embeddings",
        description="Trigger embedding generation for ingredients. Use limit parameter for testing."
    )
    def generate_ingredient_embeddings(
        limit: Optional[int] = Query(None, ge=1, le=1000, description="Limit number of ingredients to process"),
        db: Session = Depends(get_db)
    ):
        """
        Generate embeddings for ingredients

        This endpoint triggers batch embedding generation for ingredients
        that don't already have embeddings.
        """
        logger.info(f"Generating ingredient embeddings (limit: {limit})")

        try:
            service = EmbeddingService(db)
            stats = service.generate_ingredient_embeddings(limit=limit)

            return EmbeddingGenerationResponse(
                success=stats['failed'] == 0,
                processed=stats['processed'],
                failed=stats['failed'],
                skipped=stats['skipped'],
                message=f"Processed {stats['processed']} ingredients, {stats['failed']} failed, {stats['skipped']} skipped"
            )

        except Exception as e:
            logger.error(f"Error generating ingredient embeddings: {e}")
            return EmbeddingGenerationResponse(
                success=False,
                processed=0,
                failed=0,
                skipped=0,
                message=f"Error: {str(e)}"
            )

    @staticmethod
    @admin_route.get(
        "/embeddings/stats",
        status_code=status.HTTP_200_OK,
        summary="Get embedding statistics",
        description="Get statistics about generated embeddings"
    )
    def get_embedding_stats(db: Session = Depends(get_db)):
        """Get statistics about embeddings in the database"""
        from models import Recipe, Ingredient

        try:
            total_recipes = db.query(Recipe).count()
            recipe_embeddings = db.query(Recipe).filter(Recipe.embedding != None).count()

            total_ingredients = db.query(Ingredient).count()
            ingredient_embeddings = db.query(Ingredient).filter(Ingredient.embedding != None).count()

            return {
                "recipes": {
                    "total": total_recipes,
                    "with_embeddings": recipe_embeddings,
                    "pending": total_recipes - recipe_embeddings
                },
                "ingredients": {
                    "total": total_ingredients,
                    "with_embeddings": ingredient_embeddings,
                    "pending": total_ingredients - ingredient_embeddings
                }
            }

        except Exception as e:
            logger.error(f"Error getting embedding stats: {e}")
            return {
                "error": str(e)
            }
