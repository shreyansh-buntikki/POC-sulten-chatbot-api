"""
Pipeline Orchestrator
Ties together all stages of the recipe search pipeline
"""
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from openai import OpenAI

from apps.fastapi.src.services.session_memory_manager import (
    SessionMemoryManager, SessionState
)
from apps.fastapi.src.agents.nlid_agent import NLIDAgent
from apps.fastapi.src.services.retrieval_strategy import (
    RetrievalStrategyDecider, RetrievalPlan
)
from apps.fastapi.src.services.embedding_service import EmbeddingService
from apps.fastapi.src.services.sql_generator import (
    SQLGenerator, SQLExecutionService, SQLGenerationResult
)
from apps.fastapi.src.services.user_context_service import UserContextService
from models import Recipe, RecipeIngredient, Ingredient, BundleRecipe, UserPurchase


class RecipeSearchPipeline:
    """
    Complete 10-stage pipeline for recipe search

    Stages:
    1. Session Memory (conversation state)
    2. Intent & Entity Detection (NLID)
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
        Initialize the pipeline

        Args:
            db: SQLAlchemy database session
            openai_client: OpenAI client for LLM calls
        """
        self.db = db
        self.client = openai_client

        # Stage components
        self.session_manager = SessionMemoryManager()
        self.nlid_agent = NLIDAgent(db, openai_client)
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
        try:
            # ============ STAGE 1: Session Memory ============
            session = self.session_manager.get_or_create_session(
                session_id, user_uid, language
            )
            session.add_to_history("user", query)

            # ============ STAGE 2: Intent & Entity Detection ============
            nlid_result = await self.nlid_agent.process(query, {})

            # Check if query is cooking-related
            is_supported, rejection_message = self.nlid_agent.is_query_supported(nlid_result)
            if not is_supported:
                session.add_to_history("assistant", rejection_message)
                self.session_manager.save_session(session)
                return {
                    "response": rejection_message,
                    "metadata": {
                        "is_cooking_related": False,
                        "intent": "not_supported"
                    }
                }

            # Update session state from NLID results
            session = self.session_manager.update_session_from_nlid(session, nlid_result)
            session_context = self.session_manager.get_user_context(session, {})

            # ============ STAGE 3: Retrieval Strategy Decision ============
            retrieval_plan = self.strategy_decider.decide_strategy(
                query, nlid_result, session_context
            )

            # ============ STAGE 4: Retrieval Execution (Candidates) ============
            candidate_ids = None
            similarity_scores = {}

            if retrieval_plan.strategy in [
                "embeddings_only",
                "hybrid_vector_to_sql"
            ]:
                # Use embedding search
                embedding_results = self.embedding_service.search_recipes_by_embedding(
                    query_text=retrieval_plan.vector_query or query,
                    limit=retrieval_plan.top_k,
                    threshold=0.65
                )
                candidate_ids = [str(r.id) for r, _ in embedding_results]
                similarity_scores = {str(r.id): s for r, s in embedding_results}

            elif retrieval_plan.strategy == "hybrid_sql_to_vector":
                # First get SQL candidates (will be done in stage 8)
                pass

            # ============ STAGE 5: Schema Understanding ============
            from apps.fastapi.src.services.schema_understanding import get_schema_service
            schema_service = get_schema_service()
            relevant_schema = schema_service.get_relevant_schema(
                nlid_result["intent"],
                retrieval_plan.sql_filters,
                session_context
            )

            # ============ STAGE 6: SQL Generation ============
            sql_result = self.sql_generator.generate_sql(
                query,
                nlid_result,
                retrieval_plan.sql_filters,
                session_context,
                candidate_ids
            )

            # ============ STAGE 7: SQL Validation ============
            # (Done within SQL generation stage)

            # ============ STAGE 8: SQL Execution ============
            execution_result = self.sql_executor.execute_with_fallback(sql_result)

            if not execution_result["success"]:
                # Fallback response
                error_response = self._generate_error_response(query, execution_result)
                session.add_to_history("assistant", error_response)
                self.session_manager.save_session(session)

                return {
                    "response": error_response,
                    "metadata": {
                        "error": execution_result.get("error"),
                        "fallback_used": True
                    }
                }

            # ============ STAGE 9: Post-Processing & Ranking ============
            processed_recipes = self._post_process_and_rank(
                execution_result["rows"],
                similarity_scores,
                session_context
            )

            # Limit to max recipes
            final_recipes = processed_recipes[:self.MAX_RECIPES]

            # ============ STAGE 10: Natural Language Answer Generation ============
            response = self._generate_natural_language_response(
                query, final_recipes, nlid_result
            )

            # Save to session
            session.add_to_history("assistant", response)
            self.session_manager.save_session(session)

            # Prepare metadata
            metadata = {
                "intent": nlid_result["intent"],
                "is_cooking_related": True,
                "retrieval_strategy": retrieval_plan.strategy,
                "num_results": len(final_recipes),
                "recipes": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "similarity": round(r.get("similarity", 0), 3),
                        "access_level": r["access_level"],
                        "is_liked": r.get("is_liked", False),
                    }
                    for r in final_recipes
                ]
            }

            return {
                "response": response,
                "metadata": metadata
            }

        except Exception as e:
            # Handle any unexpected errors
            error_response = f"I apologize, but I encountered an error processing your request. Please try again."
            return {
                "response": error_response,
                "metadata": {"error": str(e)}
            }

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
        - Apply personalization ranking
        """
        processed = []

        for row in rows:
            recipe_id = row.get("id")

            # Get full recipe object for additional checks
            recipe = self.db.query(Recipe).filter(Recipe.id == recipe_id).first()
            if not recipe:
                continue

            # Final eligibility guard
            if recipe.private or recipe.deletedAt:
                continue

            # Determine access level
            user_uid = session_context.get("user_uid")
            access_level = self.user_context_service.get_recipe_access_level(
                recipe_id, user_uid
            )

            # Calculate priority score
            priority_score = 0
            if user_uid:
                user_ctx = self.user_context_service.get_user_context(user_uid)
                if recipe_id in user_ctx.get("liked_recipe_ids", set()):
                    priority_score = 100
                elif recipe_id in user_ctx.get("created_recipe_ids", set()):
                    priority_score = 50

            # Get similarity score if available
            similarity = similarity_scores.get(recipe_id, 0.7)

            processed.append({
                "id": recipe_id,
                "name": row.get("name") or recipe.name,
                "ingress": row.get("ingress") or recipe.ingress,
                "difficulty": row.get("difficulty") or recipe.difficulty,
                "total_time": row.get("total_time", (recipe.prepTime or 0) + (recipe.cookTime or 0)),
                "image": row.get("image") or recipe.image,
                "servings": row.get("servings") or recipe.servings,
                "similarity": similarity,
                "priority_score": priority_score,
                "access_level": access_level,
                "is_liked": priority_score == 100,
            })

        # Sort by: priority_score (desc), then similarity (desc)
        processed.sort(key=lambda x: (x["priority_score"], x["similarity"]), reverse=True)

        return processed

    def _generate_natural_language_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        nlid_result: Dict[str, Any]
    ) -> str:
        """Generate natural language response from recipe results"""
        if not recipes:
            return self._generate_no_results_response(query, nlid_result)

        # Build context for LLM
        recipes_context = []
        for recipe in recipes:
            access_note = ""
            if recipe["access_level"] == "name_only":
                access_note = " (Premium recipe - upgrade for full details)"

            context_parts = [
                f"- **{recipe['name']}**{access_note}"
            ]

            if recipe["ingress"]:
                context_parts.append(f"  {recipe['ingress']}")

            context_parts.append(
                f"  Time: {recipe['total_time']} min | Difficulty: {recipe['difficulty'] or 'N/A'}"
            )

            if recipe.get("is_liked"):
                context_parts.append("  ⭐ You liked this recipe")

            recipes_context.append("\n".join(context_parts))

        messages = [
            {
                "role": "system",
                "content": """You are a recipe assistant helping users find recipes.

Present recipe options clearly and concisely:
- Start with brief acknowledgment
- List up to 5 recipes with key details
- Mark premium recipes appropriately
- Offer to provide full details
- Keep under 300 words"""
            },
            {
                "role": "user",
                "content": f"""User query: "{query}"

Available recipes:
{chr(10).join(recipes_context)}

Provide a helpful response with recipe suggestions."""
            }
        ]

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
            max_tokens=400
        )

        return response.choices[0].message.content

    def _generate_no_results_response(
        self,
        query: str,
        nlid_result: Dict[str, Any]
    ) -> str:
        """Generate response when no recipes found"""
        messages = [
            {
                "role": "system",
                "content": "You are a helpful recipe assistant."
            },
            {
                "role": "user",
                "content": f"""User query: "{query}"

No recipes found matching the search criteria.

Provide a helpful response that:
1. Acknowledges we couldn't find matches
2. Asks clarifying questions or suggests alternatives
3. Encourages trying different search terms

Keep under 200 words."""
            }
        ]

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
            max_tokens=200
        )

        return response.choices[0].message.content

    def _generate_error_response(
        self,
        query: str,
        execution_result: Dict[str, Any]
    ) -> str:
        """Generate error response"""
        return "I apologize, but I encountered an error searching for recipes. Please try a different search or contact support if the problem persists."
