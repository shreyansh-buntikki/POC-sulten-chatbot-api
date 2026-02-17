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
from apps.fastapi.src.services.prompt_service import PromptService, PromptUpdateRequest

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


# Prompt Management Models
class PromptResponse(BaseModel):
    """Response model for a single agent prompt"""
    id: UUID
    agent_key: str
    agent_name: str
    description: Optional[str]
    current_prompt: str
    model_name: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PromptListResponse(BaseModel):
    """Response model for list of prompts"""
    prompts: List[PromptResponse]
    total: int



class PromptHistoryEntry(BaseModel):
    """Response model for a prompt history entry"""
    id: UUID
    version: int
    prompt_text: str
    changed_by_user_uid: Optional[str]
    change_reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PromptHistoryResponse(BaseModel):
    """Response model for prompt history"""
    agent_key: str
    agent_name: str
    history: List[PromptHistoryEntry]
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
        description="Retrieve a list of agent prompts with optional filtering"
    )
    def list_prompts(
        agent_key: Optional[str] = Query(None, description="Filter by agent key"),
        is_active: Optional[bool] = Query(None, description="Filter by active status"),
        db: Session = Depends(get_db)
    ):
        """
        List agent prompts

        This endpoint retrieves a list of prompts for agents,
        with optional filtering by agent key and active status.
        """
        logger.info(f"Listing prompts (agent_key: {agent_key}, is_active: {is_active})")

        try:
            service = PromptService(db)
            prompts, total = service.get_prompts(agent_key=agent_key, is_active=is_active)

            return PromptListResponse(prompts=prompts, total=total)

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
    def get_prompt(
        agent_key: str,
        db: Session = Depends(get_db)
    ):
        """
        Get agent prompt by key

        This endpoint retrieves a specific prompt for an agent,
        identified by the agent key.
        """
        logger.info(f"Getting prompt for agent_key: {agent_key}")

        try:
            service = PromptService(db)
            prompt = service.get_prompt(agent_key=agent_key)

            if not prompt:
                raise HTTPException(status_code=404, detail="Prompt not found")

            return prompt

        except Exception as e:
            logger.error(f"Error getting prompt: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
    @admin_route.post(
        "/prompts/{agent_key}/update",
        status_code=status.HTTP_200_OK,
        response_model=PromptResponse,
        summary="Update agent prompt",
        description="Update the prompt for a specific agent"
    )
    def update_prompt(
        agent_key: str,
        request: PromptUpdateRequest,
        db: Session = Depends(get_db)
    ):
        """
        Update agent prompt

        This endpoint updates the prompt for an agent,
        identified by the agent key.
        """
        logger.info(f"Updating prompt for agent_key: {agent_key}")

        try:
            service = PromptService(db)
            prompt = service.update_prompt(agent_key=agent_key, request=request)

            if not prompt:
                raise HTTPException(status_code=404, detail="Prompt not found")

            return prompt

        except Exception as e:
            logger.error(f"Error updating prompt: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
    @admin_route.get(
        "/prompts/{agent_key}/history",
        status_code=status.HTTP_200_OK,
        response_model=PromptHistoryResponse,
        summary="Get prompt history",
        description="Retrieve the change history of a specific agent prompt"
    )
    def get_prompt_history(
        agent_key: str,
        db: Session = Depends(get_db)
    ):
        """
        Get prompt change history

        This endpoint retrieves the change history for a specific prompt
        of an agent, identified by the agent key.
        """
        logger.info(f"Getting prompt history for agent_key: {agent_key}")

        try:
            service = PromptService(db)

            # Get the prompt to get the agent_name
            prompt = service.get_prompt(agent_key=agent_key)
            if not prompt:
                raise HTTPException(status_code=404, detail="Prompt not found")

            history, total = service.get_prompt_history(agent_key=agent_key)

            return PromptHistoryResponse(
                agent_key=agent_key,
                agent_name=prompt.agent_name,
                history=history,
                total=total
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting prompt history: {e}")
            raise HTTPException(status_code=500, detail=str(e))
