"""
SQL Query Builders for Cost and Nutrition Filters
Builds SQL queries that filter recipes by recipe_metadata (pricing/nutrition)
and query ingredient tables directly.
"""
from typing import Dict, Any, List, Optional, Union, Tuple


# =====================================================
# NUTRIENT KEY MAPPING - Maps user input to DB columns
# =====================================================
# Format: "user_input" -> "nutrition_type:column_name"
# - Macros: "column_name" (no prefix) -> path: macros
# - Micros: "micros:column_name" -> path: micros

NUTRIENT_KEY_MAPPING = {
    # === MACRONUTRIENTS (stored in macros section) ===
    # Protein
    "protein": "protein",
    "proteins": "protein",

    # Carbohydrates
    "carb": "carbohydrates",
    "carbs": "carbohydrates",
    "carbohydrate": "carbohydrates",
    "carbohydrates": "carbohydrates",

    # Fat
    "fat": "totalFat",
    "fats": "totalFat",
    "totalFat": "totalFat",

    # Calories/Energy
    "calories": "energyKcal",
    "calorie": "energyKcal",
    "energy": "energyKcal",
    "energyKcal": "energyKcal",
    "kcal": "energyKcal",

    # Fiber
    "fiber": "totalFiber",
    "fibre": "totalFiber",
    "fibers": "totalFiber",
    "totalFiber": "totalFiber",

    # Sugar
    "sugar": "totalSugars",
    "sugars": "totalSugars",
    "totalSugars": "totalSugars",

    # Sodium (in macros)
    "sodium": "sodium",
    "salt": "sodium",

    # Cholesterol (in macros)
    "cholesterol": "cholesterol",

    # Saturated Fat (in macros)
    "saturated": "saturatedFat",
    "saturated fat": "saturatedFat",
    "saturatedfat": "saturatedFat",

    # Trans Fat (in macros)
    "trans fat": "transFat",
    "transfat": "transFat",

    # Starch (in macros)
    "starch": "starch",

    # === MICRONUTRIENTS - MINERALS (stored in micros section) ===
    "calcium": "micros:calcium",
    "ca": "micros:calcium",

    "iron": "micros:iron",
    "fe": "micros:iron",

    "magnesium": "micros:magnesium",
    "mg": "micros:magnesium",

    "zinc": "micros:zinc",
    "zn": "micros:zinc",

    "potassium": "micros:potassium",
    "k": "micros:potassium",

    "copper": "micros:copper",
    "cu": "micros:copper",

    "phosphorus": "micros:phosphorus",
    "phosphate": "micros:phosphorus",
    "p": "micros:phosphorus",

    "manganese": "micros:manganese",
    "mn": "micros:manganese",

    "selenium": "micros:selenium",
    "se": "micros:selenium",

    "fluoride": "micros:fluoride",
    "fluorine": "micros:fluoride",

    # === MICRONUTRIENTS - VITAMINS (stored in micros section) ===
    "vitamin a": "micros:vitaminA",
    "vitamina": "micros:vitaminA",
    "retinol": "micros:vitaminA",
    "vit a": "micros:vitaminA",

    "vitamin c": "micros:vitaminC",
    "vitaminc": "micros:vitaminC",
    "ascorbic": "micros:vitaminC",
    "ascorbic acid": "micros:vitaminC",
    "vit c": "micros:vitaminC",

    "vitamin d": "micros:vitaminD",
    "vitamind": "micros:vitaminD",
    "calciferol": "micros:vitaminD",
    "vit d": "micros:vitaminD",

    "vitamin e": "micros:vitaminE",
    "vitamine": "micros:vitaminE",
    "tocopherol": "micros:vitaminE",
    "vit e": "micros:vitaminE",

    "vitamin k": "micros:vitaminK",
    "vitamink": "micros:vitaminK",
    "phylloquinone": "micros:vitaminK",
    "vit k": "micros:vitaminK",

    # B Vitamins
    "vitamin b1": "micros:thiamineB1",
    "thiamine": "micros:thiamineB1",
    "thiamin": "micros:thiamineB1",
    "b1": "micros:thiamineB1",

    "vitamin b2": "micros:riboflavinB2",
    "riboflavin": "micros:riboflavinB2",
    "b2": "micros:riboflavinB2",

    "vitamin b3": "micros:niacinB3",
    "niacin": "micros:niacinB3",
    "b3": "micros:niacinB3",

    "vitamin b5": "micros:pantothenicAcidB5",
    "pantothenic": "micros:pantothenicAcidB5",
    "pantothenic acid": "micros:pantothenicAcidB5",
    "b5": "micros:pantothenicAcidB5",

    "vitamin b6": "micros:vitaminB6",
    "pyridoxine": "micros:vitaminB6",
    "b6": "micros:vitaminB6",

    "vitamin b7": "micros:biotinB7",
    "biotin": "micros:biotinB7",
    "b7": "micros:biotinB7",

    "vitamin b9": "micros:folateB9",
    "folate": "micros:folateB9",
    "folic acid": "micros:folateB9",
    "b9": "micros:folateB9",

    "vitamin b12": "micros:vitaminB12",
    "cobalamin": "micros:vitaminB12",
    "b12": "micros:vitaminB12",

    "choline": "micros:choline",
}


def get_nutrition_type_and_column(nutrient_key: str) -> Tuple[str, str]:
    """
    Determine the nutrition type (macros/micros) and column name for a nutrient.

    Args:
        nutrient_key: The nutrient key (may be "column" or "micros:column")

    Returns:
        Tuple of (nutrition_type, column_name)
        - nutrition_type: "macros" or "micros"
        - column_name: The actual column name without prefix
    """
    if nutrient_key.startswith("micros:"):
        return "micros", nutrient_key[6:]
    return "macros", nutrient_key


def build_nutrition_json_path(column_name: str, nutrition_type: str = "macros") -> str:
    """
    Build the JSON path expression for a nutrient in recipe_metadata.

    Args:
        column_name: The nutrient column name
        nutrition_type: "macros" or "micros"

    Returns:
        JSON path string for use in SQL queries
    """
    return f"r.\"recipe_metadata\"->'totalNutrition'->'{nutrition_type}'->>'{column_name}'"


def build_nutrition_where_clause(nutrient_key: str) -> str:
    """
    Build WHERE clause for checking if a nutrient exists.

    Args:
        nutrient_key: The nutrient key (from NUTRIENT_KEY_MAPPING)

    Returns:
        SQL WHERE clause string
    """
    nutrition_type, column_name = get_nutrition_type_and_column(nutrient_key)
    json_path = build_nutrition_json_path(column_name, nutrition_type)

    if nutrition_type == "micros":
        # For micros, we also need to check that the micros section exists
        return f"""  AND r."recipe_metadata"->'totalNutrition' IS NOT NULL
  AND r."recipe_metadata"->'totalNutrition'->'micros' IS NOT NULL
  AND {json_path} IS NOT NULL
"""
    else:
        return f"""  AND r."recipe_metadata"->'totalNutrition' IS NOT NULL
  AND r."recipe_metadata"->'totalNutrition'->'macros' IS NOT NULL
  AND {json_path} IS NOT NULL
"""


def build_nutrition_order_clause(nutrient_key: str, order: str = "DESC") -> str:
    """
    Build ORDER BY clause for sorting by a nutrient.

    Args:
        nutrient_key: The nutrient key (from NUTRIENT_KEY_MAPPING)
        order: "ASC" or "DESC"

    Returns:
        SQL ORDER BY clause string
    """
    nutrition_type, column_name = get_nutrition_type_and_column(nutrient_key)
    json_path = build_nutrition_json_path(column_name, nutrition_type)
    return f"CAST({json_path} AS FLOAT) {order}"


def normalize_nutrient_key(raw_key: str) -> str:
    """
    Normalize a nutrient key using the mapping.

    Args:
        raw_key: Raw nutrient key from user input or NLID

    Returns:
        Normalized nutrient key (may have "micros:" prefix)
    """
    key_lower = raw_key.lower() if raw_key else "protein"
    return NUTRIENT_KEY_MAPPING.get(key_lower, key_lower)


def build_recipe_cost_filter_sql(
    cost_filter: Dict[str, Any],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build SQL query for recipe cost filtering using recipe_metadata.

    Sorting logic:
    - Qualitative "low cost" / "cheap" → ASC (cheapest first)
    - Qualitative "high cost" / "expensive" → DESC (most expensive first)
    - Specific budget "under 500" → filter <= 500, sort DESC (nearest to budget first, not cheapest)

    Args:
        cost_filter: Dict with operator, value, country, sort_order, level
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        limit: Max results

    Returns:
        SQL query string
    """
    country = cost_filter.get("country", "Norway")
    operator = cost_filter.get("operator", "<=")
    value = cost_filter.get("value")

    # Map country codes to lowercase keys used in DB
    country_key_map = {
        "US": "usa",
        "India": "india",
        "Norway": "norway"
    }
    country_key = country_key_map.get(country, country.lower())

    # Determine sort order:
    # 1. If NLID explicitly set sort_order, use that
    # 2. For specific budget (value set): DESC (nearest to budget, not cheapest)
    # 3. For qualitative low cost / cheap: ASC (cheapest first)
    # 4. For qualitative high cost / expensive: DESC (most expensive first)
    explicit_sort = cost_filter.get("sort_order")
    level = cost_filter.get("level")

    if explicit_sort:
        order_direction = explicit_sort.upper()
    elif value is not None:
        # Specific budget → DESC to show recipes nearest to budget
        order_direction = "DESC"
    elif level == "high":
        order_direction = "DESC"
    else:
        # Default for "low cost", "cheap", "affordable", "budget" → cheapest first
        order_direction = "ASC"

    # Build price condition only when a specific value is provided
    if value is not None:
        price_condition = f"  AND CAST(r.\"recipe_metadata\"->'pricing'->'{country_key}'->>'total' AS FLOAT) {operator} {value}\n"
    else:
        price_condition = ""  # No filtering, just ordering

    # The actual structure is: recipe_metadata -> pricing -> country -> total
    base_query = f"""
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings",
       r."recipe_metadata"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = '{language}'
  AND (r."private" = false OR r."userUid" = '{user_uid}' OR br."bundleId" IS NOT NULL)
  AND r."recipe_metadata" IS NOT NULL
  AND r."recipe_metadata"->'pricing' IS NOT NULL
  AND r."recipe_metadata"->'pricing'->'{country_key}' IS NOT NULL
  AND r."recipe_metadata"->'pricing'->'{country_key}'->>'total' IS NOT NULL
{price_condition}"""

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    base_query += f"""
ORDER BY CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) {order_direction}
LIMIT {limit}
"""

    return base_query


def build_recipe_time_filter_sql(
    time_filter: Dict[str, Any],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build SQL query for recipe time sorting.

    Unlike cost/nutrition filters, time queries sort by (prepTime + cookTime)
    rather than filtering. This shows all recipes sorted by total cooking time.

    Args:
        time_filter: Dict with sort_order ("ASC" for quick, "DESC" for long)
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        limit: Max results

    Returns:
        SQL query string
    """
    sort_order = time_filter.get("sort_order", "ASC").upper()

    # Validate sort_order
    if sort_order not in ["ASC", "DESC"]:
        sort_order = "ASC"

    base_query = f"""
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings",
       r."recipe_metadata"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = '{language}'
  AND (r."private" = false OR r."userUid" = '{user_uid}' OR br."bundleId" IS NOT NULL)
"""

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    base_query += f"""
ORDER BY (r."prepTime" + r."cookTime") {sort_order}
LIMIT {limit}
"""

    return base_query


def build_recipe_base_sql(
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build a plain SQL query for recipes without any specific sort order.

    Used for structural-only queries (like servings filter) where no
    semantic search or specific sorting is needed.

    Args:
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        limit: Max results

    Returns:
        SQL query string
    """
    base_query = f"""
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings",
       r."recipe_metadata"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = '{language}'
  AND (r."private" = false OR r."userUid" = '{user_uid}' OR br."bundleId" IS NOT NULL)
"""

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    # Order by name alphabetically as a neutral default
    base_query += f"""
ORDER BY r."name" ASC
LIMIT {limit}
"""

    return base_query


def build_recipe_multi_filter_sql(
    cost_filter: Optional[Dict[str, Any]] = None,
    time_filter: Optional[Dict[str, Any]] = None,
    nutrition_filter: Optional[Dict[str, Any]] = None,
    user_uid: str = "",
    language: str = "en",
    additional_conditions: Optional[List[str]] = None,
    candidate_ids: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build a single SQL query combining any combination of cost, time, and nutrition filters.
    Used when multiple filters are active from multi-turn context management.

    Args:
        cost_filter: Cost filter dict with operator, value, country
        time_filter: Time filter dict with sort_order
        nutrition_filter: Nutrition filter dict with sort_by, order
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        candidate_ids: Optional list of recipe IDs to restrict search to (from embedding)
        limit: Max results

    ORDER BY priority: cost > time > nutrition > default (name)
    """
    country_key_map = {"US": "usa", "India": "india", "Norway": "norway"}

    base_query = f"""
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings",
       r."recipe_metadata"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = '{language}'
  AND (r."private" = false OR r."userUid" = '{user_uid}' OR br."bundleId" IS NOT NULL)
"""

    # Add candidate_ids restriction if provided (from embedding search)
    if candidate_ids:
        ids_list = ", ".join([f"'{cid}'" for cid in candidate_ids])
        base_query += f"  AND r.\"id\" IN ({ids_list})\n"

    # Add cost filter WHERE conditions
    cost_order = None
    if cost_filter:
        country = cost_filter.get("country", "Norway")
        country_key = country_key_map.get(country, country.lower())
        value = cost_filter.get("value")
        operator = cost_filter.get("operator", "<=")

        base_query += f"""  AND r."recipe_metadata" IS NOT NULL
  AND r."recipe_metadata"->'pricing' IS NOT NULL
  AND r."recipe_metadata"->'pricing'->'{country_key}' IS NOT NULL
  AND r."recipe_metadata"->'pricing'->'{country_key}'->>'total' IS NOT NULL
"""
        if value is not None:
            base_query += f"  AND CAST(r.\"recipe_metadata\"->'pricing'->'{country_key}'->>'total' AS FLOAT) {operator} {value}\n"

        explicit_sort = cost_filter.get("sort_order")
        level = cost_filter.get("level")
        if explicit_sort:
            cost_order = explicit_sort.upper()
        elif value is not None:
            cost_order = "DESC"
        elif level == "high":
            cost_order = "DESC"
        else:
            cost_order = "ASC"

    # Add nutrition filter WHERE conditions
    nutrient_key = None
    nutrition_order = None
    if nutrition_filter:
        raw_key = nutrition_filter.get("nutrient_key") or nutrition_filter.get("sort_by", "protein")
        nutrient_key = normalize_nutrient_key(raw_key)
        nutrition_order = nutrition_filter.get("order", "DESC")

        # Build the WHERE clause using the helper function
        base_query += build_nutrition_where_clause(nutrient_key)

    # Add additional conditions (allergens, tags, etc.)
    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    # Add time filter to WHERE (only exclude zero-time recipes for time queries)
    time_order = None
    if time_filter:
        time_order = time_filter.get("sort_order", "ASC").upper()

    # Build ORDER BY - primary sort is the current intent's filter, secondary is from session
    order_parts = []
    if cost_filter and cost_order:
        country = cost_filter.get("country", "Norway")
        country_key = country_key_map.get(country, country.lower())
        order_parts.append(f"CAST(r.\"recipe_metadata\"->'pricing'->'{country_key}'->>'total' AS FLOAT) {cost_order}")
    if time_order:
        order_parts.append(f"(r.\"prepTime\" + r.\"cookTime\") {time_order}")
    if nutrient_key and nutrition_order:
        order_parts.append(build_nutrition_order_clause(nutrient_key, nutrition_order))

    if order_parts:
        base_query += f"\nORDER BY {', '.join(order_parts)}"
    else:
        base_query += "\nORDER BY r.\"name\""

    base_query += f"\nLIMIT {limit}\n"

    return base_query


def build_recipe_nutrition_filter_sql(
    nutrition_filter: Union[Dict[str, Any], List[Dict[str, Any]]],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build SQL query for recipe nutrition filtering/sorting using recipe_metadata.

    Supports both macronutrients (protein, carbs, fat, etc.) and micronutrients
    (vitamins, minerals like iron, zinc, calcium, etc.).

    Args:
        nutrition_filter: Dict with sort_by, order, nutrient_key, OR a list of such dicts
                          When a list is provided, uses the first for primary sorting
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        limit: Max results

    Returns:
        SQL query string
    """
    # Handle list of nutrition filters - use the first one for primary sorting
    if isinstance(nutrition_filter, list):
        if not nutrition_filter:
            raise ValueError("nutrition_filter list is empty")
        # Use the first filter for primary sorting (usually the most important one)
        primary_filter = nutrition_filter[0]
    else:
        primary_filter = nutrition_filter

    # Get nutrient key - prefer nutrient_key, fallback to sort_by
    raw_key = primary_filter.get("nutrient_key") or primary_filter.get("sort_by", "protein")
    nutrient_key = normalize_nutrient_key(raw_key)

    order = primary_filter.get("order", "DESC")

    # Get the nutrition type and column name
    nutrition_type, column_name = get_nutrition_type_and_column(nutrient_key)

    # Build base query
    base_query = f"""
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings",
       r."recipe_metadata"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = '{language}'
  AND (r."private" = false OR r."userUid" = '{user_uid}' OR br."bundleId" IS NOT NULL)
"""

    # Add the nutrition WHERE clause
    base_query += build_nutrition_where_clause(nutrient_key)

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    # Build ORDER BY clause
    base_query += f"""
ORDER BY {build_nutrition_order_clause(nutrient_key, order)}
LIMIT {limit}
"""

    return base_query


def build_recipe_combined_filter_sql(
    cost_filter: Optional[Dict[str, Any]],
    nutrition_filter: Optional[Dict[str, Any]],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build SQL query combining both cost and nutrition filters.

    Args:
        cost_filter: Dict with operator, value, country (or None)
        nutrition_filter: Dict with sort_by, order, nutrient_key (or None)
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        limit: Max results

    Returns:
        SQL query string
    """
    # Map country codes to lowercase keys used in DB
    country_key_map = {
        "US": "usa",
        "India": "india",
        "Norway": "norway"
    }

    base_query = f"""
SELECT r."id", r."name", r."ingress", r."image",
       (r."prepTime" + r."cookTime") as total_time,
       r."difficulty", r."servings",
       r."recipe_metadata"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = '{language}'
  AND (r."private" = false OR r."userUid" = '{user_uid}' OR br."bundleId" IS NOT NULL)
  AND r."recipe_metadata" IS NOT NULL
"""

    order_clause = ""

    # Add cost filter conditions
    if cost_filter:
        country = cost_filter.get("country", "Norway")
        country_key = country_key_map.get(country, country.lower())
        operator = cost_filter.get("operator", "<=")
        value = cost_filter.get("value")
        base_query += f"""  AND r."recipe_metadata"->'pricing' IS NOT NULL
  AND r."recipe_metadata"->'pricing'->'{country_key}' IS NOT NULL
  AND r."recipe_metadata"->'pricing'->'{country_key}'->>'total' IS NOT NULL
  AND CAST(r."recipe_metadata"->'pricing'->'{country_key}'->>'total' AS FLOAT) {operator} {value}
"""

    # Add nutrition filter conditions
    if nutrition_filter:
        # Get nutrient key - prefer nutrient_key, fallback to sort_by
        raw_key = nutrition_filter.get("nutrient_key") or nutrition_filter.get("sort_by", "protein")
        nutrient_key = normalize_nutrient_key(raw_key)

        if nutrient_key:
            # Use helper function to build WHERE clause
            base_query += build_nutrition_where_clause(nutrient_key)
            order = nutrition_filter.get("order", "DESC")
            order_clause = f"ORDER BY {build_nutrition_order_clause(nutrient_key, order)}"

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    # Default order by cost if no nutrition sort specified
    if not order_clause and cost_filter:
        country = cost_filter.get("country", "Norway")
        country_key = country_key_map.get(country, country.lower())
        # Use sort_order from cost_filter: DESC for specific budget (nearest to budget), ASC for cheap
        cost_sort = cost_filter.get("sort_order", "DESC" if cost_filter.get("value") else "ASC")
        order_clause = f"ORDER BY CAST(r.\"recipe_metadata\"->'pricing'->'{country_key}'->>'total' AS FLOAT) {cost_sort}"

    base_query += f"""
{order_clause}
LIMIT {limit}
"""

    return base_query


def build_ingredient_price_sql(
    ingredient_name: str,
    country: Optional[str] = None
) -> str:
    """
    Build SQL query for ingredient price lookup.

    Args:
        ingredient_name: Name of ingredient to look up
        country: Country code (US, India, Norway) or None for all

    Returns:
        SQL query string
    """
    # Escape single quotes in ingredient name
    safe_name = ingredient_name.replace("'", "''")

    if country:
        return f"""
SELECT i."id", i."name", ip."price", ip."measurementUnit" as measurement_unit, ip."country"
FROM ingredient i
JOIN ingredient_pricing ip ON i."id" = ip."ingredientId"
WHERE LOWER(i."name") LIKE LOWER('%{safe_name}%')
  AND ip."country" = '{country}'
LIMIT 10
"""
    else:
        return f"""
SELECT i."id", i."name", ip."price", ip."measurementUnit" as measurement_unit, ip."country"
FROM ingredient i
JOIN ingredient_pricing ip ON i."id" = ip."ingredientId"
WHERE LOWER(i."name") LIKE LOWER('%{safe_name}%')
ORDER BY ip."country"
LIMIT 30
"""


def build_ingredient_nutrition_sql(
    ingredient_name: str,
    nutrient: Optional[str] = None,
    nutrition_type: str = "macros"
) -> str:
    """
    Build SQL query for ingredient nutrition lookup.

    Args:
        ingredient_name: Name of ingredient to look up
        nutrient: Specific nutrient column name (or None for all)
        nutrition_type: "macros" or "micros"

    Returns:
        SQL query string
    """
    # Escape single quotes in ingredient name
    safe_name = ingredient_name.replace("'", "''")

    table = "ingredient_macros" if nutrition_type == "macros" else "ingredient_micros"

    if nutrient:
        # Escape column name
        safe_nutrient = nutrient.replace('"', '""')
        return f"""
SELECT i."id", i."name", im."{safe_nutrient}", im."servingSize" as serving_size
FROM ingredient i
JOIN {table} im ON i."id" = im."ingredientId"
WHERE LOWER(i."name") LIKE LOWER('%{safe_name}%')
LIMIT 10
"""
    else:
        return f"""
SELECT i."id", i."name", im.*
FROM ingredient i
JOIN {table} im ON i."id" = im."ingredientId"
WHERE LOWER(i."name") LIKE LOWER('%{safe_name}%')
LIMIT 10
"""


def build_session_filter_conditions(
    session_filters: Dict[str, Any]
) -> List[str]:
    """
    Build additional SQL conditions from session context filters.

    Args:
        session_filters: Session context filters dict

    Returns:
        List of SQL condition strings
    """
    conditions = []

    if not session_filters:
        return conditions

    # Include ingredients filter (check both key variants)
    include_ingredients = session_filters.get("include_ingredients") or session_filters.get("included_ingredients", [])
    if include_ingredients:
        # Escape and join ingredient names
        escaped_ingredients = [ing.replace("'", "''").lower() for ing in include_ingredients]
        like_conditions = " OR ".join([f"LOWER(i.\"name\") LIKE '%{ing}%'" for ing in escaped_ingredients])
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM recipe_ingredient ri
                JOIN ingredient i ON ri."ingredientId" = i."id"
                WHERE ri."recipeId" = r."id"
                AND ({like_conditions})
            )
        """)

    # Exclude ingredients filter (checks name, description, AND ingredients)
    # Support both key variants: exclude_ingredients and excluded_ingredients
    exclude_ingredients = session_filters.get("exclude_ingredients") or session_filters.get("excluded_ingredients", [])
    if exclude_ingredients:
        for ing in exclude_ingredients:
            escaped_ing = ing.replace("'", "''").lower()
            conditions.append(f"""
                (
                    LOWER(r."name") NOT LIKE '%{escaped_ing}%'
                    AND LOWER(r."ingress") NOT LIKE '%{escaped_ing}%'
                    AND NOT EXISTS (
                        SELECT 1 FROM recipe_ingredient ri
                        JOIN ingredient i ON ri."ingredientId" = i."id"
                        WHERE ri."recipeId" = r."id"
                        AND LOWER(i."name") LIKE '%{escaped_ing}%'
                    )
                )
            """)

    # Cuisine filter (via tags)
    cuisines = session_filters.get("cuisines", [])
    if cuisines:
        escaped_cuisines = [c.replace("'", "''").lower() for c in cuisines]
        or_conditions = " OR ".join([f"LOWER(t.\"name\") = '{c}'" for c in escaped_cuisines])
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM recipe_tags_tag rtt
                JOIN tag t ON rtt."tagId" = t."id"
                WHERE rtt."recipeId" = r."id"
                AND ({or_conditions})
            )
        """)

    # Tags filter
    tags = session_filters.get("tags", [])
    if tags:
        escaped_tags = [t.replace("'", "''").lower() for t in tags]
        or_conditions = " OR ".join([f"LOWER(t.\"name\") = '{t}'" for t in escaped_tags])
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM recipe_tags_tag rtt
                JOIN tag t ON rtt."tagId" = t."id"
                WHERE rtt."recipeId" = r."id"
                AND ({or_conditions})
            )
        """)

    # Difficulty filter - supports both single value and list
    # When user asks for "easy" or "beginner", we include both "easy" and "normal"
    difficulty = session_filters.get("difficulty")
    if difficulty:
        if isinstance(difficulty, list):
            # Handle list of difficulties (e.g., ["easy", "normal"])
            escaped_values = [d.replace("'", "''") for d in difficulty]
            values_list = ", ".join([f"'{v}'" for v in escaped_values])
            conditions.append(f"r.\"difficulty\" IN ({values_list})")
        else:
            # Handle single difficulty value
            escaped_difficulty = difficulty.replace("'", "''")
            conditions.append(f"r.\"difficulty\" = '{escaped_difficulty}'")

    # Time filter
    max_time = session_filters.get("max_time")
    if max_time:
        try:
            max_time_int = int(max_time)
            conditions.append(f"(r.\"prepTime\" + r.\"cookTime\") <= {max_time_int}")
        except (ValueError, TypeError):
            pass

    # Excluded recipe IDs filter (for negative feedback: "I don't like these")
    excluded_recipe_ids = session_filters.get("excluded_recipe_ids", [])
    if excluded_recipe_ids:
        # Create list of UUIDs for NOT IN clause
        uuid_list = ", ".join([f"'{rid}'" for rid in excluded_recipe_ids])
        conditions.append(f"r.\"id\" NOT IN ({uuid_list})")

    # Servings filter - supports multiple formats:
    # - Exact integer: servings = 4
    # - Range dict: {"min": 2, "max": 4} → servings >= 2 AND servings <= 4
    # - Comparison dict: {"operator": ">=", "value": 4} → servings >= 4
    # - Sort only: {"sort": "DESC"} → just order by servings DESC (no filter)
    servings = session_filters.get("servings")
    if servings:
        if isinstance(servings, int):
            # Simple integer - exact match
            conditions.append(f'r.servings = {servings}')
        elif isinstance(servings, dict):
            # Complex servings filter
            min_val = servings.get("min")
            max_val = servings.get("max")
            operator = servings.get("operator")
            value = servings.get("value")
            # Note: "sort" key is handled separately in ORDER BY, not here

            if min_val is not None and max_val is not None:
                # Range filter: servings >= min AND servings <= max
                try:
                    min_int = int(min_val)
                    max_int = int(max_val)
                    conditions.append(f'r.servings >= {min_int} AND r.servings <= {max_int}')
                except (ValueError, TypeError):
                    pass
            elif operator and value is not None:
                # Comparison filter: servings >= 4, servings > 3, etc.
                operator_map = {"==": "=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}
                sql_op = operator_map.get(operator, "=")
                try:
                    value_int = int(value)
                    conditions.append(f'r.servings {sql_op} {value_int}')
                except (ValueError, TypeError):
                    pass
        else:
            # Try to parse as integer (string or other type)
            try:
                servings_int = int(servings)
                conditions.append(f'r.servings = {servings_int}')
            except (ValueError, TypeError):
                pass

    # Ingredient count filter (for "recipes with 5 ingredients" or "under 5 ingredients")
    ingredient_count = session_filters.get("ingredient_count")
    if ingredient_count:
        operator = ingredient_count.get("operator", "==")
        value = ingredient_count.get("value", 5)
        operator_map = {"==": "=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}
        sql_op = operator_map.get(operator, "=")
        try:
            value_int = int(value)
            conditions.append(f"""
                (SELECT COUNT(*) FROM recipe_ingredient ri
                 WHERE ri."recipeId" = r."id"
                 AND ri."deletedAt" IS NULL) {sql_op} {value_int}
            """)
        except (ValueError, TypeError):
            pass

    # Legacy ingredient count filter (for backward compatibility)
    ingredient_count_max = session_filters.get("ingredient_count_max")
    if ingredient_count_max and not ingredient_count:
        try:
            count_int = int(ingredient_count_max)
            conditions.append(f"""
                (SELECT COUNT(*) FROM recipe_ingredient ri
                 WHERE ri."recipeId" = r."id"
                 AND ri."deletedAt" IS NULL) <= {count_int}
            """)
        except (ValueError, TypeError):
            pass

    # Creator filter (for "recipes by username")
    creator_uid = session_filters.get("creator_uid")
    if creator_uid:
        escaped_uid = creator_uid.replace("'", "''")
        conditions.append(f"r.\"userUid\" = '{escaped_uid}'")

    return conditions
