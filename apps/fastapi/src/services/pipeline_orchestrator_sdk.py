"""
Pipeline Orchestrator (SDK Version)
Ties together all stages of the recipe search pipeline using OpenAI Agents SDK
"""
from typing import Dict, Any, List, Optional, Tuple
import time
from sqlalchemy.orm import Session
from openai import OpenAI
from agents import Runner

from apps.fastapi import logger

from apps.fastapi.src.services.session_memory_manager import (
    SessionMemoryManager, SessionState
)
from apps.fastapi.src.agents.sdk_nlid_agent import nlid_agent, IntentOutput
from apps.fastapi.src.agents.sdk_orchestrator_agent import orchestrator_agent
from apps.fastapi.src.agents.sdk_nlg_agent import (
    nlg_agent,
    generate_recipe_response,
    generate_no_results_response,
    generate_error_response
)
from apps.fastapi.src.agents.agent_tools import (
    search_recipes_by_embedding,
    get_recipe_details,
    apply_recipe_filters,
    rank_recipes,
    get_recipe_nutrition,
    get_ingredient_nutrition,
)
from apps.fastapi.src.services.retrieval_strategy import (
    RetrievalStrategyDecider, RetrievalPlan
)
from apps.fastapi.src.services.embedding_service import EmbeddingService
from apps.fastapi.src.services.sql_generator import (
    SQLGenerator, SQLExecutionService, SQLGenerationResult
)
from apps.fastapi.src.services.user_context_service import UserContextService
from models import Recipe, UserLikesRecipe


class RecipeSearchPipelineSDK:
    """
    Complete 10-stage pipeline for recipe search using OpenAI Agents SDK

    Stages:
    1. Session Memory (conversation state)
    2. Intent & Entity Detection (NLID via SDK)
    3. Retrieval Strategy (Decision)
    4. Retrieval Execution (Candidates)
    5. Schema Understanding (SQL context)
    6. SQL Generation (Filtering & Enforcement)
    7. SQL Validation
    8. SQL Execution
    9. Post-Processing & Ranking
    10. Natural Language Answer Generation
    """

    MAX_RECIPES = 5

    def __init__(self, db: Session, openai_client: OpenAI):
        """
        Initialize the pipeline with SDK agents

        Args:
            db: SQLAlchemy database session
            openai_client: OpenAI client for LLM calls
        """
        self.db = db
        self.client = openai_client

        # Stage components
        self.session_manager = SessionMemoryManager()
        self.strategy_decider = RetrievalStrategyDecider()
        self.embedding_service = EmbeddingService(db)
        self.sql_generator = SQLGenerator(openai_client)
        self.sql_executor = SQLExecutionService(db)
        self.user_context_service = UserContextService(db)

    async def process_query(
        self,
        query: str,
        session_id: str,
        user_uid: Optional[str] = None,
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Process a user query through the complete pipeline

        Args:
            query: User's query text
            session_id: Session identifier
            user_uid: Optional user identifier
            language: Language code

        Returns:
            Dictionary with response and metadata
        """
        pipeline_start_time = time.time()
        logger.info("=" * 80)
        logger.info(f"[PIPELINE START] Query: {query[:100]} | Session: {session_id} | User: {user_uid}")
        logger.info("=" * 80)

        try:
            # ============ STAGE 1: Session Memory ============
            stage_start = time.time()
            logger.info(f"[STAGE 1] Session Management - session: {session_id}, user: {user_uid}, language: {language}")
            session = self.session_manager.get_or_create_session(
                session_id, user_uid, language
            )
            session.add_to_history("user", query)
            logger.info(f"[STAGE 1] ✓ Completed in {time.time() - stage_start:.3f}s | Session ID: {session.session_id}")

            # ============ STAGE 2: Intent & Entity Detection (SDK) ============
            stage_start = time.time()
            logger.info(f"[STAGE 2] Running NLID Agent on query: {query[:50]}...")
            nlid_result = await Runner.run(
                nlid_agent,
                query,
                context={"session_id": session_id, "user_uid": user_uid}
            )

            # Get structured output from SDK agent
            # With AgentOutputSchema, final_output is already the typed object
            output = nlid_result.final_output
            nlid_data = output if isinstance(output, IntentOutput) else IntentOutput(**output)

            logger.info(f"[STAGE 2] ✓ NLID completed in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 2]   - Cooking Related: {nlid_data.is_cooking_related}")
            logger.info(f"[STAGE 2]   - Intent: {nlid_data.intent}")
            logger.info(f"[STAGE 2]   - Confidence: {nlid_data.confidence}")
            logger.info(f"[STAGE 2]   - Summary: {nlid_data.summary[:100] if nlid_data.summary else 'N/A'}")
            if nlid_data.entities:
                logger.info(f"[STAGE 2]   - Entities: {list(nlid_data.entities.keys())}")
            if nlid_data.filters:
                logger.info(f"[STAGE 2]   - Filters: {nlid_data.filters}")

            # Convert to dict for compatibility with existing pipeline
            nlid_result_dict = {
                "is_cooking_related": nlid_data.is_cooking_related,
                "intent": nlid_data.intent,
                "entities": nlid_data.entities,
                "parameters": nlid_data.parameters,
                "filters": nlid_data.filters,
                "summary": nlid_data.summary,
                "confidence": nlid_data.confidence,
            }

            # Check if query is cooking-related
            if not nlid_data.is_cooking_related:
                logger.warning(f"[STAGE 2] Query NOT cooking-related, triggering guardrail response")
                # Use the orchestrator's guardrail response
                stage_start = time.time()
                orchestrator_result = await Runner.run(
                    orchestrator_agent,
                    query,
                    context={"session_id": session_id, "user_uid": user_uid}
                )
                rejection_message = orchestrator_result.final_output

                logger.info(f"[STAGE 2] Guardrail response generated in {time.time() - stage_start:.3f}s")
                logger.info(f"[STAGE 2] Rejection message: {rejection_message[:150]}...")

                session.add_to_history("assistant", rejection_message)
                self.session_manager.save_session(session)

                return {
                    "response": rejection_message,
                    "metadata": {
                        "is_cooking_related": False,
                        "intent": "not_supported",
                        "retrieval_strategy": "none",
                        "num_results": 0,
                    }
                }

            # Update session state from NLID results
            session = self.session_manager.update_session_from_nlid(session, nlid_result_dict)
            session_context = self.session_manager.get_user_context(session, {})
            logger.info(f"[STAGE 2] Session context updated with NLID results")

            # ============ STAGE 3: Retrieval Strategy Decision ============
            stage_start = time.time()
            logger.info(f"[STAGE 3] Deciding retrieval strategy...")
            retrieval_plan = self.strategy_decider.decide_strategy(
                query, nlid_result_dict, session_context
            )

            logger.info(f"[STAGE 3] ✓ Completed in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 3]   - Strategy: {retrieval_plan.strategy}")
            logger.info(f"[STAGE 3]   - Top K: {retrieval_plan.top_k}")
            logger.info(f"[STAGE 3]   - Vector Query: {retrieval_plan.vector_query[:50] if retrieval_plan.vector_query else 'N/A'}")
            if retrieval_plan.sql_filters:
                logger.info(f"[STAGE 3]   - SQL Filters: {list(retrieval_plan.sql_filters.keys())}")

            # ============ STAGE 4: Retrieval Execution (Candidates) ============
            stage_start = time.time()
            logger.info(f"[STAGE 4] Executing retrieval strategy: {retrieval_plan.strategy}")
            candidate_ids = None
            similarity_scores = {}

            if retrieval_plan.strategy in [
                "embeddings_only",
                "hybrid_vector_to_sql"
            ]:
                # Use embedding search via tools
                logger.info(f"[STAGE 4] Using embedding search (vector_query: {retrieval_plan.vector_query or query[:50]})")
                embedding_results = search_recipes_by_embedding(
                    self.db,
                    query_text=retrieval_plan.vector_query or query,
                    limit=retrieval_plan.top_k,
                    threshold=0.4  # Lowered from 0.65 to get more results
                )
                candidate_ids = [str(r.id) for r, _ in embedding_results]
                similarity_scores = {str(r.id): s for r, s in embedding_results}
                logger.info(f"[STAGE 4] ✓ Found {len(embedding_results)} candidates via embeddings in {time.time() - stage_start:.3f}s")
                if embedding_results:
                    top_scores = sorted([s for _, s in embedding_results], reverse=True)[:3]
                    logger.info(f"[STAGE 4]   - Top similarity scores: {top_scores}")

            elif retrieval_plan.strategy == "hybrid_sql_to_vector":
                # First get SQL candidates (will be done in stage 8)
                logger.info(f"[STAGE 4] Will use SQL-first approach (candidates fetched in STAGE 8)")
            else:
                logger.info(f"[STAGE 4] Strategy {retrieval_plan.strategy} - no vector search needed")

            # ============ STAGE 5: Schema Understanding ============
            stage_start = time.time()
            logger.info(f"[STAGE 5] Understanding schema for intent: {nlid_result_dict['intent']}")
            from apps.fastapi.src.services.schema_understanding import get_schema_service
            schema_service = get_schema_service()
            relevant_schema = schema_service.get_relevant_schema(
                nlid_result_dict["intent"],
                retrieval_plan.sql_filters,
                session_context
            )
            logger.info(f"[STAGE 5] ✓ Schema retrieved in {time.time() - stage_start:.3f}s")
            if relevant_schema:
                logger.info(f"[STAGE 5]   - Tables: {[t.name for t in relevant_schema.tables[:5]]}...")
                logger.info(f"[STAGE 5]   - Business rules: {len(relevant_schema.business_rules)} rules")

            # ============ STAGE 6: SQL Generation ============
            stage_start = time.time()
            logger.info(f"[STAGE 6] Generating SQL query...")
            sql_result = self.sql_generator.generate_sql(
                query,
                nlid_result_dict,
                retrieval_plan.sql_filters,
                session_context,
                candidate_ids
            )
            logger.info(f"[STAGE 6] ✓ SQL generated in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 6]   - SQL Safe: {sql_result.is_safe}")
            logger.info(f"[STAGE 6]   - Estimated Rows: {sql_result.estimated_rows}")
            logger.info(f"[STAGE 6]   - SQL Query:\n{sql_result.sql}")
            logger.info(f"[STAGE 6]   - Explanation: {sql_result.explanation[:100] if sql_result.explanation else 'N/A'}...")
            if candidate_ids:
                logger.info(f"[STAGE 6]   - Candidate IDs (first 5): {candidate_ids[:5]}")

            # ============ STAGE 7: SQL Validation ============
            logger.info(f"[STAGE 7] SQL Validation (completed in STAGE 6)")
            logger.info(f"[STAGE 7]   - Validation Status: {'PASSED' if sql_result.is_safe else 'FAILED'}")

            # ============ STAGE 8: SQL Execution ============
            stage_start = time.time()
            logger.info(f"[STAGE 8] Executing SQL query...")
            execution_result = self.sql_executor.execute_with_fallback(sql_result)

            logger.info(f"[STAGE 8] ✓ Execution completed in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 8]   - Success: {execution_result['success']}")
            logger.info(f"[STAGE 8]   - Rows Returned: {len(execution_result.get('rows', []))}")
            if not execution_result['success']:
                logger.error(f"[STAGE 8]   - Error: {execution_result.get('error', 'Unknown')}")
                logger.error(f"[STAGE 8]   - Fallback Used: {execution_result.get('fallback_used', False)}")

            if not execution_result["success"]:
                # Fallback response
                logger.error(f"[STAGE 8] SQL execution failed, generating error response")
                error_response = await self._generate_error_response(query, execution_result)
                session.add_to_history("assistant", error_response)
                self.session_manager.save_session(session)

                return {
                    "response": error_response,
                    "metadata": {
                        "error": execution_result.get("error"),
                        "fallback_used": True,
                        "retrieval_strategy": retrieval_plan.strategy,
                        "num_results": 0,
                    }
                }

            # ============ STAGE 9: Post-Processing & Ranking ============
            stage_start = time.time()
            logger.info(f"[STAGE 9] Post-processing and ranking {len(execution_result['rows'])} recipes...")
            processed_recipes = self._post_process_and_rank(
                execution_result["rows"],
                similarity_scores,
                session_context
            )

            # Limit to max recipes
            final_recipes = processed_recipes[:self.MAX_RECIPES]
            logger.info(f"[STAGE 9] ✓ Completed in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 9]   - Processed: {len(processed_recipes)} recipes")
            logger.info(f"[STAGE 9]   - Final selection: {len(final_recipes)} recipes (max: {self.MAX_RECIPES})")
            if final_recipes:
                logger.info(f"[STAGE 9]   - Top recipes: {[r['name'] for r in final_recipes[:3]]}")
                logger.info(f"[STAGE 9]   - Access levels: {{'free': sum(1 for r in final_recipes if r['access_level'] == 'free'), 'premium': sum(1 for r in final_recipes if r['access_level'] == 'premium')}}")

            # ============ STAGE 10: Natural Language Answer Generation ============
            stage_start = time.time()
            logger.info(f"[STAGE 10] Generating natural language response...")
            logger.info(f"[STAGE 10]   - Intent: {nlid_result_dict['intent']}")
            logger.info(f"[STAGE 10]   - Has recipes: {len(final_recipes) > 0}")

            # For recipe search results, use SDK orchestrator for better responses
            if nlid_result_dict["intent"] == "recipe_search" and final_recipes:
                logger.info(f"[STAGE 10] Using SDK response generation for recipe search")
                response = await self._generate_sdk_response(
                    query,
                    final_recipes,
                    nlid_result_dict,
                    session_id,
                    user_uid
                )
            else:
                logger.info(f"[STAGE 10] Using NLG agent for response generation")
                response = await self._generate_natural_language_response(
                    query, final_recipes, nlid_result_dict
                )

            logger.info(f"[STAGE 10] ✓ Response generated in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 10]   - Response length: {len(response)} chars")
            logger.info(f"[STAGE 10]   - Response preview: {response[:150]}...")

            # Save to session
            session.add_to_history("assistant", response)
            self.session_manager.save_session(session)

            # Prepare metadata with full recipe details
            metadata = {
                "intent": nlid_result_dict["intent"],
                "is_cooking_related": True,
                "retrieval_strategy": retrieval_plan.strategy,
                "num_results": len(final_recipes),
                "pipeline_duration_ms": round((time.time() - pipeline_start_time) * 1000, 2),
                "recipes": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "description": r.get("description"),
                        "ingress": r.get("ingress"),
                        "difficulty": r.get("difficulty"),
                        "prep_time": r.get("prep_time"),
                        "cook_time": r.get("cook_time"),
                        "total_time": r.get("total_time"),
                        "image": r.get("image"),
                        "servings": r.get("servings"),
                        "similarity": round(r.get("similarity", 0), 3),
                        "priority_score": r.get("priority_score", 0),
                        "access_level": r["access_level"],
                        "is_liked": r.get("is_liked", False),
                        "is_created": r.get("is_created", False),
                        "is_bundle_recipe": r.get("is_bundle_recipe", False),
                        "is_bundle_free_recipe": r.get("is_bundle_free_recipe", False),
                        "bundle_name": r.get("bundle_name"),
                        "ingredients": r.get("ingredients", []),
                        "instructions": r.get("instructions", []),
                    }
                    for r in final_recipes
                ]
            }

            logger.info("=" * 80)
            logger.info(f"[PIPELINE COMPLETE] Total time: {time.time() - pipeline_start_time:.3f}s | Results: {len(final_recipes)} recipes")
            logger.info("=" * 80)

            return {
                "response": response,
                "metadata": metadata
            }

        except Exception as e:
            # Handle any unexpected errors
            import traceback
            error_time = time.time() - pipeline_start_time
            logger.error("=" * 80)
            logger.error(f"[PIPELINE ERROR] Failed after {error_time:.3f}s")
            logger.error(f"[PIPELINE ERROR] Error: {str(e)}")
            logger.error(f"[PIPELINE ERROR] Traceback:")
            for line in traceback.format_exc().split('\n'):
                logger.error(f"  {line}")
            logger.error("=" * 80)

            error_response = f"I apologize, but I encountered an error processing your request. Please try again."
            return {
                "response": error_response,
                "metadata": {"error": str(e), "pipeline_duration_ms": round(error_time * 1000, 2)}
            }

    async def _generate_sdk_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        nlid_result: Dict[str, Any],
        session_id: str,
        user_uid: Optional[str] = None
    ) -> str:
        """
        Generate natural language response using SDK NLG agent

        This uses the SDK's NLG agent for consistent responses
        """
        logger.info(f"[NLG] Generating SDK response for {len(recipes)} recipes")
        # Build user context
        user_context = {}
        if user_uid:
            user_ctx = self.user_context_service.get_user_context(user_uid)
            user_context["has_liked_recipes"] = len(user_ctx.get("liked_recipe_ids", set())) > 0
            logger.info(f"[NLG] User context: has_liked_recipes={user_context.get('has_liked_recipes', False)}")

        # Use SDK NLG agent to generate response
        response = await generate_recipe_response(query, recipes, user_context)
        logger.info(f"[NLG] ✓ SDK response generated ({len(response)} chars)")
        return response

    def _post_process_and_rank(
        self,
        rows: List[Dict[str, Any]],
        similarity_scores: Dict[str, float],
        session_context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Post-process and rank recipes

        - Apply final eligibility checks
        - Determine bundle access
        - Fetch full recipe details (ingredients, instructions, bundle info)
        - Apply personalization ranking
        """
        processed = []
        user_uid = session_context.get("user_uid")

        for row in rows:
            recipe_id = row.get("id")

            # Get full recipe object
            recipe = self.db.query(Recipe).filter(Recipe.id == recipe_id).first()
            if not recipe:
                continue

            # Final eligibility guard
            if recipe.private or recipe.deletedAt:
                continue

            # Determine access level
            access_level = self.user_context_service.get_recipe_access_level(
                recipe_id, user_uid
            )

            # Check if recipe is from a bundle
            from sqlalchemy import text
            bundle_check = self.db.execute(text("""
                SELECT br."bundleId", b.name as bundle_name, br."isFree"
                FROM bundle_recipe br
                JOIN bundle b ON br."bundleId" = b.id
                WHERE br."recipeId" = :recipe_id
                LIMIT 1
            """), {"recipe_id": str(recipe_id)}).fetchone()

            is_bundle_recipe = bundle_check is not None
            is_bundle_free_recipe = bundle_check[2] if bundle_check else False
            bundle_name = bundle_check[1] if bundle_check else None

            # Calculate priority score
            priority_score = 0
            is_created = False
            is_liked = False

            if user_uid:
                user_ctx = self.user_context_service.get_user_context(user_uid)
                if recipe_id in user_ctx.get("liked_recipe_ids", set()):
                    priority_score = 100
                    is_liked = True
                elif recipe_id in user_ctx.get("created_recipe_ids", set()):
                    priority_score = 50
                    is_created = True

            # Get similarity score
            similarity = similarity_scores.get(recipe_id, 0.7)

            # Fetch ingredients
            from models import RecipeIngredient, Ingredient
            ingredients = self.db.query(RecipeIngredient, Ingredient).join(
                Ingredient, RecipeIngredient.ingredientId == Ingredient.id
            ).filter(
                RecipeIngredient.recipeId == recipe_id,
                RecipeIngredient.deletedAt == None
            ).order_by(RecipeIngredient.order).all()

            ingredient_list = []
            for ri, ing in ingredients:
                ingredient_list.append({
                    "name": ing.name,
                    "amount": ri.amount,
                    "unit": str(ri.unitId) if ri.unitId else None
                })

            # Fetch instructions
            from models import RecipeInstruction
            instructions = self.db.query(RecipeInstruction).filter(
                RecipeInstruction.recipeId == recipe_id,
                RecipeInstruction.deletedAt == None
            ).order_by(RecipeInstruction.order).all()

            instruction_list = [
                {
                    "order": instr.order,
                    "description": instr.description,
                    "image": instr.image
                }
                for instr in instructions
            ]

            processed.append({
                "id": recipe_id,
                "name": row.get("name") or recipe.name,
                "ingress": row.get("ingress") or recipe.ingress,
                "description": recipe.ingress,  # Using ingress as description
                "difficulty": row.get("difficulty") or recipe.difficulty,
                "total_time": row.get("total_time", (recipe.prepTime or 0) + (recipe.cookTime or 0)),
                "prep_time": recipe.prepTime,
                "cook_time": recipe.cookTime,
                "image": row.get("image") or recipe.image,
                "servings": row.get("servings") or recipe.servings,
                "similarity": similarity,
                "priority_score": priority_score,
                "access_level": access_level,
                "is_liked": is_liked,
                "is_created": is_created,
                "is_bundle_recipe": is_bundle_recipe,
                "is_bundle_free_recipe": is_bundle_free_recipe,
                "bundle_name": bundle_name,
                "ingredients": ingredient_list,
                "instructions": instruction_list,
            })

        # Sort by: priority_score (desc), then similarity (desc)
        processed.sort(key=lambda x: (x["priority_score"], x["similarity"]), reverse=True)

        return processed

    async def _generate_natural_language_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        nlid_result: Dict[str, Any]
    ) -> str:
        """Generate natural language response from recipe results using SDK"""
        logger.info(f"[NLG] Generating natural language response")
        if not recipes:
            logger.warning(f"[NLG] No recipes found, generating no-results response")
            return await self._generate_no_results_response(query, nlid_result)

        # Use SDK NLG agent to generate response
        response = await generate_recipe_response(query, recipes)
        logger.info(f"[NLG] ✓ Response generated ({len(response)} chars)")
        return response

    async def _generate_no_results_response(
        self,
        query: str,
        nlid_result: Dict[str, Any]
    ) -> str:
        """Generate response when no recipes are found using SDK"""
        logger.info(f"[NLG] Generating no-results response for intent: {nlid_result.get('intent', 'unknown')}")
        response = await generate_no_results_response(
            query,
            nlid_result.get("intent", "unknown"),
            nlid_result.get("entities", {}),
            nlid_result.get("filters", {})
        )
        logger.info(f"[NLG] ✓ No-results response generated ({len(response)} chars)")
        return response

    async def _generate_error_response(
        self,
        query: str,
        execution_result: Dict[str, Any]
    ) -> str:
        """Generate response for SQL execution errors using SDK"""
        error = execution_result.get("error", "Unknown error")
        logger.error(f"[NLG] Generating error response for: {error[:100]}")
        response = await generate_error_response(query, error)
        logger.info(f"[NLG] ✓ Error response generated ({len(response)} chars)")
        return response
