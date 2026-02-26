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
from agents import Agent, Runner

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
LLM_MODEL = os.getenv('INGREDIENT_MATCHER_MODEL')

# ============================================================================
# CATEGORY DEFINITIONS
# Categories that need FULL expansion (e.g., "nuts" -> "walnut", "almond", ...)
# ============================================================================

# Categories that should be expanded to specific items.
# ONLY true category/group words belong here — words where the base
# word genuinely doesn't match specific ingredient names in the DB.
# Specific ingredients (milk, chicken, eggs, etc.) should NOT be here;
# they get synonym/plural expansion via INGREDIENT_SYNONYMS instead.
# This prevents "I ran out of milk" from excluding all dairy products.
EXPAND_CATEGORIES: set = {
    "nuts", "nut", "fruits", "fruit", "dairy", "seafood",
    "shellfish", "meat", "poultry", "vegetables", "vegetable",
    "spices", "spice", "gluten",
}


# ============================================================================
# INGREDIENT SYNONYMS AND RELATED ITEMS
# For exclusions, users often want to exclude related ingredients together
# e.g., "lemon" -> also exclude "lime" (both citrus)
# e.g., "cilantro" -> also exclude "coriander" (same plant)
# ============================================================================

INGREDIENT_SYNONYMS: Dict[str, List[str]] = {
    # Citrus family
    "lemon": ["lemon", "lemons", "lime", "limes", "lemon juice", "lime juice"],
    "lime": ["lime", "limes", "lemon", "lemons", "lime juice", "lemon juice"],
    "orange": ["orange", "oranges", "orange juice", "orange zest"],
    "grapefruit": ["grapefruit", "grapefruits"],

    # Herbs - same plant, different names/parts
    "cilantro": ["cilantro", "coriander", "coriander leaves", "fresh coriander"],
    "coriander": ["coriander", "cilantro", "coriander seeds", "coriander leaves"],
    "parsley": ["parsley", "curly parsley", "flat-leaf parsley", "italian parsley"],

    # Vegetables - regional names
    "eggplant": ["eggplant", "aubergine", "eggplants", "aubergines"],
    "aubergine": ["aubergine", "eggplant", "aubergines", "eggplants"],
    "zucchini": ["zucchini", "courgette", "zucchinis", "courgettes"],
    "courgette": ["courgette", "zucchini", "courgettes", "zucchinis"],
    "arugula": ["arugula", "rocket", "rucola"],
    "rocket": ["rocket", "arugula", "rucola"],
    "bell pepper": ["bell pepper", "capsicum", "sweet pepper", "bell peppers"],
    "capsicum": ["capsicum", "bell pepper", "sweet pepper"],

    # Allium family (related)
    "onion": ["onion", "onions"],
    "scallion": ["scallion", "scallions", "green onion", "green onions", "spring onion", "spring onions"],
    "shallot": ["shallot", "shallots"],

    # Seafood - related items
    "shrimp": ["shrimp", "shrimps", "prawn", "prawns"],
    "prawn": ["prawn", "prawns", "shrimp", "shrimps"],
    "squid": ["squid", "calamari"],
    "calamari": ["calamari", "squid"],

    # Meat - related cuts/types
    "bacon": ["bacon", "pancetta", "bacon fat"],
    "pancetta": ["pancetta", "bacon"],

    # Dairy alternatives - keep minimal, ILIKE '%cream%' catches all variants
    "cream": ["cream"],
    "heavy cream": ["cream"],
    "whipping cream": ["cream"],
    "double cream": ["cream"],
    "sour cream": ["cream"],

    # Sweeteners
    "honey": ["honey", "raw honey"],
    "maple syrup": ["maple syrup", "pure maple syrup"],

    # Oils - related
    "olive oil": ["olive oil", "extra virgin olive oil", "evoo"],

    # Alcohol in cooking
    "wine": ["wine", "white wine", "red wine", "cooking wine"],
    "white wine": ["white wine", "wine", "cooking wine"],

    # Other common synonyms
    "breadcrumbs": ["breadcrumbs", "breadcrumb", "bread crumbs", "bread crumbs", "panko"],
    "panko": ["panko", "breadcrumbs", "bread crumbs"],
    "scallions": ["scallions", "scallion", "green onion", "green onions", "spring onion", "spring onions"],

    # Specific ingredients — synonym/plural expansion only.
    # These are NOT category words, so "I ran out of milk" excludes milk,
    # not all dairy. Use "dairy" if you want the full category.
    "milk": ["milk", "milks"],
    "egg": ["egg", "eggs"],
    "eggs": ["egg", "eggs"],
    "chicken": ["chicken"],
    "beef": ["beef"],
    "pork": ["pork"],
    "wheat": ["wheat"],
    "soy": ["soy", "soya"],
    "chocolate": ["chocolate"],
    "cocoa": ["cocoa", "cacao"],
    "cheese": ["cheese"],
}


# ============================================================================
# SMART EXPANSION FOR EXCLUSIONS
# For exclusions (allergies), we use minimal expansion:
# - Categories: Full expansion (nuts -> walnut, almond, etc.)
# - Synonyms: Related ingredients (lemon -> lime, etc.)
# - Specific ingredients: Only singular/plural (tomato -> tomato, tomatoes)
# ============================================================================

def smart_expand_for_exclusion(ingredient: str) -> List[str]:
    """
    Smart expansion for allergen exclusions.

    For exclusions, ILIKE '%ingredient%' does substring matching, so:
    - '%tomato%' matches "cherry tomato", "roma tomato", "tomato sauce", etc.
    - '%garlic%' matches "roasted garlic", "garlic powder", etc.

    We only need to expand:
    1. Categories (nuts -> walnut, almond, etc.) - because '%nuts%' doesn't match 'walnut'
    2. Singular/plural forms (strawberry/strawberries) - because they're different words

    Args:
        ingredient: The ingredient to expand

    Returns:
        List of expanded ingredient names for SQL ILIKE matching
    """
    ing_lower = ingredient.lower().strip()
    expanded = [ing_lower]

    # 1. Check if it's a category that needs full expansion
    if ing_lower in EXPAND_CATEGORIES:
        predefined = PREDEFINED_ALLERGEN_EXPANSIONS.get(ing_lower)
        if predefined:
            logger.info(f"[INGREDIENT_MATCHER] Category expansion for '{ingredient}' -> {len(predefined)} items")
            return predefined

    # 2. Check if it has synonyms/related ingredients
    if ing_lower in INGREDIENT_SYNONYMS:
        synonyms = INGREDIENT_SYNONYMS[ing_lower]
        logger.info(f"[INGREDIENT_MATCHER] Synonym expansion for '{ingredient}' -> {synonyms}")
        return synonyms

    # 3. For specific ingredients, only add singular/plural forms
    # Handle common English pluralization rules

    # Words ending in 'y' -> 'ies' (strawberry -> strawberries)
    if ing_lower.endswith('y') and len(ing_lower) > 2:
        # But not words ending in vowel+y (day -> days, not daies)
        if ing_lower[-2] not in 'aeiou':
            plural_form = ing_lower[:-1] + 'ies'
            expanded.append(plural_form)
            logger.debug(f"[INGREDIENT_MATCHER] Added plural: {ing_lower} -> {plural_form}")

    # Words ending in 'ies' -> 'y' (strawberries -> strawberry)
    elif ing_lower.endswith('ies') and len(ing_lower) > 4:
        singular_form = ing_lower[:-3] + 'y'
        expanded.append(singular_form)
        logger.debug(f"[INGREDIENT_MATCHER] Added singular: {ing_lower} -> {singular_form}")

    # Words ending in 'oes' (tomatoes, potatoes) -> remove 'es' to get singular
    elif ing_lower.endswith('oes') and len(ing_lower) > 4:
        singular_form = ing_lower[:-2]  # tomatoes -> tomato
        expanded.append(singular_form)
        logger.debug(f"[INGREDIENT_MATCHER] Added singular: {ing_lower} -> {singular_form}")

    # Words ending in 'es' (but not 'oes' or 'ies' or 'ss') -> remove 'es'
    elif ing_lower.endswith('es') and not ing_lower.endswith(('oes', 'ies', 'ss')) and len(ing_lower) > 3:
        singular_form = ing_lower[:-2]
        expanded.append(singular_form)
        logger.debug(f"[INGREDIENT_MATCHER] Added singular: {ing_lower} -> {singular_form}")

    # Words ending in 's' (but not 'ss' or 'es') -> remove 's'
    elif ing_lower.endswith('s') and not ing_lower.endswith(('ss', 'es')) and len(ing_lower) > 2:
        singular_form = ing_lower[:-1]
        expanded.append(singular_form)
        logger.debug(f"[INGREDIENT_MATCHER] Added singular: {ing_lower} -> {singular_form}")

    # Words not ending in 's' -> add plural form
    elif not ing_lower.endswith('s'):
        # Special case: words ending in 'o' often add 'es' (tomato -> tomatoes, potato -> potatoes)
        # But not all (photo -> photos, piano -> pianos) - we use 'es' for common food items
        common_oes_words = {'tomato', 'potato', 'hero', 'torpedo', 'mosquito', 'volcano', 'tornado'}
        if ing_lower in common_oes_words:
            plural_form = ing_lower + 'es'
        else:
            plural_form = ing_lower + 's'
        expanded.append(plural_form)
        logger.debug(f"[INGREDIENT_MATCHER] Added plural: {ing_lower} -> {plural_form}")

    # Remove duplicates and return
    result = list(set(expanded))
    logger.info(f"[INGREDIENT_MATCHER] Smart expansion for '{ingredient}' -> {result}")
    return result


# ============================================================================
# PRE-DEFINED ALLERGEN EXPANSIONS (Avoids LLM calls for common allergens)
# Limited to ~15 most common items per category for SQL performance
# ============================================================================

# Static expansions for common allergen categories - no LLM call needed
PREDEFINED_ALLERGEN_EXPANSIONS: Dict[str, List[str]] = {
    # Fruits category - top 15 most common
    "fruit": ["fruit", "fruits", "apple", "orange", "banana", "strawberry", "grape",
              "mango", "pineapple", "kiwi", "peach", "pear", "cherry", "lemon", "melon"],
    "fruits": ["fruit", "fruits", "apple", "orange", "banana", "strawberry", "grape",
               "mango", "pineapple", "kiwi", "peach", "pear", "cherry", "lemon", "melon"],

    # Nuts category - top 15
    "nut": ["nut", "nuts", "walnut", "almond", "cashew", "pecan", "hazelnut",
            "pistachio", "peanut", "pine nut", "macadamia", "chestnut", "brazil nut"],
    "nuts": ["nut", "nuts", "walnut", "almond", "cashew", "pecan", "hazelnut",
             "pistachio", "peanut", "pine nut", "macadamia", "chestnut", "brazil nut"],

    # Dairy category - minimal list, ILIKE handles multi-word matches
    # e.g., '%milk%' catches: milk, buttermilk, condensed milk, evaporated milk
    # e.g., '%cream%' catches: cream, heavy cream, sour cream, whipping cream
    # e.g., '%cheese%' catches: cheese, cream cheese, cottage cheese
    # Only include items that DON'T contain the base word as substring
    "dairy": [
        "dairy", "milk", "cheese", "butter", "cream", "yogurt", "yoghurt",
        "ghee", "whey", "casein", "lactose",
        # Cheese types that don't contain "cheese" in their name
        "parmesan", "pecorino", "asiago", "gruyere", "cheddar", "gouda", "edam",
        "provolone", "mozzarella", "burrata", "ricotta", "mascarpone", "feta",
        "brie", "camembert", "roquefort", "gorgonzola", "stilton", "halloumi", "paneer",
        # Other dairy items that need explicit inclusion
        "buttermilk", "kefir", "custard"
    ],
    "milk": [
        "milk", "dairy", "lactose", "whey", "casein",
        "butter", "cream", "yogurt", "yoghurt", "kefir", "custard", "ghee"
    ],
    "cheese": [
        "cheese", "dairy", "lactose", "whey", "casein",
        # Cheese types that don't contain "cheese" in their name
        "parmesan", "pecorino", "asiago", "gruyere", "cheddar", "gouda", "edam",
        "provolone", "mozzarella", "burrata", "ricotta", "mascarpone", "feta",
        "brie", "camembert", "roquefort", "gorgonzola", "stilton", "halloumi", "paneer"
    ],

    # Seafood category - top 15
    "seafood": ["seafood", "fish", "shrimp", "prawn", "crab", "lobster", "salmon",
                "tuna", "cod", "oyster", "mussel", "clam", "squid", "calamari", "anchovy"],
    "shellfish": ["shellfish", "shrimp", "prawn", "crab", "lobster", "oyster",
                  "mussel", "clam", "scallop", "squid", "calamari", "octopus"],

    # Eggs - rely on ILIKE '%egg%' for most variants (egg white, egg yolk, etc.)
    # Only add items that DON'T contain "egg" as substring
    "egg": ["egg", "eggs", "albumin", "albumen"],
    "eggs": ["egg", "eggs", "albumin", "albumen"],

    # Gluten/Wheat - top 15
    "gluten": ["gluten", "wheat", "flour", "bread", "pasta", "noodle", "barley",
               "rye", "semolina", "couscous", "cracker", "pretzel", "breadcrumb"],
    "wheat": ["wheat", "gluten", "flour", "bread", "pasta", "noodle", "barley", "rye", "semolina"],

    # Soy - top 15
    "soy": ["soy", "soya", "soybean", "tofu", "tempeh", "edamame", "miso",
            "soy sauce", "tamari", "soy milk", "lecithin"],

    # Chocolate/Cocoa
    "chocolate": ["chocolate", "cocoa", "cacao", "cocoa powder", "dark chocolate",
                  "milk chocolate", "chocolate chips"],
    "cocoa": ["chocolate", "cocoa", "cacao", "cocoa powder", "dark chocolate", "milk chocolate"],

    # Vegetables - top 15 most common
    "vegetable": ["vegetable", "carrot", "broccoli", "spinach", "tomato", "onion",
                  "garlic", "potato", "celery", "cucumber", "pepper", "cabbage", "mushroom"],
    "vegetables": ["vegetable", "carrot", "broccoli", "spinach", "tomato", "onion",
                   "garlic", "potato", "celery", "cucumber", "pepper", "cabbage", "mushroom"],

    # Meat - top 15
    "meat": ["meat", "beef", "pork", "chicken", "lamb", "turkey", "duck",
             "bacon", "ham", "sausage", "steak", "mince", "prosciutto"],
    "pork": ["pork", "bacon", "ham", "sausage", "prosciutto", "pepperoni", "chorizo"],
    "beef": ["beef", "steak", "mince", "ground beef", "roast beef", "sirloin", "ribeye"],

    # Poultry
    "poultry": ["poultry", "chicken", "turkey", "duck", "goose", "quail"],
    "chicken": ["chicken", "poultry", "chicken breast", "chicken thigh"],

    # Spices - top 15
    "spice": ["spice", "cinnamon", "cumin", "turmeric", "paprika", "ginger",
              "cloves", "nutmeg", "cardamom", "pepper", "chili", "oregano", "basil"],
    "spices": ["spice", "cinnamon", "cumin", "turmeric", "paprika", "ginger",
               "cloves", "nutmeg", "cardamom", "pepper", "chili", "oregano", "basil"],
}


def _get_predefined_expansion(ingredient: str) -> Optional[List[str]]:
    """Get pre-defined expansion for common allergen categories (no LLM call needed)."""
    ingredient_lower = ingredient.lower().strip()

    # Direct match
    if ingredient_lower in PREDEFINED_ALLERGEN_EXPANSIONS:
        logger.info(f"[INGREDIENT_MATCHER] Using predefined expansion for '{ingredient}' (no LLM call)")
        return PREDEFINED_ALLERGEN_EXPANSIONS[ingredient_lower]

    # Singular/plural variations
    if ingredient_lower.endswith('s') and ingredient_lower[:-1] in PREDEFINED_ALLERGEN_EXPANSIONS:
        return PREDEFINED_ALLERGEN_EXPANSIONS[ingredient_lower[:-1]]
    if ingredient_lower + 's' in PREDEFINED_ALLERGEN_EXPANSIONS:
        return PREDEFINED_ALLERGEN_EXPANSIONS[ingredient_lower + 's']

    return None


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

    def smart_expand_for_exclusions(self, ingredients: List[str]) -> List[str]:
        """
        Smart expansion for allergen exclusions - NO LLM calls.

        For exclusions, SQL ILIKE '%ingredient%' does substring matching:
        - '%tomato%' matches "cherry tomato", "tomato sauce", etc.
        - '%garlic%' matches "roasted garlic", "garlic powder", etc.

        We only expand:
        1. Categories (nuts -> walnut, almond) - because '%nuts%' doesn't match 'walnut'
        2. Singular/plural forms - because 'strawberry' doesn't match 'strawberries'

        This produces minimal, efficient SQL conditions instead of 30+ redundant ones.

        Args:
            ingredients: List of ingredients to exclude

        Returns:
            List of expanded ingredient names for SQL ILIKE matching
        """
        if not ingredients:
            return []

        all_expanded = []
        for ingredient in ingredients:
            expanded = smart_expand_for_exclusion(ingredient)
            all_expanded.extend(expanded)

        # Remove duplicates while preserving order
        seen = set()
        result = []
        for item in all_expanded:
            if item not in seen:
                seen.add(item)
                result.append(item)

        logger.info(
            f"[INGREDIENT_MATCHER] Smart exclusion expansion: "
            f"{len(ingredients)} inputs -> {len(result)} outputs"
        )
        return result

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
        Expand ingredients using predefined expansions first, then LLM as fallback.

        Priority:
        1. Check predefined expansions (instant, no LLM call)
        2. Check cache
        3. Use LLM for unknown ingredients
        """
        # Step 1: Check predefined expansions for all ingredients
        all_expanded = []
        ingredients_needing_llm = []

        for ingredient in ingredients:
            predefined = _get_predefined_expansion(ingredient)
            if predefined:
                all_expanded.extend(predefined)
            else:
                ingredients_needing_llm.append(ingredient)

        # If all ingredients have predefined expansions, return immediately
        if not ingredients_needing_llm:
            logger.info(f"[INGREDIENT_MATCHER] All ingredients have predefined expansions - no LLM call needed")
            return list(set(all_expanded))

        # Step 2: Check cache for remaining ingredients
        cache_key = "|".join(sorted(ingredients_needing_llm)).lower()
        cached = _get_from_cache(_EXPANSION_CACHE, cache_key)
        if cached:
            logger.info("[INGREDIENT_MATCHER] Cache hit for remaining expansion")
            return list(set(all_expanded + cached))

        # Step 3: Use LLM for unknown ingredients
        logger.info(f"[INGREDIENT_MATCHER] Using LLM for: {ingredients_needing_llm}")

        # Create agent for ingredient expansion
        expansion_agent = Agent(
            name="IngredientExpansionAgent",
            instructions="""You are a culinary expert. Process ingredients for recipe search.

For EACH ingredient provided, return ALL variations that could match in a database:
1. Category expansion (if category like "nuts" -> all specific nuts)
2. Spelling corrections (fix any typos)
3. Plural/singular forms (onion, onions)
4. Common synonyms (cilantro <-> coriander)
5. Regional names (kaju = cashew, aubergine = eggplant)
6. Common variations (tomato, tomatoes, cherry tomato)

Return a JSON object mapping each input ingredient to its variations array.

Rules:
- Maximum 30 variations per ingredient
- Include ALL common names and spellings
- Always include both singular and plural forms
- Return ONLY valid JSON, no explanation text""",
            model=LLM_MODEL
        )

        user_prompt = f"""Process these ingredients: {json.dumps(ingredients_needing_llm)}

Example output format:
{{
  "nuts": ["walnut", "walnuts", "almond", "almonds", "cashew", "cashews", "kaju", "pecan", "pecans", "hazelnut", "hazelnuts", "pistachio", "macadamia", "peanut", "peanuts", "groundnut", "pine nut", "brazil nut", "chestnut"],
  "tomatos": ["tomato", "tomatoes", "cherry tomato", "roma tomato"]
}}

Now process: {json.dumps(ingredients_needing_llm)}"""

        try:
            result = await Runner.run(expansion_agent, user_prompt)
            content = result.final_output.strip()
            expansion_dict = self._parse_json_response(content)

            # Flatten to single list
            llm_variations = []
            for variations in expansion_dict.values():
                llm_variations.extend([v.lower().strip() for v in variations])

            # Combine with predefined expansions
            all_expanded.extend(llm_variations)

            # Remove duplicates
            expanded = list(set(all_expanded))

            # Cache only the LLM results for the ingredients that needed LLM
            if llm_variations:
                _set_cache(_EXPANSION_CACHE, cache_key, list(set(llm_variations)))

            logger.info(
                f"[INGREDIENT_MATCHER] Combined: {len(expanded)} total variations "
                f"({len(set(all_expanded) - set(llm_variations))} predefined + {len(set(llm_variations))} LLM)"
            )

            return expanded

        except Exception as e:
            logger.error(f"[INGREDIENT_MATCHER] LLM expansion failed: {e}")
            # Fallback: return predefined + original items
            all_expanded.extend([ing.lower() for ing in ingredients_needing_llm])
            return list(set(all_expanded))

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


def get_smart_exclusion_expansion(ingredients: List[str]) -> List[str]:
    """
    Convenience function for smart allergen expansion without LLM calls.

    Use this for SQL ILIKE-based exclusions (allergies):
    - Categories (nuts, fruits) -> full expansion
    - Specific ingredients (garlic, tomato) -> only singular/plural

    This is SYNCHRONOUS and requires no database or LLM calls.

    Usage:
        expanded = get_smart_exclusion_expansion(["garlic", "tomato", "nuts"])
        # Returns: ["garlic", "tomato", "tomatoes", "nut", "nuts", "walnut", "almond", ...]
    """
    if not ingredients:
        return []

    all_expanded = []
    for ingredient in ingredients:
        expanded = smart_expand_for_exclusion(ingredient)
        all_expanded.extend(expanded)

    # Remove duplicates while preserving order
    seen = set()
    result = []
    for item in all_expanded:
        if item not in seen:
            seen.add(item)
            result.append(item)

    return result
