"""
Pipeline Orchestrator (SDK Version)
Ties together all stages of the recipe search pipeline using OpenAI Agents SDK
"""
from typing import Dict, Any, List, Optional, Tuple
import time
import asyncio
from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI
from agents import Runner

from apps.fastapi import logger

from apps.fastapi.src.services.session_memory_manager import (
    SessionMemoryManager, SessionState
)
from apps.fastapi.src.services.retrieval_strategy import RetrievalPlan, RetrievalStrategy
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
    get_ingredient_pricing,
    get_recipe_cost,
    search_ingredients_by_name,
)
from apps.fastapi.src.services.retrieval_strategy import (
    RetrievalStrategyDecider, RetrievalPlan, RetrievalStrategy
)
from apps.fastapi.src.services.embedding_service import EmbeddingService
from apps.fastapi.src.services.schema_understanding import get_schema_service
from apps.fastapi.src.services.sql_generator import (
    SQLGenerator, SQLExecutionService, SQLGenerationResult
)
from apps.fastapi.src.services.user_context_service import UserContextService
from apps.fastapi.src.utils.cost_nutrition_filters import (
    extract_cost_filter,
    extract_nutrition_filter,
    is_cost_nutrition_filter_query,
    NUTRITION_KEYWORDS
)
from apps.fastapi.src.utils.sql_builders import (
    build_recipe_cost_filter_sql,
    build_recipe_nutrition_filter_sql,
    build_recipe_combined_filter_sql,
    build_session_filter_conditions
)
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
        self.schema_understanding = get_schema_service()
        self.sql_generator = SQLGenerator(openai_client)
        self.sql_executor = SQLExecutionService(db)
        self.user_context_service = UserContextService(db)

        # Import ConversationStore for session context loading
        from apps.fastapi.src.services.conversation_store import ConversationStore
        self.conversation_store = ConversationStore(db)

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
        logger.info(f" [PIPELINE START] Query: {query[:100]} | Session: {session_id} | User: {user_uid}")

        # Initialize retrieval_plan to None to avoid undefined variable errors
        retrieval_plan = None

        try:
            # ============ STAGE 1: Session Memory ============
            stage_start = time.time()
            logger.info(f"[STAGE 1] Session Management - session: {session_id}, user: {user_uid}, language: {language}")
            session = self.session_manager.get_or_create_session(
                session_id, user_uid, language or "en", self.conversation_store
            )
            session.add_to_history("user", query)
            logger.info(f"[STAGE 1] ✓ Completed in {time.time() - stage_start:.3f}s | Session ID: {session.session_id}")

            # ============ PARALLEL PHASE: NLID + Schema Pre-fetch ============
            # Run NLID (intent detection) and schema pre-fetching in parallel
            # Most queries are recipe_search, so we pre-fetch that schema speculatively
            parallel_start = time.time()
            logger.info(f"[PARALLEL] Starting NLID + speculative schema fetch...")

            # Get conversation history for NLID context
            conversation_history = session.get_context_window(limit=10)
            logger.info(f"[PARALLEL] Loaded {len(conversation_history)} messages from conversation history for NLID context")

            # Create enhanced context with previous search context if available
            enhanced_context = {
                "session_id": session_id,
                "user_uid": user_uid,
                "conversation_history": conversation_history,
                "previous_search_context": None
            }

            # Add previous search context if available
            if session.context_entities.last_vector_query:
                enhanced_context["previous_search_context"] = {
                    "last_query": session.context_entities.last_vector_query,
                    "last_filters": session.context_entities.last_search_filters,
                    "last_intent": session.last_intent,
                    "excluded_ingredients": session.excluded_ingredients,
                    "included_ingredients": session.included_ingredients
                }

            async def run_nlid():
                """Run NLID agent with conversation history and previous search context"""
                return await Runner.run(
                    nlid_agent,
                    query,
                    context=enhanced_context
                )

            async def prefetch_schema():
                """Speculatively pre-fetch recipe_search schema (most common intent)"""
                # Pre-fetch session context and default schema
                _ = self.session_manager.get_user_context(session, {})
                _ = self.schema_understanding.get_relevant_schema(
                    "recipe_search",  # Most queries are recipe searches
                    {},  # Empty filters for pre-fetch
                    {"language": language or "en"}  # Basic context
                )
                return "schema_prefetched"

            # Run NLID and schema pre-fetch in parallel
            nlid_result, _ = await asyncio.gather(
                run_nlid(),
                prefetch_schema(),
                return_exceptions=True
            )

            logger.info(f"[PARALLEL] ✓ Completed in {time.time() - parallel_start:.3f}s")

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
                logger.warning(f"[STAGE 2] Query NOT cooking-related, returning scope message")
                # Return a direct rejection message without running orchestrator
                # (orchestrator has its own guardrail that would raise an exception)
                rejection_message = (
                    "I'm Sulten Chatbot, your cooking and recipe assistant! "
                    "I can help you with:\n"
                    "- Finding recipes and meal ideas\n"
                    "- Nutritional information about foods\n"
                    "- Cooking tips and techniques\n"
                    "- Ingredient substitutions\n\n"
                    "I'm not able to help with non-cooking topics, but I'd be happy to assist with any food-related questions!"
                )

                logger.info(f"[STAGE 2] Returning scope message for non-cooking query")

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

            # ============ SPECIAL HANDLING: Pricing and Nutrition Queries ============
            # Handle pricing_info and nutritional_info intents directly
            if nlid_result_dict["intent"] in ["pricing_info", "nutritional_info"]:
                return await self._handle_special_query(
                    query, nlid_result_dict, session, user_uid, language
                )

            # ============ SPECIAL HANDLING: Cost and Nutrition Filter Queries ============
            # Handle price_filter and nutrition_filter intents with direct SQL (NO EMBEDDING)
            # These queries filter recipes by recipe_metadata (pricing/nutrition)
            if nlid_result_dict["intent"] in ["price_filter", "nutrition_filter"]:
                logger.info(f"[STAGE 3] Detected {nlid_result_dict['intent']} - routing to DIRECT SQL (skipping embedding search)")
                return await self._handle_cost_nutrition_filter_query(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Also check if NLID returned requires_embedding=False or if we can detect cost/nutrition filters
            requires_embedding = getattr(nlid_data, 'requires_embedding', True)
            if not requires_embedding:
                logger.info(f"[STAGE 3] NLID indicated requires_embedding=False - checking for cost/nutrition filters")
                # Try to extract filters from query
                cost_filter = extract_cost_filter(query)
                nutrition_filter = extract_nutrition_filter(query)
                if cost_filter or nutrition_filter:
                    logger.info(f"[STAGE 3] Found cost/nutrition filters - routing to DIRECT SQL")
                    # Update NLID result with extracted filters
                    if cost_filter and "cost" not in nlid_result_dict.get("filters", {}):
                        if "filters" not in nlid_result_dict:
                            nlid_result_dict["filters"] = {}
                        nlid_result_dict["filters"]["cost"] = cost_filter
                        nlid_result_dict["intent"] = "price_filter"
                    if nutrition_filter and "nutrition" not in nlid_result_dict.get("filters", {}):
                        if "filters" not in nlid_result_dict:
                            nlid_result_dict["filters"] = {}
                        nlid_result_dict["filters"]["nutrition"] = nutrition_filter
                        if not cost_filter:
                            nlid_result_dict["intent"] = "nutrition_filter"
                    return await self._handle_cost_nutrition_filter_query(
                        query, nlid_result_dict, session, user_uid, language
                    )

            # Update session state from NLID results
            session = self.session_manager.update_session_from_nlid(session, nlid_result_dict)

            # Get user context
            session_context = self.session_manager.get_user_context(session, {})

            # CRITICAL: Ensure session context includes the latest search information
            # This is needed for proper refinement detection
            session_context["last_intent"] = session.last_intent
            session_context["context_entities"] = {
                "last_vector_query": session.context_entities.last_vector_query,
                "last_search_filters": session.context_entities.last_search_filters,
            }
            logger.info(f"[SESSION LOAD] Session context before build: {session_context.get('context_entities', {})}")
            logger.info(f"[SESSION LOAD] Loaded last_vector_query: {session.context_entities.last_vector_query}")
            logger.info(f"[SESSION LOAD] Loaded last_intent: {session.last_intent}")

            # Add language to session context for SQL filtering
            session_context["language"] = language or "en"

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

            # ============ SPECIAL HANDLING: Dietary Preference & Allergy Statements ============
            # If user is stating a dietary preference (vegetarian, vegan, etc.) or allergy
            # Two cases:
            # 1. Standalone preference (no previous search) → Acknowledge and save
            # 2. Refinement after search → Run NEW search with combined constraints

            # Check if this is a refinement scenario:
            # - User mentioned an allergy (excluded_ingredients) OR dietary preference (tags)
            # - There's a previous recipe search to refine
            filters = nlid_result_dict.get("filters", {})
            excluded_ingredients = filters.get("excluded_ingredients", [])
            has_allergy = bool(excluded_ingredients)

            # Also check query text directly for allergy patterns (in case NLID missed it)
            query_lower = query.lower()
            if "allergic to" in query_lower or "allergy" in query_lower:
                import re
                allergy_match = re.search(r"allergic to (\w+(?:\s+\w+)*)", query_lower)
                if allergy_match and not has_allergy:
                    excluded_ingredients = [allergy_match.group(1)]
                    has_allergy = True
                    # Update the filters with extracted allergy
                    if "filters" not in nlid_result_dict:
                        nlid_result_dict["filters"] = {}
                    nlid_result_dict["filters"]["excluded_ingredients"] = excluded_ingredients

            # Check for dietary preference patterns
            tags = filters.get("tags", [])
            dietary_tags = ["vegetarian", "vegan", "gluten-free", "dairy-free", "keto"]
            mentioned_tags = []

            # Check NLID-extracted tags
            if any(tag in dietary_tags for tag in tags):
                mentioned_tags = tags

            # Also check query text directly for common patterns
            dietary_patterns = {
                "i am vegetarian": "vegetarian",
                "i'm vegetarian": "vegetarian",
                "im vegetarian": "vegetarian",
                "i am vegan": "vegan",
                "i'm vegan": "vegan",
                "im vegan": "vegan",
                "i am gluten-free": "gluten-free",
                "i'm gluten-free": "gluten-free",
                "gluten free": "gluten-free",
            }

            for pattern, tag in dietary_patterns.items():
                if pattern in query_lower and tag not in mentioned_tags:
                    mentioned_tags.append(tag)

            has_dietary_preference = bool(mentioned_tags)

            # Check if we should handle this as a special case:
            # 1. general_chat intent with dietary preference OR allergy
            # 2. recipe_search intent with ONLY allergies (no other search intent)
            is_special_case = (
                (nlid_result_dict["intent"] == "general_chat" and (has_dietary_preference or has_allergy)) or
                (nlid_result_dict["intent"] == "recipe_search" and has_allergy and not filters.get("included_ingredients") and not filters.get("max_time") and not filters.get("difficulty"))
            )

            if is_special_case:
                    if mentioned_tags:
                        logger.info(f"[PREFERENCE] User stated dietary preference: {mentioned_tags}")
                    if excluded_ingredients:
                        logger.info(f"[ALLERGY] User stated allergy to: {excluded_ingredients}")

                    # Check if this is a refinement (has previous recipe search)
                    last_vector_query = session_context.get("context_entities", {}).get("last_vector_query")
                    last_intent = session_context.get("last_intent")

                    if last_vector_query and last_intent == "recipe_search":
                        # This is a REFINEMENT - run NEW search with combined constraints
                        logger.info(f"[REFINEMENT] This is a refinement - running NEW search with combined constraints")
                        logger.info(f"[REFINEMENT] Previous search: {last_vector_query}")
                        if mentioned_tags:
                            logger.info(f"[REFINEMENT] Adding dietary constraint: {mentioned_tags}")
                        if excluded_ingredients:
                            logger.info(f"[REFINEMENT] Adding allergy constraint: {excluded_ingredients}")

                        # Build comprehensive contextual query from conversation history
                        contextual_query = self._build_contextual_query_from_history(
                            session, excluded_ingredients, mentioned_tags
                        )

                        logger.info(f"[REFINEMENT] Using contextual query from history: {contextual_query}")

                        # Update the intent to recipe_search
                        nlid_result_dict["intent"] = "recipe_search"

                        # Ensure filters exist
                        if "filters" not in nlid_result_dict:
                            nlid_result_dict["filters"] = {}

                        # Add dietary tags if any
                        if mentioned_tags:
                            if "tags" not in nlid_result_dict["filters"]:
                                nlid_result_dict["filters"]["tags"] = []
                            nlid_result_dict["filters"]["tags"].extend(mentioned_tags)

                        # Add allergies to excluded_ingredients if any
                        if excluded_ingredients:
                            if "excluded_ingredients" not in nlid_result_dict["filters"]:
                                nlid_result_dict["filters"]["excluded_ingredients"] = []
                            # Deduplicate before extending to avoid duplicates
                            current_excluded = set(nlid_result_dict["filters"]["excluded_ingredients"])
                            new_excluded = [ing for ing in excluded_ingredients if ing not in current_excluded]
                            nlid_result_dict["filters"]["excluded_ingredients"].extend(new_excluded)

                            # Save allergies to session for future queries
                            for allergy in excluded_ingredients:
                                if allergy not in session.excluded_ingredients:
                                    session.excluded_ingredients.append(allergy)

                        # Create a retrieval plan that preserves the original embedding search but adds allergy filters
                        # The vector query should remain the original search (for orange recipes)
                        # But we need to add the allergy exclusion filter
                        original_vector_query = session_context.get("context_entities", {}).get("last_vector_query")
                        logger.info(f"[ALLERGY] Session context: {session_context.get('context_entities', {})}")
                        logger.info(f"[ALLERGY] Original vector query: {original_vector_query}")
                        logger.info(f"[ALLERGY] Current query: {query}")

                        # Use the original vector query if available, otherwise fallback to a sensible default
                        if not original_vector_query:
                            # If no previous vector query, extract the main ingredient from context or create a default
                            if session.included_ingredients:
                                original_vector_query = " ".join(session.included_ingredients) + " recipes"
                            else:
                                # Fallback to common ingredient patterns from the original search
                                original_vector_query = "recipes"  # Generic fallback

                        retrieval_plan = RetrievalPlan(
                            strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                            reasoning="Refinement search - preserving original search with allergy exclusion",
                            vector_query=original_vector_query,  # Keep original search (e.g., "orange recipes")
                            sql_filters={
                                "excluded_ingredients": excluded_ingredients
                                # Only include excluded ingredients for refinements, not all filters
                            },
                            top_k=20
                        )

                        # Debug: log retrieval plan contents
                        logger.info(f"[REFINEMENT] Created new retrieval plan with constraints")
                        logger.info(f"[REFINEMENT] Vector query: {retrieval_plan.vector_query}")
                        logger.info(f"[REFINEMENT] SQL filters: {dict(retrieval_plan.sql_filters)}")
                        logger.info(f"[REFINEMENT] SQL filters excluded_ingredients: {retrieval_plan.sql_filters.get('excluded_ingredients', [])}" if retrieval_plan.sql_filters else None)
                        logger.info(f"[REFINEMENT] Vector query: {retrieval_plan.vector_query}")
                        logger.info(f"[REFINEMENT] SQL filters: {retrieval_plan.sql_filters}")
                    else:
                        # Standalone preference - search for recipes with this preference
                        logger.info(f"[PREFERENCE] Standalone preference - searching with constraints")

                        # Save to session object's filters for future queries
                        # This persists across turns
                        for tag in mentioned_tags:
                            if tag not in session.filters.tags:
                                session.filters.tags.append(tag)

                        for allergy in excluded_ingredients:
                            if allergy not in session.excluded_ingredients:
                                session.excluded_ingredients.append(allergy)

                        # Rebuild session_context to include the updated filters
                        session_context = self.session_manager.get_user_context(session, {})
                        session_context["language"] = language or "en"

                        logger.info(f"[PREFERENCE] Saved dietary restrictions to session: {session.filters.tags}")
                        logger.info(f"[PREFERENCE] Saved allergies to session: {session.excluded_ingredients}")

                        # Convert to recipe_search with dietary tags/allergies
                        nlid_result_dict["intent"] = "recipe_search"

                        # Ensure filters exist
                        if "filters" not in nlid_result_dict:
                            nlid_result_dict["filters"] = {}

                        # Add dietary tags if any
                        if mentioned_tags:
                            if "tags" not in nlid_result_dict["filters"]:
                                nlid_result_dict["filters"]["tags"] = []
                            nlid_result_dict["filters"]["tags"].extend(mentioned_tags)

                        # Add allergies if any
                        if excluded_ingredients:
                            if "excluded_ingredients" not in nlid_result_dict["filters"]:
                                nlid_result_dict["filters"]["excluded_ingredients"] = []
                            nlid_result_dict["filters"]["excluded_ingredients"].extend(excluded_ingredients)

                        # Update the query to search for recipes with this preference
                        # Build contextual query with constraints
                        constraint_parts = []
                        if mentioned_tags:
                            constraint_parts.append(mentioned_tags[0])
                        if excluded_ingredients:
                            constraint_parts.append(f"without {excluded_ingredients[0]}")

                        if constraint_parts:
                            dietary_query = f"{' '.join(constraint_parts)} recipes"
                        else:
                            dietary_query = "recipes"

                        if len(mentioned_tags) > 1 or len(excluded_ingredients) > 1:
                            all_constraints = mentioned_tags + [f"without {a}" for a in excluded_ingredients]
                            dietary_query = f"{' '.join(all_constraints)} recipes"

                        # Create a retrieval plan for standalone preferences
                        # Only include the preference filters, not all NLID filters
                        logger.info(f"[PREFERENCE] Creating retrieval plan for standalone preferences")
                        retrieval_plan = RetrievalPlan(
                            strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                            reasoning="Standalone preference search with dietary constraints",
                            vector_query=dietary_query,
                            sql_filters={
                                "tags": mentioned_tags,
                                "excluded_ingredients": excluded_ingredients
                            },
                            top_k=20
                        )

                        # Continue with normal recipe search flow
                        logger.info(f"[PREFERENCE] Will search with constraints")

            # Finalize the retrieval_plan to use for the rest of the pipeline
            # If the refinement path created a new plan, use it; otherwise use the original plan
            # This ensures we don't lose the refinement plan created above
            if 'retrieval_plan' in locals() and isinstance(locals()['retrieval_plan'], RetrievalPlan):
                # refinement_path_created_plan is a local variable from the if block above
                pass  # Keep the plan created in the refinement/standalone logic
            else:
                # No plan created yet, will use the original retrieval_plan from strategy_decider
                pass

            # ============ STAGE 4+5+6: Embedding Search + Schema + SQL Generation ============
            # For HYBRID_VECTOR_TO_SQL: Wait for embedding search first, then generate SQL with candidate_ids
            # For SQL_ONLY (filter-only queries): Skip embedding search entirely, run schema + SQL only
            # For other strategies: Can run in parallel
            parallel2_start = time.time()

            # Initialize variables for all paths
            candidate_ids = None
            similarity_scores = {}
            relevant_schema = None
            sql_result = None

            if retrieval_plan.strategy == RetrievalStrategy.SQL_ONLY:
                # FILTER-ONLY queries: Skip embedding search, run schema + SQL directly
                logger.info(f"[FILTER-ONLY] Starting Schema + SQL generation (no embeddings needed)...")

                # Schema fetch (cached, very fast)
                relevant_schema = self.schema_understanding.get_relevant_schema(
                    nlid_result_dict["intent"],
                    retrieval_plan.sql_filters,
                    session_context
                )

                # SQL generation for filter-only queries
                logger.info(f"[FILTER-ONLY] Generating SQL for direct filtering...")
                logger.info(f"[FILTER-ONLY] Passing sql_filters to generator: {retrieval_plan.sql_filters}")
                sql_result = self.sql_generator.generate_sql(
                    query,
                    nlid_result_dict,
                    retrieval_plan.sql_filters,
                    session_context,
                    None  # No candidate_ids for filter-only queries
                )
                logger.info(f"[FILTER-ONLY] ✓ Schema + SQL completed in {time.time() - parallel2_start:.3f}s")

            elif retrieval_plan.strategy == RetrievalStrategy.HYBRID_VECTOR_TO_SQL:
                # SEQUENTIAL for hybrid: Embedding first, then SQL with candidate_ids
                logger.info(f"[HYBRID] Starting Embedding Search first (sequential)...")

                # Step 1: Run embedding search
                embedding_limit = max(retrieval_plan.top_k, 10)
                embedding_results = search_recipes_by_embedding(
                    self.db,
                    query_text=retrieval_plan.vector_query or query,
                    limit=embedding_limit,
                    threshold=0.4,
                    language_id=language
                )
                candidate_ids = [str(r.id) for r, _ in embedding_results]
                similarity_scores = {str(r.id): s for r, s in embedding_results}
                logger.info(f"[HYBRID] ✓ Embedding search: {len(embedding_results)} candidates")

                # Step 2: Schema fetch (cached, fast)
                relevant_schema = self.schema_understanding.get_relevant_schema(
                    nlid_result_dict["intent"],
                    retrieval_plan.sql_filters,
                    session_context
                )

                # Step 3: SQL generation WITH candidate_ids (filters the embedding candidates)
                logger.info(f"[HYBRID] Generating SQL to filter {len(candidate_ids)} embedding candidates...")
                logger.info(f"[HYBRID] Passing sql_filters to generator: {retrieval_plan.sql_filters}")
                sql_result = self.sql_generator.generate_sql(
                    query,
                    nlid_result_dict,
                    retrieval_plan.sql_filters,
                    session_context,
                    candidate_ids  # Pass candidate_ids so SQL only searches within them
                )
                logger.info(f"[HYBRID] ✓ Embedding + SQL completed in {time.time() - parallel2_start:.3f}s")

            else:
                # PARALLEL for non-hybrid strategies
                logger.info(f"[PARALLEL PHASE 2] Starting Embedding Search + Schema + SQL Generation...")

                async def run_embedding_search():
                    """Run embedding search if needed"""
                    if retrieval_plan.strategy not in ["embeddings_only", "hybrid_vector_to_sql"]:
                        return None, {}

                    embedding_limit = max(retrieval_plan.top_k, 10)
                    logger.info(f"[PARALLEL] Starting embedding search...")
                    embedding_results = search_recipes_by_embedding(
                        self.db,
                        query_text=retrieval_plan.vector_query or query,
                        limit=embedding_limit,
                        threshold=0.4,
                        language_id=language
                    )
                    cand_ids = [str(r.id) for r, _ in embedding_results]
                    sim_scores = {str(r.id): s for r, s in embedding_results}
                    logger.info(f"[PARALLEL] ✓ Embedding search: {len(embedding_results)} candidates")
                    return cand_ids, sim_scores

                async def run_schema_and_sql():
                    """Run schema fetch and SQL generation in parallel"""
                    logger.info(f"[PARALLEL] Starting schema + SQL generation...")

                    # Schema fetch (cached, very fast)
                    rel_schema = self.schema_understanding.get_relevant_schema(
                        nlid_result_dict["intent"],
                        retrieval_plan.sql_filters,
                        session_context
                    )

                    # SQL generation (expensive, ~3s)
                    # For non-hybrid queries, pass None for candidate_ids
                    gen_sql_result = self.sql_generator.generate_sql(
                        query,
                        nlid_result_dict,
                        retrieval_plan.sql_filters,
                        session_context,
                        None
                    )

                    logger.info(f"[PARALLEL] ✓ Schema + SQL generated")
                    return rel_schema, gen_sql_result

                # Run embedding search and schema+SQL in parallel
                results = await asyncio.gather(
                    run_embedding_search(),
                    run_schema_and_sql(),
                    return_exceptions=True
                )

                # Unpack results
                embedding_result = results[0]
                schema_sql_result = results[1]

                # Handle embedding results
                if embedding_result and not isinstance(embedding_result, Exception):
                    candidate_ids, similarity_scores = embedding_result
                    logger.info(f"[STAGE 4] ✓ Found {len(candidate_ids)} candidates via embeddings (parallel)")
                else:
                    candidate_ids = None
                    similarity_scores = {}
                    logger.info(f"[STAGE 4] No embedding search needed for this strategy")

                # Handle schema + SQL results
                if not isinstance(schema_sql_result, Exception):
                    relevant_schema, sql_result = schema_sql_result
                    logger.info(f"[STAGE 5+6] ✓ Schema + SQL generated in parallel in {time.time() - parallel2_start:.3f}s")
                else:
                    logger.error(f"[STAGE 5+6] Error in parallel execution: {schema_sql_result}")
                    raise schema_sql_result

            logger.info(f"[STAGE 5]   - Tables: {[t.name for t in relevant_schema.tables[:5]]}...")
            logger.info(f"[STAGE 6]   - SQL Safe: {sql_result.is_safe}")
            logger.info(f"[STAGE 6]   - Estimated Rows: {sql_result.estimated_rows}")

            # Log strategy-specific information
            if retrieval_plan.strategy == RetrievalStrategy.SQL_ONLY:
                logger.info(f"[STAGE 6]   - Strategy: Filter-only query (skipped embeddings)")
                logger.info(f"[STAGE 6]   - Filters applied: {list(retrieval_plan.sql_filters.keys())}")
            else:
                logger.info(f"[STAGE 6]   - Strategy: Standard search with embeddings")

            # ============ STAGE 7: SQL Validation ============
            logger.info(f"[STAGE 7]   - Validation Status: {'PASSED' if sql_result.is_safe else 'FAILED'}")

            # ============ STAGE 8: SQL Execution ============
            stage_start = time.time()
            logger.info(f"[STAGE 8] Executing SQL query...")
            logger.info(f"[STAGE 8] SQL Query:\n{sql_result.sql}")

            # Prepare parameters for SQL execution
            params = {
                "user_uid": user_uid,
                "language_id": language or "en"
            }

            execution_result = self.sql_executor.execute_sql(sql_result.sql, params)

            logger.info(f"[STAGE 8] ✓ Execution completed in {time.time() - stage_start:.3f}s")
            logger.info(f"[STAGE 8]   - Success: {execution_result['success']}")
            logger.info(f"[STAGE 8]   - Rows Returned: {execution_result.get('row_count', 0)}")

            if not execution_result["success"]:
                # Error response - no fallback
                logger.error(f"[STAGE 8] SQL execution failed, generating error response")
                logger.error(f"[STAGE 8]   - Error: {execution_result.get('error', 'Unknown')}")
                error_response = await self._generate_error_response(query, execution_result)
                session.add_to_history("assistant", error_response)
                self.session_manager.save_session(session)

                return {
                    "response": error_response,
                    "metadata": {
                        "error": execution_result.get("error"),
                        "retrieval_strategy": retrieval_plan.strategy,
                        "num_results": 0,
                    }
                }

            # Update session context with successful search for future refinements
            # This enables follow-up queries like "I am allergic to tomato" to maintain context
            if execution_result.get("row_count", 0) > 0:
                self.session_manager.update_search_context(
                    session,
                    query,
                    retrieval_plan.vector_query,
                    retrieval_plan.sql_filters,
                    nlid_result_dict["intent"]
                )

            # ============ STAGE 9+10: PARALLEL PHASE 3 - Post-Processing + NLG ============
            # Run post-processing and NLG in parallel
            # Post-processing is fast (~0.5s), NLG is slow (~4s)
            # We start NLG with basic SQL results while post-processing adds details
            parallel3_start = time.time()
            logger.info(f"[PARALLEL PHASE 3] Starting Post-Processing + NLG...")

            async def run_post_processing():
                """Run post-processing and ranking"""
                processed_recipes = self._post_process_and_rank(
                    execution_result["rows"],
                    similarity_scores,
                    session_context,
                    retrieval_plan
                )
                final_recipes = processed_recipes[:self.MAX_RECIPES]
                logger.info(f"[PARALLEL] ✓ Post-processing: {len(final_recipes)} recipes")
                return final_recipes

            async def run_nlg():
                """Run NLG with the SQL results directly"""
                # Use SDK NLG agent for natural language responses
                # We pass the SQL results directly; NLG will format them
                response = await self._generate_natural_language_response(
                    query,
                    execution_result["rows"][:self.MAX_RECIPES],  # Use SQL results directly
                    nlid_result_dict
                )

                return response

            # Run post-processing and NLG in parallel
            results = await asyncio.gather(
                run_post_processing(),
                run_nlg(),
                return_exceptions=True
            )

            # Unpack results
            post_process_result = results[0]
            nlg_result = results[1]

            # Handle results
            if not isinstance(post_process_result, Exception):
                final_recipes = post_process_result
            else:
                logger.error(f"[STAGE 9] Error: {post_process_result}")
                final_recipes = execution_result["rows"][:self.MAX_RECIPES]

            if not isinstance(nlg_result, Exception):
                response = nlg_result
            else:
                logger.error(f"[STAGE 10] Error: {nlg_result}")
                response = "I found some recipes for you, but encountered an error generating the response."

            logger.info(f"[PARALLEL PHASE 3] ✓ Post-Processing + NLG completed in {time.time() - parallel3_start:.3f}s")

            # Save to session
            session.add_to_history("assistant", response)
            self.session_manager.save_session(session)

            # Prepare metadata with full recipe details
            metadata = {
                "intent": nlid_result_dict["intent"],
                "is_cooking_related": True,
                "retrieval_strategy": retrieval_plan.strategy.value if retrieval_plan else "unknown",
                "num_results": len(final_recipes),
                "pipeline_duration_ms": round((time.time() - pipeline_start_time) * 1000, 2),
                "recipes": [
                    {
                        "id": str(r["id"]) if r.get("id") else None,
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
                        "access_level": r.get("access_level", "full"),
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

            # Prepare pipeline metadata for conversation storage
            # This ensures search context is persisted across requests
            pipeline_metadata = {
                "vector_query": getattr(retrieval_plan, 'vector_query', None),
                "intent": nlid_result_dict.get("intent"),
                "filters": getattr(retrieval_plan, 'sql_filters', {})
            }

            # Update session with the last search query for future refinements
            # For filter-only queries, create a descriptive vector query from the filters
            if nlid_result_dict.get("intent") == "recipe_search":
                # For filter-only queries (nutrition/price), create a descriptive query from filters
                if retrieval_plan.strategy == RetrievalStrategy.SQL_ONLY:
                    filter_parts = []
                    if retrieval_plan.sql_filters.get("nutrition_filters"):
                        nutrition = retrieval_plan.sql_filters["nutrition_filters"]
                        if "protein" in nutrition:
                            filter_parts.append("high protein")
                        if "carbs" in nutrition:
                            filter_parts.append("low carb" if nutrition["carbs"] == "low" else "high carb")
                        if "calories" in nutrition:
                            filter_parts.append("low calorie" if nutrition["calories"] == "low" else "high calorie")

                    if retrieval_plan.sql_filters.get("price_filters"):
                        price = retrieval_plan.sql_filters["price_filters"]
                        if "max_price" in price:
                            filter_parts.append(f"under {price['max_price']}")

                    if filter_parts:
                        descriptive_query = " ".join(filter_parts) + " recipes"
                    else:
                        descriptive_query = "filtered recipes"

                    # Use the descriptive query for session context
                    vector_query_for_session = descriptive_query
                else:
                    # Use the original vector query for non-filter queries
                    vector_query_for_session = retrieval_plan.vector_query

                # Check if this is a refinement or a new search
                context_entities = session_context.get("context_entities", {})
                last_vector_query = context_entities.get("last_vector_query")

                # Debug information - moved above to avoid duplication

            return {
                "response": response,
                "metadata": metadata,
                "pipeline_metadata": pipeline_metadata  # For conversation context persistence
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
                "metadata": {"error": str(e), "pipeline_duration_ms": round(error_time * 1000, 2)},
                "pipeline_metadata": {}
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
        session_context: Dict[str, Any],
        retrieval_plan: Optional[RetrievalPlan] = None
    ) -> List[Dict[str, Any]]:
        """
        OPTIMIZED: Post-process and rank recipes using batch queries

        Performance improvements:
        - Batch load all recipe objects in ONE query (not N queries)
        - Batch load all bundle info in ONE query (not N queries)
        - Batch load all purchase info in ONE query (not N queries)
        - Batch load all ingredients in ONE query (not N queries)
        - Batch load all instructions in ONE query (not N queries)
        - Fetch user context ONCE (not N times)

        Reduces database queries from 40-50 to just 5-6 total!
        """
        from sqlalchemy import text
        from models import RecipeIngredient, Ingredient, RecipeInstruction

        processed = []
        user_uid = session_context.get("user_uid")
        seen_recipe_ids = set()  # Track seen recipe IDs to avoid duplicates

        # =====================================================
        # BATCH LOAD: Extract unique recipe IDs
        # =====================================================
        unique_recipe_ids = list(set(row.get("id") for row in rows if row.get("id")))
        if not unique_recipe_ids:
            return []

        logger.info(f"[STAGE 9] Batch loading {len(unique_recipe_ids)} unique recipes...")

        # =====================================================
        # BATCH QUERY 1: Load all recipe objects at once
        # =====================================================
        recipes_map = {
            str(r.id): r
            for r in self.db.query(Recipe).filter(Recipe.id.in_(unique_recipe_ids)).all()
        }

        # =====================================================
        # BATCH QUERY 2: Load all bundle info at once
        # =====================================================
        # A recipe can be in multiple bundles - we need to track ALL of them
        # to determine if it's free in ANY bundle (free trumps paid)
        # bundle.userUid indicates the user who purchased/owns the bundle
        bundle_info_map = {}  # recipe_id -> list of bundle entries
        if unique_recipe_ids:
            bundle_results = self.db.execute(text("""
                SELECT br."recipeId", br."bundleId", b.name as bundle_name, br."isFree", b."userUid" as bundle_owner
                FROM bundle_recipe br
                JOIN bundle b ON br."bundleId" = b.id
                WHERE br."recipeId" = ANY(:recipe_ids)
                ORDER BY br."recipeId", br."isFree" DESC  -- Free bundles first
            """), {"recipe_ids": unique_recipe_ids}).fetchall()

            for br in bundle_results:
                recipe_id = str(br[0])
                if recipe_id not in bundle_info_map:
                    bundle_info_map[recipe_id] = []
                bundle_info_map[recipe_id].append({
                    "bundle_id": str(br[1]),
                    "bundle_name": br[2],
                    "is_free": br[3],
                    "bundle_owner": br[4]  # User who purchased/owns this bundle
                })

        # =====================================================
        # BATCH QUERY 4: Load all ingredients at once
        # =====================================================
        ingredients_map = {}
        if unique_recipe_ids:
            ingredient_results = self.db.execute(text("""
                SELECT
                    ri."recipeId", ri.amount, ri."unitId", ri.order as ri_order,
                    i.id as ing_id, i.name as ing_name,
                    mut.name as unit_name
                FROM recipe_ingredient ri
                JOIN ingredient i ON ri."ingredientId" = i.id
                LEFT JOIN measuring_unit_translation mut ON ri."unitId" = mut."measuringUnitId" AND mut."languageId" = 'en'
                WHERE ri."recipeId" = ANY(:recipe_ids)
                AND ri."deletedAt" IS NULL
                ORDER BY ri."recipeId", ri.order
            """), {"recipe_ids": unique_recipe_ids}).fetchall()

            for ir in ingredient_results:
                recipe_id = str(ir[0])
                if recipe_id not in ingredients_map:
                    ingredients_map[recipe_id] = []
                ingredients_map[recipe_id].append({
                    "name": ir[5],
                    "amount": ir[1],
                    "unit": ir[6],  # unit name from translation table
                    "unit_id": str(ir[2]) if ir[2] else None
                })

        # =====================================================
        # BATCH QUERY 5: Load all instructions at once
        # =====================================================
        instructions_map = {}
        if unique_recipe_ids:
            instruction_results = self.db.execute(text("""
                SELECT "recipeId", "order", description, image
                FROM recipe_instruction
                WHERE "recipeId" = ANY(:recipe_ids)
                AND "deletedAt" IS NULL
                ORDER BY "recipeId", "order"
            """), {"recipe_ids": unique_recipe_ids}).fetchall()

            for instr in instruction_results:
                recipe_id = str(instr[0])
                if recipe_id not in instructions_map:
                    instructions_map[recipe_id] = []
                instructions_map[recipe_id].append({
                    "order": instr[1],
                    "description": instr[2],
                    "image": instr[3]
                })

        # =====================================================
        # FETCH USER CONTEXT ONCE (not in loop!)
        # =====================================================
        user_ctx = None
        if user_uid:
            user_ctx = self.user_context_service.get_user_context(user_uid)

        total_bundle_entries = sum(len(entries) for entries in bundle_info_map.values())
        logger.info(f"[STAGE 9] Batch queries completed ({len(recipes_map)} recipes, {len(bundle_info_map)} recipes in bundles, {total_bundle_entries} total bundle entries)")

        # =====================================================
        # PROCESS RECIPES (in-memory, no more queries!)
        # =====================================================
        for row in rows:
            recipe_id = str(row.get("id"))  # Convert UUID to string for lookup

            # Skip duplicates (SQL JOINs can produce multiple rows for same recipe)
            if recipe_id in seen_recipe_ids:
                continue
            seen_recipe_ids.add(recipe_id)

            # Get recipe from pre-loaded map
            recipe = recipes_map.get(recipe_id)
            if not recipe:
                logger.warning(f"[STAGE 9]   - Recipe {recipe_id} not found in pre-loaded map")
                continue

            # =====================================================
            # ACCESS CONTROL: Always exclude private recipes
            # =====================================================
            if recipe.private:
                if user_uid and str(recipe.userUid) == user_uid:
                    pass  # User created this recipe, allow access
                else:
                    logger.info(f"[STAGE 9]   - Excluding private recipe: {recipe_id}")
                    continue

            # Exclude deleted recipes
            if recipe.deletedAt:
                logger.info(f"[STAGE 9]   - Excluding deleted recipe: {recipe_id}")
                continue

            # Get bundle info from pre-loaded map
            # A recipe can be in multiple bundles - check ALL of them
            bundle_entries = bundle_info_map.get(recipe_id, [])
            is_bundle_recipe = len(bundle_entries) > 0

            # Check if recipe is free in ANY bundle (free trumps paid)
            is_bundle_free_recipe = any(b["is_free"] for b in bundle_entries)

            # For display purposes, show the first bundle name (or "free" bundle name if available)
            bundle_name = None
            if bundle_entries:
                # Try to get a free bundle name first, otherwise get the first bundle
                free_bundle = next((b for b in bundle_entries if b["is_free"]), None)
                bundle_to_show = free_bundle if free_bundle else bundle_entries[0]
                bundle_name = bundle_to_show["bundle_name"]

            # =====================================================
            # BUNDLE ACCESS CONTROL: Check access level
            # =====================================================
            # Rules:
            # 1. If isFree=true in ANY bundle → Show full details (free recipe)
            # 2. If userUid in bundle matches current user → Show full details (user owns bundle)
            # 3. Otherwise → Show name only
            # =====================================================
            # Note: bundle.userUid indicates the user who purchased/owns the bundle
            should_show_name_only = False

            if is_bundle_recipe and not is_bundle_free_recipe:
                # Recipe is NOT free in any bundle - check if user owns ANY bundle with this recipe
                user_owns_any_bundle = user_uid and any(
                    b["bundle_owner"] == user_uid
                    for b in bundle_entries
                )

                if user_owns_any_bundle:
                    # User owns/purchased the bundle - full access
                    logger.info(f"[STAGE 9]   - Bundle recipe (full access - bundle owner): {recipe_id} in '{bundle_name}'")
                else:
                    # User doesn't own any bundle with this recipe - show name only
                    should_show_name_only = True
                    logger.info(f"[STAGE 9]   - Bundle recipe (name-only): {recipe_id} in '{bundle_name}' (not owner)")
            elif is_bundle_recipe and is_bundle_free_recipe:
                # Recipe is free in at least one bundle - show full details
                logger.info(f"[STAGE 9]   - Bundle recipe (full access - free): {recipe_id} in '{bundle_name}'")

            if should_show_name_only:
                processed.append({
                    "id": recipe_id,
                    "name": row.get("name") or recipe.name,
                    "image": row.get("image") or recipe.image,
                    "access_level": "name_only",
                    "is_bundle_recipe": True,
                    "is_bundle_free_recipe": False,
                    "bundle_name": bundle_name,
                    "ingress": None,
                    "description": None,
                    "difficulty": None,
                    "total_time": None,
                    "prep_time": None,
                    "cook_time": None,
                    "servings": None,
                    "ingredients": [],
                    "instructions": [],
                    "similarity": similarity_scores.get(recipe_id, 0.7),
                    "priority_score": 0,
                    "is_liked": False,
                    "is_created": False,
                })
                continue

            # Determine access level for full-access recipes
            access_level = "full"
            if user_ctx:
                if recipe_id in user_ctx.get("created_recipe_ids", set()):
                    access_level = "full"
                elif recipe_id in user_ctx.get("purchased_recipe_ids", set()):
                    access_level = "full"
                elif is_bundle_recipe and not is_bundle_free_recipe:
                    # This shouldn't happen due to above check, but keeping for safety
                    access_level = "name_only"

            # Calculate priority score
            priority_score = 0
            is_created = False
            is_liked = False

            if user_ctx:
                liked_ids = user_ctx.get("liked_recipe_ids", set())
                created_ids = user_ctx.get("created_recipe_ids", set())

                if recipe_id in liked_ids:
                    priority_score = 100
                    is_liked = True
                if recipe_id in created_ids:
                    is_created = True
                    if priority_score == 0:
                        priority_score = 50

            # Get similarity score (default to 0.7 for SQL_ONLY queries where no embeddings were used)
            if retrieval_plan and retrieval_plan.strategy == RetrievalStrategy.SQL_ONLY:
                similarity = similarity_scores.get(recipe_id, 0.5)
            else:
                similarity = similarity_scores.get(recipe_id, 0.7)

            # Get ingredients from pre-loaded map
            ingredient_list = ingredients_map.get(recipe_id, [])

            # Get instructions from pre-loaded map
            instruction_list = instructions_map.get(recipe_id, [])

            processed.append({
                "id": recipe_id,
                "name": row.get("name") or recipe.name,
                "ingress": row.get("ingress") or recipe.ingress,
                "description": recipe.ingress,
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

    def _build_contextual_query_from_history(
        self,
        session: SessionState,
        excluded_ingredients: List[str] = None,
        dietary_tags: List[str] = None
    ) -> str:
        """
        Build a comprehensive contextual query from the conversation history and previous search context.

        Prioritizes previous search context if available, otherwise analyzes conversation history.
        - Previous search context includes last vector query, ingredients, filters
        - Conversation history provides additional context and refinements

        Args:
            session: The current session state
            excluded_ingredients: New allergies to add (from current query)
            dietary_tags: New dietary preferences to add (from current query)

        Returns:
            A comprehensive contextual query string
        """
        import re
        from collections import Counter

        # Check if we have previous search context to use
        if session.context_entities.last_vector_query:
            # Build query from previous search context + new constraints
            contextual_parts = []

            # Add main ingredient(s) from previous search
            if session.included_ingredients:
                contextual_parts.extend(session.included_ingredients)
            else:
                # Try to extract ingredient from last vector query
                last_query = session.context_entities.last_vector_query.lower()
                common_ingredients = ['pasta', 'rice', 'chicken', 'beef', 'fish', 'potato', 'tomato', 'onion', 'chole', 'chickpeas']
                for ingredient in common_ingredients:
                    if ingredient in last_query:
                        contextual_parts.append(ingredient)
                        break

            if contextual_parts:
                contextual_query = f"{' '.join(contextual_parts)} recipes"
            else:
                contextual_query = "recipes"

            # CRITICAL: Do NOT add allergies to the vector query
            # Allergies should only be applied as SQL filters (excluded_ingredients)
            # The embedding search should find semantically similar recipes based on the original query
            # Then SQL will filter out recipes containing the allergens
            # This prevents the issue where "garlic" gets embedded and finds garlic-containing recipes

            logger.info(f"[CONTEXTUAL QUERY] Built from previous search context: {contextual_query}")
            if excluded_ingredients:
                logger.info(f"[CONTEXTUAL QUERY] Excluded ingredients (allergies) will be applied as SQL filters, NOT in embedding query: {excluded_ingredients}")
            return contextual_query

        # Fallback: Build from conversation history
        history = session.get_context_window(limit=10)

        # Extract context from history
        mentioned_ingredients = []
        mentioned_allergies = set(session.excluded_ingredients)
        mentioned_dietary = set(session.filters.tags)
        time_constraints = []
        difficulty_preferences = []
        meal_types = []
        cuisines = []

        # Patterns to extract from messages
        time_patterns = {
            r'\bquick\b': 'quick',
            r'\bfast\b': 'quick',
            r'\brapid\b': 'quick',
            r'\bunder\s+(\d+)\s*min': lambda m: f"under {m.group(1)} min",
            r'\blesst\s+than\s+(\d+)\s*min': lambda m: f"less than {m.group(1)} min",
            r'\b(\d+)\s*minute': lambda m: f"{m.group(1)} minute",
        }

        difficulty_patterns = {
            r'\beasy\b': 'easy',
            r'\bsimple\b': 'easy',
            r'\bmedium\b': 'medium',
            r'\bhard\b': 'hard',
            r'\bdifficult\b': 'hard',
            r'\bcomplex\b': 'hard',
        }

        meal_type_patterns = {
            r'\bbreakfast\b': 'breakfast',
            r'\blunch\b': 'lunch',
            r'\bdinner\b': 'dinner',
            r'\bsupper\b': 'dinner',
            r'\bdessert\b': 'dessert',
            r'\bsnack\b': 'snack',
            r'\bappetizer\b': 'appetizer',
            r'\bmain course\b': 'main course',
        }

        dietary_preference_patterns = {
            r'\bvegetarian\b': 'vegetarian',
            r'\bvegan\b': 'vegan',
            r'\bgluten.?free\b': 'gluten-free',
            r'\bdairy.?free\b': 'dairy-free',
            r'\bketo\b': 'keto',
        }

        # Parse conversation history
        for msg in history:
            if msg.get("role") == "user":
                content = msg.get("content", "").lower()

                # Extract ingredients (common cooking patterns)
                # Patterns like "carrot recipes", "make from carrot", "with chicken"
                ingredient_match = re.search(r'(?:recipe|make|cook|with|from|using|for)\s+([a-z]+(?:\s+[a-z]+)?)', content)
                if ingredient_match:
                    potential_ingredient = ingredient_match.group(1).strip()
                    # Filter out non-ingredient words
                    non_ingredient_words = {'recipe', 'recipes', 'make', 'cook', 'with', 'from', 'using', 'for', 'something', 'anything', 'dish', 'meal', 'lunch', 'dinner', 'breakfast'}
                    if potential_ingredient and potential_ingredient not in non_ingredient_words:
                        mentioned_ingredients.append(potential_ingredient)

                # Check for "What can I make from X" pattern
                make_from_match = re.search(r'what can i make from\s+([a-z]+(?:\s+[a-z]+)*)', content)
                if make_from_match:
                    mentioned_ingredients.append(make_from_match.group(1).strip())

                # Extract time constraints
                for pattern, replacement in time_patterns.items():
                    match = re.search(pattern, content)
                    if match:
                        if callable(replacement):
                            time_constraints.append(replacement(match))
                        else:
                            time_constraints.append(replacement)

                # Extract difficulty preferences
                for pattern, difficulty in difficulty_patterns.items():
                    if re.search(pattern, content):
                        difficulty_preferences.append(difficulty)

                # Extract meal types
                for pattern, meal_type in meal_type_patterns.items():
                    if re.search(pattern, content):
                        meal_types.append(meal_type)

                # Extract dietary preferences
                for pattern, dietary in dietary_preference_patterns.items():
                    if re.search(pattern, content):
                        mentioned_dietary.add(dietary)

        # Add new allergies from current query
        if excluded_ingredients:
            mentioned_allergies.update(excluded_ingredients)

        # Add new dietary tags from current query
        if dietary_tags:
            mentioned_dietary.update(dietary_tags)

        # Build contextual query
        query_parts = []

        # Add dietary preferences
        if mentioned_dietary:
            query_parts.extend(list(mentioned_dietary))

        # Add meal type
        if meal_types:
            # Use the most recently mentioned meal type
            query_parts.append(meal_types[-1])

        # Add main ingredient (most frequently mentioned)
        if mentioned_ingredients:
            # Count ingredient frequency
            ingredient_counter = Counter(mentioned_ingredients)
            main_ingredient = ingredient_counter.most_common(1)[0][0]
            query_parts.append(main_ingredient)

        # Add time constraint
        if time_constraints:
            query_parts.append(time_constraints[-1])

        # Add difficulty preference
        if difficulty_preferences:
            query_parts.append(difficulty_preferences[-1])

        # Add "recipes" at the end
        query_parts.append("recipes")

        # Build final query
        if query_parts:
            contextual_query = " ".join(query_parts)
        else:
            contextual_query = "recipes"

        # Add allergies at the end
        if mentioned_allergies:
            allergy_list = list(mentioned_allergies)
            contextual_query += f" without {allergy_list[0]}"
            if len(allergy_list) > 1:
                contextual_query += f" or {' or '.join(allergy_list[1:])}"

        logger.info(f"[CONTEXTUAL QUERY] Built from history: {contextual_query}")
        logger.info(f"[CONTEXTUAL QUERY] Ingredients: {mentioned_ingredients}")
        logger.info(f"[CONTEXTUAL QUERY] Allergies: {list(mentioned_allergies)}")
        logger.info(f"[CONTEXTUAL QUERY] Dietary: {list(mentioned_dietary)}")
        logger.info(f"[CONTEXTUAL QUERY] Time: {time_constraints}")
        logger.info(f"[CONTEXTUAL QUERY] Difficulty: {difficulty_preferences}")
        logger.info(f"[CONTEXTUAL QUERY] Meal types: {meal_types}")

        return contextual_query

    async def _handle_cost_nutrition_filter_query(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle price_filter and nutrition_filter intents with DIRECT SQL queries.

        These queries bypass embedding search entirely and query recipe_metadata directly.
        This provides accurate filtering by cost and nutrition values stored in the database.

        Args:
            query: User's query text
            nlid_result: NLID detection result
            session: Session state
            user_uid: User identifier
            language: Language code

        Returns:
            Response dictionary with recipes and metadata
        """
        from sqlalchemy import text

        pipeline_start_time = time.time()
        intent = nlid_result.get("intent", "")
        filters = nlid_result.get("filters", {})

        logger.info(f"[COST/NUTRITION FILTER] Processing intent: {intent}")
        logger.info(f"[COST/NUTRITION FILTER] Filters from NLID: {filters}")

        # Extract cost and nutrition filters
        cost_filter = filters.get("cost")
        nutrition_filter = filters.get("nutrition")

        # Fallback: try to extract from query if NLID didn't provide them
        if not cost_filter and intent == "price_filter":
            cost_filter = extract_cost_filter(query)
            logger.info(f"[COST/NUTRITION FILTER] Extracted cost filter from query: {cost_filter}")

        if not nutrition_filter and intent == "nutrition_filter":
            nutrition_filter = extract_nutrition_filter(query)
            logger.info(f"[COST/NUTRITION FILTER] Extracted nutrition filter from query: {nutrition_filter}")

        # Get session context for additional filters (dietary restrictions, allergies, etc.)
        session_context = self.session_manager.get_user_context(session, {})
        session_filters = session_context.get("filters", {})

        # Merge with NLID filters
        merged_filters = {**session_filters, **filters}

        # Build additional SQL conditions from session context
        additional_conditions = build_session_filter_conditions(merged_filters)

        # Build the SQL query
        sql_query = None

        if cost_filter and nutrition_filter:
            # Combined cost + nutrition filter
            logger.info(f"[COST/NUTRITION FILTER] Building combined cost + nutrition SQL")
            sql_query = build_recipe_combined_filter_sql(
                cost_filter=cost_filter,
                nutrition_filter=nutrition_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=20
            )
        elif cost_filter:
            # Cost-only filter
            logger.info(f"[COST/NUTRITION FILTER] Building cost filter SQL: {cost_filter}")
            sql_query = build_recipe_cost_filter_sql(
                cost_filter=cost_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=20
            )
        elif nutrition_filter:
            # Nutrition-only filter
            logger.info(f"[COST/NUTRITION FILTER] Building nutrition filter SQL: {nutrition_filter}")
            sql_query = build_recipe_nutrition_filter_sql(
                nutrition_filter=nutrition_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=20
            )
        else:
            # No valid filters found - fall back to error message
            logger.warning(f"[COST/NUTRITION FILTER] No valid filters found, returning guidance")
            error_response = "I couldn't understand the cost or nutrition filter you're looking for. Try queries like 'recipes under $20' or 'high protein meals'."
            session.add_to_history("assistant", error_response)
            self.session_manager.save_session(session)
            return {
                "response": error_response,
                "metadata": {
                    "intent": intent,
                    "is_cooking_related": True,
                    "retrieval_strategy": "direct_sql",
                    "num_results": 0,
                    "error": "No valid filters"
                }
            }

        logger.info(f"[COST/NUTRITION FILTER] Generated SQL:\n{sql_query}")

        # Execute the SQL query
        try:
            result = self.db.execute(text(sql_query))
            rows = [dict(row._mapping) for row in result.fetchall()]
            logger.info(f"[COST/NUTRITION FILTER] ✓ SQL executed, {len(rows)} results")
        except Exception as e:
            logger.error(f"[COST/NUTRITION FILTER] SQL execution error: {e}")
            error_response = "I encountered an error while searching for recipes. Please try again."
            session.add_to_history("assistant", error_response)
            self.session_manager.save_session(session)
            return {
                "response": error_response,
                "metadata": {
                    "intent": intent,
                    "is_cooking_related": True,
                    "retrieval_strategy": "direct_sql",
                    "num_results": 0,
                    "error": str(e)
                }
            }

        # Post-process the results
        processed_recipes = await self._post_process_cost_nutrition_results(
            rows,
            user_uid,
            cost_filter,
            nutrition_filter
        )

        # Limit to MAX_RECIPES
        final_recipes = processed_recipes[:self.MAX_RECIPES]

        # Generate natural language response
        if final_recipes:
            response = await self._generate_cost_nutrition_response(
                query, final_recipes, intent, cost_filter, nutrition_filter
            )
        else:
            response = await self._generate_no_results_response(query, nlid_result)

        # Save to session
        session.add_to_history("assistant", response)
        self.session_manager.save_session(session)

        # Build metadata
        metadata = {
            "intent": intent,
            "is_cooking_related": True,
            "retrieval_strategy": "direct_sql",
            "num_results": len(final_recipes),
            "pipeline_duration_ms": round((time.time() - pipeline_start_time) * 1000, 2),
            "filters_applied": {
                "cost": cost_filter,
                "nutrition": nutrition_filter
            },
            "recipes": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "ingress": r.get("ingress"),
                    "image": r.get("image"),
                    "total_time": r.get("total_time"),
                    "difficulty": r.get("difficulty"),
                    "servings": r.get("servings"),
                    "cost": r.get("cost"),
                    "nutrition_highlight": r.get("nutrition_highlight"),
                    "access_level": r.get("access_level", "full"),
                    "ingredients": r.get("ingredients", []),
                    "instructions": r.get("instructions", []),
                }
                for r in final_recipes
            ]
        }

        logger.info(f"[COST/NUTRITION FILTER] ✓ Pipeline completed in {time.time() - pipeline_start_time:.3f}s | Results: {len(final_recipes)}")

        return {
            "response": response,
            "metadata": metadata
        }

    async def _post_process_cost_nutrition_results(
        self,
        rows: List[Dict[str, Any]],
        user_uid: Optional[str],
        cost_filter: Optional[Dict[str, Any]],
        nutrition_filter: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Post-process cost/nutrition filter results.

        Extracts relevant cost/nutrition data from recipe_metadata for display.
        Also fetches ingredients and instructions for each recipe.
        """
        processed = []

        # Map country codes to lowercase keys used in DB
        country_key_map = {
            "US": "usa",
            "India": "india",
            "Norway": "norway"
        }

        # Get all recipe IDs for batch fetching ingredients and instructions
        recipe_ids = [row.get("id") for row in rows if row.get("id")]

        # Batch fetch ingredients
        ingredients_map = {}
        if recipe_ids:
            try:
                ingredient_results = self.db.execute(text("""
                    SELECT
                        ri."recipeId", ri.amount, ri."unitId", ri.order as ri_order,
                        i.id as ing_id, i.name as ing_name,
                        mut.name as unit_name
                    FROM recipe_ingredient ri
                    JOIN ingredient i ON ri."ingredientId" = i.id
                    LEFT JOIN measuring_unit_translation mut ON ri."unitId" = mut."measuringUnitId" AND mut."languageId" = 'en'
                    WHERE ri."recipeId" = ANY(:recipe_ids)
                    AND ri."deletedAt" IS NULL
                    ORDER BY ri."recipeId", ri.order
                """), {"recipe_ids": recipe_ids}).fetchall()

                for ir in ingredient_results:
                    recipe_id = str(ir[0])
                    if recipe_id not in ingredients_map:
                        ingredients_map[recipe_id] = []
                    ingredients_map[recipe_id].append({
                        "name": ir[5],
                        "amount": ir[1],
                        "unit": ir[6],
                        "unit_id": str(ir[2]) if ir[2] else None
                    })
            except Exception as e:
                logger.warning(f"[COST/NUTRITION FILTER] Failed to fetch ingredients: {e}")

        # Batch fetch instructions
        instructions_map = {}
        if recipe_ids:
            try:
                instruction_results = self.db.execute(text("""
                    SELECT "recipeId", "order", description, image
                    FROM recipe_instruction
                    WHERE "recipeId" = ANY(:recipe_ids)
                    AND "deletedAt" IS NULL
                    ORDER BY "recipeId", "order"
                """), {"recipe_ids": recipe_ids}).fetchall()

                for instr in instruction_results:
                    recipe_id = str(instr[0])
                    if recipe_id not in instructions_map:
                        instructions_map[recipe_id] = []
                    instructions_map[recipe_id].append({
                        "order": instr[1],
                        "description": instr[2],
                        "image": instr[3]
                    })
            except Exception as e:
                logger.warning(f"[COST/NUTRITION FILTER] Failed to fetch instructions: {e}")

        for row in rows:
            recipe_id = str(row.get("id"))
            metadata = row.get("recipe_metadata") or {}

            # Parse metadata if it's a string
            if isinstance(metadata, str):
                import json
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}

            # Extract cost info for the relevant country
            # Structure: pricing -> country -> {total, currency}
            cost_info = None
            if cost_filter:
                country = cost_filter.get("country", "US")
                country_key = country_key_map.get(country, country.lower())
                pricing = metadata.get("pricing", {})
                if pricing and country_key in pricing:
                    country_pricing = pricing[country_key]
                    cost_info = {
                        "amount": country_pricing.get("total", 0),
                        "country": country,
                        "currency": country_pricing.get("currency", "$" if country == "US" else "₹" if country == "India" else "NOK")
                    }

            # Extract nutrition highlight
            # Structure: totalNutrition -> macros -> nutrient
            nutrition_highlight = None
            if nutrition_filter:
                nutrient_key = nutrition_filter.get("nutrient_key") or nutrition_filter.get("sort_by")
                if nutrient_key:
                    # Map common names to actual keys
                    key_mapping = {
                        "protein": "protein",
                        "carbohydrates": "carbohydrates",
                        "carbs": "carbohydrates",
                        "totalFat": "totalFat",
                        "fat": "totalFat",
                        "energyKcal": "energyKcal",
                        "calories": "energyKcal",
                        "totalFiber": "totalFiber",
                        "fiber": "totalFiber",
                        "totalSugars": "totalSugars",
                        "sugar": "totalSugars"
                    }
                    actual_key = key_mapping.get(nutrient_key, nutrient_key)
                    # Get from totalNutrition -> macros
                    total_nutrition = metadata.get("totalNutrition", {})
                    macros = total_nutrition.get("macros", {})
                    if macros and actual_key in macros:
                        nutrition_highlight = {
                            "nutrient": nutrient_key,
                            "value": macros[actual_key],
                            "level": nutrition_filter.get("level", "high")
                        }

            processed.append({
                "id": recipe_id,
                "name": row.get("name"),
                "ingress": row.get("ingress"),
                "image": row.get("image"),
                "total_time": row.get("total_time"),
                "difficulty": row.get("difficulty"),
                "servings": row.get("servings"),
                "cost": cost_info,
                "nutrition_highlight": nutrition_highlight,
                "recipe_metadata": metadata,
                "access_level": "full",
                "ingredients": ingredients_map.get(recipe_id, []),
                "instructions": instructions_map.get(recipe_id, [])
            })

        return processed

    async def _generate_cost_nutrition_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        intent: str,
        cost_filter: Optional[Dict[str, Any]],
        nutrition_filter: Optional[Dict[str, Any]]
    ) -> str:
        """
        Generate natural language response for cost/nutrition filter queries.
        Uses the standard NLG agent for consistent, natural responses.
        """
        if not recipes:
            return await self._generate_no_results_response(query, {"intent": intent})

        # Use the standard NLG agent for generating response
        # This provides consistent, natural language responses
        nlid_result = {
            "intent": intent,
            "filters": {}
        }
        if cost_filter:
            nlid_result["filters"]["cost"] = cost_filter
        if nutrition_filter:
            nlid_result["filters"]["nutrition"] = nutrition_filter

        return await self._generate_natural_language_response(query, recipes, nlid_result)

    async def _handle_special_query(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle special intents like pricing_info and nutritional_info.

        These queries don't involve recipe search but rather direct
        database lookups for ingredient pricing and nutrition data.
        """
        intent = nlid_result.get("intent", "")
        entities = nlid_result.get("entities", {})

        logger.info(f"[SPECIAL QUERY] Handling intent: {intent}")
        logger.info(f"[SPECIAL QUERY] Entities: {entities}")

        response_data = None
        metadata = {
            "intent": intent,
            "is_cooking_related": True,
            "retrieval_strategy": "direct_lookup",
        }

        if intent == "pricing_info":
            # Handle pricing queries - can be for ingredients OR recipes
            # Entities can be: {"ingredients": ["sugar"]} or {"ingredients": "sugar"}
            raw_ingredients = entities.get("ingredients", [])
            raw_recipes = entities.get("recipes", [])

            # Normalize to list
            ingredients = raw_ingredients if isinstance(raw_ingredients, list) else [raw_ingredients] if raw_ingredients else []
            recipes = raw_recipes if isinstance(raw_recipes, list) else [raw_recipes] if raw_recipes else []

            parameters = nlid_result.get("parameters", {})
            country_code = parameters.get("country") or parameters.get("region")

            # Also check entities for country
            if not country_code:
                raw_country = entities.get("country", [])
                country_list = raw_country if isinstance(raw_country, list) else [raw_country] if raw_country else []
                if country_list:
                    country_code = country_list[0]

            logger.info(f"[SPECIAL QUERY] Parsed - ingredients: {ingredients}, recipes: {recipes}, country: {country_code}")

            # Check if this is explicitly a recipe cost query (patterns like "price to make X", "cost of making X")
            query_lower = query.lower()
            is_recipe_cost_query = any(pattern in query_lower for pattern in [
                "to make", "to cook", "cost of making", "price to make", "how much to make", "recipe"
            ])

            # Determine lookup order based on NLID classification
            # If NLID says it's an ingredient, check ingredients FIRST
            # If NLID says it's a recipe OR query has recipe patterns, check recipes FIRST
            check_ingredient_first = bool(ingredients) and not recipes and not is_recipe_cost_query

            item_to_lookup = None
            if ingredients:
                item_to_lookup = ingredients[0]
            elif recipes:
                item_to_lookup = recipes[0]

            if item_to_lookup:
                from models import Recipe

                if check_ingredient_first:
                    # NLID identified as ingredient - check ingredient pricing first
                    logger.info(f"[PRICING] Looking up price for ingredient: {item_to_lookup}, country: {country_code}")
                    pricing_data = get_ingredient_pricing(self.db, item_to_lookup, country_code)

                    if pricing_data:
                        response_data = pricing_data
                        response_data["query_type"] = "ingredient_price"
                        metadata["ingredient"] = item_to_lookup
                        metadata["country_code"] = country_code
                    else:
                        # Ingredient not found OR no pricing data for specified country
                        # DO NOT fall back to recipe lookup for ingredient price queries
                        # This prevents "price of banana" from returning "Banana Blueberry Muffins" recipe cost
                        logger.info(f"[PRICING] No pricing data found for ingredient: {item_to_lookup}")
                        metadata["ingredient"] = item_to_lookup
                        metadata["country_code"] = country_code
                        # Let LLM fallback provide a general knowledge answer
                else:
                    # NLID identified as recipe OR has recipe patterns - check recipe first
                    # BUT: If query has strong ingredient pricing signals AND item is likely a single ingredient
                    # (not a multi-word recipe name), try ingredient first

                    # Only consider ingredient pricing if:
                    # 1. Has ingredient pricing patterns
                    # 2. NOT a recipe cost query pattern
                    # 3. Item is short (1-2 words, likely an ingredient not a recipe name)
                    item_word_count = len(item_to_lookup.split()) if item_to_lookup else 0
                    is_likely_ingredient_price = (
                        any(pattern in query_lower for pattern in [
                            "price of", "cost of", "price for", "cost for", "how much is", "what is the price", "what is the cost"
                        ])
                        and not is_recipe_cost_query
                        and item_word_count <= 2  # Single ingredient like "banana" or "tomato sauce"
                        and not recipes  # NLID didn't identify it as a recipe
                    )

                    if is_likely_ingredient_price:
                        # Query looks like ingredient price, try that first
                        logger.info(f"[PRICING] Query suggests ingredient price, checking ingredient first: {item_to_lookup}")
                        pricing_data = get_ingredient_pricing(self.db, item_to_lookup, country_code)
                        if pricing_data:
                            response_data = pricing_data
                            response_data["query_type"] = "ingredient_price"
                            metadata["ingredient"] = item_to_lookup
                            metadata["country_code"] = country_code
                        else:
                            # No ingredient pricing, let LLM handle it (don't fall back to recipe)
                            logger.info(f"[PRICING] No ingredient pricing found for: {item_to_lookup}")
                            metadata["ingredient"] = item_to_lookup
                            metadata["country_code"] = country_code
                    else:
                        # Standard recipe cost query
                        recipe = self.db.query(Recipe).filter(
                            Recipe.name.ilike(f"%{item_to_lookup}%")
                        ).first()

                        if recipe:
                            logger.info(f"[PRICING] Looking up recipe cost for: {recipe.name}, country: {country_code}")
                            cost_data = get_recipe_cost(self.db, str(recipe.id), country_code)
                            if cost_data:
                                response_data = cost_data
                                response_data["query_type"] = "recipe_cost"
                                metadata["recipe"] = recipe.name
                                metadata["recipe_id"] = str(recipe.id)
                                metadata["country_code"] = country_code
                            else:
                                # Recipe found but no cost data - set recipe metadata for error response
                                response_data = {
                                    "query_type": "recipe_cost",
                                    "recipe": recipe.name,
                                    "recipe_id": str(recipe.id),
                                    "country_code": country_code,
                                    "error": f"No cost data found for recipe '{recipe.name}'"
                                }
                                metadata["recipe"] = recipe.name
                                metadata["recipe_id"] = str(recipe.id)
                                metadata["country_code"] = country_code
                        else:
                            # Not found as recipe - try as ingredient (fallback)
                            logger.info(f"[PRICING] Recipe not found, trying as ingredient: {item_to_lookup}")
                            pricing_data = get_ingredient_pricing(self.db, item_to_lookup, country_code)
                            if pricing_data:
                                response_data = pricing_data
                                response_data["query_type"] = "ingredient_price"
                                metadata["ingredient"] = item_to_lookup
                                metadata["country_code"] = country_code
                            else:
                                # Not found anywhere - let LLM handle it
                                logger.info(f"[PRICING] No data found anywhere for: {item_to_lookup}")
                                metadata["ingredient"] = item_to_lookup
                                metadata["country_code"] = country_code

        elif intent == "nutritional_info":
            # Handle ingredient nutrition queries
            ingredients = entities.get("ingredients", [])
            recipes = entities.get("recipes", [])

            if ingredients:
                # Ingredient nutrition
                ingredient_name = ingredients[0]
                logger.info(f"[NUTRITION] Looking up nutrition for ingredient: {ingredient_name}")

                # First find the ingredient
                ingredient_results = search_ingredients_by_name(self.db, ingredient_name, limit=1)
                if ingredient_results:
                    nutrition_data = get_ingredient_nutrition(self.db, str(ingredient_results[0].id))
                    if nutrition_data:
                        response_data = nutrition_data
                        response_data["query_type"] = "ingredient_nutrition"
                        metadata["ingredient"] = ingredient_name
                    else:
                        logger.info(f"[NUTRITION] No nutrition data found for ingredient: {ingredient_name}")
                        response_data = {
                            "query_type": "ingredient_nutrition",
                            "ingredient": ingredient_name,
                            "error": f"No nutrition data found for '{ingredient_name}'"
                        }
                        metadata["ingredient"] = ingredient_name
                else:
                    logger.info(f"[NUTRITION] Ingredient not found: {ingredient_name}")
                    response_data = {
                        "query_type": "ingredient_nutrition",
                        "ingredient": ingredient_name,
                        "error": f"Ingredient '{ingredient_name}' not found"
                    }
                    metadata["ingredient"] = ingredient_name

            elif recipes:
                # Recipe nutrition
                recipe_name = recipes[0]
                logger.info(f"[NUTRITION] Looking up nutrition for recipe: {recipe_name}")

                # Search for recipe by name
                from models import Recipe
                recipe = self.db.query(Recipe).filter(
                    Recipe.name.ilike(f"%{recipe_name}%")
                ).first()

                if recipe:
                    nutrition_data = get_recipe_nutrition(self.db, str(recipe.id))
                    if nutrition_data:
                        response_data = nutrition_data
                        response_data["query_type"] = "recipe_nutrition"
                        metadata["recipe"] = recipe_name
                        metadata["recipe_id"] = str(recipe.id)
                    else:
                        logger.info(f"[NUTRITION] No nutrition data found for recipe: {recipe_name}")
                        response_data = {
                            "query_type": "recipe_nutrition",
                            "recipe": recipe_name,
                            "recipe_id": str(recipe.id),
                            "error": f"No nutrition data found for recipe '{recipe_name}'"
                        }
                        metadata["recipe"] = recipe_name
                        metadata["recipe_id"] = str(recipe.id)
                else:
                    logger.info(f"[NUTRITION] Recipe not found: {recipe_name}")
                    response_data = {
                        "query_type": "recipe_nutrition",
                        "recipe": recipe_name,
                        "error": f"Recipe '{recipe_name}' not found"
                    }
                    metadata["recipe"] = recipe_name

        # Generate natural language response
        # Check if we have actual data (not just empty pricing list or error)
        has_data = False
        has_error = False
        if response_data:
            has_error = "error" in response_data
            if not has_error:
                if intent == "pricing_info":
                    query_type = response_data.get("query_type", "ingredient_price")
                    if query_type == "recipe_cost":
                        # Check for multi-country response (countries list) OR single-country response (total_cost)
                        has_data = bool(response_data.get("countries")) or response_data.get("total_cost") is not None
                    else:
                        has_data = bool(response_data.get("pricing"))
                elif intent == "nutritional_info":
                    has_data = bool(response_data.get("macros") or response_data.get("micros"))

        if has_error:
            # Generate error response
            error_msg = response_data.get("error", "Data not found")
            response = f"I'm sorry, I couldn't find the information you're looking for. {error_msg}"
            session.add_to_history("assistant", response)
            self.session_manager.save_session(session)

            return {
                "response": response,
                "metadata": {**metadata, "num_results": 0, "error": error_msg}
            }
        elif has_data:
            response = await self._format_special_query_response(
                query, intent, response_data
            )
            session.add_to_history("assistant", response)
            self.session_manager.save_session(session)

            return {
                "response": response,
                "metadata": metadata
            }
        else:
            # No data found in database - use LLM to answer as general food knowledge
            logger.info(f"[SPECIAL QUERY] No database data found, using LLM fallback for {intent}")
            llm_response = await self._generate_llm_fallback_response(query, intent, entities)
            session.add_to_history("assistant", llm_response)
            self.session_manager.save_session(session)

            return {
                "response": llm_response,
                "metadata": {**metadata, "num_results": 0, "fallback": "llm"}
            }

    async def _format_special_query_response(
        self,
        query: str,
        intent: str,
        data: Dict[str, Any]
    ) -> str:
        """Format a natural language response from special query data"""
        if intent == "pricing_info":
            return self._format_pricing_response(data)
        elif intent == "nutritional_info":
            return self._format_nutrition_response(data)
        else:
            return "I found some information, but I'm not sure how to format it."

    def _format_pricing_response(self, data: Dict[str, Any]) -> str:
        """Format pricing data into natural language"""
        query_type = data.get("query_type", "ingredient_price")

        # Handle recipe cost queries
        if query_type == "recipe_cost":
            return self._format_recipe_cost_response(data)

        # Handle ingredient pricing queries
        if not data or not data.get("pricing"):
            return f"Sorry, I couldn't find pricing information for {data.get('name', 'that ingredient')}."

        ingredient_name = data.get("name", "the ingredient")
        pricing_list = data.get("pricing", [])

        # If only one pricing entry, provide a cleaner response
        if len(pricing_list) == 1:
            pricing = pricing_list[0]
            country = pricing.get("country", "Unknown")
            country_name = pricing.get("country_name", country)
            currency = pricing.get("currency_symbol", "")
            price = pricing.get("price_per_unit", 0)
            quantity = pricing.get("quantity", 1)
            unit = pricing.get("unit", "")
            total = pricing.get("total_price", price)

            if quantity and quantity > 1:
                return f"The price of **{ingredient_name}** in {country_name} ({country}) is {currency}{total:.2f} for {quantity} {unit} ({currency}{price:.2f} per {unit})."
            else:
                return f"The price of **{ingredient_name}** in {country_name} ({country}) is {currency}{price:.2f} per {unit}."

        # Multiple pricing entries - list them all
        response_parts = [f"Here's the pricing information for **{ingredient_name}**:\n"]

        for pricing in pricing_list:
            country = pricing.get("country", "Unknown")
            country_name = pricing.get("country_name", country)
            currency = pricing.get("currency_symbol", "")
            price = pricing.get("price_per_unit", 0)
            quantity = pricing.get("quantity", 1)
            unit = pricing.get("unit", "")
            total = pricing.get("total_price", price)

            if quantity and quantity > 1:
                response_parts.append(
                    f"- **{country_name}**: {currency}{total:.2f} for {quantity} {unit} ({currency}{price:.2f} per {unit})"
                )
            else:
                response_parts.append(f"- **{country_name}**: {currency}{price:.2f} per {unit}")

        return "\n".join(response_parts)

    def _format_recipe_cost_response(self, data: Dict[str, Any]) -> str:
        """Format recipe cost data into natural language"""
        recipe_name = data.get("recipe_name", "the recipe")
        servings = data.get("servings", 1)

        # Check if this is a multi-country response
        if "countries" in data:
            # Multi-country response format
            countries_data = data.get("countries", [])
            if not countries_data:
                return f"Sorry, I couldn't find pricing information for {recipe_name}."

            response_parts = [f"Here's the pricing information for **{recipe_name}**:\n"]

            for country_data in countries_data:
                country_name = country_data.get("country_name", "Unknown")
                currency = country_data.get("currency_symbol", "")
                total_cost = country_data.get("total_cost", 0)
                cost_per_serving = country_data.get("cost_per_serving", total_cost)
                ingredient_costs = country_data.get("ingredient_costs", [])

                # Country header with total cost
                serving_text = f" per serving" if servings > 1 else ""
                response_parts.append(f"**{country_name}**: {currency}{total_cost:.2f}{serving_text}")

                # Ingredient breakdown
                if ingredient_costs:
                    for ing in ingredient_costs:
                        ing_name = ing.get("ingredient", "")
                        amount = ing.get("amount", "")
                        unit = ing.get("unit", "")
                        cost = ing.get("cost")

                        # Format amount with unit if available
                        amount_str = f" ({amount} {unit})" if amount and unit else f" ({amount})" if amount else ""

                        if cost is not None:
                            response_parts.append(f"  - {ing_name}{amount_str}: {currency}{cost:.2f}")
                        elif ing.get("note"):
                            response_parts.append(f"  - {ing_name}{amount_str}: {ing.get('note')}")

                response_parts.append("")  # Empty line between countries

            return "\n".join(response_parts).rstrip()

        # Single country response format
        if not data or data.get("total_cost") is None:
            return f"Sorry, I couldn't find pricing information for {recipe_name}."

        currency = data.get("currency_symbol", "")
        currency_code = data.get("currency", "")
        country_name = data.get("country_name", "")
        total_cost = data.get("total_cost", 0)
        cost_per_serving = data.get("cost_per_serving", total_cost)
        ingredient_costs = data.get("ingredient_costs", [])

        # Build response with country info if available
        if country_name:
            response_parts = [
                f"The price of **{recipe_name}** in {country_name} ({currency_code}) is {currency}{total_cost:.2f}."
            ]
        else:
            response_parts = [
                f"The approximate cost to make **{recipe_name}** is {currency}{total_cost:.2f}."
            ]

        if servings > 1:
            response_parts.append(f"That's about {currency}{cost_per_serving:.2f} per serving (serves {servings}).")

        # Add ingredient breakdown if available - SHOW ALL INGREDIENTS with amounts
        if ingredient_costs:
            response_parts.append("")  # Empty line before breakdown
            for ing in ingredient_costs:
                ing_name = ing.get("ingredient", "")
                amount = ing.get("amount", "")
                unit = ing.get("unit", "")
                cost = ing.get("cost")

                # Format amount with unit if available
                amount_str = f" ({amount} {unit})" if amount and unit else f" ({amount})" if amount else ""

                if cost is not None:
                    response_parts.append(f"  - {ing_name}{amount_str}: {currency}{cost:.2f}")
                elif ing.get("note"):
                    response_parts.append(f"  - {ing_name}{amount_str}: {ing.get('note')}")

        return "\n".join(response_parts)

    def _format_nutrition_response(self, data: Dict[str, Any]) -> str:
        """Format nutrition data into natural language"""
        if not data:
            return "Sorry, I couldn't find nutritional information."

        name = data.get("name", "the ingredient")
        macros = data.get("macros", {})
        micros = data.get("micros", {})

        response_parts = [f"Here's the nutritional information for **{name}** (per 100g):\n"]

        # Key macros
        if macros.get("energyKcal"):
            response_parts.append(f"- **Calories**: {macros['energyKcal']:.0f} kcal")
        if macros.get("protein"):
            response_parts.append(f"- **Protein**: {macros['protein']:.1f}g")
        if macros.get("carbohydrates"):
            response_parts.append(f"- **Carbohydrates**: {macros['carbohydrates']:.1f}g")
        if macros.get("totalFat"):
            response_parts.append(f"- **Total Fat**: {macros['totalFat']:.1f}g")
        if macros.get("totalFiber"):
            response_parts.append(f"- **Fiber**: {macros['totalFiber']:.1f}g")
        if macros.get("totalSugars"):
            response_parts.append(f"- **Sugars**: {macros['totalSugars']:.1f}g")

        # Key micros
        if micros.get("calcium"):
            response_parts.append(f"- **Calcium**: {micros['calcium']:.1f}mg")
        if micros.get("iron"):
            response_parts.append(f"- **Iron**: {micros['iron']:.1f}mg")
        if micros.get("vitaminC"):
            response_parts.append(f"- **Vitamin C**: {micros['vitaminC']:.1f}mg")

        # For recipe nutrition
        if "recipe_id" in data:
            response_parts = [f"Here's the nutritional information for **{name}**:\n"]
            total = data.get("total", {})
            if total.get("energyKcal"):
                response_parts.append(f"- **Total Calories**: {total['energyKcal']:.0f} kcal")
            if total.get("protein"):
                response_parts.append(f"- **Total Protein**: {total['protein']:.1f}g")
            if total.get("carbohydrates"):
                response_parts.append(f"- **Total Carbohydrates**: {total['carbohydrates']:.1f}g")
            if total.get("totalFat"):
                response_parts.append(f"- **Total Fat**: {total['totalFat']:.1f}g")

        return "\n".join(response_parts)

    async def _generate_llm_fallback_response(
        self,
        query: str,
        intent: str,
        entities: Dict[str, Any]
    ) -> str:
        """
        Generate response using LLM when database data is not available.

        This allows the chatbot to still provide helpful information based on
        general food knowledge even when specific database records are missing.
        """
        # Build context for the LLM
        context_parts = [
            "You are Sulten Chatbot, a cooking and food assistant.",
            "The user asked a question about food, but you don't have specific data in your database.",
            "Use your general food knowledge to provide a helpful answer.",
        ]

        if intent == "pricing_info":
            # Check if this is a recipe cost query
            query_lower = query.lower()
            is_recipe_cost_query = any(pattern in query_lower for pattern in [
                "to make", "to cook", "cost of making", "price to make", "how much to make"
            ])

            if is_recipe_cost_query:
                # Recipe cost query
                import re
                recipe_match = re.search(r'(?:to make|to cook|cost of making|price to make|how much to make)\s+(.+?)(?:\s|$|\?)', query_lower)
                recipe_name = recipe_match.group(1).strip() if recipe_match else "the recipe"
                parameters = entities.get("parameters", {})
                country = parameters.get("country") or parameters.get("region")

                if country:
                    context_parts.extend([
                        f"The user is asking about the approximate cost to make: {recipe_name}",
                        f"Specifically for: {country}",
                        f"Estimate the total cost to make {recipe_name} including main ingredients,",
                        "Break down the cost by key ingredients if possible,",
                        "Mention that prices vary by brand, quality, and location within the country.",
                    ])
                else:
                    context_parts.extend([
                        f"The user is asking about the approximate cost to make: {recipe_name}",
                        "Estimate the total cost to make this recipe including main ingredients,",
                        "Break down the cost by key ingredients if possible,",
                        "Mention that prices vary by region, brand, and quality.",
                    ])
            else:
                # Ingredient pricing query
                ingredients_list = entities.get("ingredients", [])
                ingredient = ingredients_list[0] if ingredients_list else None
                if not ingredient:
                    # Try to extract ingredient from query
                    import re
                    ing_match = re.search(r'(?:price|cost|how much).+?(?:of|for|in)?\s+([a-z]+(?:\s+[a-z]+)?)', query_lower)
                    ingredient = ing_match.group(1).strip() if ing_match else "the ingredient"

                parameters = entities.get("parameters", {})
                country = parameters.get("country") or parameters.get("region")

                if country:
                    context_parts.extend([
                        f"The user is asking about the price/cost of: {ingredient}",
                        f"Specifically for: {country}",
                        f"Provide typical pricing for {ingredient} in {country} if you know it,",
                        "or explain that prices vary by region, season, and store type.",
                        "Mention typical price ranges per kg or per unit as appropriate.",
                    ])
                else:
                    context_parts.extend([
                        f"The user is asking about the price/cost of: {ingredient}",
                        "Provide general pricing guidance if you know it, or explain that prices vary by region/season.",
                        "Be helpful and suggest where they might find current pricing information.",
                    ])

        elif intent == "nutritional_info":
            ingredients_list = entities.get("ingredients", [])
            recipes_list = entities.get("recipes", [])
            item = ingredients_list[0] if ingredients_list else (recipes_list[0] if recipes_list else "that item")
            context_parts.extend([
                f"The user is asking about nutrition for: {item}",
                "Provide general nutritional information if you know it.",
                "Be helpful but note that exact values can vary by brand, preparation method, etc.",
            ])
        else:
            context_parts.append(f"The user asked: {query}")

        # Use the NLG agent to generate a helpful response
        try:
            from openai import OpenAI
            import os

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                # Fallback to simple response if no API key
                if intent == "pricing_info":
                    ingredient = entities.get("ingredients", ["that item"])[0]
                    parameters = entities.get("parameters", {})
                    country = parameters.get("country") or parameters.get("region")
                    if country:
                        return f"I don't have specific pricing data for **{ingredient}** in {country}. Food prices vary by region, season, and where you shop. I'd recommend checking local grocery stores or online delivery services for current pricing in {country}."
                    return f"I don't have specific pricing data for **{ingredient}** in my database. Food prices vary significantly by region, season, and where you shop. I'd recommend checking your local grocery store or online delivery services for current pricing."
                else:
                    return "I don't have specific information in my database, but I can help with recipe suggestions and cooking tips!"

            client = OpenAI(api_key=api_key)
            system_prompt = "\n".join(context_parts)

            response = client.chat.completions.create(
                model=os.getenv("NLG_AGENT_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                max_tokens=300,
                temperature=0.7
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"[LLM FALLBACK] Error generating response: {e}")
            # Final fallback
            return (
                "I don't have specific information in my database for that item. "
                "However, I can help you find recipes, cooking tips, and general food advice! "
                "Would you like me to help you with something else?"
            )
