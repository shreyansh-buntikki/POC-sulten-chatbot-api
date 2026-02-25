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
from apps.fastapi.src.agents.sdk_nlid_agent import nlid_agent, IntentOutput, create_nlid_agent
from apps.fastapi.src.agents.sdk_orchestrator_agent import (
    orchestrator_agent,
    create_orchestrator_agent,
    create_cooking_guardrail_agent,
    create_orchestrator_with_custom_agents
)
from apps.fastapi.src.agents.sdk_nlg_agent import (
    nlg_agent,
    generate_recipe_response,
    generate_no_results_response,
    generate_error_response,
    generate_recipe_detail_response,
    generate_educational_response,
    generate_combined_meal_response,
    generate_no_results_with_context_response,
    create_nlg_agent
)
from apps.fastapi.src.agents.sdk_recipe_agent import recipe_agent, create_recipe_agent
from apps.fastapi.src.agents.sdk_nutritional_agent import nutritional_agent, create_nutritional_agent
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
    build_recipe_time_filter_sql,
    build_recipe_base_sql,
    build_session_filter_conditions
)
from apps.fastapi.src.services.ingredient_matcher import IntelligentIngredientMatcher
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

    # Waterfall fallback constants
    FALLBACK_THRESHOLD = 5  # Trigger fallback when results < this (user wants at least 5 results)
    # Hard filters: never relaxed (safety / user constraints)
    HARD_FILTERS = frozenset({
        "excluded_ingredients",
        "exclude_ingredients",
        "creator_uid",
    })
    # Soft filters: relaxed in priority order (first = removed first)
    SOFT_FILTERS_PRIORITY = [
        "tags",
        "difficulty",
        "servings",
        "cuisines",
        "nutrition_filters",
        "cost_filter",
        "time_filter",
        "max_time",
    ]

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
        language: str = "en",
        custom_prompts: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Process a user query through the complete pipeline

        Args:
            query: User's query text
            session_id: Session identifier
            user_uid: Optional user identifier
            language: Language code
            custom_prompts: Optional dictionary of custom prompts for agents

        Returns:
            Dictionary with response and metadata
        """
        pipeline_start_time = time.time()
        logger.info(f" [PIPELINE START] Query: {query[:100]} | Session: {session_id} | User: {user_uid}")

        # Create agents with custom prompts if provided
        current_nlid_agent = nlid_agent
        current_nlg_agent = nlg_agent
        current_recipe_agent = recipe_agent
        current_nutritional_agent = nutritional_agent
        current_orchestrator_agent = orchestrator_agent
        current_guardrail_agent = None
        use_agent_flow = False  # Flag to determine if we should use agent-based flow

        if custom_prompts:
            # Create custom agents based on provided prompts
            if "nlid_agent" in custom_prompts:
                current_nlid_agent = create_nlid_agent(custom_prompts["nlid_agent"])
                use_agent_flow = True

            if "nlg_agent" in custom_prompts:
                current_nlg_agent = create_nlg_agent(custom_prompts["nlg_agent"])

            if "recipe_agent" in custom_prompts:
                current_recipe_agent = create_recipe_agent(custom_prompts["recipe_agent"])
                use_agent_flow = True

            if "nutritional_agent" in custom_prompts:
                current_nutritional_agent = create_nutritional_agent(custom_prompts["nutritional_agent"])
                use_agent_flow = True

            if "cooking_guardrail" in custom_prompts:
                current_guardrail_agent = create_cooking_guardrail_agent(custom_prompts["cooking_guardrail"])
                use_agent_flow = True

            if "orchestrator_agent" in custom_prompts:
                # Create orchestrator with custom agents as handoffs
                current_orchestrator_agent = create_orchestrator_with_custom_agents(
                    prompt=custom_prompts["orchestrator_agent"],
                    guardrail_agent=current_guardrail_agent,
                    custom_recipe_agent=current_recipe_agent if "recipe_agent" in custom_prompts else None,
                    custom_nutritional_agent=current_nutritional_agent if "nutritional_agent" in custom_prompts else None,
                    custom_nlid_agent=current_nlid_agent if "nlid_agent" in custom_prompts else None
                )
                use_agent_flow = True
            elif current_guardrail_agent:
                # Custom guardrail but default orchestrator prompt - still create new orchestrator
                current_orchestrator_agent = create_orchestrator_with_custom_agents(
                    guardrail_agent=current_guardrail_agent,
                    custom_recipe_agent=current_recipe_agent if "recipe_agent" in custom_prompts else None,
                    custom_nutritional_agent=current_nutritional_agent if "nutritional_agent" in custom_prompts else None,
                    custom_nlid_agent=current_nlid_agent if "nlid_agent" in custom_prompts else None
                )

            if use_agent_flow:
                logger.info(f"[CUSTOM PROMPTS] Agent-based flow activated ({len(custom_prompts)} custom prompts)")

        # If custom prompts provided, use agent-based flow for full agent testing
        if use_agent_flow:
            logger.info("[PIPELINE] Redirecting to agent-based flow...")
            return await self.process_with_agent_flow(
                query=query,
                session_id=session_id,
                user_uid=user_uid,
                language=language,
                orchestrator=current_orchestrator_agent,
                nlg_agent_instance=current_nlg_agent if custom_prompts and "nlg_agent" in custom_prompts else None
            )

        # Initialize retrieval_plan to None to avoid undefined variable errors
        retrieval_plan = None

        try:
            # ============ STAGE 1: Session Memory ============
            stage_start = time.time()
            session = self.session_manager.get_or_create_session(
                session_id, user_uid, language or "en", self.conversation_store
            )
            session.add_to_history("user", query)
            logger.info(f"[STAGE 1] ✓ Completed in {time.time() - stage_start:.3f}s | Session ID: {session.session_id}")

            # ============ PARALLEL PHASE: NLID + Schema Pre-fetch ============
            # Run NLID (intent detection) and schema pre-fetching in parallel
            # Most queries are recipe_search, so we pre-fetch that schema speculatively
            parallel_start = time.time()

            # Get conversation history for NLID context
            conversation_history = session.get_context_window(limit=10)

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
                    current_nlid_agent,
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


            # If nlid_result is an exception (e.g. APITimeoutError), re-raise it
            # so the outer error handler can return a graceful response.
            if isinstance(nlid_result, BaseException):
                logger.error(f"[STAGE 2] NLID call failed: {nlid_result}")
                raise nlid_result

            # Get structured output from SDK agent
            # With AgentOutputSchema, final_output is already the typed object
            output = nlid_result.final_output
            nlid_data = output if isinstance(output, IntentOutput) else IntentOutput(**output)

            logger.info(f"[STAGE 2] ✓ NLID completed in {time.time() - stage_start:.3f}s | intent={nlid_data.intent} | confidence={nlid_data.confidence}")

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

            # ============ PRICING FOLLOW-UP DETECTION ============
            # If the last intent was `pricing_info` and the current query looks like
            # a country/currency refinement (e.g., "And in India?", "in Norway"),
            # reuse the previously priced item with the new country instead of
            # misrouting to recipe_search.
            if (
                session.last_intent == "pricing_info"
                and session.context_entities.last_pricing_item
                and nlid_result_dict["intent"] not in ["pricing_info", "nutritional_info"]
            ):
                detected_country = self._extract_country_from_query(query)
                if detected_country:
                    item_name = session.context_entities.last_pricing_item
                    item_type = (
                        session.context_entities.last_pricing_item_type or "ingredient"
                    )
                    logger.info(
                        f"[PRICING FOLLOW-UP] Country refinement detected. "
                        f"Reusing last priced {item_type}: '{item_name}' "
                        f"with country '{detected_country}'"
                    )
                    if item_type == "recipe":
                        override_entities = {
                            "ingredients": [],
                            "recipes": [item_name],
                        }
                    else:
                        override_entities = {
                            "ingredients": [item_name],
                            "recipes": [],
                        }
                    nlid_result_dict["intent"] = "pricing_info"
                    nlid_result_dict["entities"] = override_entities
                    nlid_result_dict["parameters"] = {"country": detected_country}
                    nlid_result_dict["filters"] = nlid_result_dict.get("filters", {})

            # ============ SPECIAL HANDLING: Pricing and Nutrition Queries ============
            # Handle pricing_info and nutritional_info intents directly
            if nlid_result_dict["intent"] in ["pricing_info", "nutritional_info"]:
                return await self._handle_special_query(
                    query, nlid_result_dict, session, user_uid, language
                )

            # ============ SPECIAL HANDLING: Cost, Nutrition, and Time Filter Queries ============
            # Handle price_filter, nutrition_filter, and time_filter intents.
            # CRITICAL: If the query ALSO contains semantic content (cuisines, tags like "dessert",
            # ingredients, meal types, etc.), we must do embedding search first, then apply filters.
            # Only use direct SQL (no embedding) if there's NO semantic content.
            if nlid_result_dict["intent"] in ["price_filter", "nutrition_filter", "time_filter"]:
                _filter_intent = nlid_result_dict["intent"]
                _filters_for_filter_intent = nlid_result_dict.get("filters", {})
                _entities_for_filter_intent = nlid_result_dict.get("entities", {})

                # Check for semantic content that requires embedding search
                # Tags like "dessert", "dinner", cuisines like "italian", ingredients, etc.
                _has_semantic_for_filter = bool(
                    _filters_for_filter_intent.get("cuisines")
                    or _filters_for_filter_intent.get("tags")  # e.g., "dessert", "dinner"
                    or _filters_for_filter_intent.get("included_ingredients")
                    or _filters_for_filter_intent.get("include_ingredients")
                    or _entities_for_filter_intent.get("cuisines")
                    or _entities_for_filter_intent.get("ingredients")
                    or _entities_for_filter_intent.get("meal_types")
                    or _entities_for_filter_intent.get("recipe_name")
                )

                if _has_semantic_for_filter:
                    # Query has semantic content + filter - treat as recipe_search with filter
                    # This will do embedding search first, then apply the price/time/nutrition filter
                    logger.info(
                        f"[FILTER INTENT WITH SEMANTIC] Intent={_filter_intent} has semantic content "
                        f"(cuisines={_filters_for_filter_intent.get('cuisines')}, "
                        f"tags={_filters_for_filter_intent.get('tags')}, "
                        f"ingredients={_entities_for_filter_intent.get('ingredients')}). "
                        f"Converting to recipe_search for embedding + filter."
                    )
                    # Change intent to recipe_search and let it flow through the normal pipeline
                    nlid_result_dict["intent"] = "recipe_search"
                    # Continue to the recipe_search flow below (don't return here)
                else:
                    # No semantic content - use direct SQL (no embedding)
                    # Update session from NLID first so filters are persisted for multi-turn context
                    session = self.session_manager.update_session_from_nlid(session, nlid_result_dict)
                    return await self._handle_filter_query(
                        query, nlid_result_dict, session, user_uid, language
                    )

            # ============ NEW INTENTS: Recipe Reference, Negative Feedback, etc. ============

            # Handle recipe_reference intent - user asks about previously shown recipe
            if nlid_result_dict["intent"] == "recipe_reference":
                return await self._handle_recipe_reference(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Handle negative_feedback intent - user doesn't like shown results
            if nlid_result_dict["intent"] == "negative_feedback":
                return await self._handle_negative_feedback(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Handle show_more intent - user wants more results
            if nlid_result_dict["intent"] == "show_more":
                return await self._handle_show_more(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Handle educational_info intent - user asks about cooking concepts
            if nlid_result_dict["intent"] == "educational_info":
                return await self._handle_educational_info(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Handle festival_occasion intent - user wants seasonal/occasion recipes
            if nlid_result_dict["intent"] == "festival_occasion":
                pass
                # Continue with recipe_search flow but add seasonality filters
                # The festival info will be used to add appropriate tags/filters

            # Handle clear_filters intent - user wants to reset session
            if nlid_result_dict["intent"] == "clear_filters":
                return await self._handle_clear_filters(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Handle combined_meal_search intent - user wants multiple courses
            if nlid_result_dict["intent"] == "combined_meal_search":
                return await self._handle_combined_meal_search(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Also check if NLID returned requires_embedding=False or if we can detect cost/nutrition filters
            # IMPORTANT: Skip this check if intent was already converted to recipe_search from a filter intent
            # (e.g., "dessert with budget 400" was converted from price_filter to recipe_search)
            requires_embedding = getattr(nlid_data, 'requires_embedding', True)
            if not requires_embedding and nlid_result_dict["intent"] != "recipe_search":
                # Try to extract filters from query
                cost_filter = extract_cost_filter(query)
                nutrition_filter = extract_nutrition_filter(query)
                if cost_filter or nutrition_filter:
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
                    return await self._handle_filter_query(
                        query, nlid_result_dict, session, user_uid, language
                    )

            # ============ CREATOR USERNAME/NAME RESOLUTION ============
            # Priority 1: @username pattern (exact username match)
            # Priority 2: "by Name" pattern (fuzzy name/username match)
            filters = nlid_result_dict.get("filters", {})
            entities = nlid_result_dict.get("entities", {})

            # Check for @username first (highest priority)
            creator_username = filters.get("creator_username") or entities.get("creator_username")
            creator_name = filters.get("creator_name") or entities.get("creator_name")

            from apps.fastapi.src.services.user_service import UserService

            if creator_username:
                # @username pattern - exact username match only
                logger.info(f"[CREATOR] Looking up user by @username: {creator_username}")

                user, error = UserService.get_user_by_username(self.db, creator_username)

                if user:
                    # Found the user - add creator_uid to filters
                    logger.info(f"[CREATOR] Found user by @username: {user.username} (uid: {user.uid})")
                    nlid_result_dict["filters"]["creator_uid"] = user.uid
                    nlid_result_dict["filters"]["creator_username"] = user.username
                    # Also add to session context for display (use username for @username)
                    nlid_result_dict["creator_resolved"] = {
                        "name": user.name or user.username,
                        "username": user.username,
                        "uid": user.uid
                    }
                else:
                    # User not found - return error response
                    logger.info(f"[CREATOR] User not found with @username: {creator_username}")
                    return {
                        "response": f"Sorry, I couldn't find a user with the username '@{creator_username}'. Please check the spelling or try again with a different username.",
                        "recipes": [],
                        "metadata": {
                            "intent": "recipe_search",
                            "error": "user_not_found",
                            "creator_username": creator_username,
                        },
                        "num_results": 0,
                    }
            elif creator_name:
                # "by Name" pattern - check both username and name fields
                logger.info(f"[CREATOR] Looking up user by name: {creator_name}")

                user, error = UserService.get_user_by_name(self.db, creator_name)

                if user:
                    # Found the user - add creator_uid to filters
                    logger.info(f"[CREATOR] Found user: {user.name} (uid: {user.uid})")
                    nlid_result_dict["filters"]["creator_uid"] = user.uid
                    nlid_result_dict["filters"]["creator_username"] = user.username
                    # Also add to session context for display
                    nlid_result_dict["creator_resolved"] = {
                        "name": user.name,
                        "username": user.username,
                        "uid": user.uid
                    }
                else:
                    # User not found - return error response
                    logger.info(f"[CREATOR] User not found: {creator_name}")
                    return {
                        "response": f"Sorry, I couldn't find a user named '{creator_name}'. Please check the spelling and try again.",
                        "recipes": [],
                        "metadata": {
                            "intent": "recipe_search",
                            "error": "user_not_found",
                            "creator_name": creator_name,
                        },
                        "num_results": 0,
                    }

            # Update session state from NLID results
            session = self.session_manager.update_session_from_nlid(session, nlid_result_dict)

            # ============ RE-ROUTE: recipe_search with cost/time/nutrition filter ============
            # When NLID detects both @username (→ recipe_search) and a budget/time
            # constraint ("under 200"), the cost/time filter lives in filters.cost or
            # filters.time.  Route to the deterministic filter handler instead of
            # the LLM SQL generator which often misinterprets "under 200" as time.
            # Also re-route when the SESSION has persisted cost/time/nutrition from
            # a previous turn (e.g. Q1 "budget 400" → Q2 "dessert recipes" should
            # keep the 400 kr constraint).
            _filters_for_reroute = nlid_result_dict.get("filters", {})
            _entities_for_reroute = nlid_result_dict.get("entities", {})
            _has_cost_reroute = bool(_filters_for_reroute.get("cost"))
            _has_time_reroute = bool(_filters_for_reroute.get("time"))
            _has_nutrition_reroute = bool(_filters_for_reroute.get("nutrition"))
            _has_servings_reroute = bool(_filters_for_reroute.get("servings"))

            # Check session-persisted filters too
            _has_session_cost = bool(session.filters.cost_filter)
            _has_session_time = bool(session.filters.time_filter)
            _has_session_nutrition = bool(session.filters.nutrition_filter)
            _has_session_servings = bool(session.filters.servings)

            # Check if there's semantic content that requires embedding search
            # (cuisines, ingredients, meal types, tags, etc.)
            # Tags like "dessert", "dinner", "breakfast" are semantic content that should trigger embedding
            _has_semantic_content = bool(
                _filters_for_reroute.get("cuisines")
                or _filters_for_reroute.get("tags")  # e.g., "dessert", "dinner", "breakfast"
                or _filters_for_reroute.get("included_ingredients")
                or _filters_for_reroute.get("include_ingredients")
                or _entities_for_reroute.get("cuisines")
                or _entities_for_reroute.get("ingredients")
                or _entities_for_reroute.get("meal_types")
                or _entities_for_reroute.get("recipe_name")
            )

            # Only route to _handle_filter_query if:
            # 1. There are cost/time/nutrition filters (need special SQL for metadata sorting)
            # 2. OR there are ONLY servings/exclusions with NO semantic content
            # Do NOT route if there's semantic content that needs embedding search
            _has_metadata_filter = (
                _has_cost_reroute or _has_time_reroute or _has_nutrition_reroute
                or _has_session_cost or _has_session_time or _has_session_nutrition
            )
            _has_structural_only = (
                (_has_servings_reroute or _has_session_servings
                 or _filters_for_reroute.get("excluded_ingredients")
                 or _filters_for_reroute.get("exclude_ingredients"))
                and not _has_semantic_content
            )

            if (
                nlid_result_dict["intent"] == "recipe_search"
                and (_has_metadata_filter or _has_structural_only)
                and not _has_semantic_content  # Don't route if there's semantic content
            ):
                logger.info(
                    f"[REROUTE] recipe_search has filter(s): "
                    f"cost={_has_cost_reroute or _has_session_cost}, "
                    f"time={_has_time_reroute or _has_session_time}, "
                    f"nutrition={_has_nutrition_reroute or _has_session_nutrition}, "
                    f"servings={_has_servings_reroute or _has_session_servings}. "
                    f"(from_nlid={_has_cost_reroute or _has_time_reroute or _has_nutrition_reroute or _has_servings_reroute}, "
                    f"from_session={_has_session_cost or _has_session_time or _has_session_nutrition or _has_session_servings}) "
                    f"semantic_content={_has_semantic_content}. "
                    f"Re-routing to _handle_filter_query for deterministic SQL."
                )
                return await self._handle_filter_query(
                    query, nlid_result_dict, session, user_uid, language
                )

            # Get user context
            session_context = self.session_manager.get_user_context(session, {})

            # CRITICAL: Ensure session context includes the latest search information
            # This is needed for proper refinement detection
            session_context["last_intent"] = session.last_intent
            session_context["context_entities"] = {
                "last_vector_query": session.context_entities.last_vector_query,
                "last_search_filters": session.context_entities.last_search_filters,
            }
            # Add language to session context for SQL filtering
            session_context["language"] = language or "en"

            # ============ STAGE 3: Retrieval Strategy Decision ============
            stage_start = time.time()
            retrieval_plan = self.strategy_decider.decide_strategy(
                query, nlid_result_dict, session_context
            )
            logger.info(
                f"[STAGE 3] Strategy={retrieval_plan.strategy.value} | "
                f"top_k={retrieval_plan.top_k} | "
                f"vector_query={retrieval_plan.vector_query[:50] if retrieval_plan.vector_query else 'None'}"
            )

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
                # Non-veg patterns (means vegetarian)
                "don't eat non veg": "vegetarian",
                "dont eat non veg": "vegetarian",
                "don't eat non-veg": "vegetarian",
                "dont eat non-veg": "vegetarian",
                "no non veg": "vegetarian",
                "no non-veg": "vegetarian",
                "non veg free": "vegetarian",
                "non-veg free": "vegetarian",
                "vegetarian only": "vegetarian",
                "veg only": "vegetarian",
                "no meat": "vegetarian",
                "meat free": "vegetarian",
            }

            for pattern, tag in dietary_patterns.items():
                if pattern in query_lower and tag not in mentioned_tags:
                    mentioned_tags.append(tag)

            # ============ TAG REMOVAL: Handle negative dietary preferences ============
            # When user says "no sugar free", "don't want gluten free", etc.,
            # remove those tags from session.filters.tags
            tag_removal_patterns = {
                "no sugar free": "sugar-free",
                "no sugar-free": "sugar-free",
                "not sugar free": "sugar-free",
                "not sugar-free": "sugar-free",
                "don't want sugar free": "sugar-free",
                "dont want sugar free": "sugar-free",
                "don't want sugar-free": "sugar-free",
                "dont want sugar-free": "sugar-free",
                "without sugar free": "sugar-free",
                "no gluten free": "gluten-free",
                "no gluten-free": "gluten-free",
                "not gluten free": "gluten-free",
                "not gluten-free": "gluten-free",
                "don't want gluten free": "gluten-free",
                "dont want gluten free": "gluten-free",
                "no dairy free": "dairy-free",
                "no dairy-free": "dairy-free",
                "not dairy free": "dairy-free",
                "don't want dairy free": "dairy-free",
                "no keto": "keto",
                "not keto": "keto",
                "don't want keto": "keto",
                "no vegan": "vegan",
                "not vegan": "vegan",
                "don't want vegan": "vegan",
                "no vegetarian": "vegetarian",
                "not vegetarian": "vegetarian",
                "don't want vegetarian": "vegetarian",
            }

            tags_to_remove = []
            for pattern, tag in tag_removal_patterns.items():
                if pattern in query_lower:
                    tags_to_remove.append(tag)
                    # Also remove from mentioned_tags if it was added
                    if tag in mentioned_tags:
                        mentioned_tags.remove(tag)

            # Remove tags from session
            if tags_to_remove:
                for tag in tags_to_remove:
                    if tag in session.filters.tags:
                        session.filters.tags.remove(tag)
                        logger.info(f"[TAG REMOVAL] Removed tag '{tag}' from session based on query pattern")
                self.session_manager.save_session(session)

            has_dietary_preference = bool(mentioned_tags)

            # Check if the query has any meaningful POSITIVE search context
            # (ingredients, meal types, cuisines, etc.) that should drive the embedding.
            # If positive context exists, the normal retrieval strategy should run so
            # the embedding targets the positive term and SQL handles exclusions.
            entities_nlid = nlid_result_dict.get("entities", {})
            # Build the set of items the user explicitly wants to EXCLUDE so we
            # can filter them out of the positive-entity check below.
            _excluded_set: set = set()
            for _item in (
                filters.get("excluded_ingredients") or []
            ) + (
                filters.get("exclude_ingredients") or []
            ):
                if isinstance(_item, str):
                    _excluded_set.add(_item.lower())
            # Only treat ingredients as positive context if they are NOT in the
            # exclusion list.  NLID sometimes puts "potato" in both
            # entities.ingredients AND filters.excluded_ingredients for queries
            # like "I dont like potatoes".  Without this filter,
            # has_positive_entities=True → is_special_case=False → the raw
            # negative query drives the embedding → potato recipes get surfaced
            # and immediately excluded → 0 results.
            _raw_ingredients = entities_nlid.get("ingredients") or []
            if isinstance(_raw_ingredients, str):
                _raw_ingredients = [_raw_ingredients]
            positive_ingredients = [
                ing for ing in _raw_ingredients
                if isinstance(ing, str) and ing.lower() not in _excluded_set
            ]
            has_positive_entities = bool(
                positive_ingredients
                or entities_nlid.get("cuisines")
                or entities_nlid.get("meal_types")
                or filters.get("cuisines")
                or filters.get("tags")
            )

            # Check if we should handle this as a special case:
            # 1. general_chat intent with dietary preference OR allergy
            # 2. recipe_search intent with ONLY allergies (no other search intent)
            #    NOTE: exclude when there are positive entities (dinner, italian, etc.)
            #    so those go through normal retrieval where embedding=positive_term,
            #    SQL=excluded_ingredients.
            is_special_case = (
                (nlid_result_dict["intent"] == "general_chat" and (has_dietary_preference or has_allergy)) or
                (nlid_result_dict["intent"] == "recipe_search" and has_allergy
                 and not has_positive_entities
                 and not filters.get("included_ingredients") and not filters.get("max_time") and not filters.get("difficulty"))
            )

            if is_special_case:
                    if mentioned_tags or excluded_ingredients:
                        logger.info(f"[SPECIAL CASE] dietary={mentioned_tags} | allergies={excluded_ingredients}")

                    # Check if this is a refinement (has previous recipe search)
                    last_vector_query = session_context.get("context_entities", {}).get("last_vector_query")
                    last_intent = session_context.get("last_intent")

                    if last_vector_query and last_intent == "recipe_search":
                        # This is a REFINEMENT - run NEW search with combined constraints
                        logger.info(f"[REFINEMENT] Refining previous search '{last_vector_query}' with dietary={mentioned_tags} allergies={excluded_ingredients}")

                        # Build comprehensive contextual query from conversation history
                        contextual_query = self._build_contextual_query_from_history(
                            session, excluded_ingredients, mentioned_tags
                        )

                        logger.info(f"[REFINEMENT] Using contextual query: {contextual_query}")

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

                        # ============ VEGETARIAN EXCLUSION ============
                        # If user mentioned vegetarian, add non-veg ingredients to exclusions
                        if "vegetarian" in mentioned_tags:
                            NON_VEGETARIAN_INGREDIENTS = [
                                # Eggs - ILIKE '%egg%' catches egg, eggs, egg white, egg yolk, etc.
                                "egg", "eggs",
                                # General meat - ILIKE '%meat%' catches meat, meats, minced meat, etc.
                                "meat", "meats",
                                # Poultry
                                "chicken", "chickens", "turkey", "duck", "ducks", "goose", "geese", "quail",
                                # Red meat
                                "beef", "pork", "lamb", "mutton", "goat", "veal", "venison",
                                # Processed meat
                                "bacon", "ham", "sausage", "sausages", "salami", "pepperoni", "lard",
                                "prosciutto", "chorizo", "hotdog", "hot dog", "hot dogs",
                                # Fish & seafood
                                "fish", "fishes", "seafood", "shellfish", "prawn", "prawns",
                                "shrimp", "shrimps", "crab", "crabs", "lobster", "lobsters", "oyster", "oysters",
                                "mussel", "mussels", "scallop", "scallops", "clam", "clams", "anchovy", "anchovies",
                                "tuna", "salmon", "cod", "halibut", "tilapia", "trout", "trouts",
                                "sardine", "sardines", "mackerel", "herring", "catfish",
                                # Animal-derived fats/stock
                                "gelatin", "bone broth", "chicken broth", "beef broth",
                                "chicken stock", "beef stock", "fish sauce", "anchovy paste",
                            ]
                            for non_veg in NON_VEGETARIAN_INGREDIENTS:
                                if non_veg not in excluded_ingredients:
                                    excluded_ingredients.append(non_veg)
                            logger.info(f"[VEGETARIAN REFINEMENT] Added {len(NON_VEGETARIAN_INGREDIENTS)} non-veg ingredients to exclusions")

                        # Add allergies to excluded_ingredients if any
                        # Use SMART ingredient expansion (no LLM calls for specific ingredients)
                        expanded_allergens = excluded_ingredients  # Default to original list
                        if excluded_ingredients:
                            try:
                                # Initialize ingredient matcher
                                ingredient_matcher = IntelligentIngredientMatcher(self.db, self.client)
                                # SMART expansion: categories -> full expansion, specific -> singular/plural only
                                # e.g., "nuts" -> ["walnut", "almond", ...] but "garlic" -> ["garlic"]
                                #       "tomato" -> ["tomato", "tomatoes"] (NOT 30+ varieties)
                                expanded_allergens = ingredient_matcher.smart_expand_for_exclusions(excluded_ingredients)
                                logger.info(f"[ALLERGY] Expanded allergens: {excluded_ingredients} -> {expanded_allergens}")
                            except Exception as e:
                                logger.warning(f"[ALLERGY] Failed to expand allergens: {e}, using original list")
                                expanded_allergens = excluded_ingredients

                            if "excluded_ingredients" not in nlid_result_dict["filters"]:
                                nlid_result_dict["filters"]["excluded_ingredients"] = []
                            # Use expanded allergens instead of original
                            current_excluded = set(nlid_result_dict["filters"]["excluded_ingredients"])
                            new_excluded = [ing for ing in expanded_allergens if ing not in current_excluded]
                            nlid_result_dict["filters"]["excluded_ingredients"].extend(new_excluded)

                            # Also save to session.excluded_ingredients for backward compatibility
                            for allergen in expanded_allergens:
                                if allergen not in session.excluded_ingredients:
                                    session.excluded_ingredients.append(allergen)

                        # Create a retrieval plan that preserves the original embedding search but adds allergy filters
                        # The vector query should remain the original search (for orange recipes)
                        # But we need to add the allergy exclusion filter
                        # IMPORTANT: Increase top_k when allergies are present to get more candidates before filtering
                        original_vector_query = session_context.get("context_entities", {}).get("last_vector_query")

                        # Use the original vector query if available, otherwise fallback to a sensible default
                        if not original_vector_query:
                            # If no previous vector query, extract the main ingredient from context or create a default
                            if session.included_ingredients:
                                original_vector_query = " ".join(session.included_ingredients) + " recipes"
                            else:
                                # Fallback to common ingredient patterns from the original search
                                original_vector_query = "recipes"  # Generic fallback

                        # Merge current expanded allergens with ALL previously stored allergens
                        # (e.g., garlic was stored in session.excluded_ingredients from Q2,
                        # tomato is new for Q3 — we need both in the SQL filter)
                        # Also include any exclusions already in retrieval_plan.sql_filters
                        # (e.g., vegetarian exclusions added by retrieval_strategy.py)
                        existing_exclusions = session.excluded_ingredients or []
                        retrieval_plan_exclusions = (
                            retrieval_plan.sql_filters.get("excluded_ingredients", [])
                            if retrieval_plan and retrieval_plan.sql_filters
                            else []
                        )
                        merged_exclusions = list(dict.fromkeys(
                            existing_exclusions
                            + retrieval_plan_exclusions
                            + [a for a in expanded_allergens
                               if a not in existing_exclusions and a not in retrieval_plan_exclusions]
                        ))
                        logger.info(
                            f"[REFINEMENT] Merged exclusions: session={len(existing_exclusions)}, "
                            f"retrieval_plan={len(retrieval_plan_exclusions)}, "
                            f"new={len(expanded_allergens)}, total={len(merged_exclusions)}"
                        )
                        # Also update session so future turns carry everything forward
                        session.excluded_ingredients = merged_exclusions

                        # Check if the original vector query IS one of the excluded ingredients.
                        # This happens when session memory extracted the ingredient name from the
                        # current "I dont like X" query as the last_vector_query — meaning there is
                        # no real positive prior context, only the thing being excluded.
                        # In that case skip embedding entirely and use SQL_ONLY.
                        expanded_lower = {e.lower() for e in merged_exclusions}
                        original_lower = (original_vector_query or "").lower().strip()
                        vector_is_excluded = (
                            original_lower in expanded_lower
                            or any(original_lower == e.lower() for e in excluded_ingredients)
                            or all(
                                word in expanded_lower or word in {e.lower() for e in excluded_ingredients}
                                for word in original_lower.split()
                                if len(word) > 2
                            )
                        )

                        # Filter session.included_ingredients against the full expanded
                        # exclusion set.  NLID can return the disliked ingredient in
                        # both entities.ingredients AND filters.excluded_ingredients,
                        # which would otherwise make has_positive_context=True and
                        # force an unwanted HYBRID plan for a pure exclusion query.
                        _all_excluded_lower = expanded_lower | {
                            e.lower() for e in excluded_ingredients
                        }
                        _positive_included = [
                            ing for ing in session.included_ingredients
                            if ing.lower() not in _all_excluded_lower
                        ]
                        has_positive_context = bool(
                            mentioned_tags
                            or session.filters.max_time
                            or session.filters.difficulty
                            or _positive_included
                        )

                        if vector_is_excluded and not has_positive_context:
                            # The only "context" is the ingredient being excluded — use SQL_ONLY
                            logger.info(
                                f"[REFINEMENT] original_vector_query '{original_vector_query}' is the excluded "
                                f"ingredient — routing to SQL_ONLY to avoid zero-result embedding bias"
                            )
                            # Merge session filters (cuisines, tags, etc.) with retrieval_plan filters
                            _merged_sql_filters = {
                                **{k: v for k, v in retrieval_plan.sql_filters.items()
                                   if k != "excluded_ingredients"},
                                "excluded_ingredients": merged_exclusions,
                            }
                            # Also include session cuisines if not already present
                            if session.filters.cuisines and "cuisines" not in _merged_sql_filters:
                                _merged_sql_filters["cuisines"] = session.filters.cuisines
                            retrieval_plan = RetrievalPlan(
                                strategy=RetrievalStrategy.SQL_ONLY,
                                reasoning="Exclusion-only refinement: original vector query IS the excluded ingredient",
                                vector_query=None,
                                sql_filters=_merged_sql_filters,
                                top_k=100
                            )
                        else:
                            # Increase top_k when allergies present to handle semantic dominance
                            # e.g., "dessert recipes" + "allergic to chocolate" -> chocolate recipes dominate top 20
                            top_k = 150 if merged_exclusions else 100
                            logger.info(f"[ALLERGY] Using top_k={top_k} for hybrid refinement with allergens")

                            # Merge session filters (cuisines, tags, etc.) with retrieval_plan filters
                            _merged_sql_filters = {
                                **{k: v for k, v in retrieval_plan.sql_filters.items()
                                   if k != "excluded_ingredients"},
                                "excluded_ingredients": merged_exclusions,
                            }
                            # Also include session cuisines if not already present
                            if session.filters.cuisines and "cuisines" not in _merged_sql_filters:
                                _merged_sql_filters["cuisines"] = session.filters.cuisines
                            retrieval_plan = RetrievalPlan(
                                strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                                reasoning="Refinement search - preserving original search with allergy exclusion",
                                vector_query=original_vector_query,
                                sql_filters=_merged_sql_filters,
                                top_k=top_k
                            )

                        # Debug: log retrieval plan contents
                        logger.info(f"[REFINEMENT] Plan: {retrieval_plan.strategy.value} | vector_query={retrieval_plan.vector_query}")
                    else:
                        # Standalone preference - search for recipes with this preference

                        # Save to session object's filters for future queries
                        # This persists across turns
                        for tag in mentioned_tags:
                            if tag not in session.filters.tags:
                                session.filters.tags.append(tag)

                        # ============ VEGETARIAN EXCLUSION ============
                        # If user mentioned vegetarian, add non-veg ingredients to exclusions
                        if "vegetarian" in mentioned_tags:
                            NON_VEGETARIAN_INGREDIENTS = [
                                # Eggs - ILIKE '%egg%' catches egg, eggs, egg white, egg yolk, etc.
                                "egg", "eggs",
                                # General meat - ILIKE '%meat%' catches meat, meats, minced meat, etc.
                                "meat", "meats",
                                # Poultry
                                "chicken", "chickens", "turkey", "duck", "ducks", "goose", "geese", "quail",
                                # Red meat
                                "beef", "pork", "lamb", "mutton", "goat", "veal", "venison",
                                # Processed meat
                                "bacon", "ham", "sausage", "sausages", "salami", "pepperoni", "lard",
                                "prosciutto", "chorizo", "hotdog", "hot dog", "hot dogs",
                                # Fish & seafood
                                "fish", "fishes", "seafood", "shellfish", "prawn", "prawns",
                                "shrimp", "shrimps", "crab", "crabs", "lobster", "lobsters", "oyster", "oysters",
                                "mussel", "mussels", "scallop", "scallops", "clam", "clams", "anchovy", "anchovies",
                                "tuna", "salmon", "cod", "halibut", "tilapia", "trout", "trouts",
                                "sardine", "sardines", "mackerel", "herring", "catfish",
                                # Animal-derived fats/stock
                                "gelatin", "bone broth", "chicken broth", "beef broth",
                                "chicken stock", "beef stock", "fish sauce", "anchovy paste",
                            ]
                            for non_veg in NON_VEGETARIAN_INGREDIENTS:
                                if non_veg not in excluded_ingredients:
                                    excluded_ingredients.append(non_veg)
                            logger.info(f"[VEGETARIAN] Added {len(NON_VEGETARIAN_INGREDIENTS)} non-veg ingredients to exclusions")

                        # Use SMART ingredient expansion (no LLM calls for specific ingredients)
                        expanded_allergens = excluded_ingredients  # Default to original list
                        if excluded_ingredients:
                            try:
                                # Initialize ingredient matcher
                                ingredient_matcher = IntelligentIngredientMatcher(self.db, self.client)
                                # SMART expansion: categories -> full expansion, specific -> singular/plural only
                                expanded_allergens = ingredient_matcher.smart_expand_for_exclusions(excluded_ingredients)
                                logger.info(f"[ALLERGY] Expanded allergens: {excluded_ingredients} -> {expanded_allergens}")

                                # Store expanded allergens in session for persistence
                                for original_allergen in excluded_ingredients:
                                    session = self.session_manager.add_allergy(session, original_allergen, expanded_allergens)
                            except Exception as e:
                                logger.warning(f"[ALLERGY] Failed to expand allergens: {e}, using original list")
                                expanded_allergens = excluded_ingredients

                            # Also save to session.excluded_ingredients for backward compatibility
                            for allergen in expanded_allergens:
                                if allergen not in session.excluded_ingredients:
                                    session.excluded_ingredients.append(allergen)

                        # Rebuild session_context to include the updated filters
                        session_context = self.session_manager.get_user_context(session, {})
                        session_context["language"] = language or "en"

                        logger.info(f"[PREFERENCE] Saved: tags={session.filters.tags} | allergies={session.excluded_ingredients}")

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

                        # Add allergies if any - use expanded allergens
                        if expanded_allergens:
                            if "excluded_ingredients" not in nlid_result_dict["filters"]:
                                nlid_result_dict["filters"]["excluded_ingredients"] = []
                            nlid_result_dict["filters"]["excluded_ingredients"].extend(expanded_allergens)

                        # Update the query to search for recipes with this preference.
                        # IMPORTANT: Only put POSITIVE terms (dietary tags) in the
                        # vector query.  Exclusions must go into SQL filters only —
                        # adding them to the embedding query causes undesirable
                        # semantic pull toward the excluded item (e.g., searching
                        # "without chicken recipes" still surfaces chicken recipes).
                        constraint_parts = []
                        if mentioned_tags:
                            constraint_parts.extend(mentioned_tags)

                        if constraint_parts:
                            dietary_query = f"{' '.join(constraint_parts)} recipes"
                        else:
                            dietary_query = "recipes"

                        # Create a retrieval plan for standalone preferences.
                        # KEY DECISION: if the user expressed ONLY exclusions with no
                        # positive dietary tag / difficulty / time constraint, skip
                        # embedding entirely.  Running HYBRID with a generic
                        # "recipes" vector query can return recipes containing the
                        # excluded item (e.g. potato recipes for "I dont like potatoes")
                        # because that item dominates the embedding space near "recipes".
                        # SQL_ONLY on the full recipe table with the exclusion filter is
                        # the correct approach for pure exclusion queries.
                        has_positive_constraints = bool(
                            mentioned_tags
                            or filters.get("max_time")
                            or filters.get("difficulty")
                            or filters.get("included_ingredients")
                        )
                        # Merge new expanded allergens with ALL previously
                        # stored session exclusions so multi-turn exclusions
                        # accumulate correctly (e.g. Q2: "no egg" + Q3: "no potato").
                        # Also include any exclusions already in retrieval_plan.sql_filters
                        # (e.g., vegetarian exclusions added by retrieval_strategy.py)
                        existing_exclusions = session.excluded_ingredients or []
                        retrieval_plan_exclusions = (
                            retrieval_plan.sql_filters.get("excluded_ingredients", [])
                            if retrieval_plan and retrieval_plan.sql_filters
                            else []
                        )
                        merged_exclusions = list(dict.fromkeys(
                            existing_exclusions
                            + retrieval_plan_exclusions
                            + [a for a in expanded_allergens
                               if a not in existing_exclusions and a not in retrieval_plan_exclusions]
                        ))
                        session.excluded_ingredients = merged_exclusions
                        self.session_manager.save_session(session)

                        # Preserve other filters from the original retrieval_plan.sql_filters
                        # (e.g., max_time, difficulty, cuisines, etc.)
                        # Also include session filters that may have been set in previous turns
                        preserved_filters = {
                            k: v for k, v in (retrieval_plan.sql_filters or {}).items()
                            if k not in ("excluded_ingredients", "exclude_ingredients", "tags")
                        }
                        # Include session cuisines if not already in preserved_filters
                        if session.filters.cuisines and "cuisines" not in preserved_filters:
                            preserved_filters["cuisines"] = session.filters.cuisines
                        logger.info(
                            f"[STANDALONE] Merged exclusions: session={len(existing_exclusions)}, "
                            f"retrieval_plan={len(retrieval_plan_exclusions)}, "
                            f"new={len(expanded_allergens)}, total={len(merged_exclusions)}"
                        )

                        top_k = 150 if merged_exclusions else 100
                        if expanded_allergens and not has_positive_constraints:
                            logger.info(f"[EXCLUSION-ONLY] SQL_ONLY routing for standalone exclusion query")
                            retrieval_plan = RetrievalPlan(
                                strategy=RetrievalStrategy.SQL_ONLY,
                                reasoning="Standalone exclusion-only query – SQL filters full recipe table without embedding bias",
                                vector_query=None,
                                sql_filters={
                                    **preserved_filters,
                                    "excluded_ingredients": merged_exclusions,
                                },
                                top_k=top_k
                            )
                        else:
                            retrieval_plan = RetrievalPlan(
                                strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                                reasoning="Standalone preference search with dietary constraints",
                                vector_query=dietary_query,
                                sql_filters={
                                    **preserved_filters,
                                    "tags": mentioned_tags,
                                    "excluded_ingredients": merged_exclusions,
                                },
                                top_k=top_k
                            )

                        # Continue with normal recipe search flow

            # ============ ALLERGEN EXPANSION FOR NORMAL RECIPE SEARCH ============
            # If this is a normal recipe search with excluded_ingredients (not special case),
            # we need to expand the allergens for better filtering using SMART expansion
            # Support both key variants: "excluded_ingredients" and "exclude_ingredients"
            raw_allergens = filters.get("excluded_ingredients") or filters.get("exclude_ingredients")
            if not is_special_case and raw_allergens:

                try:
                    # Initialize ingredient matcher
                    ingredient_matcher = IntelligentIngredientMatcher(self.db, self.client)
                    # SMART expansion: categories -> full expansion, specific -> singular/plural only
                    new_expanded_allergens = ingredient_matcher.smart_expand_for_exclusions(raw_allergens)
                    logger.info(f"[ALLERGY] Expanded allergens: {raw_allergens} -> {new_expanded_allergens}")

                    # Store new allergens in session for persistence
                    for original_allergen in raw_allergens:
                        session = self.session_manager.add_allergy(session, original_allergen, new_expanded_allergens)

                    # Merge with existing session exclusions AND any exclusions already in retrieval_plan
                    # (e.g., vegetarian exclusions added by retrieval_strategy.py)
                    existing_exclusions = session.excluded_ingredients or []
                    retrieval_plan_exclusions = (
                        retrieval_plan.sql_filters.get("excluded_ingredients", [])
                        if retrieval_plan and retrieval_plan.sql_filters
                        else []
                    )
                    all_exclusions = list(dict.fromkeys(
                        existing_exclusions
                        + retrieval_plan_exclusions
                        + [a for a in new_expanded_allergens
                           if a not in existing_exclusions and a not in retrieval_plan_exclusions]
                    ))
                    logger.info(
                        f"[ALLERGY] Merged exclusions: session={len(existing_exclusions)}, "
                        f"retrieval_plan={len(retrieval_plan_exclusions)}, "
                        f"new={len(new_expanded_allergens)}, total={len(all_exclusions)}"
                    )

                    # Update session's excluded_ingredients with merged list
                    session.excluded_ingredients = all_exclusions
                    self.session_manager.save_session(session)

                    # Update the NLID result with ALL exclusions (existing + new)
                    nlid_result_dict["filters"]["excluded_ingredients"] = all_exclusions

                    # Also update retrieval_plan if it has sql_filters
                    if retrieval_plan.sql_filters and "excluded_ingredients" in retrieval_plan.sql_filters:
                        retrieval_plan.sql_filters["excluded_ingredients"] = all_exclusions

                    # Increase top_k to handle semantic dominance of allergens in embedding results
                    if retrieval_plan.top_k < 150:
                        logger.debug(f"[ALLERGY] Increasing top_k from {retrieval_plan.top_k} to 150 for allergen filtering")
                        retrieval_plan = RetrievalPlan(
                            strategy=retrieval_plan.strategy,
                            reasoning=retrieval_plan.reasoning + " (allergens detected, increased top_k)",
                            vector_query=retrieval_plan.vector_query,
                            sql_filters=retrieval_plan.sql_filters,
                            top_k=150
                        )
                except Exception as e:
                    logger.warning(f"[ALLERGY] Failed to expand allergens in normal search: {e}")

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

            # ---- Cache-aware strategy upgrade / reuse ----
            # Check if we have cached embedding candidates from a previous search.
            # This applies to BOTH SQL_ONLY (upgrade to HYBRID) AND HYBRID_VECTOR_TO_SQL
            # (reuse cached candidates for refinements like "I am allergic to chicken").
            _search_cache = self.session_manager.get_search_cache(session)
            _cached_candidates = _search_cache.get("embedding_candidates", [])
            _cached_vector_query = _search_cache.get("vector_query")
            _cache_created_at = _search_cache.get("created_at")
            _cache_is_valid = (
                _cached_candidates
                and _cached_vector_query
                and _cache_created_at is not None
                and (time.time() - _cache_created_at) < 1800  # SEARCH_CACHE_TTL_SECONDS
            )

            if (
                retrieval_plan.strategy == RetrievalStrategy.SQL_ONLY
                and _cache_is_valid
            ):
                # SQL_ONLY with cached candidates → upgrade to HYBRID
                logger.info(
                    f"[STRATEGY UPGRADE] SQL_ONLY → HYBRID_VECTOR_TO_SQL: "
                    f"reusing {len(_cached_candidates)} cached candidates "
                    f"from '{_cached_vector_query}'"
                )
                candidate_ids = _cached_candidates
                similarity_scores = {}
                retrieval_plan = RetrievalPlan(
                    strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                    reasoning=(
                        retrieval_plan.reasoning
                        + " (upgraded: cached embedding candidates available)"
                    ),
                    vector_query=_cached_vector_query,
                    sql_filters=retrieval_plan.sql_filters,
                    top_k=retrieval_plan.top_k,
                )
            elif (
                retrieval_plan.strategy == RetrievalStrategy.HYBRID_VECTOR_TO_SQL
                and _cache_is_valid
                and retrieval_plan.vector_query == _cached_vector_query
            ):
                # HYBRID with same vector query → reuse cached candidates (no re-embedding)
                logger.info(
                    f"[CACHE REUSE] HYBRID_VECTOR_TO_SQL: "
                    f"reusing {len(_cached_candidates)} cached candidates "
                    f"from '{_cached_vector_query}' (no re-embedding needed)"
                )
                candidate_ids = _cached_candidates
                similarity_scores = {}

            if retrieval_plan.strategy == RetrievalStrategy.SQL_ONLY:
                # FILTER-ONLY queries: Skip embedding search, use deterministic
                # SQL builder instead of the LLM to avoid broken SQL.
                relevant_schema = self.schema_understanding.get_relevant_schema(
                    nlid_result_dict["intent"],
                    retrieval_plan.sql_filters,
                    session_context
                )

                # Build SQL deterministically using session filters
                _sf = retrieval_plan.sql_filters or {}
                _session_filters_dict: Dict[str, Any] = {
                    "excluded_ingredients": _sf.get(
                        "excluded_ingredients", []
                    ),
                    "included_ingredients": _sf.get(
                        "included_ingredients", []
                    ),
                    "tags": _sf.get("tags", []),
                    "cuisines": _sf.get("cuisines", []),
                    "difficulty": _sf.get("difficulty"),
                    "max_time": _sf.get("max_time"),
                    "servings": _sf.get("servings"),  # Servings filter
                    "ingredient_count": _sf.get("ingredient_count"),  # Ingredient count filter
                    "creator_uid": _sf.get("creator_uid"),  # Creator filter
                    "excluded_recipe_ids": (
                        session.excluded_recipe_ids or []
                    ),
                }
                _additional = build_session_filter_conditions(
                    _session_filters_dict
                )

                # Pick the right builder based on persisted session
                # filters (time / cost / nutrition) so multi-turn
                # context is preserved (e.g. Q1: "quick" → time ASC
                # carried into Q2: "no eggs").
                _time_f = session.filters.time_filter
                _cost_f = session.filters.cost_filter
                _nutr_f = session.filters.nutrition_filter

                _sql_limit = retrieval_plan.top_k or 100
                if _time_f:
                    _direct_sql = build_recipe_time_filter_sql(
                        time_filter=_time_f,
                        user_uid=user_uid or "",
                        language=language or "en",
                        additional_conditions=_additional,
                        limit=_sql_limit,
                    )
                elif _cost_f:
                    _direct_sql = build_recipe_cost_filter_sql(
                        cost_filter=_cost_f,
                        user_uid=user_uid or "",
                        language=language or "en",
                        additional_conditions=_additional,
                        limit=_sql_limit,
                    )
                elif _nutr_f:
                    _direct_sql = build_recipe_nutrition_filter_sql(
                        nutrition_filter=_nutr_f,
                        user_uid=user_uid or "",
                        language=language or "en",
                        additional_conditions=_additional,
                        limit=_sql_limit,
                    )
                else:
                    # No session filter — plain base query without specific sort order.
                    # Used for structural-only queries (like servings, ingredients).
                    _direct_sql = build_recipe_base_sql(
                        user_uid=user_uid or "",
                        language=language or "en",
                        additional_conditions=_additional,
                        limit=_sql_limit,
                    )

                from apps.fastapi.src.services.sql_generator import (
                    SQLGenerationResult,
                )
                sql_result = SQLGenerationResult(
                    sql=_direct_sql,
                    explanation="Direct SQL builder (no LLM)",
                    params={},
                    estimated_rows=20,
                    is_safe=True,
                )
                logger.info(
                    f"[SQL_ONLY] ✓ Direct SQL built with persisted "
                    f"filters in {time.time() - parallel2_start:.3f}s"
                )

            elif retrieval_plan.strategy == RetrievalStrategy.HYBRID_VECTOR_TO_SQL:
                # SEQUENTIAL for hybrid: Embedding first, then SQL with candidate_ids

                # Step 1: Run embedding search (skip if candidates already loaded from cache)
                if candidate_ids:
                    logger.info(
                        f"[HYBRID] Skipping embedding search — using "
                        f"{len(candidate_ids)} pre-loaded cached candidates"
                    )
                else:
                    embedding_limit = max(retrieval_plan.top_k, 10)
                    # If the search is scoped to a specific creator, filter the embedding
                    # candidates to that creator's recipes only — avoids wasting candidate
                    # slots on other users' recipes and ensures the SQL creator filter
                    # always has enough candidates to work with.
                    embedding_creator_uid = (
                        retrieval_plan.sql_filters.get("creator_uid")
                        if retrieval_plan.sql_filters else None
                    )
                    embedding_results = search_recipes_by_embedding(
                        self.db,
                        query_text=retrieval_plan.vector_query or query,
                        limit=embedding_limit,
                        threshold=0.4,
                        language_id=language,
                        creator_uid=embedding_creator_uid
                    )
                    candidate_ids = [str(r.id) for r, _ in embedding_results]
                    similarity_scores = {str(r.id): s for r, s in embedding_results}
                    logger.info(f"[HYBRID] ✓ Embedding search: {len(embedding_results)} candidates")

                # If embedding returned very few results but we have structural SQL filters
                # (tags, cuisines, difficulty, etc.), drop the candidate restriction so SQL
                # can search across all recipes via those filters instead of being locked
                # to a tiny embedding result set.
                MIN_CANDIDATES_FOR_RESTRICTION = 3
                structural_filters = {
                    k: v for k, v in (retrieval_plan.sql_filters or {}).items()
                    if k not in ("excluded_ingredients", "exclude_ingredients")
                }
                if len(candidate_ids) < MIN_CANDIDATES_FOR_RESTRICTION and structural_filters:
                    logger.info(
                        f"[HYBRID] Only {len(candidate_ids)} embedding candidates but "
                        f"structural SQL filters {list(structural_filters.keys())} exist. "
                        f"Dropping candidate restriction – falling back to SQL-only search."
                    )
                    candidate_ids = []

                # Initialize search cache for "show more" feature
                # Store embedding candidates and search context
                if candidate_ids:
                    from apps.fastapi.src.services.session_memory_manager import EMBEDDING_BATCH_SIZE
                    self.session_manager.init_search_cache(
                        session,
                        vector_query=retrieval_plan.vector_query or query,
                        sql_filters=retrieval_plan.sql_filters or {},
                        original_intent=nlid_result_dict.get("intent")
                    )
                    self.session_manager.store_embedding_candidates(
                        session,
                        candidate_ids,
                        offset=0  # Initial search starts at offset 0
                    )

                # Step 2: Schema fetch (cached, fast)
                relevant_schema = self.schema_understanding.get_relevant_schema(
                    nlid_result_dict["intent"],
                    retrieval_plan.sql_filters,
                    session_context
                )

                # Step 3: SQL generation WITH candidate_ids (filters the embedding candidates)
                sql_result = await self.sql_generator.generate_sql(
                    query,
                    nlid_result_dict,
                    retrieval_plan.sql_filters,
                    session_context,
                    candidate_ids  # Pass candidate_ids so SQL only searches within them
                )
                logger.info(f"[HYBRID] ✓ Embedding + SQL completed in {time.time() - parallel2_start:.3f}s")

            else:
                # PARALLEL for non-hybrid strategies

                async def run_embedding_search():
                    """Run embedding search if needed"""
                    if retrieval_plan.strategy not in ["embeddings_only", "hybrid_vector_to_sql"]:
                        return None, {}

                    embedding_limit = max(retrieval_plan.top_k, 10)
                    _emb_creator_uid = (
                        retrieval_plan.sql_filters.get("creator_uid")
                        if retrieval_plan.sql_filters else None
                    )
                    embedding_results = search_recipes_by_embedding(
                        self.db,
                        query_text=retrieval_plan.vector_query or query,
                        limit=embedding_limit,
                        threshold=0.4,
                        language_id=language,
                        creator_uid=_emb_creator_uid
                    )
                    cand_ids = [str(r.id) for r, _ in embedding_results]
                    sim_scores = {str(r.id): s for r, s in embedding_results}
                    return cand_ids, sim_scores

                async def run_schema_and_sql():
                    """Run schema fetch and SQL generation in parallel"""

                    # Schema fetch (cached, very fast)
                    rel_schema = self.schema_understanding.get_relevant_schema(
                        nlid_result_dict["intent"],
                        retrieval_plan.sql_filters,
                        session_context
                    )

                    # SQL generation (expensive, ~3s)
                    # For non-hybrid queries, pass None for candidate_ids
                    gen_sql_result = await self.sql_generator.generate_sql(
                        query,
                        nlid_result_dict,
                        retrieval_plan.sql_filters,
                        session_context,
                        None
                    )

                    logger.debug(f"[PARALLEL] Schema + SQL done")
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
                    logger.info(f"[STAGE 4] ✓ Found {len(candidate_ids)} candidates via embeddings")

                    # Initialize search cache for "show more" feature (parallel path)
                    if candidate_ids:
                        self.session_manager.init_search_cache(
                            session,
                            vector_query=retrieval_plan.vector_query or query,
                            sql_filters=retrieval_plan.sql_filters or {},
                            original_intent=nlid_result_dict.get("intent")
                        )
                        self.session_manager.store_embedding_candidates(
                            session,
                            candidate_ids,
                            offset=0
                        )
                else:
                    candidate_ids = None
                    similarity_scores = {}

                # Handle schema + SQL results
                if not isinstance(schema_sql_result, Exception):
                    relevant_schema, sql_result = schema_sql_result
                    logger.info(f"[STAGE 5+6] ✓ Schema + SQL generated in parallel in {time.time() - parallel2_start:.3f}s")
                else:
                    logger.error(f"[STAGE 5+6] Error in parallel execution: {schema_sql_result}")
                    raise schema_sql_result

            if not sql_result.is_safe:
                logger.warning(f"[STAGE 6] SQL validation FAILED | estimated_rows={sql_result.estimated_rows}")

            # ============ STAGE 8: SQL Execution ============
            stage_start = time.time()
            logger.info(f"[STAGE 8] SQL Query:\n{sql_result.sql}")

            # Prepare parameters for SQL execution
            params = {
                "user_uid": user_uid,
                "language_id": language or "en"
            }

            execution_result = self.sql_executor.execute_sql(sql_result.sql, params)

            logger.info(f"[STAGE 8] ✓ Execution: success={execution_result['success']} | rows={execution_result.get('row_count', 0)} | time={time.time() - stage_start:.3f}s")

            # CRITICAL: Always save search context for multi-turn refinement, even on SQL failure
            # This allows users to refine queries like "I am allergic to tomato" after a failed search
            if retrieval_plan.vector_query and nlid_result_dict["intent"] == "recipe_search":
                self.session_manager.update_search_context(
                    session,
                    query,
                    retrieval_plan.vector_query,
                    retrieval_plan.sql_filters,
                    nlid_result_dict["intent"]
                )
                logger.info(f"[STAGE 8] Saved search context: vector_query='{retrieval_plan.vector_query}'")

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

            # ============ RE-EMBED FALLBACK ============
            # When the primary query returns too few results (e.g. 100 dessert
            # candidates but none with servings=3), re-run the embedding search
            # excluding the candidates we already tried.  This fetches the
            # *next* batch of semantically similar recipes and applies the same
            # SQL filters on them — preserving both semantic relevance and user
            # constraints instead of dropping either.
            relaxed_filters: Dict[str, Any] = {}   # tracks what was relaxed for NLG
            row_count = execution_result.get("row_count", 0)

            if row_count < self.FALLBACK_THRESHOLD and execution_result["success"] and candidate_ids:
                # Get all previously shown recipes from search cache to exclude them
                cache = self.session_manager.get_search_cache(session)
                shown_ids = set(cache.get("shown_recipe_ids", []))
                # Combine current candidates with shown recipes for exclusion
                all_exclude_ids = list(set(candidate_ids) | shown_ids)

                logger.info(
                    f"[FALLBACK] Only {row_count} results (threshold={self.FALLBACK_THRESHOLD}). "
                    f"Re-embedding with {len(all_exclude_ids)} excluded IDs "
                    f"(candidates={len(candidate_ids)}, shown={len(shown_ids)})."
                )

                embedding_limit = max(retrieval_plan.top_k, 10)
                embedding_creator_uid = (
                    retrieval_plan.sql_filters.get("creator_uid")
                    if retrieval_plan.sql_filters else None
                )
                fallback_vector_query = retrieval_plan.vector_query or query

                fallback_embedding_results = search_recipes_by_embedding(
                    self.db,
                    query_text=fallback_vector_query,
                    limit=embedding_limit,
                    threshold=0.4,
                    language_id=language,
                    creator_uid=embedding_creator_uid,
                    exclude_ids=all_exclude_ids  # skip all previously tried/shown recipes
                )
                fallback_candidate_ids = [
                    str(r.id) for r, _ in fallback_embedding_results
                ]
                logger.info(
                    f"[FALLBACK] Re-embedding returned {len(fallback_candidate_ids)} "
                    f"new candidates (excluded {len(all_exclude_ids)} total)"
                )

                if fallback_candidate_ids:
                    # Merge similarity scores
                    for r, s in fallback_embedding_results:
                        similarity_scores[str(r.id)] = s

                    # Re-run SQL with the new candidate set
                    fallback_sql = await self.sql_generator.generate_sql(
                        query,
                        nlid_result_dict,
                        retrieval_plan.sql_filters,
                        session_context,
                        fallback_candidate_ids
                    )
                    fallback_result = self.sql_executor.execute_sql(
                        fallback_sql.sql, params
                    )
                    fallback_count = fallback_result.get("row_count", 0)
                    logger.info(f"[FALLBACK] Re-embed SQL result: {fallback_count} rows")

                    if fallback_result["success"] and fallback_count > row_count:
                        execution_result = fallback_result
                        sql_result = fallback_sql
                        candidate_ids = fallback_candidate_ids
                        relaxed_filters["re_embedded"] = True
                        logger.info(
                            f"[FALLBACK] ✓ Using re-embedded results: "
                            f"{fallback_count} rows"
                        )

                        # Merge new candidates with existing cache candidates
                        # This ensures we have a larger pool for future filtering
                        cache = self.session_manager.get_search_cache(session)
                        existing_candidates = cache.get("embedding_candidates", [])
                        merged_candidates = list(set(existing_candidates + fallback_candidate_ids))
                        self.session_manager.store_embedding_candidates(
                            session, merged_candidates, offset=len(merged_candidates)
                        )
                        logger.info(
                            f"[FALLBACK] Merged candidates: {len(existing_candidates)} + {len(fallback_candidate_ids)} = {len(merged_candidates)}"
                        )
                    else:
                        logger.info(
                            f"[FALLBACK] Re-embed did not improve results "
                            f"({fallback_count} vs {row_count}). Keeping original."
                        )

            # ============ STAGE 9+10: PARALLEL PHASE 3 - Post-Processing + NLG ============
            # Run post-processing and NLG in parallel
            # Post-processing is fast (~0.5s), NLG is slow (~4s)
            # We start NLG with basic SQL results while post-processing adds details
            parallel3_start = time.time()

            async def run_post_processing():
                """Run post-processing and ranking"""
                processed_recipes = self._post_process_and_rank(
                    execution_result["rows"],
                    similarity_scores,
                    session_context,
                    retrieval_plan
                )
                final_recipes = processed_recipes[:self.MAX_RECIPES]
                return final_recipes

            async def run_nlg():
                """Run NLG with the SQL results directly"""
                # Use SDK NLG agent for natural language responses
                # We pass the SQL results directly; NLG will format them
                nlg_filter_ctx = self._build_nlg_filter_context(session)

                # If filters were relaxed during waterfall fallback, inform NLG
                if relaxed_filters and nlg_filter_ctx is not None:
                    nlg_filter_ctx["relaxed_filters"] = relaxed_filters
                elif relaxed_filters:
                    nlg_filter_ctx = {"relaxed_filters": relaxed_filters}

                response = await self._generate_natural_language_response(
                    query,
                    execution_result["rows"][:self.MAX_RECIPES],  # Use SQL results directly
                    nlid_result_dict,
                    current_nlg_agent,  # Pass the custom agent
                    filter_context=nlg_filter_ctx
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

            logger.info(f"[STAGE 9+10] ✓ Post-processing + NLG in {time.time() - parallel3_start:.3f}s")

            # Save to session
            session.add_to_history("assistant", response)

            # Store recipe results for reference queries ("Explain the 1st recipe")
            if final_recipes:
                self.session_manager.update_last_recipe_results(session, final_recipes, query)

                # Mark these recipes as shown in search cache (for "show more" feature)
                shown_recipe_ids = [str(r.get("id")) for r in final_recipes if r.get("id")]
                if shown_recipe_ids:
                    self.session_manager.add_shown_recipes(session, shown_recipe_ids)

            self.session_manager.save_session(session)

            # Prepare metadata with full recipe details
            metadata = {
                "intent": nlid_result_dict["intent"],
                "is_cooking_related": True,
                "retrieval_strategy": retrieval_plan.strategy.value if retrieval_plan else "unknown",
                "num_results": len(final_recipes),
                "pipeline_duration_ms": round((time.time() - pipeline_start_time) * 1000, 2),
                "fallback_applied": relaxed_filters if relaxed_filters else None,
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
                        "recipe_cost": r.get("recipe_cost"),
                        "nutritional_info": r.get("nutritional_info"),
                        "seasonality": r.get("seasonality"),
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

    async def process_with_agent_flow(
        self,
        query: str,
        session_id: str,
        user_uid: Optional[str] = None,
        language: str = "en",
        orchestrator: Optional[Any] = None,
        nlg_agent_instance: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Process a query using the multi-agent handoff flow.

        This uses the OrchestratorAgent to route queries to specialist agents
        (RecipeAgent, NutritionalAgent, NLIDAgent) based on the query type.

        Args:
            query: User's query text
            session_id: Session identifier
            user_uid: Optional user identifier
            language: Language code
            orchestrator: Optional custom orchestrator agent
            nlg_agent_instance: Optional custom NLG agent for final response

        Returns:
            Dictionary with response and metadata
        """
        pipeline_start_time = time.time()
        logger.info(f"[AGENT FLOW START] Query: {query[:100]} | Session: {session_id}")

        try:
            # Use provided orchestrator or default
            orch_agent = orchestrator if orchestrator else orchestrator_agent

            # Run the orchestrator agent
            logger.info("[AGENT FLOW] Running orchestrator agent...")
            result = await Runner.run(
                orch_agent,
                query,
                context={"user_uid": user_uid, "session_id": session_id, "language": language}
            )

            agent_response = result.final_output
            logger.info(f"[AGENT FLOW] ✓ Orchestrator completed in {time.time() - pipeline_start_time:.3f}s")

            # Generate final NLG response if custom NLG agent provided
            if nlg_agent_instance:
                logger.info("[AGENT FLOW] Generating final response with custom NLG agent...")
                # Wrap the agent response for NLG
                final_response = await generate_recipe_response(
                    query,
                    [],  # No recipes - agent already handled the search
                    None,
                    nlg_agent_instance
                )
            else:
                final_response = str(agent_response)

            logger.info(f"[AGENT FLOW COMPLETE] Total time: {time.time() - pipeline_start_time:.3f}s")

            return {
                "response": final_response,
                "metadata": {
                    "intent": "agent_handoff",
                    "is_cooking_related": True,
                    "retrieval_strategy": "agent_handoff",
                    "num_results": 0,
                    "pipeline_duration_ms": round((time.time() - pipeline_start_time) * 1000, 2),
                    "recipes": [],
                    "used_agent_flow": True
                },
                "pipeline_metadata": {}
            }

        except Exception as e:
            import traceback
            error_time = time.time() - pipeline_start_time
            logger.error("=" * 80)
            logger.error(f"[AGENT FLOW ERROR] Failed after {error_time:.3f}s")
            logger.error(f"[AGENT FLOW ERROR] Error: {str(e)}")
            for line in traceback.format_exc().split('\n'):
                logger.error(f"  {line}")
            logger.error("=" * 80)

            # Fallback to standard pipeline
            logger.info("[AGENT FLOW] Falling back to standard pipeline due to error")
            return await self.process_query(
                query=query,
                session_id=session_id,
                user_uid=user_uid,
                language=language,
                custom_prompts=None  # Don't pass custom prompts to avoid infinite loop
            )

    async def _generate_sdk_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        nlid_result: Dict[str, Any],
        session_id: str,
        user_uid: Optional[str] = None,
        nlg_agent_instance = None
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

        # Use SDK NLG agent to generate response
        response = await generate_recipe_response(query, recipes, user_context, nlg_agent_instance)
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
        # BATCH QUERY 6: Load all seasonality info at once
        # =====================================================
        seasonality_map = {}  # recipe_id -> list of seasonality names
        if unique_recipe_ids:
            seasonality_results = self.db.execute(text("""
                SELECT rs."recipeId", st.name, st."languageId", s.type
                FROM recipe_seasonality rs
                JOIN seasonality s ON rs."seasonalityId" = s.id
                JOIN seasonality_translation st ON s.id = st."seasonalityId"
                WHERE rs."recipeId" = ANY(:recipe_ids)
                ORDER BY rs."recipeId", s.type, st."languageId"
            """), {"recipe_ids": unique_recipe_ids}).fetchall()

            for se in seasonality_results:
                recipe_id = str(se[0])
                if recipe_id not in seasonality_map:
                    seasonality_map[recipe_id] = {
                        "weather": [],
                        "festival": []
                    }
                seasonality_type = "weather" if se[3] == "WEATHER" else "festival"
                # Only add English names for simplicity (can be expanded for i18n)
                if se[2] == "en":
                    seasonality_map[recipe_id][seasonality_type].append(se[1])

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
                    logger.debug(f"[STAGE 9] Excluding private recipe: {recipe_id}")
                    continue

            # Exclude deleted recipes
            if recipe.deletedAt:
                logger.debug(f"[STAGE 9] Excluding deleted recipe: {recipe_id}")
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
                    pass
                else:
                    # User doesn't own any bundle with this recipe - show name only
                    should_show_name_only = True
            elif is_bundle_recipe and is_bundle_free_recipe:
                pass  # Recipe is free in at least one bundle - show full details

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

            # =====================================================
            # EXTRACT NUTRITION, PRICING, AND SEASONALITY FROM METADATA
            # =====================================================
            recipe_cost = None
            nutritional_info = None

            if recipe.recipe_metadata:
                metadata = recipe.recipe_metadata
                # Handle both dict and JSON string formats
                if isinstance(metadata, str):
                    try:
                        import json
                        metadata = json.loads(metadata)
                    except Exception:
                        metadata = {}

                # Extract pricing (cost in all 3 countries)
                if "pricing" in metadata:
                    pricing = metadata["pricing"]
                    recipe_cost = {
                        "usa": {
                            "total": pricing.get("usa", {}).get("total"),
                            "currency": pricing.get("usa", {}).get("currency", "USD")
                        },
                        "india": {
                            "total": pricing.get("india", {}).get("total"),
                            "currency": pricing.get("india", {}).get("currency", "INR")
                        },
                        "norway": {
                            "total": pricing.get("norway", {}).get("total"),
                            "currency": pricing.get("norway", {}).get("currency", "NOK")
                        }
                    }

                # Extract nutrition (macros and micros)
                if "totalNutrition" in metadata:
                    nutrition = metadata["totalNutrition"]
                    nutritional_info = {
                        "macros": nutrition.get("macros", {}),
                        "micros": nutrition.get("micros", {})
                    }

            # Get seasonality from pre-loaded map
            seasonality = seasonality_map.get(recipe_id, {
                "weather": [],
                "festival": []
            })

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
                "recipe_cost": recipe_cost,
                "nutritional_info": nutritional_info,
                "seasonality": seasonality,
            })

        # Sort by: priority_score (desc), then similarity (desc)
        processed.sort(key=lambda x: (x["priority_score"], x["similarity"]), reverse=True)

        return processed

    def _build_nlg_filter_context(
        self,
        session: SessionState,
    ) -> Optional[Dict[str, Any]]:
        """
        Build a context dict describing all active session filters
        for the NLG agent so it can mention them in the intro text.

        Returns None when no meaningful filters are active.
        """
        context: Dict[str, Any] = {}

        if session.excluded_ingredients:
            context["excluded_ingredients"] = session.excluded_ingredients
        if session.included_ingredients:
            context["included_ingredients"] = session.included_ingredients
        if session.filters.creator_username:
            context["creator_username"] = session.filters.creator_username
        if session.filters.cost_filter:
            context["cost_filter"] = session.filters.cost_filter
        if session.filters.time_filter:
            context["time_filter"] = session.filters.time_filter
        if session.filters.nutrition_filter:
            context["nutrition_filter"] = session.filters.nutrition_filter
        if session.filters.tags:
            context["dietary_tags"] = session.filters.tags
        if session.filters.cuisines:
            context["cuisines"] = session.filters.cuisines
        if session.filters.max_time:
            context["max_time_minutes"] = session.filters.max_time
        if session.filters.difficulty:
            context["difficulty"] = session.filters.difficulty

        return context if context else None

    async def _generate_natural_language_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        nlid_result: Dict[str, Any],
        nlg_agent_instance = None,
        filter_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate natural language response from recipe results using SDK"""
        if not recipes:
            logger.warning(f"[NLG] No recipes found, generating no-results response")
            return await self._generate_no_results_response(
                query, nlid_result, nlg_agent_instance,
                filter_context=filter_context
            )

        # Use SDK NLG agent to generate response
        response = await generate_recipe_response(
            query, recipes, None, nlg_agent_instance,
            filter_context=filter_context
        )
        return response

    async def _generate_no_results_response(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        nlg_agent = None,
        filter_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate response when no recipes are found using SDK"""
        response = await generate_no_results_response(
            query,
            nlid_result.get("intent", "unknown"),
            nlid_result.get("entities", {}),
            nlid_result.get("filters", {}),
            nlg_agent,
            filter_context=filter_context
        )
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

            # CRITICAL: Remove excluded ingredients from the vector query.
            # If a user says "I don't like X", X must NOT appear in the embedding
            # query — otherwise the embedding finds X-recipes which SQL must then
            # filter out, often leaving 0 results.
            if excluded_ingredients:
                excluded_lower = {e.lower() for e in excluded_ingredients}
                contextual_parts = [
                    p for p in contextual_parts
                    if p.lower() not in excluded_lower
                ]
                # Also check if the entire last_vector_query is the excluded term
                if not contextual_parts:
                    last_q = session.context_entities.last_vector_query.lower().strip()
                    # Remove recipe/recipes suffix for comparison
                    last_q_clean = last_q.replace(" recipes", "").replace(" recipe", "").strip()
                    # If what remains is fully covered by exclusions, keep parts empty
                    # (will fall through to "recipes" generic fallback below)
                    logger.info(
                        f"[CONTEXTUAL QUERY] All contextual parts were excluded "
                        f"({excluded_ingredients}), falling back to generic query"
                    )

            if contextual_parts:
                contextual_query = f"{' '.join(contextual_parts)} recipes"
            else:
                # No specific ingredients found — use the stored vector query directly
                # (e.g. "something sweet" → keep searching in the same semantic space)
                last_q = session.context_entities.last_vector_query.strip()
                contextual_query = last_q if last_q else "recipes"

            # CRITICAL: Do NOT add allergies to the vector query
            # Allergies should only be applied as SQL filters (excluded_ingredients)
            # The embedding search should find semantically similar recipes based on the original query
            # Then SQL will filter out recipes containing the allergens
            # This prevents the issue where "garlic" gets embedded and finds garlic-containing recipes

            logger.info(f"[CONTEXTUAL QUERY] Built from previous search context: {contextual_query}")
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
            # Easy difficulty mappings
            r'\beasy\b': 'easy',
            r'\bsimple\b': 'easy',
            r'\bquick\b': 'easy',  # quick recipes are often easy
            r'\bbasic\b': 'easy',
            r'\bbeginner\b': 'easy',
            r'\bbeginners\b': 'easy',
            r'\bbeginner-friendly\b': 'easy',
            r'\bnew to cooking\b': 'easy',
            r'\bjust starting\b': 'easy',
            r'\bstarter\b': 'easy',
            r'\bnovice\b': 'easy',
            r'\bfirst.?time\b': 'easy',
            r'\bentry.?level\b': 'easy',
            r'\bfoolproof\b': 'easy',
            r'\bidiot.?proof\b': 'easy',
            r'\bno.?brainer\b': 'easy',
            r'\bfor.?kids\b': 'easy',
            r'\bchild.?friendly\b': 'easy',

            # Medium difficulty mappings
            r'\bmedium\b': 'medium',
            r'\bmoderate\b': 'medium',
            r'\bintermediate\b': 'medium',
            r'\baverage\b': 'medium',
            r'\bregular\b': 'medium',
            r'\bstandard\b': 'medium',
            r'\bnormal\b': 'medium',

            # Hard difficulty mappings
            r'\bhard\b': 'hard',
            r'\bdifficult\b': 'hard',
            r'\bcomplex\b': 'hard',
            r'\badvanced\b': 'hard',
            r'\bexpert\b': 'hard',
            r'\bexperts\b': 'hard',
            r'\bprofessional\b': 'hard',
            r'\bpro\b': 'hard',
            r'\bmaster\b': 'hard',
            r'\bmastery\b': 'hard',
            r'\bchef.?level\b': 'hard',
            r'\bchallenging\b': 'hard',
            r'\belaborate\b': 'hard',
            r'\bintricate\b': 'hard',
            r'\bsophisticated\b': 'hard',
            r'\bfancy\b': 'hard',
            r'\bgourmet\b': 'hard',
            r'\bfine.?dining\b': 'hard',
            r'\brestaurant.?quality\b': 'hard',
            r'\bimpressive\b': 'hard',
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

        return contextual_query

    async def _handle_filter_query(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle price_filter, nutrition_filter, and time_filter intents.

        If there's a previous search context (e.g., "dessert recipes"), this will
        combine that with the filter as a refinement (embedding search + filter).
        If no previous context, falls back to direct SQL query (no embedding).

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

        # Extract cost, nutrition, time, and servings filters
        cost_filter = filters.get("cost")
        nutrition_filter = filters.get("nutrition")
        time_filter = filters.get("time")
        servings_filter = filters.get("servings")

        # Fallback: try to extract from query if NLID didn't provide them
        if not cost_filter and intent == "price_filter":
            cost_filter = extract_cost_filter(query)

        # If cost_filter still doesn't have a country, default to Norway
        if cost_filter and not cost_filter.get("country"):
            cost_filter["country"] = "Norway"

        if not nutrition_filter and intent == "nutrition_filter":
            nutrition_filter = extract_nutrition_filter(query)

        if not time_filter and intent == "time_filter":
            # Extract time qualifier from query - qualitative only (quick, fast, slow)
            query_lower = query.lower()
            if any(word in query_lower for word in [
                "quick", "short", "fast", "speedy", "quickly", "hurry",
                "not much time", "short time", "very little time",
                "don't have time", "dont have time", "no time",
                "in a hurry", "rush", "rapid"
            ]):
                time_filter = {"sort_order": "ASC"}
            elif any(word in query_lower for word in [
                "long", "slow", "elaborate", "takes time", "all day",
                "leisurely", "weekend project"
            ]):
                time_filter = {"sort_order": "DESC"}

        # ============ MULTI-TURN CONTEXT: Merge with session-persisted filters ============
        # If the user previously set a filter (e.g., Q1: "quick recipes" → time ASC)
        # and now adds another (e.g., Q2: "budget is 100" → cost filter), carry forward
        # the previous filter. Current query's filter always takes priority.
        session_cost = session.filters.cost_filter
        session_time = session.filters.time_filter
        session_nutrition = session.filters.nutrition_filter
        session_servings = session.filters.servings

        if not cost_filter and session_cost:
            cost_filter = session_cost
        if not time_filter and session_time:
            time_filter = session_time
        if not nutrition_filter and session_nutrition:
            nutrition_filter = session_nutrition
        if not servings_filter and session_servings:
            servings_filter = session_servings

        # Persist current filters back to session for future turns
        if cost_filter:
            session.filters.cost_filter = cost_filter
        if time_filter:
            session.filters.time_filter = time_filter
        if nutrition_filter:
            session.filters.nutrition_filter = nutrition_filter
        if servings_filter:
            session.filters.servings = servings_filter
        self.session_manager.save_session(session)
        logger.info(f"[FILTER QUERY] Active filters: cost={cost_filter is not None}, time={time_filter is not None}, nutrition={nutrition_filter is not None}, servings={servings_filter is not None}")

        # Extract previous search context (from search cache or last_vector_query)
        search_cache = session.context_entities.search_cache or {}
        previous_vector_query = (
            search_cache.get("vector_query")
            or session.context_entities.last_vector_query
        )

        # ============ REFINEMENT MODE: Check for previous search context ============

        if previous_vector_query:
            logger.info(f"[FILTER QUERY] Refining previous search '{previous_vector_query}' with filters")
            # Route to refinement handler which will do embedding search + filter
            return await self._handle_filter_refinement(
                query, nlid_result, session, user_uid, language,
                previous_vector_query, cost_filter, nutrition_filter, time_filter
            )

        # ============ STANDALONE MODE: No previous context, use direct SQL ============
        logger.info(f"[FILTER QUERY] No previous search context, using direct SQL")

        # Build filter data directly from session state so that NLID empty
        # lists cannot overwrite accumulated session exclusions.
        filter_data: Dict[str, Any] = {
            "excluded_ingredients": list(session.excluded_ingredients),
            "included_ingredients": list(session.included_ingredients),
            "excluded_recipe_ids": list(session.excluded_recipe_ids),
            "tags": list(session.filters.tags),
            "cuisines": list(session.filters.cuisines),
            "difficulty": session.filters.difficulty,
            "max_time": session.filters.max_time,
            "creator_uid": session.filters.creator_uid,
            "servings": servings_filter or session.filters.servings,  # Use merged servings or session
            "ingredient_count": session.filters.ingredient_count,  # Include ingredient count
        }

        # Merge any NEW exclusions/inclusions from the current NLID result
        for ing in filters.get("excluded_ingredients", []):
            if ing and ing not in filter_data["excluded_ingredients"]:
                filter_data["excluded_ingredients"].append(ing)
        for ing in filters.get("included_ingredients", []):
            if ing and ing not in filter_data["included_ingredients"]:
                filter_data["included_ingredients"].append(ing)

        # ============ VEGETARIAN EXCLUSION ============
        # If user wants vegetarian, exclude all non-vegetarian ingredients
        # This is more reliable than relying on the "vegetarian" tag alone
        _tags = filter_data.get("tags", [])
        if "vegetarian" in _tags:
            NON_VEGETARIAN_INGREDIENTS = [
                # Eggs - ILIKE '%egg%' catches egg, eggs, egg white, egg yolk, etc.
                "egg", "eggs",
                # General meat - ILIKE '%meat%' catches meat, meats, minced meat, etc.
                "meat", "meats",
                # Poultry
                "chicken", "chickens", "turkey", "duck", "ducks", "goose", "geese", "quail",
                # Red meat
                "beef", "pork", "lamb", "mutton", "goat", "veal", "venison",
                # Processed meat
                "bacon", "ham", "sausage", "sausages", "salami", "pepperoni", "lard",
                "prosciutto", "chorizo", "hotdog", "hot dog", "hot dogs",
                # Fish & seafood
                "fish", "fishes", "seafood", "shellfish", "prawn", "prawns",
                "shrimp", "shrimps", "crab", "crabs", "lobster", "lobsters", "oyster", "oysters",
                "mussel", "mussels", "scallop", "scallops", "clam", "clams", "anchovy", "anchovies",
                "tuna", "salmon", "cod", "halibut", "tilapia", "trout", "trouts",
                "sardine", "sardines", "mackerel", "herring", "catfish",
                # Animal-derived fats/stock
                "gelatin", "bone broth", "chicken broth", "beef broth",
                "chicken stock", "beef stock", "fish sauce", "anchovy paste",
            ]
            existing_excluded = filter_data.get("excluded_ingredients", [])
            existing_lower = {e.lower() for e in existing_excluded}
            additional = [m for m in NON_VEGETARIAN_INGREDIENTS if m.lower() not in existing_lower]
            filter_data["excluded_ingredients"] = existing_excluded + additional
            logger.info(
                f"[FILTER QUERY] Vegetarian: Added {len(additional)} non-veg ingredients to exclusions"
            )

        # ============ ALLERGEN EXPANSION ============
        # Expand allergens to include all variants (e.g., "eggs" -> "egg", "egg white", "egg yolk", etc.)
        # This ensures comprehensive exclusion in SQL
        raw_exclusions = filter_data.get("excluded_ingredients", [])
        if raw_exclusions:
            try:
                from apps.fastapi.src.services.ingredient_matcher import IntelligentIngredientMatcher
                ingredient_matcher = IntelligentIngredientMatcher(self.db, self.client)
                expanded_exclusions = ingredient_matcher.smart_expand_for_exclusions(raw_exclusions)
                logger.info(f"[FILTER QUERY] Expanded exclusions: {raw_exclusions} -> {expanded_exclusions}")

                # Update filter_data with expanded exclusions
                filter_data["excluded_ingredients"] = expanded_exclusions

                # Also update session to persist the expanded exclusions
                for allergen in expanded_exclusions:
                    if allergen not in session.excluded_ingredients:
                        session.excluded_ingredients.append(allergen)
                self.session_manager.save_session(session)
            except Exception as e:
                logger.warning(f"[FILTER QUERY] Failed to expand allergens: {e}, using original list")

        # Build additional SQL conditions from accumulated filter data
        additional_conditions = build_session_filter_conditions(filter_data)

        # Build the SQL query
        sql_query = None

        # Count active filters to decide which builder to use
        active_filter_count = sum(1 for f in [cost_filter, nutrition_filter, time_filter] if f)

        # Check if we have session-level filters that need SQL
        _has_servings = bool(filter_data.get("servings"))
        _has_ingredient_count = bool(filter_data.get("ingredient_count"))
        _has_exclusions = bool(filter_data.get("excluded_ingredients"))
        _has_inclusions = bool(filter_data.get("included_ingredients"))
        _has_tags = bool(filter_data.get("tags"))
        _has_cuisines = bool(filter_data.get("cuisines"))
        _has_session_filters = _has_servings or _has_ingredient_count or _has_exclusions or _has_inclusions or _has_tags or _has_cuisines

        _filter_limit = 100  # Larger pool for multi-turn filter narrowing
        if active_filter_count >= 2:
            # Multiple filters active → use combined builder
            from apps.fastapi.src.utils.sql_builders import build_recipe_multi_filter_sql
            sql_query = build_recipe_multi_filter_sql(
                cost_filter=cost_filter,
                time_filter=time_filter,
                nutrition_filter=nutrition_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=_filter_limit
            )
        elif cost_filter:
            # Cost-only filter
            sql_query = build_recipe_cost_filter_sql(
                cost_filter=cost_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=_filter_limit
            )
        elif nutrition_filter:
            # Nutrition-only filter
            sql_query = build_recipe_nutrition_filter_sql(
                nutrition_filter=nutrition_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=_filter_limit
            )
        elif time_filter:
            # Time-only filter (sort by prep + cook time)
            sql_query = build_recipe_time_filter_sql(
                time_filter=time_filter,
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=_filter_limit
            )
        elif _has_session_filters:
            # No cost/time/nutrition filter, but we have session filters (servings, exclusions, etc.)
            # Use base SQL builder with additional conditions
            from apps.fastapi.src.utils.sql_builders import build_recipe_base_sql
            logger.info(
                f"[FILTER QUERY] Using base SQL with session filters: "
                f"servings={_has_servings}, exclusions={_has_exclusions}, "
                f"inclusions={_has_inclusions}, tags={_has_tags}, cuisines={_has_cuisines}"
            )
            sql_query = build_recipe_base_sql(
                user_uid=user_uid or "",
                language=language or "en",
                additional_conditions=additional_conditions,
                limit=_filter_limit
            )
        else:
            # No valid filters found - fall back to error message
            logger.warning(f"[FILTER QUERY] No valid filters found, returning guidance")
            error_response = "I couldn't understand the filter you're looking for. Try queries like 'recipes under $20', 'high protein meals', or 'quick recipes'."
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

        try:
            result = self.db.execute(text(sql_query))
            logger.info(f"SQL QUERY: {str(sql_query)}")
            rows = [dict(row._mapping) for row in result.fetchall()]
            logger.info(f"[FILTER QUERY] ✓ SQL executed: {len(rows)} results")
        except Exception as e:
            logger.error(f"[FILTER QUERY] SQL execution error: {e}")
            # Rollback the DB session to prevent cascading InFailedSqlTransaction errors
            try:
                self.db.rollback()
            except Exception:
                pass
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

        # Save search filters to session so follow-ups carry forward
        # creator, cost, etc.
        # Do NOT save vector_query here. Standalone mode uses direct SQL
        # (no embedding), so saving the raw query as vector_query would
        # cause the next turn to route to _handle_filter_refinement which
        # does embedding search on a small candidate pool.  Follow-up
        # queries should also use standalone SQL (full database) with all
        # accumulated session filters (tags, exclusions, cost, etc.).
        if final_recipes:
            sql_filters_snapshot = dict(filters)
            if session.filters.creator_uid:
                sql_filters_snapshot["creator_uid"] = session.filters.creator_uid
            session.context_entities.last_search_filters = sql_filters_snapshot
            session.last_intent = intent
            self.session_manager.save_session(session)

        # Generate natural language response
        if final_recipes:
            response = await self._generate_cost_nutrition_response(
                query, final_recipes, intent, cost_filter, nutrition_filter,
                session=session
            )
        else:
            nlg_filter_ctx = self._build_nlg_filter_context(session)
            response = await self._generate_no_results_response(
                query, nlid_result, None,
                filter_context=nlg_filter_ctx
            )

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
                "nutrition": nutrition_filter,
                "time": time_filter
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
                    "recipe_cost": r.get("recipe_cost"),
                    "nutritional_info": r.get("nutritional_info"),
                    "seasonality": r.get("seasonality"),
                }
                for r in final_recipes
            ]
        }

        logger.info(f"[FILTER QUERY] ✓ Pipeline completed in {time.time() - pipeline_start_time:.3f}s | Results: {len(final_recipes)}")

        return {
            "response": response,
            "metadata": metadata
        }

    async def _handle_filter_refinement(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str],
        previous_vector_query: str,
        cost_filter: Optional[Dict[str, Any]],
        nutrition_filter: Optional[Dict[str, Any]],
        time_filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Handle cost/nutrition/time filter as a refinement to a previous search.

        This runs embedding search with the previous query and applies filters
        via SQL, combining both constraints.

        Args:
            query: User's current query (e.g., "My budget is 200$")
            nlid_result: NLID detection result
            session: Session state
            user_uid: User identifier
            language: Language code
            previous_vector_query: Previous search query (e.g., "dessert recipes")
            cost_filter: Cost filter dict with operator, value, country
            nutrition_filter: Nutrition filter dict
            time_filter: Time filter dict with sort_order ("ASC" or "DESC")

        Returns:
            Response dictionary with recipes and metadata
        """
        from apps.fastapi.src.agents.agent_tools import search_recipes_by_embedding
        from apps.fastapi.src.services.session_memory_manager import EMBEDDING_BATCH_SIZE
        from apps.fastapi.src.services.session_memory_manager import SEARCH_CACHE_TTL_SECONDS
        from sqlalchemy import text
        import json

        pipeline_start_time = time.time()

        logger.info(
            f"[FILTER REFINEMENT] Refining '{previous_vector_query}' | "
            f"cost={cost_filter is not None}, nutrition={nutrition_filter is not None}, time={time_filter is not None}"
        )

        # Get session context for allergen exclusions
        session_context = self.session_manager.get_user_context(session, {})
        excluded_ingredients = session_context.get("excluded_ingredients", [])

        # Resolve creator_uid from session context so the embedding
        # search is scoped to that creator's recipes (avoids wasting
        # candidate slots on other users' recipes).
        last_search_filters = (
            session.context_entities.last_search_filters or {}
        )
        embedding_creator_uid = (
            last_search_filters.get("creator_uid")
            or session.filters.creator_uid
        )

        # Step 1: Try to reuse cached embedding candidates instead of re-embedding
        # Reuse when: same vector query, cache not expired, same creator scope
        cache = self.session_manager.get_search_cache(session)
        cached_candidates = cache.get("embedding_candidates", [])
        cached_vector_query = cache.get("vector_query")
        cached_created_at = cache.get("created_at")
        cached_creator_uid = (cache.get("sql_filters") or {}).get("creator_uid")

        cache_is_valid = (
            cached_candidates
            and cached_vector_query == previous_vector_query
            and cached_created_at is not None
            and (time.time() - cached_created_at) < SEARCH_CACHE_TTL_SECONDS
            and cached_creator_uid == embedding_creator_uid
        )

        if cache_is_valid:
            candidate_ids = cached_candidates
            logger.info(
                f"[FILTER REFINEMENT] Reusing {len(candidate_ids)} cached "
                f"embedding candidates (query='{cached_vector_query}')"
            )
        else:
            # Cache miss — re-embed
            reason = (
                "no cached candidates" if not cached_candidates
                else f"query changed ('{cached_vector_query}' → '{previous_vector_query}')"
                if cached_vector_query != previous_vector_query
                else "cache expired"
                if cached_created_at and (time.time() - cached_created_at) >= SEARCH_CACHE_TTL_SECONDS
                else f"creator scope changed ({cached_creator_uid} → {embedding_creator_uid})"
            )
            logger.info(f"[FILTER REFINEMENT] Cache miss ({reason}), re-embedding")

            embedding_results = search_recipes_by_embedding(
                self.db,
                query_text=previous_vector_query,
                limit=EMBEDDING_BATCH_SIZE,
                threshold=0.35,
                language_id=language or "en",
                offset=0,
                creator_uid=embedding_creator_uid,
            )

            if not embedding_results:
                logger.info(
                    "[FILTER REFINEMENT] No embedding results found, "
                    "falling back to standalone SQL mode"
                )
                # Clear the previous_vector_query so _handle_filter_query
                # uses standalone mode instead of looping back here.
                session.context_entities.last_vector_query = None
                if session.context_entities.search_cache:
                    session.context_entities.search_cache.pop(
                        "vector_query", None
                    )
                self.session_manager.save_session(session)

                return await self._handle_filter_query(
                    query, nlid_result, session, user_uid, language
                )

            candidate_ids = [str(r.id) for r, _ in embedding_results]
            logger.info(f"[FILTER REFINEMENT] ✓ {len(candidate_ids)} candidates")

        # Step 2: Build SQL with cost/nutrition filter + allergen exclusions
        country_key_map = {"US": "usa", "India": "india", "Norway": "norway"}

        # Build cost filter condition
        cost_condition = ""
        if cost_filter:
            country = cost_filter.get("country", "Norway")
            country_key = country_key_map.get(country, "norway")
            operator = cost_filter.get("operator", "<=")
            value = cost_filter.get("value")

            # Always need pricing data to exist for cost queries
            cost_condition = f"""
            AND r."recipe_metadata" IS NOT NULL
            AND r."recipe_metadata"->'pricing' IS NOT NULL
            AND r."recipe_metadata"->'pricing'->'{country_key}' IS NOT NULL
            AND r."recipe_metadata"->'pricing'->'{country_key}'->>'total' IS NOT NULL
            """

            # Only add value filter if a specific value was provided
            if value is not None:
                cost_condition += f"""
            AND CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) {operator} {value}
            """

        # Build allergen exclusion conditions
        allergen_conditions = ""
        if excluded_ingredients:
            for ingredient in excluded_ingredients[:10]:  # Limit to prevent huge queries
                escaped = ingredient.replace("'", "''").replace("%", "\\%")
                allergen_conditions += f"""
            AND (
                LOWER(r."name") NOT LIKE '%{escaped.lower()}%'
                AND LOWER(r."ingress") NOT LIKE '%{escaped.lower()}%'
                AND NOT EXISTS (
                    SELECT 1 FROM recipe_ingredient ri
                    JOIN ingredient i ON ri."ingredientId" = i."id"
                    WHERE ri."recipeId" = r."id"
                    AND LOWER(i."name") LIKE '%{escaped.lower()}%'
                )
            )
                """

        # Build ingredient inclusion conditions from session
        # e.g. Q1 "I only have banana" → Q2 "My budget is 200" should
        # still require banana in every result.
        included_ingredients = session_context.get("included_ingredients", [])
        inclusion_conditions = ""
        if included_ingredients:
            for ingredient in included_ingredients[:10]:
                escaped = ingredient.replace("'", "''").replace("%", "\\%")
                inclusion_conditions += f"""
            AND EXISTS (
                SELECT 1 FROM recipe_ingredient ri
                JOIN ingredient i ON ri."ingredientId" = i."id"
                WHERE ri."recipeId" = r."id"
                AND LOWER(i."name") LIKE '%{escaped.lower()}%'
            )
                """

        # Build time-based ORDER BY clause
        # Priority: cost sort > time sort > default
        time_order_clause = "ORDER BY r.name"  # Default
        extra_select_cols = ""  # Extra columns needed for ORDER BY

        # ============ SESSION CONTEXT FILTERS ============
        # Carry forward filters from the original search (creator_uid, cuisines, tags, etc.)
        session_filter_conditions = ""

        # 1. Creator filter from last_search_filters
        last_search_filters = session.context_entities.last_search_filters or {}
        if last_search_filters.get("creator_uid"):
            creator_uid_val = last_search_filters["creator_uid"]
            session_filter_conditions += f"""
          AND r."userUid" = '{creator_uid_val}'"""

        # 2. Creator filter from session filters
        elif session.filters.creator_uid:
            session_filter_conditions += f"""
          AND r."userUid" = '{session.filters.creator_uid}'"""

        # 3. Cuisine/Tags filter - REMOVED strict filtering
        # Cuisines and tags are now used for SCORING only (LEFT JOIN LATERAL)
        # Recipes WITH matching tags/cuisines rank higher, but recipes WITHOUT are NOT excluded
        # This prevents narrowing down results too aggressively when tags are incomplete in the database
        tag_cuisine_conditions = []
        if session.filters.cuisines:
            tag_cuisine_conditions.extend([f"LOWER(t.\"name\") = '{c.lower()}'" for c in session.filters.cuisines])
        if session.filters.tags:
            tag_cuisine_conditions.extend([f"LOWER(t.\"name\") = '{t.lower()}'" for t in session.filters.tags])

        # Build LEFT JOIN LATERAL for tag scoring (not added to WHERE, but to FROM clause)
        tag_lateral_join = ""
        tag_order_clause = ""
        if tag_cuisine_conditions:
            or_conditions = " OR ".join(tag_cuisine_conditions)
            tag_lateral_join = f"""
        LEFT JOIN LATERAL (
            SELECT 1 AS match
            FROM recipe_tags_tag rtt
            JOIN tag t ON rtt."tagId" = t."id"
            WHERE rtt."recipeId" = r."id"
            AND ({or_conditions})
            LIMIT 1
        ) tag_match ON true"""
            tag_order_clause = "(CASE WHEN tag_match.match IS NOT NULL THEN 1 ELSE 0 END) DESC,"

        # 4. (Removed - merged with cuisine above for combined scoring)

        # 5. Servings filter from session
        # e.g., Q1: "for 2 people" → Q2: "my budget is 100" → servings=2 preserved
        if session.filters.servings:
            servings = session.filters.servings
            if isinstance(servings, int):
                session_filter_conditions += f"""
          AND r.servings = {servings}"""
            elif isinstance(servings, dict):
                # Handle range/comparison servings
                min_val = servings.get("min")
                max_val = servings.get("max")
                operator = servings.get("operator")
                value = servings.get("value")
                if min_val is not None and max_val is not None:
                    session_filter_conditions += f"""
          AND r.servings >= {min_val} AND r.servings <= {max_val}"""
                elif operator and value is not None:
                    sql_op = {">=": ">=", "<=": "<=", ">": ">", "<": "<", "=": "="}.get(operator, "=")
                    session_filter_conditions += f"""
          AND r.servings {sql_op} {value}"""

        if cost_filter:
            # Cost-based ordering
            country = cost_filter.get("country", "Norway")
            country_key = country_key_map.get(country, "norway")
            # Use sort_order from cost_filter: DESC for specific budget (nearest to budget), ASC for cheap
            cost_sort = cost_filter.get("sort_order", "DESC" if cost_filter.get("value") else "ASC")
            extra_select_cols += f""",
               CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) as _cost_sort"""
            if time_filter:
                # Both cost and time active
                time_sort = time_filter.get("sort_order", "ASC").upper()
                time_order_clause = f"ORDER BY {tag_order_clause} _cost_sort {cost_sort}, (r.\"prepTime\" + r.\"cookTime\") {time_sort}"
            else:
                time_order_clause = f"ORDER BY {tag_order_clause} _cost_sort {cost_sort}"
        elif time_filter:
            sort_order = time_filter.get("sort_order", "ASC").upper()
            time_order_clause = f"ORDER BY {tag_order_clause} (r.\"prepTime\" + r.\"cookTime\") {sort_order}"
        elif tag_order_clause:
            # Only tag scoring, no time or cost filter
            time_order_clause = f"ORDER BY {tag_order_clause} r.name"
        else:
            # No filters, default order
            time_order_clause = "ORDER BY r.name"

        # Build the full SQL query
        candidate_list = ", ".join([f"'{cid}'" for cid in candidate_ids])

        sql_query = f"""
        SELECT r.id, r.name, r.ingress,
               r.difficulty, r."prepTime" as prep_time, r."cookTime" as cook_time,
               r.image, r.servings, r."userUid" as creator_uid,
               r."private", r."deletedAt", r.recipe_metadata{extra_select_cols}
        FROM recipe r
        LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
        LEFT JOIN "bundle" b ON br."bundleId" = b."id"
        {tag_lateral_join}
        WHERE r.id IN ({candidate_list})
          AND r."deletedAt" IS NULL
          AND r."status" = 'published'
          AND r."languageId" = '{language or "en"}'
          AND (r."private" = false OR r."userUid" = '{user_uid or ""}' OR br."bundleId" IS NOT NULL)
          {cost_condition}
          {allergen_conditions}
          {inclusion_conditions}
          {session_filter_conditions}
        {time_order_clause}
        LIMIT 20
        """

        logger.info(f"[FILTER REFINEMENT] Executing SQL:\n{sql_query}")

        try:
            result = self.db.execute(text(sql_query))
            rows = result.fetchall()

            if not rows:
                logger.info("[FILTER REFINEMENT] No results after filtering")
                return {
                    "response": f"I couldn't find any {previous_vector_query} matching your criteria and dietary restrictions. Would you like to try different criteria?",
                    "metadata": {
                        "intent": nlid_result.get("intent", "filter"),
                        "is_cooking_related": True,
                        "num_results": 0,
                        "recipes": []
                    }
                }

            logger.info(f"[FILTER REFINEMENT] Found {len(rows)} results after filtering")

            # Step 3: Process results (similar to _process_cached_candidates)
            recipe_ids = [str(row[0]) for row in rows]

            # Batch fetch ingredients
            ingredients_map = {}
            if recipe_ids:
                ingredient_results = self.db.execute(text("""
                    SELECT ri."recipeId", ri.amount, ri."unitId", i.name as ing_name, mut.name as unit_name
                    FROM recipe_ingredient ri
                    JOIN ingredient i ON ri."ingredientId" = i.id
                    LEFT JOIN measuring_unit_translation mut ON ri."unitId" = mut."measuringUnitId" AND mut."languageId" = 'en'
                    WHERE ri."recipeId" = ANY(CAST(:recipe_ids AS uuid[]))
                    AND ri."deletedAt" IS NULL
                    ORDER BY ri."recipeId", ri.order
                """), {"recipe_ids": [str(rid) for rid in recipe_ids]}).fetchall()

                for ir in ingredient_results:
                    rid = str(ir[0])
                    if rid not in ingredients_map:
                        ingredients_map[rid] = []
                    ingredients_map[rid].append({
                        "name": ir[3],
                        "amount": ir[1],
                        "unit": ir[4],
                        "unit_id": str(ir[2]) if ir[2] else None
                    })

            # Batch fetch instructions
            instructions_map = {}
            if recipe_ids:
                instruction_results = self.db.execute(text("""
                    SELECT "recipeId", "order", description, image
                    FROM recipe_instruction
                    WHERE "recipeId" = ANY(CAST(:recipe_ids AS uuid[]))
                    AND "deletedAt" IS NULL
                    ORDER BY "recipeId", "order"
                """), {"recipe_ids": [str(rid) for rid in recipe_ids]}).fetchall()

                for instr in instruction_results:
                    rid = str(instr[0])
                    if rid not in instructions_map:
                        instructions_map[rid] = []
                    instructions_map[rid].append({
                        "order": instr[1],
                        "description": instr[2],
                        "image": instr[3]
                    })

            # Build recipe list
            recipes = []
            for row in rows:
                recipe_id = str(row[0])
                recipe_metadata = row[11]

                # Extract recipe_cost from metadata
                recipe_cost = None
                if recipe_metadata:
                    metadata = recipe_metadata
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except Exception:
                            metadata = {}
                    if "pricing" in metadata:
                        pricing = metadata["pricing"]
                        recipe_cost = {
                            "usa": {
                                "total": pricing.get("usa", {}).get("total"),
                                "currency": pricing.get("usa", {}).get("currency", "USD")
                            },
                            "india": {
                                "total": pricing.get("india", {}).get("total"),
                                "currency": pricing.get("india", {}).get("currency", "INR")
                            },
                            "norway": {
                                "total": pricing.get("norway", {}).get("total"),
                                "currency": pricing.get("norway", {}).get("currency", "NOK")
                            }
                        }

                prep_time = row[4]
                cook_time = row[5]

                recipes.append({
                    "id": recipe_id,
                    "name": row[1],
                    "ingress": row[2],
                    "description": row[2],
                    "difficulty": row[3],
                    "prep_time": prep_time,
                    "cook_time": cook_time,
                    "total_time": (prep_time or 0) + (cook_time or 0),
                    "image": row[6],
                    "servings": row[7],
                    "similarity": 0.7,
                    "priority_score": 0,
                    "access_level": "full",
                    "is_liked": False,
                    "is_created": False,
                    "is_bundle_recipe": False,
                    "is_bundle_free_recipe": False,
                    "bundle_name": None,
                    "ingredients": ingredients_map.get(recipe_id, []),
                    "instructions": instructions_map.get(recipe_id, []),
                    "recipe_cost": recipe_cost,
                    "nutritional_info": None,
                    "seasonality": {"weather": [], "festival": []},
                })

            # Step 4: Generate response
            response = await generate_recipe_response(
                query,
                recipes,
                None,
                None,
                filter_context=self._build_nlg_filter_context(session)
            )

            # Update session with shown recipes
            recipe_ids_to_show = [r["id"] for r in recipes[:5]]
            self.session_manager.add_shown_recipes(session, recipe_ids_to_show)
            self.session_manager.update_last_recipe_results(session, recipes[:5], "cost_nutrition_refinement")
            self.session_manager.save_session(session)

            logger.info(f"[FILTER REFINEMENT] ✓ Completed in {time.time() - pipeline_start_time:.3f}s | Results: {len(recipes)}")

            return {
                "response": response,
                "metadata": {
                    "intent": "price_filter",
                    "is_cooking_related": True,
                    "num_results": len(recipes),
                    "refined_query": previous_vector_query,
                    "recipes": recipes[:5]
                }
            }

        except Exception as e:
            logger.error(f"[FILTER REFINEMENT] Error: {e}")
            import traceback
            logger.error(f"[FILTER REFINEMENT] Traceback: {traceback.format_exc()}")
            # Rollback the DB session to prevent cascading InFailedSqlTransaction errors
            try:
                self.db.rollback()
            except Exception:
                pass
            return {
                "response": "I encountered an error while filtering recipes by price. Please try again.",
                "metadata": {
                    "intent": "price_filter",
                    "num_results": 0,
                    "error": str(e),
                    "recipes": []
                }
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
                logger.warning(f"[FILTER QUERY] Failed to fetch ingredients: {e}")

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
                logger.warning(f"[FILTER QUERY] Failed to fetch instructions: {e}")

        # Batch fetch seasonality
        seasonality_map = {}
        if recipe_ids:
            try:
                seasonality_results = self.db.execute(text("""
                    SELECT rs."recipeId", st.name, st."languageId", s.type
                    FROM recipe_seasonality rs
                    JOIN seasonality s ON rs."seasonalityId" = s.id
                    JOIN seasonality_translation st ON s.id = st."seasonalityId"
                    WHERE rs."recipeId" = ANY(:recipe_ids)
                    ORDER BY rs."recipeId", s.type, st."languageId"
                """), {"recipe_ids": recipe_ids}).fetchall()

                for se in seasonality_results:
                    recipe_id = str(se[0])
                    if recipe_id not in seasonality_map:
                        seasonality_map[recipe_id] = {
                            "weather": [],
                            "festival": []
                        }
                    seasonality_type = "weather" if se[3] == "WEATHER" else "festival"
                    if se[2] == "en":
                        seasonality_map[recipe_id][seasonality_type].append(se[1])
            except Exception as e:
                logger.warning(f"[FILTER QUERY] Failed to fetch seasonality: {e}")

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

            # Extract recipe_cost (pricing for all 3 countries)
            recipe_cost = None
            pricing = metadata.get("pricing", {})
            if pricing:
                recipe_cost = {
                    "usa": {
                        "total": pricing.get("usa", {}).get("total"),
                        "currency": pricing.get("usa", {}).get("currency", "USD")
                    },
                    "india": {
                        "total": pricing.get("india", {}).get("total"),
                        "currency": pricing.get("india", {}).get("currency", "INR")
                    },
                    "norway": {
                        "total": pricing.get("norway", {}).get("total"),
                        "currency": pricing.get("norway", {}).get("currency", "NOK")
                    }
                }

            # Extract nutritional_info (macros and micros)
            nutritional_info = None
            total_nutrition = metadata.get("totalNutrition", {})
            if total_nutrition:
                nutritional_info = {
                    "macros": total_nutrition.get("macros", {}),
                    "micros": total_nutrition.get("micros", {})
                }

            # Get seasonality from pre-loaded map
            seasonality = seasonality_map.get(recipe_id, {
                "weather": [],
                "festival": []
            })

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
                "instructions": instructions_map.get(recipe_id, []),
                "recipe_cost": recipe_cost,
                "nutritional_info": nutritional_info,
                "seasonality": seasonality,
            })

        return processed

    async def _generate_cost_nutrition_response(
        self,
        query: str,
        recipes: List[Dict[str, Any]],
        intent: str,
        cost_filter: Optional[Dict[str, Any]],
        nutrition_filter: Optional[Dict[str, Any]],
        session: Optional[SessionState] = None
    ) -> str:
        """
        Generate natural language response for cost/nutrition filter queries.
        Uses the standard NLG agent for consistent, natural responses.
        """
        if not recipes:
            ctx = self._build_nlg_filter_context(session) if session else None
            return await self._generate_no_results_response(
                query, {"intent": intent}, None,
                filter_context=ctx
            )

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

        filter_ctx = self._build_nlg_filter_context(session) if session else None
        return await self._generate_natural_language_response(
            query, recipes, nlid_result,
            filter_context=filter_ctx
        )

    # ------------------------------------------------------------------ #
    # Helper: extract a country name from a short follow-up query          #
    # e.g., "And in India?", "in Norway", "what about US?"                 #
    # Returns the matched country string or None.                           #
    # ------------------------------------------------------------------ #
    _COUNTRY_PATTERNS: Dict[str, str] = {
        # Explicit country names
        "india": "India",
        "indian": "India",
        "norway": "Norway",
        "norwegian": "Norway",
        "us": "US",
        "usa": "US",
        "united states": "US",
        "america": "US",
        "american": "US",
        # Currency cues
        "usd": "US",
        "dollar": "US",
        "dollars": "US",
        "inr": "India",
        "rupee": "India",
        "rupees": "India",
        "₹": "India",
        "nok": "Norway",
        "krone": "Norway",
        "kr": "Norway",
    }

    @classmethod
    def _extract_country_from_query(cls, query: str) -> Optional[str]:
        """
        Extract a country / currency mention from a short follow-up query.

        Returns the canonical country string used by pricing logic
        ("India", "Norway", "US") or None if nothing is found.
        """
        import re as _re
        q = query.lower().strip().rstrip("?!.")
        for pattern, country in cls._COUNTRY_PATTERNS.items():
            # Match as a whole word / token
            if _re.search(r'\b' + _re.escape(pattern) + r'\b', q):
                return country
        return None

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

        logger.info(f"[SPECIAL QUERY] intent={intent} | entities={entities}")

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

            # Check if user explicitly asks for all countries
            query_lower = query.lower()
            asks_for_all_countries = any(pattern in query_lower for pattern in [
                "all countries", "every country", "all regions", "each country",
                "compare prices", "price comparison", "in all", "for all",
                "alle land", "sammenligne priser"  # Norwegian phrases
            ])

            # Default to Norway if no country specified and not asking for all
            if not country_code and not asks_for_all_countries:
                country_code = "Norway"
                logger.info(f"[PRICING] No country specified - defaulting to Norway")

            logger.info(f"[SPECIAL QUERY] Parsed - ingredients: {ingredients}, recipes: {recipes}, country: {country_code}, all_countries: {asks_for_all_countries}")

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

            # Multi-turn follow-up: e.g., Q1 "price of clove" → Q2 "And in India?"
            # NLID correctly classifies Q2 as pricing_info but extracts no ingredient
            # (only the country).  Fall back to the last priced item from session.
            if not item_to_lookup and session.context_entities.last_pricing_item:
                item_to_lookup = session.context_entities.last_pricing_item
                session_item_type = (
                    session.context_entities.last_pricing_item_type or "ingredient"
                )
                check_ingredient_first = (session_item_type == "ingredient")
                logger.info(
                    f"[PRICING] No entity in follow-up query - reusing last priced "
                    f"{session_item_type}: '{item_to_lookup}' "
                    f"with country: '{country_code}'"
                )

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
                        # Ingredient not found - try as recipe if item has multiple words
                        # (multi-word items like "fortune cookies" are likely recipe names, not ingredients)
                        item_word_count = len(item_to_lookup.split()) if item_to_lookup else 0
                        if item_word_count >= 2:
                            logger.info(f"[PRICING] Ingredient not found, trying as recipe (multi-word item): {item_to_lookup}")
                            recipe = self.db.query(Recipe).filter(
                                Recipe.name.ilike(f"%{item_to_lookup}%")
                            ).first()
                            if recipe:
                                logger.info(f"[PRICING] Found recipe: {recipe.name}, looking up cost")
                                cost_data = get_recipe_cost(self.db, str(recipe.id), country_code)
                                if cost_data:
                                    response_data = cost_data
                                    response_data["query_type"] = "recipe_cost"
                                    metadata["recipe"] = recipe.name
                                    metadata["recipe_id"] = str(recipe.id)
                                    metadata["country_code"] = country_code

                        if not response_data:
                            # Still not found anywhere
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

        # Persist the priced item regardless of outcome so follow-up country
        # queries (e.g., "And in India?") can reuse it in subsequent turns.
        if intent == "pricing_info":
            item_name = metadata.get("ingredient") or metadata.get("recipe")
            item_type = "recipe" if metadata.get("recipe") else "ingredient"
            if item_name:
                session.context_entities.last_pricing_item = item_name
                session.context_entities.last_pricing_item_type = item_type
                session.last_intent = "pricing_info"
                logger.info(
                    f"[PRICING] Saved last_pricing_item: '{item_name}' "
                    f"(type={item_type}) to session"
                )

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

            # Create agent for fallback response generation
            from agents import Agent, Runner

            fallback_agent = Agent(
                name="FallbackResponseAgent",
                instructions="\n".join(context_parts),
                model=os.getenv("NLG_AGENT_MODEL")
            )

            result = await Runner.run(fallback_agent, query)
            return result.final_output

        except Exception as e:
            logger.error(f"[LLM FALLBACK] Error generating response: {e}")
            # Final fallback
            return (
                "I don't have specific information in my database for that item. "
                "However, I can help you find recipes, cooking tips, and general food advice! "
                "Would you like me to help you with something else?"
            )

    # =========================================================
    # NEW INTENT HANDLERS
    # =========================================================

    async def _handle_recipe_reference(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle recipe_reference intent - show details for a previously shown recipe.

        Examples: "Explain the 1st recipe", "Tell me about the second one"
        """
        from apps.fastapi.src.agents.agent_tools import get_recipe_details

        entities = nlid_result.get("entities", {})
        reference_position = entities.get("reference_position", 1)
        detail_type = entities.get("detail_type", "full")

        # Handle position words
        if isinstance(reference_position, str):
            position_map = {
                "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                "last": -1, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5
            }
            reference_position = position_map.get(
                reference_position.lower(), int(reference_position) if reference_position.isdigit() else 1
            )

        # Get last recipe results from session
        last_results = session.context_entities.last_recipe_results

        if not last_results:
            return {
                "response": "I don't have any recent recipe results to reference. Try searching for recipes first!",
                "metadata": {"intent": "recipe_reference", "num_results": 0}
            }

        # Handle "last" position
        if reference_position == -1:
            reference_position = len(last_results)

        # Validate position
        if reference_position < 1 or reference_position > len(last_results):
            return {
                "response": f"I can only reference recipes 1 through {len(last_results)}. Please specify a valid recipe number.",
                "metadata": {"intent": "recipe_reference", "num_results": 0, "available_count": len(last_results)}
            }

        # Get the referenced recipe
        target_recipe = last_results[reference_position - 1]
        recipe_id = target_recipe.get("id")

        logger.info(f"[RECIPE_REFERENCE] Referencing recipe {reference_position}: {target_recipe.get('name')}")

        # Get full recipe details
        recipe_details = get_recipe_details(self.db, recipe_id, user_uid, language or "en")

        if not recipe_details:
            return {
                "response": "I couldn't retrieve the details for that recipe.",
                "metadata": {"intent": "recipe_reference", "num_results": 0}
            }

        # Generate response based on detail type using NLG agent
        response = await generate_recipe_detail_response(
            recipe_details, detail_type, query
        )

        # Update session with referenced recipe
        session.context_entities.last_referenced_recipe_id = recipe_id
        session.context_entities.last_referenced_recipe_name = target_recipe.get("name")
        self.session_manager.save_session(session)

        return {
            "response": response,
            "metadata": {
                "intent": "recipe_reference",
                "num_results": 1,
                "recipes": [recipe_details],
                "reference_position": reference_position,
                "detail_type": detail_type
            }
        }

    async def _handle_negative_feedback(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle negative_feedback intent - exclude shown recipes and search again.

        Uses search cache for efficient re-search:
        1. Mark shown recipes as excluded in cache
        2. Try remaining cached candidates first (Tier 1)
        3. If exhausted, do new embedding search (Tier 2)

        Examples: "I don't like these", "Show me different ones"
        """
        # Get IDs of shown recipes
        shown_recipes = session.context_entities.last_recipe_results
        shown_ids = [r.get("id") for r in shown_recipes if r.get("id")]

        if not shown_ids:
            return {
                "response": "I don't have any previous results to exclude. Let me search for recipes for you.",
                "metadata": {"intent": "negative_feedback", "num_results": 0}
            }

        # Add to session's excluded recipes list (persistent)
        session = self.session_manager.add_excluded_recipes(session, shown_ids)

        logger.info(f"[NEGATIVE_FEEDBACK] Excluding {len(shown_ids)} recipes: {shown_ids[:3]}...")

        # Mark these as shown in the search cache as well
        if self.session_manager.has_search_cache(session):
            self.session_manager.add_shown_recipes(session, shown_ids)
            logger.info(f"[NEGATIVE_FEEDBACK] Marked {len(shown_ids)} recipes as shown in cache")

        # Get the last search query
        last_query = session.context_entities.last_vector_query

        if not last_query:
            return {
                "response": "I've noted your preferences. What type of recipes would you like to search for?",
                "metadata": {"intent": "negative_feedback", "num_results": 0, "excluded_count": len(shown_ids)}
            }

        # Clear the last recipe results so the new search doesn't reference old ones
        session.context_entities.last_recipe_results = []
        self.session_manager.save_session(session)

        # Try to use remaining cached candidates first (efficient)
        remaining = self.session_manager.get_remaining_candidates(session)
        if remaining:
            logger.info(f"[NEGATIVE_FEEDBACK] Using {len(remaining)} remaining cached candidates")
            cache = self.session_manager.get_search_cache(session)
            return await self._process_cached_candidates(
                remaining,
                cache,
                session,
                user_uid,
                language,
                is_tier1=True
            )

        # Re-run the search with excluded recipes
        # Recursively call process_query with the last query
        return await self.process_query(
            query=last_query,
            session_id=session.session_id,
            user_uid=user_uid,
            language=language or "en"
        )

    async def _handle_educational_info(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle educational_info intent - provide cooking knowledge + related recipes.

        Examples: "What is vegan food?", "How do I sauté?"
        """
        entities = nlid_result.get("entities", {})
        concept = entities.get("concept") or entities.get("technique")

        # Find related recipes if concept maps to a tag
        concept_to_tag = {
            "vegan": "vegan",
            "vegetarian": "vegetarian",
            "keto": "keto",
            "gluten-free": "gluten-free",
            "dairy-free": "dairy-free",
            "paleo": "paleo",
        }

        related_recipes = []
        tag = concept_to_tag.get(concept.lower() if concept else "", "")

        try:
            if tag:
                # Search for related recipes using embedding
                embedding_results = search_recipes_by_embedding(
                    self.db,
                    query_text=f"{concept} recipes",
                    limit=3,
                    threshold=0.5,
                    language_id=language or "en"
                )

                # Process through full post-processing to get complete recipe details
                if embedding_results:
                    # Convert to row format for post-processing
                    rows = [
                        {
                            "id": r.id,
                            "name": r.name,
                            "ingress": r.ingress,
                            "image": r.image,
                            "difficulty": r.difficulty,
                            "prepTime": r.prepTime,
                            "cookTime": r.cookTime,
                            "servings": r.servings,
                            "total_time": (r.prepTime or 0) + (r.cookTime or 0)
                        }
                        for r, score in embedding_results
                    ]

                    # Build similarity scores dict
                    similarity_scores = {str(r.id): score for r, score in embedding_results}

                    # Use the full post-processing pipeline
                    session_context = {
                        "user_uid": user_uid,
                        "language": language or "en"
                    }
                    related_recipes = self._post_process_and_rank(
                        rows, similarity_scores, session_context
                    )

            # Generate educational response using NLG agent (shorter format)
            educational_response = await self._generate_educational_response_short(
                query, concept, related_recipes
            )

            # Add to conversation history
            session.add_to_history("assistant", educational_response)
            self.session_manager.save_session(session)

            return {
                "response": educational_response,
                "metadata": {
                    "intent": "educational_info",
                    "is_cooking_related": True,
                    "concept": concept,
                    "recipes": related_recipes,
                    "num_results": len(related_recipes)
                }
            }

        except Exception as e:
            logger.error(f"[EDUCATIONAL_INFO] Error generating response: {e}")
            return {
                "response": f"I can help explain {concept or 'cooking concepts'}. What specific aspect would you like to know about?",
                "metadata": {"intent": "educational_info", "concept": concept, "error": str(e)}
            }

    async def _handle_clear_filters(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle clear_filters intent - reset session filters.

        Examples: "Clear filters", "Start over", "Reset search"
        """
        # Store what was cleared for the response
        cleared_items = []
        if session.filters.tags:
            cleared_items.append(f"tags: {', '.join(session.filters.tags)}")
        if session.filters.cuisines:
            cleared_items.append(f"cuisines: {', '.join(session.filters.cuisines)}")
        if session.excluded_ingredients:
            cleared_items.append(f"excluded ingredients: {', '.join(session.excluded_ingredients[:5])}")
        if session.excluded_recipe_ids:
            cleared_items.append(f"excluded recipes: {len(session.excluded_recipe_ids)} recipes")

        # Clear all filters using the session manager
        session = self.session_manager.clear_filters(session)

        response = "I've cleared all your filters and preferences."
        if cleared_items:
            response += f" Removed: {', '.join(cleared_items)}."
        response += " What would you like to search for?"

        logger.info(f"[CLEAR_FILTERS] Cleared filters for session {session.session_id}")

        return {
            "response": response,
            "metadata": {
                "intent": "clear_filters",
                "is_cooking_related": True,
                "num_results": 0,
                "cleared_items": cleared_items
            }
        }

    async def _generate_educational_response_short(
        self,
        query: str,
        concept: Optional[str],
        recipes: List[Dict[str, Any]]
    ) -> str:
        """
        Generate a short, bulleted educational response.
        """
        # Build concept explanation based on the concept
        concept_explanations = {
            "vegan": {
                "definition": "A vegan diet excludes all animal products including meat, dairy, eggs, and honey.",
                "includes": ["Vegetables", "Fruits", "Legumes", "Nuts", "Seeds", "Grains", "Plant-based alternatives"],
                "excludes": ["Meat", "Fish", "Dairy", "Eggs", "Honey", "Gelatin"]
            },
            "vegetarian": {
                "definition": "A vegetarian diet excludes meat but may include dairy and eggs.",
                "includes": ["Vegetables", "Fruits", "Dairy", "Eggs", "Legumes", "Nuts", "Grains"],
                "excludes": ["Meat", "Fish", "Seafood"]
            },
            "keto": {
                "definition": "A ketogenic diet is high-fat, low-carb to promote fat burning.",
                "includes": ["Healthy fats", "Proteins", "Low-carb vegetables", "Nuts", "Cheese"],
                "excludes": ["Bread", "Pasta", "Sugar", "Most fruits", "Starchy vegetables"]
            },
            "gluten-free": {
                "definition": "A gluten-free diet excludes wheat, barley, rye, and their derivatives.",
                "includes": ["Rice", "Corn", "Quinoa", "Vegetables", "Fruits", "Meat", "Dairy"],
                "excludes": ["Wheat", "Barley", "Rye", "Bread", "Pasta (unless GF)"]
            },
            "dairy-free": {
                "definition": "A dairy-free diet excludes milk and milk-based products.",
                "includes": ["Plant milks", "Vegetables", "Fruits", "Meat", "Nuts", "Coconut products"],
                "excludes": ["Milk", "Cheese", "Butter", "Cream", "Yogurt"]
            },
            "paleo": {
                "definition": "A paleo diet focuses on foods available to Paleolithic humans.",
                "includes": ["Lean meats", "Fish", "Vegetables", "Fruits", "Nuts", "Seeds"],
                "excludes": ["Grains", "Legumes", "Dairy", "Processed foods", "Refined sugar"]
            }
        }

        concept_lower = concept.lower() if concept else ""
        info = concept_explanations.get(concept_lower)

        if info:
            response_parts = [
                f"**{concept.title()}**\n",
                info["definition"],
                "",
                "• **Includes:** " + ", ".join(info["includes"]),
                "• **Excludes:** " + ", ".join(info["excludes"])
            ]

            if recipes:
                response_parts.append("")
                response_parts.append(f"**Related {concept} recipes:**")
                for r in recipes[:3]:
                    time_str = f"{r.get('total_time', 0)} min" if r.get('total_time') else ""
                    response_parts.append(f"• {r.get('name', 'Recipe')} ({time_str})")

            return "\n".join(response_parts)

        # Generic response for unknown concepts
        return f"**{concept.title() if concept else 'Cooking Concept'}**\n\nThis is a cooking/dietary concept. Would you like to search for related recipes?"

    async def _handle_combined_meal_search(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle combined_meal_search intent - find multiple courses for a meal.

        Examples: "3-course dinner", "Appetizer and main course under $50"
        """
        from apps.fastapi.src.agents.agent_tools import search_recipes_by_embedding

        entities = nlid_result.get("entities", {})
        meal_types = entities.get("meal_types", ["appetizer", "main course", "dessert"])
        course_count = entities.get("course_count", 3)
        parameters = nlid_result.get("parameters", {})
        combined_budget = parameters.get("combined_budget")

        # Limit to available courses
        meal_types = meal_types[:course_count]

        logger.info(f"[COMBINED_MEAL] Searching for {len(meal_types)} courses: {meal_types}")

        # Search for each course type
        meal_recipes = {}
        for meal_type in meal_types:
            course_query = f"{meal_type} recipes"

            embedding_results = search_recipes_by_embedding(
                self.db,
                query_text=course_query,
                limit=5,
                threshold=0.4,
                language_id=language or "en"
            )

            if embedding_results:
                # Take the best match
                recipe = embedding_results[0][0]
                meal_recipes[meal_type] = {
                    "id": str(recipe.id),
                    "name": recipe.name,
                    "image": recipe.image,
                    "difficulty": recipe.difficulty,
                    "total_time": (recipe.prepTime or 0) + (recipe.cookTime or 0)
                }

        if not meal_recipes:
            return {
                "response": "I couldn't find recipes for your meal combination. Try adjusting your preferences.",
                "metadata": {"intent": "combined_meal_search", "num_results": 0}
            }

        # Generate response using NLG agent
        response = await generate_combined_meal_response(
            query, meal_recipes, combined_budget
        )

        return {
            "response": response,
            "metadata": {
                "intent": "combined_meal_search",
                "is_cooking_related": True,
                "num_results": len(meal_recipes),
                "meal_types": meal_types,
                "recipes": list(meal_recipes.values())
            }
        }

    async def _handle_show_more(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """
        Handle show_more intent - retrieve additional results from cached candidates or new search.

        Multi-tier fallback:
        - Tier 1: Use remaining cached embedding candidates
        - Tier 2: Fetch new embedding candidates (if offset < max)
        - Tier 3: No more results available

        Examples: "Show me more", "More recipes", "Any others?"
        """
        from apps.fastapi.src.agents.agent_tools import search_recipes_by_embedding

        # Check if cache is expired
        if self.session_manager.is_cache_expired(session):
            logger.info("[SHOW_MORE] Cache expired, starting fresh search")
            return await self._start_fresh_search(session, user_uid, language)

        # Check if we have cached candidates
        if not self.session_manager.has_search_cache(session):
            logger.info("[SHOW_MORE] No search cache found, starting fresh search")
            return await self._start_fresh_search(session, user_uid, language)

        # Get cache info
        cache = self.session_manager.get_search_cache(session)
        logger.info(f"[SHOW_MORE] Found cache with {len(cache.get('embedding_candidates', []))} candidates")

        # Get remaining candidates that haven't been shown
        remaining_candidates = self.session_manager.get_remaining_candidates(session)
        all_shown_ids = self.session_manager.get_all_shown_recipe_ids(session)

        logger.info(f"[SHOW_MORE] Remaining candidates: {len(remaining_candidates)}, Already shown: {len(all_shown_ids)}")

        # TIER 1: Use remaining cached candidates
        if remaining_candidates:
            logger.info(f"[SHOW_MORE] TIER 1: Using {len(remaining_candidates)} remaining cached candidates")
            return await self._process_cached_candidates(
                remaining_candidates,
                cache,
                session,
                user_uid,
                language,
                is_tier1=True
            )

        # TIER 2: Fetch new embedding candidates if possible
        if self.session_manager.can_fetch_more(session):
            logger.info("[SHOW_MORE] TIER 2: Cache exhausted, fetching new embedding candidates")
            return await self._fetch_new_candidates(
                cache,
                session,
                user_uid,
                language
            )

        # TIER 3: No more results
        logger.info("[SHOW_MORE] TIER 3: No more results available")
        return {
            "response": "I've shown you all the available recipes matching your criteria. Would you like to try a different search or adjust your preferences?",
            "metadata": {
                "intent": "show_more",
                "is_cooking_related": True,
                "num_results": 0,
                "tier": 3,
                "message": "no_more_results",
                "recipes": []
            }
        }

    async def _start_fresh_search(
        self,
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """Start a fresh search when no cache is available."""
        # Clear the old cache
        self.session_manager.clear_search_cache(session)

        # Get the last user query if available
        last_query = session.context_entities.last_user_query or session.context_entities.last_vector_query

        if not last_query:
            return {
                "response": "I don't have any previous search context. What recipes would you like me to find?",
                "metadata": {"intent": "show_more", "num_results": 0, "message": "no_context", "recipes": []}
            }

        # Re-run the search
        return await self.process_query(
            query=last_query,
            session_id=session.session_id,
            user_uid=user_uid,
            language=language or "en"
        )

    async def _process_cached_candidates(
        self,
        candidate_ids: List[str],
        cache: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str],
        is_tier1: bool = True
    ) -> Dict[str, Any]:
        """Process cached candidates with SQL filtering and full recipe enrichment."""
        from apps.fastapi.src.utils.sql_builders import build_session_filter_conditions
        import json

        # Get SQL filters from cache
        sql_filters = cache.get("sql_filters", {})
        excluded_ingredients = sql_filters.get("excluded_ingredients", [])

        # Add currently shown recipes to exclusion
        shown_ids = self.session_manager.get_all_shown_recipe_ids(session)
        excluded_recipe_ids = session.excluded_recipe_ids + shown_ids

        # Build SQL to filter candidates - fetch full recipe data including metadata
        candidate_list = ", ".join([f"'{cid}'" for cid in candidate_ids])

        base_query = f"""
            SELECT DISTINCT r.id, r.name, r.ingress,
                   r.difficulty, r."prepTime" as prep_time, r."cookTime" as cook_time,
                   r.image, r.servings, r."userUid" as creator_uid,
                   r."private", r."deletedAt", r.recipe_metadata
            FROM recipe r
            WHERE r.id IN ({candidate_list})
              AND r."deletedAt" IS NULL
              AND r."status" = 'published'
        """

        # Add exclusion conditions
        conditions = []

        # Exclude already shown recipes
        if excluded_recipe_ids:
            excluded_list = ", ".join([f"'{rid}'" for rid in excluded_recipe_ids])
            conditions.append(f"r.id NOT IN ({excluded_list})")

        # Add allergen exclusions
        if excluded_ingredients:
            for ingredient in excluded_ingredients[:20]:  # Limit to prevent huge queries
                escaped = ingredient.replace("'", "''")
                conditions.append(f"r.ingredients_text NOT ILIKE '%{escaped}%'")

        # Add session filters
        session_filters_dict = {
            "included_ingredients": [],
            "excluded_ingredients": session.excluded_ingredients or [],
            "tags": [],
            "cuisines": [],
            "categories": [],
            "max_time": session.filters.max_time if hasattr(session, 'filters') else None,
            "difficulty": session.filters.difficulty if hasattr(session, 'filters') else None,
        }
        session_conditions = build_session_filter_conditions(session_filters_dict)
        conditions.extend(session_conditions)

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        base_query += " ORDER BY r.name LIMIT 5"

        logger.info(f"[SHOW_MORE] Executing SQL for {len(candidate_ids)} candidates:\n{base_query}")

        try:
            result = self.db.execute(text(base_query))
            rows = result.fetchall()

            if not rows:
                # No results from cached candidates, try Tier 2
                logger.info("[SHOW_MORE] No results from cached candidates, trying Tier 2")
                if self.session_manager.can_fetch_more(session):
                    return await self._fetch_new_candidates(cache, session, user_uid, language)
                return {
                    "response": "I couldn't find more recipes matching your criteria. Would you like to try a different search?",
                    "metadata": {"intent": "show_more", "num_results": 0, "message": "filtered_out", "recipes": []}
                }

            # Collect unique recipe IDs for batch queries (keep as UUIDs for PostgreSQL)
            from uuid import UUID
            recipe_ids = [row[0] if isinstance(row[0], UUID) else UUID(row[0]) for row in rows]

            # =====================================================
            # BATCH QUERY: Load all ingredients at once
            # =====================================================
            ingredients_map = {}
            ingredient_results = self.db.execute(text("""
                SELECT
                    ri."recipeId", ri.amount, ri."unitId", ri.order as ri_order,
                    i.id as ing_id, i.name as ing_name,
                    mut.name as unit_name
                FROM recipe_ingredient ri
                JOIN ingredient i ON ri."ingredientId" = i.id
                LEFT JOIN measuring_unit_translation mut ON ri."unitId" = mut."measuringUnitId" AND mut."languageId" = 'en'
                WHERE ri."recipeId" = ANY(CAST(:recipe_ids AS uuid[]))
                AND ri."deletedAt" IS NULL
                ORDER BY ri."recipeId", ri.order
            """), {"recipe_ids": [str(rid) for rid in recipe_ids]}).fetchall()

            for ir in ingredient_results:
                rid = str(ir[0])
                if rid not in ingredients_map:
                    ingredients_map[rid] = []
                ingredients_map[rid].append({
                    "name": ir[5],
                    "amount": ir[1],
                    "unit": ir[6],
                    "unit_id": str(ir[2]) if ir[2] else None
                })

            # =====================================================
            # BATCH QUERY: Load all instructions at once
            # =====================================================
            instructions_map = {}
            instruction_results = self.db.execute(text("""
                SELECT "recipeId", "order", description, image
                FROM recipe_instruction
                WHERE "recipeId" = ANY(CAST(:recipe_ids AS uuid[]))
                AND "deletedAt" IS NULL
                ORDER BY "recipeId", "order"
            """), {"recipe_ids": [str(rid) for rid in recipe_ids]}).fetchall()

            for instr in instruction_results:
                rid = str(instr[0])
                if rid not in instructions_map:
                    instructions_map[rid] = []
                instructions_map[rid].append({
                    "order": instr[1],
                    "description": instr[2],
                    "image": instr[3]
                })

            # =====================================================
            # BATCH QUERY: Load all seasonality info at once
            # =====================================================
            seasonality_map = {}
            seasonality_results = self.db.execute(text("""
                SELECT rs."recipeId", st.name, st."languageId", s.type
                FROM recipe_seasonality rs
                JOIN seasonality s ON rs."seasonalityId" = s.id
                JOIN seasonality_translation st ON s.id = st."seasonalityId"
                WHERE rs."recipeId" = ANY(CAST(:recipe_ids AS uuid[]))
                ORDER BY rs."recipeId", s.type, st."languageId"
            """), {"recipe_ids": [str(rid) for rid in recipe_ids]}).fetchall()

            for se in seasonality_results:
                rid = str(se[0])
                if rid not in seasonality_map:
                    seasonality_map[rid] = {"weather": [], "festival": []}
                seasonality_type = "weather" if se[3] == "WEATHER" else "festival"
                if se[2] == "en":
                    seasonality_map[rid][seasonality_type].append(se[1])

            # =====================================================
            # BATCH QUERY: Load bundle info
            # =====================================================
            bundle_info_map = {}
            bundle_results = self.db.execute(text("""
                SELECT br."recipeId", br."bundleId", b.name, br."isFree", b."userUid"
                FROM bundle_recipe br
                JOIN bundle b ON br."bundleId" = b.id
                WHERE br."recipeId" = ANY(CAST(:recipe_ids AS uuid[]))
            """), {"recipe_ids": [str(rid) for rid in recipe_ids]}).fetchall()

            for br in bundle_results:
                rid = str(br[0])
                if rid not in bundle_info_map:
                    bundle_info_map[rid] = []
                bundle_info_map[rid].append({
                    "bundle_id": str(br[1]),
                    "bundle_name": br[2],
                    "is_free": br[3],
                    "bundle_owner": br[4]
                })

            # =====================================================
            # FETCH USER CONTEXT
            # =====================================================
            user_ctx = None
            if user_uid:
                user_ctx = self.user_context_service.get_user_context(user_uid)

            # =====================================================
            # PROCESS RECIPES WITH FULL ENRICHMENT
            # =====================================================
            recipes = []
            recipe_ids_to_show = []
            seen_ids = set()

            for row in rows:
                recipe_id = str(row[0])

                # Skip duplicates
                if recipe_id in seen_ids:
                    continue
                seen_ids.add(recipe_id)

                # Access control: exclude private recipes (unless owner)
                # Column indices: 0=id, 1=name, 2=ingress, 3=difficulty, 4=prep_time, 5=cook_time
                #                 6=image, 7=servings, 8=creator_uid, 9=private, 10=deletedAt, 11=recipe_metadata
                is_private = row[9]
                creator_uid = str(row[8]) if row[8] else None
                if is_private:
                    if user_uid and creator_uid == user_uid:
                        pass  # Owner can access
                    else:
                        logger.info(f"[SHOW_MORE] Skipping private recipe: {recipe_id}")
                        continue

                # Check bundle access
                bundle_entries = bundle_info_map.get(recipe_id, [])
                is_bundle_recipe = len(bundle_entries) > 0
                is_bundle_free_recipe = any(b["is_free"] for b in bundle_entries)

                bundle_name = None
                if bundle_entries:
                    free_bundle = next((b for b in bundle_entries if b["is_free"]), None)
                    bundle_to_show = free_bundle if free_bundle else bundle_entries[0]
                    bundle_name = bundle_to_show["bundle_name"]

                # Check if user owns bundle (for access control)
                should_show_name_only = False
                if is_bundle_recipe and not is_bundle_free_recipe:
                    user_owns_any_bundle = False
                    if user_ctx:
                        user_owns_any_bundle = any(
                            b["bundle_owner"] == user_uid
                            for b in bundle_entries
                        )
                    if not user_owns_any_bundle:
                        should_show_name_only = True

                if should_show_name_only:
                    # Name-only access for bundle recipes user doesn't own
                    recipes.append({
                        "id": recipe_id,
                        "name": row[1],
                        "image": row[6],
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
                        "similarity": 0.7,
                        "priority_score": 0,
                        "is_liked": False,
                        "is_created": False,
                        "recipe_cost": None,
                        "nutritional_info": None,
                        "seasonality": {"weather": [], "festival": []},
                    })
                    recipe_ids_to_show.append(recipe_id)
                    continue

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

                # Extract recipe_cost and nutritional_info from metadata
                recipe_cost = None
                nutritional_info = None
                recipe_metadata = row[11]  # recipe_metadata column (index 11)

                if recipe_metadata:
                    metadata = recipe_metadata
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except Exception:
                            metadata = {}

                    if "pricing" in metadata:
                        pricing = metadata["pricing"]
                        recipe_cost = {
                            "usa": {
                                "total": pricing.get("usa", {}).get("total"),
                                "currency": pricing.get("usa", {}).get("currency", "USD")
                            },
                            "india": {
                                "total": pricing.get("india", {}).get("total"),
                                "currency": pricing.get("india", {}).get("currency", "INR")
                            },
                            "norway": {
                                "total": pricing.get("norway", {}).get("total"),
                                "currency": pricing.get("norway", {}).get("currency", "NOK")
                            }
                        }

                    if "totalNutrition" in metadata:
                        nutrition = metadata["totalNutrition"]
                        nutritional_info = {
                            "macros": nutrition.get("macros", {}),
                            "micros": nutrition.get("micros", {})
                        }

                # Get pre-loaded data
                ingredient_list = ingredients_map.get(recipe_id, [])
                instruction_list = instructions_map.get(recipe_id, [])
                seasonality = seasonality_map.get(recipe_id, {"weather": [], "festival": []})

                prep_time = row[4]
                cook_time = row[5]
                total_time = (prep_time or 0) + (cook_time or 0)

                recipes.append({
                    "id": recipe_id,
                    "name": row[1],
                    "ingress": row[2],
                    "description": row[2],
                    "difficulty": row[3],
                    "prep_time": prep_time,
                    "cook_time": cook_time,
                    "total_time": total_time,
                    "image": row[6],
                    "servings": row[7],
                    "similarity": 0.7,
                    "priority_score": priority_score,
                    "access_level": "full",
                    "is_liked": is_liked,
                    "is_created": is_created,
                    "is_bundle_recipe": is_bundle_recipe,
                    "is_bundle_free_recipe": is_bundle_free_recipe,
                    "bundle_name": bundle_name,
                    "ingredients": ingredient_list,
                    "instructions": instruction_list,
                    "recipe_cost": recipe_cost,
                    "nutritional_info": nutritional_info,
                    "seasonality": seasonality,
                })
                recipe_ids_to_show.append(recipe_id)

            # Sort by priority_score (desc), then similarity (desc)
            recipes.sort(key=lambda x: (x["priority_score"], x["similarity"]), reverse=True)

            # Mark these recipes as shown
            self.session_manager.add_shown_recipes(session, recipe_ids_to_show)

            # Update last_recipe_results for reference queries
            self.session_manager.update_last_recipe_results(session, recipes, "show more")
            self.session_manager.save_session(session)

            logger.info(f"[SHOW_MORE] Returning {len(recipes)} enriched recipes from cache (Tier 1)")

            # Generate response
            response = await generate_recipe_response(
                "Show me more recipes",
                recipes,
                None,
                None,
                filter_context=self._build_nlg_filter_context(session)
            )

            return {
                "response": response,
                "metadata": {
                    "intent": "show_more",
                    "is_cooking_related": True,
                    "num_results": len(recipes),
                    "tier": 1 if is_tier1 else 2,
                    "source": "cached_candidates",
                    "recipes": recipes
                }
            }

        except Exception as e:
            logger.error(f"[SHOW_MORE] Error processing cached candidates: {e}")
            import traceback
            logger.error(f"[SHOW_MORE] Traceback: {traceback.format_exc()}")
            return {
                "response": "I encountered an error while fetching more recipes. Please try again.",
                "metadata": {"intent": "show_more", "num_results": 0, "error": str(e), "recipes": []}
            }

    async def _fetch_new_candidates(
        self,
        cache: Dict[str, Any],
        session: SessionState,
        user_uid: Optional[str],
        language: Optional[str]
    ) -> Dict[str, Any]:
        """Fetch new embedding candidates when cache is exhausted."""
        from apps.fastapi.src.agents.agent_tools import search_recipes_by_embedding
        from apps.fastapi.src.services.session_memory_manager import EMBEDDING_BATCH_SIZE

        vector_query = cache.get("vector_query")
        current_offset = cache.get("embedding_offset", 0)
        sql_filters = cache.get("sql_filters", {})

        if not vector_query:
            return {
                "response": "I don't have enough context to find more recipes. What would you like to search for?",
                "metadata": {"intent": "show_more", "num_results": 0, "message": "no_query", "recipes": []}
            }

        # Fetch new batch of embedding candidates
        new_offset = current_offset + EMBEDDING_BATCH_SIZE
        logger.info(f"[SHOW_MORE] Fetching new embeddings with offset {new_offset}")

        try:
            # Get more candidates - fetch a larger batch
            embedding_results = search_recipes_by_embedding(
                self.db,
                query_text=vector_query,
                limit=EMBEDDING_BATCH_SIZE,
                threshold=0.35,  # Slightly lower threshold for "more" results
                language_id=language or "en",
                offset=new_offset
            )

            if not embedding_results:
                logger.info("[SHOW_MORE] No new embedding candidates found")
                return {
                    "response": "I've searched but couldn't find more recipes matching your criteria. Would you like to try different filters or a new search?",
                    "metadata": {
                        "intent": "show_more",
                        "is_cooking_related": True,
                        "num_results": 0,
                        "tier": 2,
                        "message": "no_new_candidates",
                        "recipes": []
                    }
                }

            # Store new candidates in cache
            new_candidate_ids = [str(r.id) for r, _ in embedding_results]
            self.session_manager.store_embedding_candidates(session, new_candidate_ids, new_offset)

            # Update SQL filters with current exclusions
            all_exclusions = session.excluded_ingredients or []
            if sql_filters.get("excluded_ingredients"):
                all_exclusions = list(set(all_exclusions + sql_filters["excluded_ingredients"]))
            sql_filters["excluded_ingredients"] = all_exclusions
            self.session_manager.update_sql_filters(session, sql_filters)

            logger.info(f"[SHOW_MORE] Stored {len(new_candidate_ids)} new candidates at offset {new_offset}")

            # Process the new candidates
            return await self._process_cached_candidates(
                new_candidate_ids,
                cache,
                session,
                user_uid,
                language,
                is_tier1=False
            )

        except Exception as e:
            logger.error(f"[SHOW_MORE] Error fetching new candidates: {e}")
            return {
                "response": "I encountered an error while searching for more recipes. Please try again.",
                "metadata": {"intent": "show_more", "num_results": 0, "error": str(e), "recipes": []}
            }
