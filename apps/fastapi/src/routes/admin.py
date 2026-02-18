"""
Admin Routes - Administrative endpoints for chatbot management
"""
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from apps.fastapi import logger
from database import get_db
from apps.fastapi.src.services.embedding_service import EmbeddingService
from apps.fastapi.src.agents.agent_loader import (
    get_agent_prompt,
    get_all_agent_prompts,
    refresh_prompts,
    AgentPromptInfo
)

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


# Prompt Management Models (updated to not require DB fields)
class PromptResponse(BaseModel):
    """Response model for a single agent prompt"""
    agent_key: str
    agent_name: str
    description: Optional[str]
    current_prompt: str
    model_name: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True


class PromptListResponse(BaseModel):
    """Response model for list of prompts"""
    prompts: List[PromptResponse]
    total: int


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

    @staticmethod
    @admin_route.get(
        "/prompts",
        status_code=status.HTTP_200_OK,
        response_model=PromptListResponse,
        summary="List agent prompts",
        description="Retrieve a list of all agent prompts from code"
    )
    def list_prompts(
        agent_key: Optional[str] = Query(None, description="Filter by agent key"),
        is_active: Optional[bool] = Query(None, description="Filter by active status (always true for code-based prompts)")
    ):
        """
        List agent prompts

        This endpoint retrieves prompts for all agents directly from the code.
        Prompts are not stored in the database - they are loaded from agent files.
        """
        logger.info(f"Listing prompts (agent_key: {agent_key}, is_active: {is_active})")

        try:
            all_prompts = get_all_agent_prompts()

            # Filter by agent_key if provided
            if agent_key:
                all_prompts = [p for p in all_prompts if p.agent_key == agent_key]

            # Convert to response model
            prompt_responses = [
                PromptResponse(
                    agent_key=p.agent_key,
                    agent_name=p.agent_name,
                    description=p.description,
                    current_prompt=p.current_prompt,
                    model_name=p.model_name,
                    is_active=p.is_active
                )
                for p in all_prompts
            ]

            return PromptListResponse(prompts=prompt_responses, total=len(prompt_responses))

        except Exception as e:
            logger.error(f"Error listing prompts: {e}")
            return PromptListResponse(prompts=[], total=0)

    @staticmethod
    @admin_route.get(
        "/prompts/{agent_key}",
        status_code=status.HTTP_200_OK,
        response_model=PromptResponse,
        summary="Get agent prompt",
        description="Retrieve a specific agent prompt by key"
    )
    def get_prompt(agent_key: str):
        """
        Get agent prompt by key

        This endpoint retrieves a specific prompt for an agent,
        identified by the agent key. Prompts are loaded from code.
        """
        logger.info(f"Getting prompt for agent_key: {agent_key}")

        try:
            prompt_info = get_agent_prompt(agent_key)

            if not prompt_info:
                raise HTTPException(status_code=404, detail=f"Prompt not found for agent: {agent_key}")

            return PromptResponse(
                agent_key=prompt_info.agent_key,
                agent_name=prompt_info.agent_name,
                description=prompt_info.description,
                current_prompt=prompt_info.current_prompt,
                model_name=prompt_info.model_name,
                is_active=prompt_info.is_active
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting prompt: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
    @admin_route.post(
        "/prompts/refresh",
        status_code=status.HTTP_200_OK,
        summary="Refresh prompts cache",
        description="Refresh the prompts cache to load latest prompts from code"
    )
    def refresh_prompts_cache():
        """
        Refresh prompts cache

        This endpoint refreshes the prompts cache, reloading all prompts
        from the agent code files.
        """
        logger.info("Refreshing prompts cache")

        try:
            refresh_prompts()
            all_prompts = get_all_agent_prompts()

            return {
                "success": True,
                "message": f"Refreshed {len(all_prompts)} agent prompts",
                "agents": [p.agent_key for p in all_prompts]
            }

        except Exception as e:
            logger.error(f"Error refreshing prompts: {e}")
            raise HTTPException(status_code=500, detail=str(e))
