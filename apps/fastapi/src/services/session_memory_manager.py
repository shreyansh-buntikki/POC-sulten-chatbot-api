"""
Session Memory Manager
Maintains conversational context and state across turns
"""
from typing import Dict, Any, List, Optional, Set, Union
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import time

from apps.fastapi import logger

# Module-level cache for session state persistence across requests
# This ensures context is preserved when SessionMemoryManager is instantiated multiple times
_MODULE_MEMORY_CACHE: Dict[str, Any] = {}  # Will hold SessionState objects

# Constants for "show more" feature
EMBEDDING_BATCH_SIZE = 20
MAX_EMBEDDING_OFFSET = 100
SEARCH_CACHE_TTL_SECONDS = 1800  # 30 minutes


class SkillLevel(str, Enum):
    """User skill level"""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


@dataclass
class SessionFilters:
    """Filter state for session"""
    tags: List[str] = field(default_factory=list)
    cuisines: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    max_time: Optional[int] = None  # minutes
    difficulty: Optional[str] = None  # easy, medium, hard
    season: Optional[str] = None  # summer, winter, etc.
    region: Optional[str] = None  # regional preference
    creator_uid: Optional[str] = None  # filter by recipe creator
    creator_username: Optional[str] = None  # resolved username for NLG context
    servings: Optional[Union[int, Dict[str, Any]]] = None  # int for exact, dict for range/comparison/sort
    # Examples:
    #   - 4 → exactly 4 servings
    #   - {"min": 2, "max": 4} → between 2 and 4 servings
    #   - {"operator": ">=", "value": 4} → at least 4 servings
    #   - {"sort": "DESC"} → just sort by servings DESC (no filter)
    ingredient_count: Optional[Dict[str, Any]] = None  # e.g. {"operator": "<=", "value": 5}
    # Persisted filter state for multi-turn context
    cost_filter: Optional[Dict[str, Any]] = None  # e.g. {"operator": "<=", "value": 100, "country": "Norway", "sort_order": "DESC"}
    time_filter: Optional[Dict[str, Any]] = None  # e.g. {"sort_order": "ASC"}
    nutrition_filter: Optional[Dict[str, Any]] = None  # e.g. {"sort_by": "protein", "order": "DESC", "level": "high"}


@dataclass
class SearchCache:
    """
    Cached search data for 'show more' feature.
    Stores embedding candidates and pagination state for efficient "show more" requests.
    """
    # Embedding candidates from current/last search
    embedding_candidates: List[str] = field(default_factory=list)

    # All recipe IDs shown to user (cumulative across "show more" requests)
    shown_recipe_ids: List[str] = field(default_factory=list)

    # SQL filters used (allergies, tags, etc.)
    sql_filters: Dict[str, Any] = field(default_factory=dict)

    # Original vector query for re-search
    vector_query: Optional[str] = None

    # Pagination offset for next embedding search
    embedding_offset: int = 0

    # Timestamp for cache invalidation
    created_at: Optional[float] = None

    # Intent of original search (for context)
    original_intent: Optional[str] = None


@dataclass
class ContextEntities:
    """Context entities for reference tracking"""
    last_referenced_recipe_id: Optional[str] = None
    last_referenced_recipe_name: Optional[str] = None
    last_ingredient_list: List[str] = field(default_factory=list)
    active_timers: List[Dict[str, Any]] = field(default_factory=list)  # [{"recipe": "X", "time": 300, "started_at": ...}]
    last_vector_query: Optional[str] = None  # Last search query for embedding search context
    last_search_filters: Optional[Dict[str, Any]] = None  # Last search filters for context
    # For recipe reference queries ("Explain the 1st recipe")
    last_recipe_results: List[Dict[str, Any]] = field(default_factory=list)  # [{"id": "uuid", "name": "Recipe Name"}, ...]
    last_user_query: Optional[str] = None  # The actual user message (for re-search after clear)

    # NEW: Search cache for "show more" feature
    search_cache: Dict[str, Any] = field(default_factory=dict)

    # NEW: Pricing context for multi-turn pricing queries
    # e.g., Q1 "price of clove" → Q2 "and in India?" reuses last_pricing_item
    last_pricing_item: Optional[str] = None       # ingredient/recipe name last priced
    last_pricing_item_type: Optional[str] = None  # "ingredient" or "recipe"


@dataclass
class UserContext:
    """User-specific context"""
    user_uid: Optional[str] = None
    language: str = "en"  # from header or default
    purchased_bundles: Set[str] = field(default_factory=set)  # bundle IDs
    liked_recipes: Set[str] = field(default_factory=set)  # recipe IDs
    created_recipes: Set[str] = field(default_factory=set)  # recipe IDs
    skill_level: Optional[SkillLevel] = None  # only if explicitly provided


@dataclass
class SessionState:
    """
    Complete session state object

    Maintained server-side per session and injected into all downstream stages.
    """
    # Core identification
    session_id: str
    user_uid: Optional[str] = None

    # Language
    language: str = "en"

    # Ingredient filters
    excluded_ingredients: List[str] = field(default_factory=list)  # allergies, dislikes
    included_ingredients: List[str] = field(default_factory=list)  # preferences

    # Recipe exclusions (from negative feedback: "I don't like these")
    excluded_recipe_ids: List[str] = field(default_factory=list)

    # Allergies (persisted across session, expanded via ingredient matcher)
    # Stores original allergy name -> expanded variants mapping
    allergies: Dict[str, List[str]] = field(default_factory=dict)  # {"chocolate": ["chocolate", "cocoa", "cacao", ...]}

    # Query filters
    filters: SessionFilters = field(default_factory=SessionFilters)

    # User context
    user_context: UserContext = field(default_factory=UserContext)

    # Context entities (for reference tracking)
    context_entities: ContextEntities = field(default_factory=ContextEntities)

    # Conversation history (for context window)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # Metadata
    turn_count: int = 0
    last_intent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        # Convert sets to lists for JSON serialization
        if 'user_context' in data:
            if data['user_context']['purchased_bundles']:
                data['user_context']['purchased_bundles'] = list(data['user_context']['purchased_bundles'])
            if data['user_context']['liked_recipes']:
                data['user_context']['liked_recipes'] = list(data['user_context']['liked_recipes'])
            if data['user_context']['created_recipes']:
                data['user_context']['created_recipes'] = list(data['user_context']['created_recipes'])
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionState':
        """Create from dictionary"""
        # Handle user_context sets
        if 'user_context' in data:
            user_ctx = data['user_context']
            if isinstance(user_ctx.get('purchased_bundles'), list):
                user_ctx['purchased_bundles'] = set(user_ctx['purchased_bundles'])
            if isinstance(user_ctx.get('liked_recipes'), list):
                user_ctx['liked_recipes'] = set(user_ctx['liked_recipes'])
            if isinstance(user_ctx.get('created_recipes'), list):
                user_ctx['created_recipes'] = set(user_ctx['created_recipes'])
            data['user_context'] = UserContext(**user_ctx)

        # Handle filters
        if 'filters' in data and isinstance(data['filters'], dict):
            data['filters'] = SessionFilters(**data['filters'])

        # Handle context_entities
        if 'context_entities' in data and isinstance(data['context_entities'], dict):
            data['context_entities'] = ContextEntities(**data['context_entities'])

        # Remove datetime fields for recreation
        data.pop('created_at', None)
        data.pop('updated_at', None)

        return cls(**data)

    def update_timestamp(self):
        """Update the updated_at timestamp"""
        self.updated_at = datetime.utcnow()

    def increment_turn(self):
        """Increment turn counter"""
        self.turn_count += 1
        self.update_timestamp()

    def add_to_history(self, role: str, content: str):
        """Add message to conversation history"""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        })
        # Keep last 10 messages
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]
        self.update_timestamp()

    def get_context_window(self, limit: int = 10) -> List[Dict[str, str]]:
        """Get recent conversation history for LLM context"""
        return self.conversation_history[-limit:]


class SessionMemoryManager:
    """
    Manages session state storage and retrieval

    Can be backed by Redis or in-memory cache.
    For existing sessions, loads conversation history from the database.
    """

    def __init__(self, use_redis: bool = False, redis_url: Optional[str] = None):
        """
        Initialize the session memory manager

        Args:
            use_redis: Whether to use Redis for persistence
            redis_url: Redis connection URL (required if use_redis=True)
        """
        self.use_redis = use_redis
        # Use module-level cache for persistence across all instances
        # This is critical because each API request creates a new SessionMemoryManager instance
        self._memory_cache = _MODULE_MEMORY_CACHE

        if use_redis and redis_url:
            try:
                import redis
                self.redis_client = redis.from_url(redis_url)
                self.redis_client.ping()
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}. Falling back to in-memory.")
                self.use_redis = False
                self.redis_client = None
        else:
            self.redis_client = None

    def get_or_create_session(
        self,
        session_id: str,
        user_uid: Optional[str] = None,
        language: str = "en",
        conversation_store: Optional[Any] = None
    ) -> SessionState:
        """
        Get existing session or create new one

        For existing sessions in the database (via conversation_store),
        loads the conversation history and reconstructs the session state.

        Args:
            session_id: Session identifier
            user_uid: Optional user identifier
            language: Language code (from header or default)
            conversation_store: Optional ConversationStore for loading history from DB

        Returns:
            SessionState object with conversation history loaded
        """
        # Try to get existing session from cache (this preserves context_entities)
        cached_session = self.get_session(session_id)
        if cached_session:
            logger.info(f"[SESSION CACHE] Loading from cache with context: {cached_session.context_entities}")
            logger.info(f"[SESSION CACHE] last_vector_query from cache: {cached_session.context_entities.last_vector_query}")

            # Update user_uid if provided (overwrite existing value)
            if user_uid:
                cached_session.user_uid = user_uid

            # Update conversation history from database
            messages = conversation_store.get_messages(session_id, limit=10) if conversation_store else []
            cached_session.conversation_history.clear()

            has_metadata = False
            for msg in messages:
                cached_session.conversation_history.append({
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                    "meta": msg.meta  # Include metadata which contains vector_query info
                })
                if msg.meta:
                    has_metadata = True

            # If conversation history has metadata but cached context is empty, restore it
            if has_metadata and not cached_session.context_entities.last_vector_query:
                logger.info(f"[SESSION CACHE] Has metadata but empty context - running extraction")
                logger.info(f"[SESSION CACHE] Number of messages with metadata: {sum(1 for msg in messages if msg.meta)}")
                cached_session = self._extract_context_from_conversation(cached_session, messages)

            # Save back to cache
            self.save_session(cached_session)
            return cached_session

        # Check if session exists in the database and load history
        if conversation_store:
            db_session = conversation_store.get_session(session_id)
            if db_session:
                logger.info(f"[SESSION MEMORY] Loading existing session {session_id} from database with conversation history")
                # Load conversation history from database
                messages = conversation_store.get_messages(session_id, limit=10)

                # Create session state with loaded history
                session = SessionState(
                    session_id=session_id,
                    user_uid=user_uid or db_session.user_uid,
                    language=language
                )

                # Populate conversation history from database
                for msg in messages:
                    session.conversation_history.append({
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.created_at.isoformat() if msg.created_at else None
                    })

                # Try to extract context from previous turns by analyzing messages
                # This is a simple approach - for production, you might want to store
                # the extracted context in the session metadata
                session = self._extract_context_from_conversation(session, messages)

                # Cache the session
                self.save_session(session)
                logger.info(f"[SESSION MEMORY] Loaded {len(messages)} messages from database for session {session_id}")
                return session

        # Create new session
        logger.info(f"[SESSION MEMORY] Creating new session {session_id}")
        session = SessionState(
            session_id=session_id,
            user_uid=user_uid,
            language=language
        )
        self.save_session(session)
        return session

    def _extract_context_from_conversation(
        self,
        session: SessionState,
        messages: List[Any]
    ) -> SessionState:
        """
        Extract context from previous conversation messages.

        This first tries to restore context from message metadata (meta field).
        If metadata is not available, falls back to keyword-based extraction.

        Args:
            session: SessionState to update
            messages: List of ChatMessage objects from database

        Returns:
            Updated SessionState
        """
        logger.info(f"[CONTEXT EXTRACT] Starting extraction from {len(messages)} messages")
        logger.info(f"[CONTEXT EXTRACT] Current last_vector_query: {session.context_entities.last_vector_query}")

        # First pass: Try to restore context from message metadata
        # The meta field contains vector_query, intent, and other search context
        for msg in reversed(messages):  # Start from most recent
            # Check if metadata is available
            if hasattr(msg, 'meta') and msg.meta:
                meta = msg.meta
                # Handle both direct metadata and nested metadata structure
                if isinstance(meta, dict):
                    # Check for direct fields
                    vector_query = meta.get('vector_query')
                    intent = meta.get('intent')
                    filters = meta.get('filters')

                    # Also check for nested metadata (some responses have nested structure)
                    if not vector_query and 'metadata' in meta:
                        nested_meta = meta.get('metadata', {})
                        vector_query = nested_meta.get('vector_query')
                        intent = nested_meta.get('intent')
                        filters = nested_meta.get('filters')

                    # Restore last_vector_query if found
                    if vector_query and not session.context_entities.last_vector_query:
                        session.context_entities.last_vector_query = vector_query
                        logger.info(f"[CONTEXT RESTORE] Restored last_vector_query from metadata: {vector_query}")

                    # Restore last_intent if found
                    if intent and intent == "recipe_search" and not session.last_intent:
                        session.last_intent = intent
                        logger.info(f"[CONTEXT RESTORE] Restored last_intent from metadata: {intent}")

                    # Restore filters if found
                    if filters and isinstance(filters, dict):
                        if not session.context_entities.last_search_filters:
                            session.context_entities.last_search_filters = filters
                            logger.info(f"[CONTEXT RESTORE] Restored filters from metadata: {list(filters.keys())}")

                        # Restore excluded ingredients (allergies)
                        excluded_ingredients = filters.get('excluded_ingredients', [])
                        if excluded_ingredients:
                            for ingredient in excluded_ingredients:
                                if ingredient and ingredient not in session.excluded_ingredients:
                                    session.excluded_ingredients.append(ingredient)

                        # Restore included ingredients (preferences)
                        included_ingredients = filters.get('included_ingredients', [])
                        if included_ingredients:
                            for ingredient in included_ingredients:
                                if ingredient and ingredient not in session.included_ingredients:
                                    session.included_ingredients.append(ingredient)

                        # Restore dietary tags
                        tags = filters.get('tags', [])
                        if tags and tags not in session.filters.tags:
                            session.filters.tags.extend(tags)

                        # Restore cuisines
                        cuisines = filters.get('cuisines', [])
                        if cuisines:
                            for cuisine in cuisines:
                                if cuisine and cuisine not in session.filters.cuisines:
                                    session.filters.cuisines.append(cuisine)

                # Break after finding the most recent assistant message with metadata
                # (Assistant messages typically have the most complete metadata)
                if msg.role == "assistant" and session.context_entities.last_vector_query:
                    break

        # Second pass: If no metadata found, use keyword-based extraction as fallback
        # This maintains backward compatibility with older messages that don't have metadata
        if not session.context_entities.last_vector_query:
            import re

            for msg in messages:
                content = msg.content.lower()
                role = msg.role

                # Only extract from user messages for keyword fallback
                if role != "user":
                    continue

                # Extract recipe search context from conversation
                # Look for recipe-related queries
                recipe_keywords = [
                    "recipes", "recipe", "cook", "cooking", "make", "prepare",
                    "suggest", "find", "show me", "what is", "how to make"
                ]

                # Check if this is a recipe search query
                is_recipe_search = any(keyword in content for keyword in recipe_keywords)

                # Skip extraction if this is clearly an exclusion/dislike message.
                # Phrases like "I dont like chicken recipes" contain "recipes" which
                # would falsely trigger is_recipe_search and set last_vector_query
                # to "chicken" — making the next stage think there was a prior
                # positive chicken search and triggering an unnecessary embedding.
                exclusion_indicators = [
                    "dont like", "don't like", "do not like", "i hate",
                    "allergic", "allergy", "can't have", "cannot have",
                    "i'm not a fan", "im not a fan", "not a fan",
                    "avoid", "without", "exclude",
                    # NEW: Handle "don't have" patterns
                    "dont have", "don't have", "do not have",
                    "ran out", "ran out of", "missing", "i'm missing",
                    "no more", "out of",
                ]
                is_exclusion_query = any(indicator in content for indicator in exclusion_indicators)

                if is_recipe_search and not is_exclusion_query:
                    # Try to extract main ingredient/dish from the query
                    # Simple approach: look for common ingredients/dishes
                    common_ingredients = [
                        "pasta", "rice", "chicken", "beef", "fish", "potato", "tomato",
                        "onion", "garlic", "chole", "chickpeas", "dal", "curry",
                        "pizza", "burger", "salad", "soup", "bread", "egg"
                    ]

                    for ingredient in common_ingredients:
                        if ingredient in content:
                            # Set this as the last vector query for refinement
                            session.context_entities.last_vector_query = ingredient
                            break

                # Look for allergy patterns
                # This is a simple regex-based approach
                # "allergic to X", "I'm allergic to X", "allergy: X"
                allergy_patterns = [
                    r"allergic to (\w+(?:\s+\w+)*)",
                    r"allergy[:\s]+(\w+(?:\s+\w+)*)",
                    r"can't have (\w+(?:\s+\w+)*)",
                    r"cannot have (\w+(?:\s+\w+)*)",
                    r"no (\w+(?:\s+\w+)*)(?:\s+please)?",
                ]

                # Additional exclusion patterns for "ran out of", "don't have", etc.
                # These catch cases where the NLID agent might miss the extraction
                exclusion_patterns = [
                    # "ran out of X", "ran out of eggs"
                    r"ran out of (\w+(?:\s+\w+)*)",
                    # "don't have X", "dont have eggs"
                    r"don'?t have (\w+(?:\s+\w+)*)",
                    # "don't like X", "dont like chicken"
                    r"don'?t like (\w+(?:\s+\w+)*)",
                    # "do not have X"
                    r"do not have (\w+(?:\s+\w+)*)",
                    # "do not like X"
                    r"do not like (\w+(?:\s+\w+)*)",
                    # "missing X", "i'm missing X"
                    r"(?:i'?m\s+)?missing (\w+(?:\s+\w+)*)",
                    # "out of X" (when referring to ingredients)
                    r"out of (\w+(?:\s+\w+)*)",
                    # "no more X"
                    r"no more (\w+(?:\s+\w+)*)",
                    # "avoid X"
                    r"avoid (\w+(?:\s+\w+)*)",
                    # "without X"
                    r"without (\w+(?:\s+\w+)*)",
                    # "hate X"
                    r"hate (\w+(?:\s+\w+)*)",
                    # "can't eat X"
                    r"can'?t eat (\w+(?:\s+\w+)*)",
                ]

                # Process allergy patterns
                for pattern in allergy_patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        ingredient = match.group(1).strip().lower()
                        if ingredient and ingredient not in ["no", "not", "dont", "don't"]:
                            if ingredient not in session.excluded_ingredients:
                                session.excluded_ingredients.append(ingredient)
                                logger.info(f"[SESSION FALLBACK] Extracted excluded ingredient via allergy pattern: {ingredient}")

                # Process exclusion patterns
                for pattern in exclusion_patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        ingredient = match.group(1).strip().lower()
                        # Filter out false positives (common words that aren't ingredients)
                        false_positives = [
                            "a", "an", "the", "it", "that", "this", "my", "your", "his", "her",
                            "any", "some", "much", "many", "more", "most", "other", "another",
                            "time", "money", "idea", "clue", "problem", "issue", "luck",
                            "recipes", "recipe", "food", "meal", "dinner", "lunch", "breakfast",
                        ]
                        if ingredient and ingredient not in false_positives:
                            if ingredient not in session.excluded_ingredients:
                                session.excluded_ingredients.append(ingredient)
                                logger.info(f"[SESSION FALLBACK] Extracted excluded ingredient via exclusion pattern: {ingredient}")

        return session

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Get session by ID"""
        # Check cache first
        cached_session = self._memory_cache.get(session_id)
        if cached_session:
            logger.info(f"[SESSION LOAD] Found session {session_id} in cache with context: {cached_session.context_entities}")

        if self.use_redis and self.redis_client:
            try:
                data = self.redis_client.get(f"session:{session_id}")
                if data:
                    session_dict = json.loads(data)
                    logger.info(f"[SESSION LOAD] Raw session dict from Redis: {session_dict.get('context_entities', {})}")
                    loaded_session = SessionState.from_dict(session_dict)
                    logger.info(f"[SESSION LOAD] Loaded session {session_id} from Redis with context: {loaded_session.context_entities}")
                    return loaded_session
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")

        return cached_session

    def save_session(self, session: SessionState, ttl: int = 3600):
        """
        Save session state

        Args:
            session: SessionState object to save
            ttl: Time to live in seconds (default 1 hour)
        """
        session.update_timestamp()

        # Debug logging
        logger.info(f"[SESSION SAVE] last_vector_query: {session.context_entities.last_vector_query}")

        if self.use_redis and self.redis_client:
            try:
                self.redis_client.setex(
                    f"session:{session.session_id}",
                    ttl,
                    json.dumps(session.to_dict())
                )
            except Exception as e:
                logger.warning(f"Redis save failed: {e}")

        self._memory_cache[session.session_id] = session

        # Cleanup old sessions from cache periodically (simple LRU-style cleanup)
        # Keep cache size manageable
        if len(self._memory_cache) > 1000:
            # Remove oldest 100 entries when cache gets too large
            keys_to_remove = list(self._memory_cache.keys())[:100]
            for key in keys_to_remove:
                del self._memory_cache[key]
            logger.info(f"[SESSION CACHE] Cleaned up {len(keys_to_remove)} old sessions from cache")

    def delete_session(self, session_id: str):
        """Delete session"""
        if self.use_redis and self.redis_client:
            try:
                self.redis_client.delete(f"session:{session_id}")
            except Exception as e:
                logger.warning(f"Redis delete failed: {e}")

        self._memory_cache.pop(session_id, None)

    def update_session_from_nlid(
        self,
        session: SessionState,
        nlid_result: Dict[str, Any]
    ) -> SessionState:
        """
        Update session state based on NLID results

        This handles multi-turn conversation state updates.
        Only updates state if explicitly provided in the query.

        Args:
            session: Current session state
            nlid_result: Result from NLID agent

        Returns:
            Updated session state
        """
        # Update last intent
        if 'intent' in nlid_result:
            session.last_intent = nlid_result['intent']

        # Update excluded ingredients (allergies) ONLY if explicitly mentioned
        filters = nlid_result.get('filters', {})
        excluded_ingredients = filters.get('excluded_ingredients', [])

        # Only add NEW excluded ingredients (don't remove old ones)
        # This maintains context across turns
        for ingredient in excluded_ingredients:
            if ingredient not in session.excluded_ingredients:
                session.excluded_ingredients.append(ingredient)

        # Update included ingredients ONLY if explicitly mentioned AND not excluded.
        # NLID sometimes echoes the disliked ingredient in entities.ingredients
        # (e.g. "I dont like chicken" → entities.ingredients=["chicken"]).
        # Adding it to session.included_ingredients causes has_positive_context=True
        # downstream, which then prevents the correct SQL_ONLY routing for pure
        # exclusion queries.
        entities = nlid_result.get('entities', {})
        included_ingredients = entities.get('ingredients', [])
        excluded_set_lower = {e.lower() for e in excluded_ingredients}

        for ingredient in included_ingredients:
            if (
                ingredient not in session.included_ingredients
                and ingredient.lower() not in excluded_set_lower
            ):
                session.included_ingredients.append(ingredient)

        # Update filters if provided
        parameters = nlid_result.get('parameters', {})

        if parameters.get('max_time'):
            session.filters.max_time = parameters['max_time']

        if parameters.get('difficulty'):
            session.filters.difficulty = parameters['difficulty']

        if filters.get('tags'):
            # Add new tags
            for tag in filters['tags']:
                if tag not in session.filters.tags:
                    session.filters.tags.append(tag)

        if filters.get('cuisines'):
            # Use cuisines from filters (not entities) - this is where NLID puts them
            for cuisine in filters['cuisines']:
                if cuisine not in session.filters.cuisines:
                    session.filters.cuisines.append(cuisine)

        if filters.get('seasonality'):
            for season in filters['seasonality']:
                session.filters.season = season

        if filters.get('regional'):
            session.filters.region = filters['regional'][0]  # Take first

        # Handle creator_uid filter (resolved from creator_name in pipeline).
        # Normalise to a plain string; the NLID agent can occasionally return a list.
        if filters.get('creator_uid'):
            raw_uid = filters['creator_uid']
            if isinstance(raw_uid, list):
                raw_uid = raw_uid[0] if raw_uid else None
            if raw_uid and isinstance(raw_uid, str):
                session.filters.creator_uid = raw_uid

        # Persist resolved creator username for NLG context
        if filters.get('creator_username'):
            session.filters.creator_username = filters['creator_username']

        # Persist cost filter for multi-turn context
        # e.g., Q1: "quick recipes" → Q2: "my budget is 100" → both filters apply
        if filters.get('cost'):
            session.filters.cost_filter = filters['cost']
            logger.info(f"[SESSION UPDATE] Persisted cost_filter: {filters['cost']}")

        # Persist time filter for multi-turn context
        if filters.get('time'):
            session.filters.time_filter = filters['time']
            logger.info(f"[SESSION UPDATE] Persisted time_filter: {filters['time']}")

        # Persist nutrition filter for multi-turn context
        if filters.get('nutrition'):
            session.filters.nutrition_filter = filters['nutrition']
            logger.info(f"[SESSION UPDATE] Persisted nutrition_filter: {filters['nutrition']}")

        # Persist servings for multi-turn context
        # e.g., Q1: "Italian for 2 people" → Q2: "allergic to chicken" → servings=2 preserved
        if filters.get('servings'):
            session.filters.servings = filters['servings']
            logger.info(f"[SESSION UPDATE] Persisted servings: {filters['servings']}")
        elif parameters.get('servings'):
            session.filters.servings = parameters['servings']
            logger.info(f"[SESSION UPDATE] Persisted servings from parameters: {parameters['servings']}")

        # Persist ingredient_count for multi-turn context
        if filters.get('ingredient_count'):
            session.filters.ingredient_count = filters['ingredient_count']
            logger.info(f"[SESSION UPDATE] Persisted ingredient_count: {filters['ingredient_count']}")

        # Save and return
        session.increment_turn()
        self.save_session(session)
        return session

    def get_user_context(
        self,
        session: SessionState,
        db_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get complete user context for downstream stages

        Combines session state with database context (likes, purchases, etc.)

        Args:
            session: Session state
            db_context: Optional pre-fetched database context

        Returns:
            Complete user context dictionary
        """
        user_context = {
            "user_uid": session.user_uid,
            "language": session.language,
            "excluded_ingredients": session.excluded_ingredients,
            "included_ingredients": session.included_ingredients,
            "excluded_recipe_ids": session.excluded_recipe_ids,
            "filters": {
                "tags": session.filters.tags,
                "cuisines": session.filters.cuisines,
                "categories": session.filters.categories,
                "max_time": session.filters.max_time,
                "difficulty": session.filters.difficulty,
                "season": session.filters.season,
                "region": session.filters.region,
                "creator_uid": session.filters.creator_uid,
                "cost_filter": session.filters.cost_filter,
                "time_filter": session.filters.time_filter,
                "nutrition_filter": session.filters.nutrition_filter,
                "servings": session.filters.servings,
                "ingredient_count": session.filters.ingredient_count,
            },
            "context_entities": {
                "last_referenced_recipe_id": session.context_entities.last_referenced_recipe_id,
                "last_referenced_recipe_name": session.context_entities.last_referenced_recipe_name,
                "last_ingredient_list": session.context_entities.last_ingredient_list,
                "active_timers": session.context_entities.active_timers,
                "last_vector_query": session.context_entities.last_vector_query,
                "last_search_filters": session.context_entities.last_search_filters,
                "last_recipe_results": session.context_entities.last_recipe_results,
                "last_user_query": session.context_entities.last_user_query,
                # Search cache for "show more" feature
                "search_cache": session.context_entities.search_cache,
            },
        }

        # Add database context if provided
        if db_context:
            user_context.update({
                "purchased_bundles": db_context.get("purchased_bundles", set()),
                "liked_recipes": db_context.get("liked_recipes", set()),
                "created_recipes": db_context.get("created_recipes", set()),
            })

        return user_context

    def clear_session(self, session_id: str):
        """Clear all state for a session (soft reset)"""
        session = self.get_session(session_id)
        if session:
            # Keep core identification, clear everything else
            session.excluded_ingredients = []
            session.included_ingredients = []
            session.excluded_recipe_ids = []
            session.allergies = {}
            session.filters = SessionFilters()
            session.context_entities = ContextEntities()
            session.conversation_history = []
            session.turn_count = 0
            session.last_intent = None
            self.save_session(session)

    def update_search_context(
        self,
        session: SessionState,
        query: str,
        vector_query: str,
        filters: Dict[str, Any],
        intent: str
    ) -> SessionState:
        """
        Update session with the last successful search context

        This enables cross-turn context awareness where follow-up queries
        (like "I am allergic to tomato") can reference the previous search.

        Args:
            session: Current session state
            query: Original user query
            vector_query: The query used for embedding search
            filters: SQL filters applied to the search
            intent: The detected intent (recipe_search, etc.)

        Returns:
            Updated session state
        """
        # Only update for successful recipe searches
        # This ensures we track meaningful searches, not failed ones
        if intent == "recipe_search" and filters:
            session.context_entities.last_vector_query = vector_query
            session.context_entities.last_search_filters = filters
            session.last_intent = intent
            self.save_session(session)

        return session

    def update_last_recipe_results(
        self,
        session: SessionState,
        recipes: List[Dict[str, Any]],
        user_query: str
    ) -> SessionState:
        """
        Update session with the last recipe results for reference queries.

        Stores only IDs and names to keep memory small.
        Enables queries like "Explain the 1st recipe" or "I don't like these".

        Args:
            session: Current session state
            recipes: List of recipe dicts from search results
            user_query: The actual user message

        Returns:
            Updated session state
        """
        # Store only essential info (id, name) for last 5 recipes
        session.context_entities.last_recipe_results = [
            {"id": str(r.get("id")), "name": r.get("name")}
            for r in recipes[:5]
            if r.get("id") and r.get("name")
        ]
        session.context_entities.last_user_query = user_query
        self.save_session(session)

        recipe_names = [
            r["name"] for r in session.context_entities.last_recipe_results
        ]
        logger.info(
            f"[SESSION] Updated last_recipe_results: "
            f"{len(session.context_entities.last_recipe_results)} recipes: {recipe_names}"
        )

        return session

    def add_excluded_recipe(
        self,
        session: SessionState,
        recipe_id: str
    ) -> SessionState:
        """
        Add a recipe to the excluded list (negative feedback).

        These recipes will be filtered out in subsequent searches.
        Used when user says "I don't like these" or "Not that one".

        Args:
            session: Current session state
            recipe_id: Recipe UUID to exclude

        Returns:
            Updated session state
        """
        if recipe_id and recipe_id not in session.excluded_recipe_ids:
            session.excluded_recipe_ids.append(recipe_id)
            self.save_session(session)
            logger.info(
                f"[SESSION] Added recipe {recipe_id} to exclusions. "
                f"Total excluded: {len(session.excluded_recipe_ids)}"
            )

        return session

    def add_excluded_recipes(
        self,
        session: SessionState,
        recipe_ids: List[str]
    ) -> SessionState:
        """
        Add multiple recipes to the excluded list (negative feedback).

        Args:
            session: Current session state
            recipe_ids: List of recipe UUIDs to exclude

        Returns:
            Updated session state
        """
        for recipe_id in recipe_ids:
            if recipe_id and recipe_id not in session.excluded_recipe_ids:
                session.excluded_recipe_ids.append(recipe_id)

        if recipe_ids:
            self.save_session(session)
            logger.info(
                f"[SESSION] Added {len(recipe_ids)} recipes to exclusions. "
                f"Total excluded: {len(session.excluded_recipe_ids)}"
            )

        return session

    def add_allergy(
        self,
        session: SessionState,
        allergen: str,
        expanded_variants: List[str]
    ) -> SessionState:
        """
        Add an allergy with its expanded variants (from ingredient matcher).

        Args:
            session: Current session state
            allergen: Original allergen name (e.g., "chocolate")
            expanded_variants: List of variants from ingredient matcher
                              (e.g., ["chocolate", "cocoa", "cacao", ...])

        Returns:
            Updated session state
        """
        allergen_lower = allergen.lower()

        # Store the allergy with its expanded variants
        if allergen_lower not in session.allergies:
            session.allergies[allergen_lower] = expanded_variants
        else:
            # Merge new variants with existing ones
            existing = set(session.allergies[allergen_lower])
            session.allergies[allergen_lower] = list(existing | set(expanded_variants))

        # Also add to excluded_ingredients for backward compatibility
        for variant in expanded_variants:
            if variant not in session.excluded_ingredients:
                session.excluded_ingredients.append(variant)

        self.save_session(session)
        logger.info(
            f"[SESSION] Added allergy '{allergen}' with {len(expanded_variants)} variants. "
            f"Total allergies: {len(session.allergies)}"
        )

        return session

    def get_all_allergen_variants(self, session: SessionState) -> List[str]:
        """
        Get all expanded allergen variants from all allergies.

        Returns:
            Flat list of all allergen variants
        """
        all_variants = set()
        for variants in session.allergies.values():
            all_variants.update(variants)
        return list(all_variants)

    def has_allergies(self, session: SessionState) -> bool:
        """Check if session has any allergies recorded."""
        return len(session.allergies) > 0

    def clear_filters(self, session: SessionState) -> SessionState:
        """
        Clear all filters and exclusions from session.

        Keeps conversation history and core identification.

        Args:
            session: Current session state

        Returns:
            Updated session state
        """
        # Clear ingredient exclusions
        session.excluded_ingredients = []
        session.included_ingredients = []

        # Clear recipe exclusions
        session.excluded_recipe_ids = []

        # Clear allergies
        session.allergies = {}

        # Clear filters
        session.filters = SessionFilters()

        self.save_session(session)
        logger.info(f"[SESSION] Cleared all filters for session {session.session_id}")

        return session

    # ========================================================================
    # SEARCH CACHE METHODS (for "show more" feature)
    # ========================================================================

    def init_search_cache(
        self,
        session: SessionState,
        vector_query: str,
        sql_filters: Dict[str, Any] = None,
        original_intent: str = None
    ) -> SessionState:
        """
        Initialize search cache for a new query.
        Call this when starting a fresh search (not "show more").

        Args:
            session: Current session state
            vector_query: The embedding search query
            sql_filters: SQL filters to apply (allergies, etc.)
            original_intent: The intent of the original search

        Returns:
            Updated session state
        """
        session.context_entities.search_cache = {
            "embedding_candidates": [],
            "shown_recipe_ids": [],
            "sql_filters": sql_filters or {},
            "vector_query": vector_query,
            "embedding_offset": 0,
            "created_at": time.time(),
            "original_intent": original_intent
        }
        self.save_session(session)
        logger.info(f"[SEARCH CACHE] Initialized for query: '{vector_query}'")
        return session

    def store_embedding_candidates(
        self,
        session: SessionState,
        candidates: List[str],
        offset: int = None
    ) -> SessionState:
        """
        Store embedding candidates in cache.

        Args:
            session: Current session state
            candidates: List of recipe IDs from embedding search
            offset: Optional offset value (defaults to current + batch size)

        Returns:
            Updated session state
        """
        cache = session.context_entities.search_cache
        if not cache:
            cache = {}
            session.context_entities.search_cache = cache

        cache["embedding_candidates"] = candidates
        if offset is not None:
            cache["embedding_offset"] = offset

        self.save_session(session)
        logger.info(f"[SEARCH CACHE] Stored {len(candidates)} embedding candidates, offset={cache.get('embedding_offset', 0)}")
        return session

    def add_shown_recipes(
        self,
        session: SessionState,
        recipe_ids: List[str]
    ) -> SessionState:
        """
        Add recipes to the "already shown" list.

        Args:
            session: Current session state
            recipe_ids: Recipe IDs to mark as shown

        Returns:
            Updated session state
        """
        cache = session.context_entities.search_cache
        if not cache:
            cache = {}
            session.context_entities.search_cache = cache

        existing_shown = cache.get("shown_recipe_ids", [])
        # Add new IDs without duplicates
        all_shown = list(set(existing_shown + recipe_ids))
        cache["shown_recipe_ids"] = all_shown

        self.save_session(session)
        logger.info(f"[SEARCH CACHE] Added {len(recipe_ids)} shown recipes, total shown: {len(all_shown)}")
        return session

    def get_remaining_candidates(self, session: SessionState) -> List[str]:
        """
        Get embedding candidates that haven't been shown yet.

        Args:
            session: Current session state

        Returns:
            List of candidate recipe IDs not yet shown
        """
        cache = session.context_entities.search_cache
        if not cache:
            return []

        candidates = cache.get("embedding_candidates", [])
        shown = set(cache.get("shown_recipe_ids", []))

        remaining = [c for c in candidates if c not in shown]
        logger.info(f"[SEARCH CACHE] Remaining candidates: {len(remaining)} (candidates={len(candidates)}, shown={len(shown)})")
        return remaining

    def get_all_shown_recipe_ids(self, session: SessionState) -> List[str]:
        """
        Get all recipe IDs shown so far (cumulative).

        Args:
            session: Current session state

        Returns:
            List of all shown recipe IDs
        """
        cache = session.context_entities.search_cache
        if not cache:
            return []
        return cache.get("shown_recipe_ids", [])

    def get_search_cache(self, session: SessionState) -> Dict[str, Any]:
        """
        Get entire search cache.

        Args:
            session: Current session state

        Returns:
            Search cache dictionary
        """
        return session.context_entities.search_cache or {}

    def update_embedding_offset(self, session: SessionState, offset: int) -> SessionState:
        """
        Update offset for next embedding search.

        Args:
            session: Current session state
            offset: New offset value

        Returns:
            Updated session state
        """
        cache = session.context_entities.search_cache
        if not cache:
            cache = {}
            session.context_entities.search_cache = cache

        cache["embedding_offset"] = offset
        self.save_session(session)
        logger.info(f"[SEARCH CACHE] Updated offset to {offset}")
        return session

    def update_sql_filters(self, session: SessionState, sql_filters: Dict[str, Any]) -> SessionState:
        """
        Update SQL filters in cache (e.g., when new allergies added).

        Args:
            session: Current session state
            sql_filters: New SQL filters

        Returns:
            Updated session state
        """
        cache = session.context_entities.search_cache
        if cache:
            cache["sql_filters"] = sql_filters
            self.save_session(session)
            logger.info(f"[SEARCH CACHE] Updated SQL filters")
        return session

    def is_cache_expired(self, session: SessionState) -> bool:
        """
        Check if search cache has expired (TTL).

        Args:
            session: Current session state

        Returns:
            True if cache is expired or doesn't exist
        """
        cache = session.context_entities.search_cache
        if not cache:
            return True

        created_at = cache.get("created_at")
        if not created_at:
            return True

        elapsed = time.time() - created_at
        is_expired = elapsed > SEARCH_CACHE_TTL_SECONDS

        if is_expired:
            logger.info(f"[SEARCH CACHE] Cache expired (elapsed: {elapsed:.0f}s > TTL: {SEARCH_CACHE_TTL_SECONDS}s)")

        return is_expired

    def is_cache_exhausted(self, session: SessionState) -> bool:
        """
        Check if cached candidates are exhausted (all shown).

        Args:
            session: Current session state

        Returns:
            True if no remaining candidates
        """
        remaining = self.get_remaining_candidates(session)
        return len(remaining) == 0

    def can_fetch_more(self, session: SessionState) -> bool:
        """
        Check if we can fetch more results (offset < max).

        Args:
            session: Current session state

        Returns:
            True if we haven't reached max offset
        """
        cache = session.context_entities.search_cache
        if not cache:
            return False

        current_offset = cache.get("embedding_offset", 0)
        return current_offset < MAX_EMBEDDING_OFFSET

    def clear_search_cache(self, session: SessionState) -> SessionState:
        """
        Clear search cache (for new topic or fresh search).

        Args:
            session: Current session state

        Returns:
            Updated session state
        """
        session.context_entities.search_cache = {}
        self.save_session(session)
        logger.info(f"[SEARCH CACHE] Cleared for session {session.session_id}")
        return session

    def has_search_cache(self, session: SessionState) -> bool:
        """
        Check if session has valid search cache.

        Args:
            session: Current session state

        Returns:
            True if cache exists and has candidates
        """
        cache = session.context_entities.search_cache
        if not cache:
            return False
        return len(cache.get("embedding_candidates", [])) > 0
