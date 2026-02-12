"""
Pipeline Orchestrator (SDK Version)
Ties together all stages of the recipe search pipeline using OpenAI Agents SDK
"""
from typing import Dict, Any, List, Optional, Tuple
import time
import asyncio
from sqlalchemy.orm import Session
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

            # ============ STAGE 4+5+6: Embedding Search + Schema + SQL Generation ============
            # For HYBRID_VECTOR_TO_SQL: Wait for embedding search first, then generate SQL with candidate_ids
            # For other strategies: Can run in parallel
            parallel2_start = time.time()

            # Initialize variables for all paths
            candidate_ids = None
            similarity_scores = {}
            relevant_schema = None
            sql_result = None

            if retrieval_plan.strategy == RetrievalStrategy.HYBRID_VECTOR_TO_SQL:
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
                    session_context
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

            # Prepare pipeline metadata for conversation storage
            # This ensures search context is persisted across requests
            pipeline_metadata = {
                "vector_query": getattr(retrieval_plan, 'vector_query', None),
                "intent": nlid_result_dict.get("intent"),
                "filters": getattr(retrieval_plan, 'sql_filters', {})
            }

            # Update session with the last search query for future refinements
            # Only save if this is a new recipe search, not a refinement
            if nlid_result_dict.get("intent") == "recipe_search" and retrieval_plan.vector_query:
                # Check if this is a refinement or a new search
                context_entities = session_context.get("context_entities", {})
                last_vector_query = context_entities.get("last_vector_query")

                # Debug information
                logger.info(f"[SESSION SAVE DEBUG] Current query: {query}")
                logger.info(f"[SESSION SAVE DEBUG] Retrieval plan vector_query: {retrieval_plan.vector_query}")
                logger.info(f"[SESSION SAVE DEBUG] Last vector query: {last_vector_query}")
                logger.info(f"[SESSION SAVE DEBUG] Query == vector_query: {query == retrieval_plan.vector_query}")

                # If we don't have a previous query (new search), save it
                # OR if this is a refinement (current query != vector_query), don't overwrite the original
                is_refinement = (last_vector_query and
                               query != retrieval_plan.vector_query and
                               nlid_result_dict.get("filters", {}).get("excluded_ingredients"))

                if not last_vector_query or not is_refinement:
                    logger.info(f"[SESSION SAVE] Saving last_vector_query: {retrieval_plan.vector_query}")
                    session.context_entities.last_vector_query = retrieval_plan.vector_query
                    session.context_entities.last_search_filters = retrieval_plan.sql_filters or {}
                    session.last_intent = "recipe_search"
                    # Save the updated session
                    self.session_manager.save_session(session)
                    logger.info(f"[SESSION SAVE] Session saved with context: {session.context_entities.last_vector_query}")
                else:
                    # This is a refinement - don't overwrite the original search query
                    logger.info(f"[SESSION SAVE] Refinement detected - preserving original vector query: {last_vector_query}")
                    logger.info(f"[SESSION SAVE] Current refinement query: {query} - NOT saving as last_vector_query")

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
        session_context: Dict[str, Any]
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
                    i.id as ing_id, i.name as ing_name
                FROM recipe_ingredient ri
                JOIN ingredient i ON ri."ingredientId" = i.id
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
                    "unit": str(ir[2]) if ir[2] else None
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

            # Get similarity score
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

        response_data = None
        metadata = {
            "intent": intent,
            "is_cooking_related": True,
            "retrieval_strategy": "direct_lookup",
        }

        if intent == "pricing_info":
            # Handle pricing queries - can be for ingredients OR recipes
            ingredients = entities.get("ingredients", [])
            recipes = entities.get("recipes", [])
            parameters = nlid_result.get("parameters", {})
            country_code = parameters.get("country") or parameters.get("region")

            # Check if this is a recipe cost query (patterns like "price to make X", "cost of making X")
            query_lower = query.lower()
            is_recipe_cost_query = any(pattern in query_lower for pattern in [
                "to make", "to cook", "cost of making", "price to make", "how much to make"
            ])

            # Prioritize recipe cost if detected by pattern or if recipe entity exists
            if (is_recipe_cost_query or recipes) and not ingredients:
                # This is a recipe cost query
                # Extract recipe name from query if not in entities
                if not recipes:
                    # Try to extract recipe name from query patterns
                    import re
                    recipe_patterns = [
                        r"price to make\s+(.+?)(?:\s|$|\?)",
                        r"cost of making\s+(.+?)(?:\s|$|\?)",
                        r"how much to make\s+(.+?)(?:\s|$|\?)",
                        r"price of\s+(.+?)(?:\s|$|\?)",
                    ]
                    for pattern in recipe_patterns:
                        match = re.search(pattern, query_lower)
                        if match:
                            recipe_name = match.group(1).strip()
                            recipes = [recipe_name]
                            break

                if recipes:
                    recipe_name = recipes[0]
                    logger.info(f"[PRICING] Looking up recipe cost for: {recipe_name}, country: {country_code}")

                    # Search for recipe by name
                    from models import Recipe
                    recipe = self.db.query(Recipe).filter(
                        Recipe.name.ilike(f"%{recipe_name}%")
                    ).first()

                    if recipe:
                        cost_data = get_recipe_cost(self.db, str(recipe.id), country_code)
                        response_data = cost_data
                        response_data["query_type"] = "recipe_cost"
                        metadata["recipe"] = recipe_name
                        metadata["recipe_id"] = str(recipe.id)
                        metadata["country_code"] = country_code
                    else:
                        logger.info(f"[PRICING] Recipe not found: {recipe_name}")

            # If not a recipe cost query, handle as ingredient pricing
            if not response_data and ingredients:
                ingredient_name = ingredients[0]
                logger.info(f"[PRICING] Looking up price for ingredient: {ingredient_name}, country: {country_code}")

                pricing_data = get_ingredient_pricing(self.db, ingredient_name, country_code)
                response_data = pricing_data
                response_data["query_type"] = "ingredient_price"
                metadata["ingredient"] = ingredient_name
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
                    response_data = nutrition_data
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
                    response_data = nutrition_data
                    metadata["recipe"] = recipe_name
                    metadata["recipe_id"] = str(recipe.id)

        # Generate natural language response
        # Check if we have actual data (not just empty pricing list)
        has_data = False
        if response_data:
            if intent == "pricing_info":
                query_type = response_data.get("query_type", "ingredient_price")
                if query_type == "recipe_cost":
                    has_data = response_data.get("total_cost") is not None
                else:
                    has_data = bool(response_data.get("pricing"))
            elif intent == "nutritional_info":
                has_data = bool(response_data.get("macros") or response_data.get("micros"))

        if has_data:
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
        if not data or data.get("total_cost") is None:
            return f"Sorry, I couldn't find pricing information for {data.get('recipe_name', 'that recipe')}."

        recipe_name = data.get("recipe_name", "the recipe")
        currency = data.get("currency_symbol", "")
        currency_code = data.get("currency", "")
        total_cost = data.get("total_cost", 0)
        servings = data.get("servings", 1)
        cost_per_serving = data.get("cost_per_serving", total_cost)
        ingredient_costs = data.get("ingredient_costs", [])

        # Build response
        response_parts = [
            f"The approximate cost to make **{recipe_name}** is {currency}{total_cost:.2f}."
        ]

        if servings > 1:
            response_parts.append(f"That's about {currency}{cost_per_serving:.2f} per serving (serves {servings}).")

        # Add ingredient breakdown if available
        if ingredient_costs:
            response_parts.append("\n**Ingredient cost breakdown:**")
            for ing in ingredient_costs[:5]:  # Show first 5 ingredients
                ing_name = ing.get("ingredient", "")
                cost = ing.get("cost")
                if cost is not None:
                    response_parts.append(f"- {ing_name}: {currency}{cost:.2f}")

            if len(ingredient_costs) > 5:
                response_parts.append(f"- ... and {len(ingredient_costs) - 5} more ingredients")

        return " ".join(response_parts)

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
                ingredient = entities.get("ingredients", [None])[0]
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
            item = entities.get("ingredients", entities.get("recipes", ["that item"]))[0]
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
