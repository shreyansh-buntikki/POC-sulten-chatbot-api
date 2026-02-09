"""
Session Memory Manager
Maintains conversational context and state across turns
"""
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
import json


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


@dataclass
class ContextEntities:
    """Context entities for reference tracking"""
    last_referenced_recipe_id: Optional[str] = None
    last_referenced_recipe_name: Optional[str] = None
    last_ingredient_list: List[str] = field(default_factory=list)
    active_timers: List[Dict[str, Any]] = field(default_factory=list)  # [{"recipe": "X", "time": 300, "started_at": ...}]


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
    """

    def __init__(self, use_redis: bool = False, redis_url: Optional[str] = None):
        """
        Initialize the session memory manager

        Args:
            use_redis: Whether to use Redis for persistence
            redis_url: Redis connection URL (required if use_redis=True)
        """
        self.use_redis = use_redis
        self._memory_cache: Dict[str, SessionState] = {}  # In-memory fallback

        if use_redis and redis_url:
            try:
                import redis
                self.redis_client = redis.from_url(redis_url)
                self.redis_client.ping()
            except Exception as e:
                print(f"Redis connection failed: {e}. Falling back to in-memory.")
                self.use_redis = False
                self.redis_client = None
        else:
            self.redis_client = None

    def get_or_create_session(
        self,
        session_id: str,
        user_uid: Optional[str] = None,
        language: str = "en"
    ) -> SessionState:
        """
        Get existing session or create new one

        Args:
            session_id: Session identifier
            user_uid: Optional user identifier
            language: Language code (from header or default)

        Returns:
            SessionState object
        """
        # Try to get existing session
        session = self.get_session(session_id)
        if session:
            # Update user_uid if provided and session was anonymous
            if user_uid and not session.user_uid:
                session.user_uid = user_uid
            return session

        # Create new session
        session = SessionState(
            session_id=session_id,
            user_uid=user_uid,
            language=language
        )
        self.save_session(session)
        return session

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Get session by ID"""
        if self.use_redis and self.redis_client:
            try:
                data = self.redis_client.get(f"session:{session_id}")
                if data:
                    return SessionState.from_dict(json.loads(data))
            except Exception as e:
                print(f"Redis get failed: {e}")

        return self._memory_cache.get(session_id)

    def save_session(self, session: SessionState, ttl: int = 3600):
        """
        Save session state

        Args:
            session: SessionState object to save
            ttl: Time to live in seconds (default 1 hour)
        """
        session.update_timestamp()

        if self.use_redis and self.redis_client:
            try:
                self.redis_client.setex(
                    f"session:{session.session_id}",
                    ttl,
                    json.dumps(session.to_dict())
                )
            except Exception as e:
                print(f"Redis save failed: {e}")

        self._memory_cache[session.session_id] = session

    def delete_session(self, session_id: str):
        """Delete session"""
        if self.use_redis and self.redis_client:
            try:
                self.redis_client.delete(f"session:{session_id}")
            except Exception as e:
                print(f"Redis delete failed: {e}")

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

        # Update included ingredients ONLY if explicitly mentioned
        entities = nlid_result.get('entities', {})
        included_ingredients = entities.get('ingredients', [])

        for ingredient in included_ingredients:
            if ingredient not in session.included_ingredients:
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
            "filters": {
                "tags": session.filters.tags,
                "cuisines": session.filters.cuisines,
                "categories": session.filters.categories,
                "max_time": session.filters.max_time,
                "difficulty": session.filters.difficulty,
                "season": session.filters.season,
                "region": session.filters.region,
            },
            "context_entities": {
                "last_referenced_recipe_id": session.context_entities.last_referenced_recipe_id,
                "last_referenced_recipe_name": session.context_entities.last_referenced_recipe_name,
                "last_ingredient_list": session.context_entities.last_ingredient_list,
                "active_timers": session.context_entities.active_timers,
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
            session.filters = SessionFilters()
            session.context_entities = ContextEntities()
            session.conversation_history = []
            session.turn_count = 0
            session.last_intent = None
            self.save_session(session)
