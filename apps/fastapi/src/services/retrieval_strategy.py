"""
Retrieval Strategy Decision Stage
Decides whether to use SQL-only, Embeddings-only, or Hybrid retrieval
"""
from typing import Dict, Any, Literal, Optional, List
from enum import Enum
from dataclasses import dataclass

from apps.fastapi import logger

# Ingredient synonym mapping for common ingredient name variations
INGREDIENT_SYNONYMS = {
    "chole": ["chickpeas", "garbanzo beans", "chana"],
    "chana": ["chickpeas", "garbanzo beans", "chole"],
    "aloo": ["potatoes", "potato"],
    "gobi": ["cauliflower"],
    "matar": ["peas"],
    "palak": ["spinach"],
    "dal": ["lentils"],
    "rajma": ["kidney beans", "red kidney beans"],
    "bhindi": ["okra"],
    "bindi": ["okra"],
    "sarson": ["mustard greens"],
    "makki": ["corn flour", "cornmeal"],
}


class RetrievalStrategy(str, Enum):
    """Retrieval strategy types"""
    SQL_ONLY = "sql_only"
    EMBEDDINGS_ONLY = "embeddings_only"
    HYBRID_VECTOR_TO_SQL = "hybrid_vector_to_sql"  # Vector search first, then SQL filter
    HYBRID_SQL_TO_VECTOR = "hybrid_sql_to_vector"  # SQL filter first, then vector re-rank


@dataclass
class RetrievalPlan:
    """
    Retrieval execution plan

    Created by Decision Stage, consumed by Execution Stage
    """
    strategy: RetrievalStrategy
    reasoning: str
    vector_query: Optional[str] = None  # Query for embedding search
    sql_filters: Dict[str, Any] = None  # Filters for SQL
    top_k: int = 20  # Number of candidates to retrieve
    use_reranking: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "strategy": self.strategy.value,
            "reasoning": self.reasoning,
            "vector_query": self.vector_query,
            "sql_filters": self.sql_filters or {},
            "top_k": self.top_k,
            "use_reranking": self.use_reranking
        }


class RetrievalStrategyDecider:
    """
    Decision Stage for Retrieval Strategy

    Determines the best retrieval approach based on:
    - Query structure (structured vs fuzzy)
    - Available filters
    - Intent type
    """

    # Keywords that indicate structured queries
    STRUCTURED_KEYWORDS = [
        "under", "less than", "more than", "minutes", "easy", "medium", "hard",
        "servings", "portion", "vegetarian", "vegan", "gluten-free", "dairy-free",
        "quick", "slow", "no ", "without", "except", "allergic"
    ]

    # Keywords that indicate fuzzy/semantic queries
    FUZZY_KEYWORDS = [
        "healthy", "delicious", "tasty", "yummy", "comforting", "refreshing",
        "light", "heavy", "rich", "simple", "elegant", "festive", "hearty",
        "ideas", "suggestions", "recommend", "inspiration"
    ]

    # Direct recipe search patterns
    DIRECT_RECIPE_PATTERNS = [
        "how to make", "how do i make", "recipe for", "show me recipe",
        "i want to cook", "i want to make", "can you teach me"
    ]

    def __init__(self):
        pass

    def _expand_ingredient_synonyms(
        self,
        ingredients: List[str]
    ) -> List[str]:
        """
        Expand ingredient list with synonyms

        Args:
            ingredients: List of ingredient names

        Returns:
            Expanded list including original ingredients and their synonyms
        """
        expanded = set(ingredients)  # Start with originals

        for ingredient in ingredients:
            ingredient_lower = ingredient.lower().strip()
            # Check if this ingredient has known synonyms
            for key, synonyms in INGREDIENT_SYNONYMS.items():
                if ingredient_lower == key or ingredient_lower in [s.lower() for s in synonyms]:
                    # Add all synonyms
                    expanded.update(synonyms)
                    expanded.add(key)

        return list(expanded)

    # Refinement detection patterns - these indicate user is refining previous search
    REFINEMENT_PATTERNS = [
        "allergic to", "allergy", "don't like", "dont like", "hate",
        "without", "no ", "except", "but no", "not include",
        "prefer", "instead of", "make it", "change to",
        "i am vegetarian", "i'm vegetarian", "im vegetarian",  # Dietary preferences
        "i am vegan", "i'm vegan", "im vegan",
        "i am gluten-free", "i'm gluten-free", "gluten free",
        "show me", "only", "just", "asian", "indian", "italian",  # Cuisine/type refinements
        # Additional exclusion patterns (not having something = exclude it)
        "don't have", "dont have", "i have no", "i don't have",
        "ran out of", "out of", "don't got", "dont got",
        "missing", "can't find", "cant find",
        # Non-veg / vegetarian related patterns
        "non veg", "non-veg", "don't eat non", "dont eat non",
        "i don't eat", "i dont eat", "no non veg", "no non-veg",
        "vegetarian only", "veg only", "no meat", "meat free",
    ]

    # Intents that typically indicate refinement vs new search
    REFINEMENT_INTENTS = ["ingredient_substitution", "nutritional_info"]

    def _extract_positive_vector_query(
        self,
        query: str,
        filters: Dict[str, Any]
    ) -> str:
        """
        Strip negative/exclusion language from the query so that the embedding
        search targets what the user *wants*, not what they want to avoid.

        Examples:
          "I dont like chicken, suggest me something"  → "suggest something"
          "vegetarian recipes without dairy"           → "vegetarian recipes"
          "something sweet but no nuts"                → "something sweet"
          "easy quick, vegetarian, allergic to dairy"  → "easy quick vegetarian"
        """
        import re

        q = query.lower()

        # Ordered from most-specific to least-specific so broad patterns
        # don't swallow more specific ones.
        STRIP_PATTERNS = [
            # "remember I am a vegetarian" → keep "vegetarian" but strip the frame
            r"remember\s+i\s+(?:'?m|am)\s+",
            # "I am / I'm allergic to X", "I am intolerant to X"
            r"(?:i\s+)?(?:i'?m|i\s+am)\s+(?:allergic|intolerant)\s+to\s+[\w\s]{1,30}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # "allergic to X"
            r"\ballergic\s+to\s+[\w\s]{1,20}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # "I don't like/want/eat/have/use X"
            r"(?:i\s+)?(?:don'?t|do\s+not)\s+(?:like|want|eat|have|use)\s+[\w\s]{1,20}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # "without X"
            r"\bwithout\s+[\w\s]{1,20}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # "but no X / except X / avoid X"
            r"\b(?:but\s+no|except|avoid(?:ing)?)\s+[\w\s]{1,20}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # "I hate X"
            r"(?:i\s+)?hate\s+[\w\s]{1,20}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # "can't eat X"
            r"can'?t\s+eat\s+[\w\s]{1,20}?(?=\s*[,.]|\s+and\b|\s+but\b|$)",
            # Standalone "no X"
            r"\bno\s+[\w]+(?:\s+[\w]+)?(?=\s+|,|\.|$)",
        ]

        for pat in STRIP_PATTERNS:
            q = re.sub(pat, " ", q, flags=re.IGNORECASE)

        # Clean up punctuation and whitespace
        q = re.sub(r'[,;]+', ' ', q)
        q = re.sub(r'\s+', ' ', q).strip().strip('.,;:!?')

        # Remove pure stop-words to check meaningful residual content
        STOP_WORDS = {
            'i', 'me', 'my', 'can', 'you', 'suggest', 'please', 'a', 'an', 'the',
            'and', 'or', 'but', 'that', 'some', 'is', 'are', 'was', 'be',
            'do', 'does', 'did', 'have', 'has', 'will', 'would', 'could', 'should',
            'tell', 'give', 'show', 'get', 'make', 'want', 'need',
            'something', 'anything', 'everything', 'it', 'its',
            'with', 'for', 'from', 'to', 'at', 'by', 'on', 'in',
        }
        tokens = [w for w in q.split() if w not in STOP_WORDS and len(w) > 1]

        if len(tokens) >= 2:
            return ' '.join(tokens)

        # Fallback: build from positive filter context
        positive_parts: List[str] = []
        if filters.get("tags"):
            positive_parts.extend(filters["tags"])
        if filters.get("included_ingredients"):
            positive_parts.extend(filters["included_ingredients"][:2])
        if filters.get("difficulty"):
            positive_parts.append(filters["difficulty"])
        if positive_parts:
            return ' '.join(positive_parts) + ' recipes'

        return 'recipe suggestions'

    def _is_query_refinement(
        self,
        query: str,
        intent: str,
        session_context: Dict[str, Any],
        nlid_result: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str]:
        """
        Detect if user is refining previous search vs starting a new one

        Args:
            query: Current user query
            intent: Detected intent from NLID
            session_context: Session context with previous searches
            nlid_result: Current NLID detection result (optional)

        Returns:
            Tuple of (is_refinement, original_vector_query)
        """
        # Check if there's a previous search to refine
        context_entities = session_context.get("context_entities", {})
        last_vector_query = context_entities.get("last_vector_query")
        last_intent = session_context.get("last_intent")

        # Get conversation history to find the most recent recipe search
        conversation_history = session_context.get("conversation_history", [])

        # If we have a previous recipe search context, use it
        has_recipe_search_context = last_vector_query and last_intent == "recipe_search"

        # If not, look through conversation history for the most recent recipe search
        if not has_recipe_search_context and conversation_history:
            # Look backwards through conversation history to find most recent recipe search
            for message in reversed(conversation_history):
                # Note: The conversation history items may have different structures
                # For now, let's check if message has the expected metadata structure
                metadata = message.get("meta", {}) or message.get("metadata", {})
                if metadata.get("intent") == "recipe_search":
                    # Try to extract vector_query from metadata
                    meta_data = metadata.get("metadata", {}) if "metadata" in message else metadata
                    if meta_data.get("vector_query"):
                        last_vector_query = meta_data.get("vector_query")
                        has_recipe_search_context = True
                        break

        # No previous search context
        if not has_recipe_search_context:
            return False, ""

        query_lower = query.lower()

        # Check if current query has refinement patterns
        has_refinement_pattern = any(
            pattern in query_lower for pattern in self.REFINEMENT_PATTERNS
        )

        # Check if intent is typically a refinement
        is_refinement_intent = intent in self.REFINEMENT_INTENTS

        # Check if current query is short (typically refinements are brief)
        is_short_query = len(query.split()) <= 6

        # Check if current query lacks new ingredients (refinements usually add constraints)
        # Get and merge filters from both NLID result (fresh) and session context (stale/persistent)
        # This preserves filters across conversation for proper refinements

        # Start with existing filters from session context (persistent across turns)
        existing_filters = session_context.get("filters", {})

        # Get fresh filters from current NLID detection
        fresh_filters = (nlid_result or {}).get("filters", {})

        # Scalar-only filter keys: these must never be accumulated into a list.
        # Using the freshest value (from current NLID output) always wins.
        # List-type keys (tags, cuisines, excluded_ingredients, etc.) are merged/extended.
        SCALAR_FILTER_KEYS = {
            "creator_uid", "creator_name", "creator_username",
            "cost_filter", "time_filter", "nutrition_filter",
            "cost", "time", "nutrition",
            "difficulty", "max_time", "season", "region",
        }

        # Merge fresh filters with existing filters
        # This ensures refinements like "I am allergic to tomatoes" add to previous filters
        # instead of replacing them
        if existing_filters and fresh_filters:
            # Merge: Add new filters to existing ones
            for key, value in fresh_filters.items():
                if key in SCALAR_FILTER_KEYS:
                    # Scalar field: always overwrite with the fresh value (no accumulation)
                    if value is not None and value != [] and value != "":
                        existing_filters[key] = value
                elif key in existing_filters:
                    # List-type key - merge values
                    if isinstance(existing_filters[key], list) and isinstance(value, list):
                        # Both are lists - extend (deduplicate)
                        for v in value:
                            if v not in existing_filters[key]:
                                existing_filters[key].append(v)
                    elif isinstance(existing_filters[key], list):
                        # Existing is list, value is single - append if not duplicate
                        if value not in existing_filters[key]:
                            existing_filters[key].append(value)
                    elif isinstance(value, list):
                        # Existing is single, value is list - combine into list
                        combined = [existing_filters[key]] + value
                        existing_filters[key] = list(dict.fromkeys(combined))  # dedupe, preserve order
                    else:
                        # Both are single non-scalar values - keep fresh
                        existing_filters[key] = value
                else:
                    # New key - just add it
                    existing_filters[key] = value
            logger.info(f"[REFINEMENT] Merging filters - existing: {existing_filters}, fresh: {fresh_filters}")
            filters = existing_filters
        else:
            # No existing or fresh filters - use what's available
            filters = existing_filters if existing_filters else fresh_filters
            logger.info(f"[REFINEMENT] Using filters - existing: {existing_filters}, fresh: {fresh_filters}")

        has_new_included_ingredients = bool(filters.get("included_ingredients"))

        # Return both boolean and original vector query
        is_refinement = has_refinement_pattern or (is_refinement_intent and is_short_query and not has_new_included_ingredients)
        original_vector_query = last_vector_query if is_refinement else ""
        return is_refinement, original_vector_query

    def _build_contextual_vector_query(
        self,
        query: str,
        session_context: Dict[str, Any]
    ) -> str:
        """
        Build vector query that incorporates conversation history context

        Analyzes the last 10 messages to extract:
        - Main ingredients mentioned
        - Dietary preferences/allergies
        - Time constraints (quick, under X min)
        - Difficulty preferences
        - Meal types (breakfast, dessert, etc.)
        - Cuisine preferences

        Examples:
            - Previous: "pasta recipes", Current: "I am vegetarian"
            - Result: "pasta recipes" (preserve original, filter via SQL for vegetarian)
            - Previous: "carrot recipes", Current: "allergic to garlic"
            - Result: "carrot recipes" (preserve original, filter via SQL for garlic-free)

        For refinement queries (allergies, preferences), preserve the original recipe query
        and apply constraints via SQL filtering instead of modifying the embedding search.
        """
        import re
        from collections import Counter

        context_entities = session_context.get("context_entities", {})
        filters = session_context.get("filters", {})

        # Get conversation history from session context
        # This should contain last 10 messages
        conversation_history = session_context.get("conversation_history", [])

        # Extract context from conversation history
        mentioned_ingredients = []
        mentioned_allergies = set(session_context.get("excluded_ingredients", []))
        mentioned_dietary = set(filters.get("tags", []))
        time_constraints = []
        difficulty_preferences = []
        meal_types = []
        cuisines = set(filters.get("cuisines", []))

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
            r'\bsweet\b': 'dessert',
            r'\bsweets\b': 'dessert',
            r'\bsugary\b': 'dessert',
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
        for msg in conversation_history:
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

        # Check current query for new constraints
        query_lower = query.lower()

        # Check for new allergies in current query
        if "allergic to" in query_lower or "allergy" in query_lower:
            allergy_match = re.search(r"allergic to (\w+(?:\s+\w+)*)", query_lower)
            if allergy_match:
                mentioned_allergies.add(allergy_match.group(1))

        # Check for "without", "no X" patterns
        if "without" in query_lower or "no " in query_lower:
            without_match = re.search(r"without (\w+)|no (\w+)", query_lower)
            if without_match:
                excluded = without_match.group(1) or without_match.group(2)
                if excluded:
                    mentioned_allergies.add(excluded)

        # Check for new dietary preferences in current query
        for pattern, dietary in dietary_preference_patterns.items():
            if re.search(pattern, query_lower):
                mentioned_dietary.add(dietary)

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

        # Add cuisines
        if cuisines:
            query_parts.extend(list(cuisines))

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
            # Fallback to last query if available
            last_query = context_entities.get("last_vector_query", "")
            contextual_query = last_query if last_query else query

        # NOTE: Allergens are handled exclusively via SQL filters (WHERE NOT EXISTS / excluded_ingredients).
        # Do NOT add allergens to the embedding query - they would bias the vector search
        # *towards* those ingredients (the embedding model finds similar things, not excludes them).

        return contextual_query

    def decide_strategy(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session_context: Dict[str, Any]
    ) -> RetrievalPlan:
        """
        Decide the retrieval strategy based on query analysis

        Args:
            query: User's query text
            nlid_result: Result from NLID agent
            session_context: Session context with filters

        Returns:
            RetrievalPlan with strategy and execution details
        """
        query_lower = query.lower()

        # Extract components
        intent = nlid_result.get("intent", "general_chat")
        entities = nlid_result.get("entities", {})
        parameters = nlid_result.get("parameters", {})
        filters = nlid_result.get("filters", {})

        # Determine the actual vector query to use
        # If this is a refinement of a previous search, preserve the original recipe query
        is_refinement, original_vector_query = self._is_query_refinement(query, intent, session_context, nlid_result)
        if is_refinement:
            # For refinements, preserve the original query for embedding search
            # Apply constraints via SQL filtering instead
            if original_vector_query:
                vector_query = original_vector_query
            else:
                # Fallback to contextual query if no original query found
                vector_query = self._build_contextual_vector_query(query, session_context)
        else:
            # For new searches, use the contextual query (may include constraints)
            vector_query = query

        # If the query contains exclusion language ("I dont like X", "allergic to X",
        # "without X", etc.), strip those negative phrases so the embedding target
        # reflects what the user *wants*, not what they want to avoid.
        has_exclusions = bool(
            filters.get("excluded_ingredients")
            or filters.get("exclude_ingredients")
        )
        if has_exclusions and not is_refinement:
            clean_query = self._extract_positive_vector_query(vector_query, filters)
            if clean_query and clean_query != vector_query:
                logger.info(
                    f"[RETRIEVAL STRATEGY] Cleaned vector query: "
                    f"'{vector_query}' → '{clean_query}'"
                )
                vector_query = clean_query

        # Check for direct recipe search
        if self._is_direct_recipe_search(query):
            strategy = RetrievalStrategy.HYBRID_VECTOR_TO_SQL
            return RetrievalPlan(
                strategy=strategy,
                reasoning="Direct recipe request - using semantic search with SQL filters",
                vector_query=vector_query,
                sql_filters=self._build_sql_filters(parameters, filters, session_context, strategy),
                top_k=20
            )

        # Check for structured query with multiple constraints
        structured_score = self._calculate_structured_score(query, parameters, filters)
        fuzzy_score = self._calculate_fuzzy_score(query)

        # Decision logic
        if structured_score >= 3 and fuzzy_score >= 2:
            # Both structured and fuzzy elements → Hybrid
            # Use vector first for candidate selection, SQL for hard constraints
            strategy = RetrievalStrategy.HYBRID_VECTOR_TO_SQL
            return RetrievalPlan(
                strategy=strategy,
                reasoning=f"Query has both structured (score:{structured_score}) and fuzzy (score:{fuzzy_score}) elements. Using vector search for relevance, SQL for constraints.",
                vector_query=vector_query,
                sql_filters=self._build_sql_filters(parameters, filters, session_context, strategy),
                top_k=20
            )

        elif structured_score >= 3:
            # Primarily structured → SQL-only (or SQL → Vector rerank)
            # If we have tight filters, SQL can efficiently narrow down
            if self._has_tight_filters(parameters, filters):
                strategy = RetrievalStrategy.SQL_ONLY
                return RetrievalPlan(
                    strategy=strategy,
                    reasoning=f"Query has tight structured filters (score:{structured_score}). SQL-only is sufficient.",
                    sql_filters=self._build_sql_filters(parameters, filters, session_context, strategy),
                    top_k=20
                )
            else:
                # Has structure but filters are loose → Hybrid (SQL → Vector)
                strategy = RetrievalStrategy.HYBRID_SQL_TO_VECTOR
                return RetrievalPlan(
                    strategy=strategy,
                    reasoning=f"Query has structured elements (score:{structured_score}) but filters are loose. Using SQL to narrow, vector to re-rank.",
                    vector_query=vector_query,
                    sql_filters=self._build_sql_filters(parameters, filters, session_context, strategy),
                    top_k=30,  # Get more candidates for re-ranking
                    use_reranking=True
                )

        elif fuzzy_score >= 2:
            # Primarily fuzzy/semantic → Embeddings-only
            return RetrievalPlan(
                strategy=RetrievalStrategy.EMBEDDINGS_ONLY,
                reasoning=f"Query is primarily fuzzy/semantic (score:{fuzzy_score}). Using embedding search for relevance.",
                vector_query=vector_query,
                sql_filters={},  # Minimal filtering
                top_k=20
            )

        else:
            # Unclear → Default to Hybrid (safest approach)
            strategy = RetrievalStrategy.HYBRID_VECTOR_TO_SQL
            return RetrievalPlan(
                strategy=strategy,
                reasoning="Query intent is unclear. Using hybrid approach for best results.",
                vector_query=vector_query,
                sql_filters=self._build_sql_filters(parameters, filters, session_context, strategy),
                top_k=20
            )

    def _is_direct_recipe_search(self, query: str) -> bool:
        """Check if query is a direct recipe search"""
        query_lower = query.lower()
        return any(pattern in query_lower for pattern in self.DIRECT_RECIPE_PATTERNS)

    def _calculate_structured_score(
        self,
        query: str,
        parameters: Dict[str, Any],
        filters: Dict[str, Any]
    ) -> int:
        """
        Calculate how structured the query is

        Returns score from 0-5
        """
        score = 0
        query_lower = query.lower()

        # Check for structured keywords
        for keyword in self.STRUCTURED_KEYWORDS:
            if keyword in query_lower:
                score += 1
                break  # Count once for presence of structured keywords

        # Check for explicit time constraints
        if parameters.get("max_time"):
            score += 1

        # Check for difficulty constraint
        if parameters.get("difficulty"):
            score += 1

        # Check for excluded ingredients (allergies)
        if filters.get("excluded_ingredients"):
            score += 1

        # Check for dietary restrictions (structured)
        dietary = filters.get("tags", [])
        structured_dietary = ["vegetarian", "vegan", "gluten-free", "dairy-free", "keto"]
        if any(d in dietary for d in structured_dietary):
            score += 1

        return min(score, 5)

    def _calculate_fuzzy_score(self, query: str) -> int:
        """
        Calculate how fuzzy/semantic the query is

        Returns score from 0-5
        """
        score = 0
        query_lower = query.lower()

        # Check for fuzzy keywords
        for keyword in self.FUZZY_KEYWORDS:
            if keyword in query_lower:
                score += 1

        # Check for concept-based queries
        concept_indicators = ["like", "similar to", "something", "anything", "ideas"]
        for indicator in concept_indicators:
            if indicator in query_lower:
                score += 1
                break

        # Check for ingredient-only queries (fuzzy - "chicken recipes")
        # Not structured if no constraints
        words = query_lower.split()
        if len(words) <= 3:  # Short query likely fuzzy
            score += 1

        return min(score, 5)

    def _has_tight_filters(
        self,
        parameters: Dict[str, Any],
        filters: Dict[str, Any]
    ) -> bool:
        """
        Check if filters are tight enough for SQL-only

        Tight filters = multiple constraints that SQL can handle efficiently
        """
        tight_filter_count = 0

        if parameters.get("max_time"):
            tight_filter_count += 1

        if parameters.get("difficulty"):
            tight_filter_count += 1

        if parameters.get("servings"):
            tight_filter_count += 1

        if filters.get("tags"):
            tight_filter_count += 1

        if filters.get("excluded_ingredients"):
            tight_filter_count += 1

        # Filter-only query intents (nutrition/pricing) - check for specific filter keys
        if filters.get("nutrition_filters") or filters.get("price_filters"):
            # These queries use metadata sorting, need different handling
            # They don't rely on WHERE clauses with static thresholds
            # Return False because they need different strategy (SQL_ONLY with ORDER BY)
            return False
        else:
            # Need at least 2 tight filters for SQL-only
            return tight_filter_count >= 2

    def _build_sql_filters(
        self,
        parameters: Dict[str, Any],
        filters: Dict[str, Any],
        session_context: Dict[str, Any],
        strategy: RetrievalStrategy = None
    ) -> Dict[str, Any]:
        """
        Build SQL filters from parameters, filters, and session context

        When using HYBRID_VECTOR_TO_SQL strategy, included_ingredients are excluded
        from SQL filters because the embeddings already handle ingredient matching.

        Returns dictionary of filters for SQL generation
        """
        sql_filters = {}

        # From parameters - handle both max_time and time_constraints
        if parameters.get("max_time"):
            sql_filters["max_time"] = parameters["max_time"]
        elif parameters.get("time_constraints"):
            # Convert time_constraints to either max_time (filter) or time_sort_order (sort)
            time_constraints = parameters["time_constraints"]
            if isinstance(time_constraints, list) and time_constraints:
                # Extract first time constraint
                constraint = time_constraints[0]
                if isinstance(constraint, str):
                    constraint_lower = constraint.lower()
                    # Qualitative time queries → sort by time (no hard filter)
                    # "quick", "short" → show quickest first (ASC)
                    # "long" → show longest first (DESC)
                    # "medium" → neutral (no special sorting)
                    sort_order_map = {
                        "quick": "ASC",
                        "short": "ASC",
                        "fast": "ASC",
                        "long": "DESC",
                        "slow": "DESC",
                    }
                    if constraint_lower in sort_order_map:
                        sql_filters["time_sort_order"] = sort_order_map[constraint_lower]
                    else:
                        # Specific time limits → filter by max_time
                        time_map = {
                            "under 30 min": 30,
                            "under 30min": 30,
                            "under 15 min": 15,
                            "under 15min": 15,
                            "under 60 min": 60,
                            "under 60min": 60,
                            "under 45 min": 45,
                            "under 45min": 45,
                        }
                        # Try to extract numeric value from "under X min" pattern
                        import re
                        match = re.search(r'under\s*(\d+)\s*min', constraint_lower)
                        if match:
                            sql_filters["max_time"] = int(match.group(1))
                        elif constraint_lower in time_map:
                            sql_filters["max_time"] = time_map[constraint_lower]
                elif isinstance(constraint, (int, float)):
                    # Numeric value = max time filter
                    sql_filters["max_time"] = int(constraint)
            elif isinstance(time_constraints, (int, float, str)):
                # Single value
                if isinstance(time_constraints, str):
                    constraint_lower = time_constraints.lower()
                    sort_order_map = {
                        "quick": "ASC",
                        "short": "ASC",
                        "fast": "ASC",
                        "long": "DESC",
                        "slow": "DESC",
                    }
                    if constraint_lower in sort_order_map:
                        sql_filters["time_sort_order"] = sort_order_map[constraint_lower]
                    else:
                        # Try numeric extraction
                        import re
                        match = re.search(r'under\s*(\d+)\s*min', constraint_lower)
                        if match:
                            sql_filters["max_time"] = int(match.group(1))
                else:
                    sql_filters["max_time"] = int(time_constraints)

        if parameters.get("difficulty"):
            # Map difficulty to database values
            # Database has: easy, normal, medium, hard
            # User keywords map to: easy (includes 'normal'), medium, hard
            difficulty = parameters["difficulty"]
            if difficulty == "easy":
                # Include both 'easy' and 'normal' for beginner-level recipes
                sql_filters["difficulty"] = ["easy", "normal"]
            else:
                sql_filters["difficulty"] = difficulty

        if parameters.get("servings"):
            sql_filters["servings"] = parameters["servings"]

        # From NLID filters
        if filters.get("tags"):
            sql_filters["tags"] = filters["tags"]

        # IMPORTANT: Skip included_ingredients for HYBRID_VECTOR_TO_SQL strategy
        # because embeddings already handle ingredient matching semantically.
        # Adding EXISTS clauses for ingredients would be redundant and too restrictive.

        logger.info(f"[RETRIEVAL STRATEGY] Strategy: {strategy}, HYBRID_VECTOR_TO_SQL: {strategy == RetrievalStrategy.HYBRID_VECTOR_TO_SQL}")
        logger.info(f"[RETRIEVAL STRATEGY] include_ingredients in filters: {'include_ingredients' in filters}")
        logger.info(f"[RETRIEVAL STRATEGY] included_ingredients in filters: {'included_ingredients' in filters}")

        if strategy != RetrievalStrategy.HYBRID_VECTOR_TO_SQL:
            if filters.get("include_ingredients"):
                # Expand ingredient synonyms for better matching
                ingredients = filters["include_ingredients"]
                if isinstance(ingredients, list):
                    expanded = self._expand_ingredient_synonyms(ingredients)
                    sql_filters["included_ingredients"] = expanded
                else:
                    sql_filters["included_ingredients"] = [ingredients]

            if filters.get("included_ingredients"):
                # Expand ingredient synonyms for better matching
                ingredients = filters["included_ingredients"]
                if isinstance(ingredients, list):
                    expanded = self._expand_ingredient_synonyms(ingredients)
                    # Merge with any existing ingredients
                    existing = sql_filters.get("included_ingredients", [])
                    sql_filters["included_ingredients"] = list(set(existing + expanded))
                else:
                    sql_filters["included_ingredients"] = [ingredients]

        # Handle excluded_ingredients from NLID (support both key variants)
        # NLID may return "exclude_ingredients" or "excluded_ingredients"
        nlid_excluded = filters.get("excluded_ingredients") or filters.get("exclude_ingredients")
        if nlid_excluded:
            # Handle case where it might be a nested list (bug fix)
            if isinstance(nlid_excluded, list):
                # Flatten if nested
                flattened = []
                for item in nlid_excluded:
                    if isinstance(item, list):
                        flattened.extend(item)
                    else:
                        flattened.append(item)
                sql_filters["excluded_ingredients"] = flattened
            else:
                sql_filters["excluded_ingredients"] = [nlid_excluded]

        if filters.get("cuisines"):
            sql_filters["cuisines"] = filters["cuisines"]

        if filters.get("seasonality"):
            sql_filters["seasonality"] = filters["seasonality"]

        if filters.get("regional"):
            sql_filters["region"] = filters["regional"]

        # From session context (persistent across turns)
        session_filters = session_context.get("filters", {})

        if session_filters.get("tags"):
            existing = sql_filters.get("tags", [])
            sql_filters["tags"] = list(set(existing + session_filters["tags"]))

        if session_filters.get("cuisines"):
            existing = sql_filters.get("cuisines", [])
            sql_filters["cuisines"] = list(set(existing + session_filters["cuisines"]))

        if session_filters.get("max_time"):
            # Use the stricter (smaller) time constraint
            existing = sql_filters.get("max_time")
            if existing:
                sql_filters["max_time"] = min(existing, session_filters["max_time"])
            else:
                sql_filters["max_time"] = session_filters["max_time"]

        if session_filters.get("difficulty"):
            existing = sql_filters.get("difficulty")
            if existing:
                # Keep the first specified difficulty
                sql_filters["difficulty"] = existing
            else:
                sql_filters["difficulty"] = session_filters["difficulty"]

        # Session excluded ingredients (allergies) - always apply
        session_excluded = session_context.get("excluded_ingredients", [])
        if session_excluded:
            existing = sql_filters.get("excluded_ingredients", [])
            sql_filters["excluded_ingredients"] = list(set(existing + session_excluded))

        # Vegetarian ingredient-based expansion:
        # Replace (or supplement) the "vegetarian" tag filter with ingredient exclusion
        # Uses ingredient exclusion instead of tag filter for more reliable filtering
        _tags = sql_filters.get("tags", [])
        if "vegetarian" in _tags:
            NON_VEGETARIAN = [
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
                # Animal-derived fats/stock used in cooking
                "gelatin", "bone broth", "chicken broth", "beef broth",
                "chicken stock", "beef stock", "fish sauce", "anchovy paste",
            ]
            existing_excluded = sql_filters.get("excluded_ingredients", [])
            existing_lower = {e.lower() for e in existing_excluded}
            additional = [m for m in NON_VEGETARIAN if m not in existing_lower]
            sql_filters["excluded_ingredients"] = existing_excluded + additional
            # Remove the generic "vegetarian" tag so the LLM does not generate
            # a tag-only SQL filter (which would miss recipes without the tag).
            sql_filters["tags"] = [t for t in _tags if t != "vegetarian"]
            logger.info(
                f"[VEGETARIAN] Replaced tag filter with ingredient exclusion "
                f"({len(additional)} non-veg ingredients added to excluded_ingredients)"
            )

        # Session included ingredients - skip for HYBRID_VECTOR_TO_SQL
        # because embeddings already handle semantic ingredient matching
        if strategy != RetrievalStrategy.HYBRID_VECTOR_TO_SQL:
            session_included = session_context.get("included_ingredients", [])
            if session_included:
                sql_filters["included_ingredients"] = session_included

        # Creator filter - filter recipes by specific user
        # Normalise to a plain string; the merge loop can occasionally produce a list.
        raw_creator_uid = session_filters.get("creator_uid")
        if raw_creator_uid:
            if isinstance(raw_creator_uid, list):
                raw_creator_uid = raw_creator_uid[0] if raw_creator_uid else None
            if raw_creator_uid and isinstance(raw_creator_uid, str):
                sql_filters["creator_uid"] = raw_creator_uid
                logger.info(f"[RETRIEVAL STRATEGY] Added creator_uid to sql_filters: {raw_creator_uid}")

        logger.info(f"[RETRIEVAL STRATEGY] Final sql_filters: {sql_filters}")
        logger.info(f"[RETRIEVAL STRATEGY] session_filters: {session_filters}")
        return sql_filters


# Convenience function for quick decision making
def decide_retrieval_strategy(
    query: str,
    nlid_result: Dict[str, Any],
    session_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Convenience function to decide retrieval strategy

    Returns retrieval plan as dictionary
    """
    decider = RetrievalStrategyDecider()
    plan = decider.decide_strategy(query, nlid_result, session_context)
    return plan.to_dict()
