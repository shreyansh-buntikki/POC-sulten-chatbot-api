"""
SQL Query Builders for Cost and Nutrition Filters
Builds SQL queries that filter recipes by recipe_metadata (pricing/nutrition)
and query ingredient tables directly.
"""
from typing import Dict, Any, List, Optional


def build_recipe_cost_filter_sql(
    cost_filter: Dict[str, Any],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build SQL query for recipe cost filtering using recipe_metadata.

    Args:
        cost_filter: Dict with operator, value, country
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

    # Determine order direction based on operator and value
    # For "low budget" queries (value is None), order by price ascending (cheapest first)
    # For specific budget queries (value is set), order by price descending (closest to budget)
    if value is None:
        # No specific value - this is a "budget" or "cheap" request
        # Order by price ascending to show cheapest recipes first
        order_direction = "ASC"
        price_condition = ""  # No filtering, just ordering
    else:
        # Specific budget provided
        order_direction = "DESC"
        price_condition = f"  AND CAST(r.\"recipe_metadata\"->'pricing'->'{country_key}'->>'total' AS FLOAT) {operator} {value}\n"

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


def build_recipe_nutrition_filter_sql(
    nutrition_filter: Dict[str, Any],
    user_uid: str,
    language: str,
    additional_conditions: Optional[List[str]] = None,
    limit: int = 20
) -> str:
    """
    Build SQL query for recipe nutrition filtering/sorting using recipe_metadata.

    Args:
        nutrition_filter: Dict with sort_by, order, nutrient_key
        user_uid: User identifier
        language: Language code
        additional_conditions: Additional WHERE conditions
        limit: Max results

    Returns:
        SQL query string
    """
    # Map common nutrient names to actual DB keys
    # If a nutrient is not in this mapping, it will be used as-is (pass-through)
    nutrient_key_mapping = {
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
        # Sodium/Salt
        "sodium": "sodium",
        "salt": "sodium",
        # Cholesterol
        "cholesterol": "cholesterol",
        # Saturated Fat
        "saturatedFat": "saturatedFat",
        "saturated fat": "saturatedFat",
        "saturated": "saturatedFat",
        # Starch
        "starch": "starch",
        # Trans Fat
        "transFat": "transFat",
        "trans fat": "transFat",
        "totalSugars": "totalSugars",
    }

    # Get nutrient key - prefer nutrient_key, fallback to sort_by
    raw_key = nutrition_filter.get("nutrient_key") or nutrition_filter.get("sort_by", "protein")
    nutrient_key = nutrient_key_mapping.get(raw_key, raw_key)

    order = nutrition_filter.get("order", "DESC")

    # The actual structure is: recipe_metadata -> totalNutrition -> macros -> nutrient
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
  AND r."recipe_metadata"->'totalNutrition' IS NOT NULL
  AND r."recipe_metadata"->'totalNutrition'->'macros' IS NOT NULL
  AND r."recipe_metadata"->'totalNutrition'->'macros'->>'{nutrient_key}' IS NOT NULL
"""

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    base_query += f"""
ORDER BY CAST(r."recipe_metadata"->'totalNutrition'->'macros'->>'{nutrient_key}' AS FLOAT) {order}
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
        # Map common nutrient names to actual DB keys
        # If a nutrient is not in this mapping, it will be used as-is (pass-through)
        nutrient_key_mapping = {
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
            # Sodium/Salt
            "sodium": "sodium",
            "salt": "sodium",
            # Cholesterol
            "cholesterol": "cholesterol",
            # Saturated Fat
            "saturatedFat": "saturatedFat",
            "saturated fat": "saturatedFat",
            "saturated": "saturatedFat",
            # Starch
            "starch": "starch",
            # Trans Fat
            "transFat": "transFat",
            "trans fat": "transFat",
        }

        # Get nutrient key - prefer nutrient_key, fallback to sort_by
        raw_key = nutrition_filter.get("nutrient_key") or nutrition_filter.get("sort_by", "protein")
        nutrient_key = nutrient_key_mapping.get(raw_key, raw_key)

        if nutrient_key:
            base_query += f"""  AND r."recipe_metadata"->'totalNutrition' IS NOT NULL
  AND r."recipe_metadata"->'totalNutrition'->'macros' IS NOT NULL
  AND r."recipe_metadata"->'totalNutrition'->'macros'->>'{nutrient_key}' IS NOT NULL
"""
            order = nutrition_filter.get("order", "DESC")
            order_clause = f"ORDER BY CAST(r.\"recipe_metadata\"->'totalNutrition'->'macros'->>'{nutrient_key}' AS FLOAT) {order}"

    if additional_conditions:
        for condition in additional_conditions:
            base_query += f"  AND {condition}\n"

    # Default order by cost if no nutrition sort specified
    if not order_clause and cost_filter:
        country = cost_filter.get("country", "Norway")
        country_key = country_key_map.get(country, country.lower())
        order_clause = f"ORDER BY CAST(r.\"recipe_metadata\"->'pricing'->'{country_key}'->>'total' AS FLOAT) ASC"

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

    # Ingredient count filter (for "recipes with under 5 ingredients")
    ingredient_count_max = session_filters.get("ingredient_count_max")
    if ingredient_count_max:
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
