"""
Intelligent Ingredient Matching System
Handles: Categories, Spelling Mistakes, Plurals, Synonyms, Regional Names
Uses: LLM + Embedding Similarity (No database category dependency)
"""
from typing import List, Dict, Any, Optional, Tuple
import json
import re
import time
import os
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI

from apps.fastapi.src.services.embedding_service import EmbeddingService
from apps.fastapi import logger


# ============================================================================
# CONFIGURATION
# ============================================================================

# Similarity threshold for embedding matching
SIMILARITY_THRESHOLD = 0.85

# Cache TTL in seconds
CACHE_TTL = 3600  # 1 hour

# LLM model for category expansion
LLM_MODEL = os.getenv('INGREDIENT_MATCHER_MODEL', 'gpt-4o-mini')


# ============================================================================
# CACHING
# ============================================================================

# In-memory cache for expansions and embeddings
_EXPANSION_CACHE: Dict[str, Tuple[List[str], float]] = {}
_EMBEDDING_CACHE: Dict[str, Tuple[List[float], float]] = {}


def _get_from_cache(cache: Dict, key: str, ttl: int = CACHE_TTL) -> Optional[Any]:
    """Get value from cache if not expired."""
    if key in cache:
        value, timestamp = cache[key]
        if time.time() - timestamp < ttl:
            return value
    return None


def _set_cache(cache: Dict, key: str, value: Any) -> None:
    """Set value in cache with current timestamp."""
    cache[key] = (value, time.time())


def clear_ingredient_matcher_cache() -> None:
    """Clear all caches (useful for testing or memory management)."""
    global _EXPANSION_CACHE, _EMBEDDING_CACHE
    _EXPANSION_CACHE.clear()
    _EMBEDDING_CACHE.clear()
    logger.info("[INGREDIENT_MATCHER] Caches cleared")


# ============================================================================
# MAIN CLASS
# ============================================================================

class IntelligentIngredientMatcher:
    """
    Handles all ingredient matching scenarios:
    - Category expansion (nuts -> walnut, almond, etc.)
    - Spelling correction (tomatos -> tomato)
    - Plural handling (onions -> onion)
    - Synonym matching (cilantro <-> coriander)
    - Regional names (kaju -> cashew)

    Uses a hybrid approach:
    1. LLM for category expansion and spelling normalization
    2. Embedding similarity for fuzzy matching
    3. Database lookups for exact/prefix matches
    """

    def __init__(self, db: Session, openai_client: Optional[OpenAI] = None):
        self.db = db
        self.embedding_service = EmbeddingService(db)
        self.openai_client = openai_client

        # Initialize OpenAI client if not provided
        if not self.openai_client:
            api_key = os.getenv('OPENAI_API_KEY')
            if api_key:
                self.openai_client = OpenAI(api_key=api_key)

    async def process_ingredients(
        self,
        ingredients: List[str],
        mode: str = "exclude"
    ) -> List[str]:
        """
        Main entry point: Process ingredient list and return database IDs.

        Args:
            ingredients: List from NLID (e.g., ["nuts", "tomatos"])
            mode: "exclude" for allergies/dislikes, "include" for search

        Returns:
            List of database ingredient IDs to filter by
        """
        if not ingredients:
            return []

        logger.info(
            f"[INGREDIENT_MATCHER] Processing {len(ingredients)} "
            f"ingredients: {ingredients}"
        )

        # Step 1: LLM expansion and normalization
        expanded_items = await self._llm_expand_and_normalize(ingredients)

        # Step 2: Get database ingredients with embeddings
        db_ingredients = self._fetch_database_ingredients()

        # Step 3: Match expanded items to database
        matched_ids = self._match_to_database(expanded_items, db_ingredients)

        logger.info(
            f"[INGREDIENT_MATCHER] Matched {len(matched_ids)} database "
            f"ingredients from {len(ingredients)} input items"
        )

        return matched_ids

    # ========================================================================
    # STEP 1: LLM EXPANSION & NORMALIZATION
    # ========================================================================

    async def _llm_expand_and_normalize(self, ingredients: List[str]) -> List[str]:
        """
        Use LLM to:
        1. Expand categories to all specific items
        2. Fix spelling mistakes
        3. Include plurals, synonyms, regional names
        """
        cache_key = "|".join(sorted(ingredients)).lower()

        # Check cache
        cached = _get_from_cache(_EXPANSION_CACHE, cache_key)
        if cached:
            logger.info("[INGREDIENT_MATCHER] Cache hit for expansion")
            return cached

        prompt = f"""You are a culinary expert. Process these ingredients for recipe search.

Input ingredients: {json.dumps(ingredients)}

For EACH ingredient, provide ALL variations that could match in a database:
1. Category expansion (if category like "nuts" -> all specific nuts)
2. Spelling corrections (fix any typos)
3. Plural/singular forms (onion, onions)
4. Common synonyms (cilantro <-> coriander)
5. Regional names (kaju = cashew, aubergine = eggplant)
6. Common variations (tomato, tomatoes, cherry tomato)

Return JSON object mapping each input to its variations:
{{
  "nuts": ["walnut", "walnuts", "almond", "almonds", "cashew", "cashews", "kaju",
           "pecan", "pecans", "hazelnut", "hazelnuts", "pistachio", "macadamia",
           "peanut", "peanuts", "groundnut", "pine nut", "brazil nut", "chestnut"],
  "tomatos": ["tomato", "tomatoes", "cherry tomato", "roma tomato"]
}}

Rules:
- Maximum 15 variations per ingredient
- Include ALL common names and spellings
- Always include both singular and plural
- Return ONLY valid JSON, no explanation

Process these ingredients: {json.dumps(ingredients)}
Output:"""

        try:
            if not self.openai_client:
                logger.warning(
                    "[INGREDIENT_MATCHER] No OpenAI client, "
                    "using fallback matching"
                )
                return [ing.lower() for ing in ingredients]

            response = self.openai_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.1
            )

            content = response.choices[0].message.content.strip()
            expansion_dict = self._parse_json_response(content)

            # Flatten to single list
            all_variations = []
            for variations in expansion_dict.values():
                all_variations.extend([v.lower().strip() for v in variations])

            # Remove duplicates
            expanded = list(set(all_variations))

            # Cache result
            _set_cache(_EXPANSION_CACHE, cache_key, expanded)

            logger.info(
                f"[INGREDIENT_MATCHER] LLM expanded {len(ingredients)} "
                f"items -> {len(expanded)} variations"
            )

            return expanded

        except Exception as e:
            logger.error(f"[INGREDIENT_MATCHER] LLM expansion failed: {e}")
            # Fallback: return original items with lowercase
            return [ing.lower() for ing in ingredients]

    # ========================================================================
    # STEP 2: FETCH DATABASE INGREDIENTS
    # ========================================================================

    def _fetch_database_ingredients(self) -> List[Dict[str, Any]]:
        """Fetch all ingredients from database with their embeddings."""
        sql = text("""
            SELECT id, name, embedding
            FROM ingredient
            WHERE "deletedAt" IS NULL
        """)
        result = self.db.execute(sql).fetchall()

        ingredients = []
        for row in result:
            embedding = row[2]
            # Parse embedding if stored as string
            if isinstance(embedding, str):
                try:
                    embedding = json.loads(embedding)
                except Exception:
                    embedding = None

            ingredients.append({
                "id": str(row[0]),
                "name": row[1],
                "name_lower": row[1].lower() if row[1] else "",
                "embedding": embedding
            })

        logger.info(
            f"[INGREDIENT_MATCHER] Fetched {len(ingredients)} "
            "ingredients from database"
        )
        return ingredients

    # ========================================================================
    # STEP 3: MATCH TO DATABASE
    # ========================================================================

    def _match_to_database(
        self,
        expanded_items: List[str],
        db_ingredients: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Match expanded items to database ingredients using multiple strategies:
        1. Exact match (case-insensitive)
        2. Substring match (handles plurals, suffixes)
        3. Word-level match (handles multi-word names)
        4. Embedding similarity (handles synonyms, regional names)
        """
        matched_ids = set()

        # Get embeddings for expanded items (batch)
        expanded_embeddings = self._get_embeddings_batch(expanded_items)

        for i, expanded_item in enumerate(expanded_items):
            expanded_lower = expanded_item.lower()
            expanded_embedding = expanded_embeddings[i]
            expanded_words = set(expanded_lower.split())

            for db_ing in db_ingredients:
                db_name_lower = db_ing["name_lower"]
                db_words = set(db_name_lower.split())

                # Strategy 1: Exact match
                if expanded_lower == db_name_lower:
                    matched_ids.add(db_ing["id"])
                    continue

                # Strategy 2: Substring match (handles plurals, suffixes)
                if len(expanded_lower) >= 4:
                    if expanded_lower in db_name_lower or db_name_lower in expanded_lower:
                        matched_ids.add(db_ing["id"])
                        continue

                # Strategy 3: Word-level match
                common_words = expanded_words & db_words
                if common_words:
                    # Filter out generic words
                    significant_words = common_words - {
                        "oil", "powder", "paste", "extract", "fresh", "dried"
                    }
                    if significant_words:
                        matched_ids.add(db_ing["id"])
                        continue

                # Strategy 4: Embedding similarity
                if expanded_embedding and db_ing["embedding"]:
                    similarity = self._cosine_similarity(
                        expanded_embedding,
                        db_ing["embedding"]
                    )
                    if similarity >= SIMILARITY_THRESHOLD:
                        matched_ids.add(db_ing["id"])
                        logger.debug(
                            f"[INGREDIENT_MATCHER] Embedding match: "
                            f"'{expanded_item}' <-> '{db_ing['name']}' "
                            f"({similarity:.2f})"
                        )

        return list(matched_ids)

    # ========================================================================
    # EMBEDDING UTILITIES
    # ========================================================================

    def _get_embeddings_batch(
        self,
        items: List[str]
    ) -> List[Optional[List[float]]]:
        """Get embeddings for multiple items (with caching)."""
        if not items:
            return []

        embeddings: List[Tuple[int, Optional[List[float]]]] = []
        items_to_fetch = []
        items_to_fetch_indices = []

        # Check cache first
        for i, item in enumerate(items):
            cache_key = item.lower()
            cached = _get_from_cache(_EMBEDDING_CACHE, cache_key)
            if cached:
                embeddings.append((i, cached))
                continue

            items_to_fetch.append(item)
            items_to_fetch_indices.append(i)

        # Fetch missing embeddings
        if items_to_fetch:
            try:
                batch_embeddings = self.embedding_service.generate_batch_embeddings(
                    items_to_fetch
                )

                for j, embedding in enumerate(batch_embeddings):
                    idx = items_to_fetch_indices[j]
                    embeddings.append((idx, embedding))

                    # Cache it
                    if embedding:
                        _set_cache(
                            _EMBEDDING_CACHE,
                            items_to_fetch[j].lower(),
                            embedding
                        )

            except Exception as e:
                logger.error(
                    f"[INGREDIENT_MATCHER] Embedding fetch failed: {e}"
                )
                for idx in items_to_fetch_indices:
                    embeddings.append((idx, None))

        # Sort by original index and extract embeddings
        embeddings.sort(key=lambda x: x[0])
        return [e[1] for e in embeddings]

    def _cosine_similarity(
        self,
        vec1: Optional[List[float]],
        vec2: Optional[List[float]]
    ) -> float:
        """Calculate cosine similarity between two vectors."""
        if not vec1 or not vec2:
            return 0.0

        try:
            import numpy as np
            v1 = np.array(vec1)
            v2 = np.array(vec2)

            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            return float(np.dot(v1, v2) / (norm1 * norm2))
        except Exception:
            return 0.0

    # ========================================================================
    # UTILITIES
    # ========================================================================

    def _parse_json_response(self, content: str) -> Dict[str, List[str]]:
        """Parse JSON from LLM response."""
        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON object
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Fallback: return empty dict
        return {}


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

async def process_excluded_ingredients(
    excluded: List[str],
    openai_client: Optional[OpenAI],
    db: Session
) -> List[str]:
    """
    Process excluded ingredients for allergy/dislike queries.

    Usage:
        excluded_ids = await process_excluded_ingredients(
            ["nuts", "tomatos"],
            openai_client,
            db
        )
        # Returns: ["uuid-walnut", "uuid-almond", "uuid-tomato", ...]
    """
    matcher = IntelligentIngredientMatcher(db, openai_client)
    return await matcher.process_ingredients(excluded, mode="exclude")


async def process_included_ingredients(
    included: List[str],
    openai_client: Optional[OpenAI],
    db: Session
) -> List[str]:
    """
    Process included ingredients for search queries.

    Usage:
        included_ids = await process_included_ingredients(
            ["chicken", "tomatos"],
            openai_client,
            db
        )
    """
    matcher = IntelligentIngredientMatcher(db, openai_client)
    return await matcher.process_ingredients(included, mode="include")
