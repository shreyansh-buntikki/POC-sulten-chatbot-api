"""
Agent Tools - Database operations as function tools for OpenAI Agents SDK
These tools provide database access for recipe search, nutrition info, etc.
"""
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, text, desc
import json

from models import (
    Recipe, Ingredient, RecipeIngredient, Seasonality,
    SeasonalityTranslation, SeasonalityTypeEnum, Tag,
    RecipeTagsTag, RecipeSeasonality, UserLikesRecipe,
    IngredientMacros, IngredientMicros, MeasuringUnit, IngredientPricing, Country, Currency
)


# =====================================================
# Recipe Search Tools
# =====================================================

def search_recipes_by_embedding(
    db: Session,
    query_text: str,
    limit: int = 10,
    threshold: float = 0.65,
    language_id: Optional[str] = None
) -> List[Tuple[Recipe, float]]:
    """
    Search recipes using semantic embeddings.

    Args:
        db: Database session
        query_text: Search query text
        limit: Maximum number of results
        threshold: Minimum similarity score (0-1)
        language_id: Optional language ID filter (e.g., 'en', 'no')

    Returns:
        List of (Recipe, similarity_score) tuples
    """
    # Debug logging
    import logging
    logger = logging.getLogger(__name__)

    from apps.fastapi.src.services.embedding_service import EmbeddingService

    embedding_service = EmbeddingService(db)
    results = embedding_service.search_recipes_by_embedding(
        query_text=query_text,
        limit=limit,
        threshold=threshold,
        language_id=language_id
    )

    logger.warning(f"[AGENT_TOOLS] search_recipes_by_embedding returned {len(results)} results")
    return results


def get_recipe_details(
    db: Session,
    recipe_id: str,
    user_uid: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get detailed information about a specific recipe.

    Args:
        db: Database session
        recipe_id: Recipe UUID
        user_uid: Optional user UID for personalized data

    Returns:
        Dictionary with recipe details or None
    """
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        return None

    # Get ingredients
    ingredients = db.query(RecipeIngredient).filter(
        RecipeIngredient.recipeId == recipe_id,
        RecipeIngredient.deletedAt == None
    ).order_by(RecipeIngredient.order).all()

    ingredient_list = []
    for ri in ingredients:
        if ri.ingredientId:
            ingr = db.query(Ingredient).filter(Ingredient.id == ri.ingredientId).first()
            unit_name = None
            if ri.unitId:
                unit = db.query(MeasuringUnit).filter(MeasuringUnit.id == ri.unitId).first()
                if unit:
                    # Try to get translation if available
                    translation = db.execute(text("""
                        SELECT name FROM measuring_unit_translation
                        WHERE "measuringUnitId" = :unit_id AND "languageId" = 'en'
                    """), {"unit_id": str(unit.id)}).fetchone()
                    unit_name = translation[0] if translation else unit.id
            if ingr:
                ingredient_list.append({
                    "id": str(ingr.id),
                    "name": ingr.name,
                    "amount": ri.amount,
                    "unit": unit_name
                })

    # Get instructions
    from models import RecipeInstruction
    instructions = db.query(RecipeInstruction).filter(
        RecipeInstruction.recipeId == recipe_id,
        RecipeInstruction.deletedAt == None
    ).order_by(RecipeInstruction.order).all()

    instruction_list = [
        {
            "order": instr.order,
            "description": instr.description,
            "image": instr.image
        }
        for instr in instructions
    ]

    # Get tags
    tags = db.execute(
        text("""
            SELECT t.name FROM tag t
            JOIN recipe_tags_tag rt ON t.id = rt."tagId"
            WHERE rt."recipeId" = :recipe_id
        """),
        {"recipe_id": str(recipe_id)}
    ).fetchall()

    tag_names = [row[0] for row in tags]

    # Get seasonality
    seasonalities = db.query(Seasonality).join(
        RecipeSeasonality, RecipeSeasonality.seasonalityId == Seasonality.id
    ).filter(
        RecipeSeasonality.recipeId == recipe_id,
        Seasonality.isActive == True
    ).all()

    season_list = [s.type for s in seasonalities]

    # Check if user likes this recipe
    is_liked = False
    if user_uid:
        like = db.query(UserLikesRecipe).filter(
            UserLikesRecipe.userUid == user_uid,
            UserLikesRecipe.recipeId == recipe_id
        ).first()
        is_liked = like is not None

    return {
        "id": str(recipe.id),
        "name": recipe.name,
        "slug": recipe.slug,
        "ingress": recipe.ingress,
        "image": recipe.image,
        "difficulty": recipe.difficulty,
        "prepTime": recipe.prepTime,
        "cookTime": recipe.cookTime,
        "servings": recipe.servings,
        "status": recipe.status,
        "ingredients": ingredient_list,
        "instructions": instruction_list,
        "tags": tag_names,
        "seasonality": season_list,
        "is_liked": is_liked
    }


def apply_recipe_filters(
    db: Session,
    recipes: List[Tuple[Recipe, float]],
    filters: Dict[str, Any]
) -> List[Tuple[Recipe, float]]:
    """
    Apply user preference filters to recipe search results.

    Args:
        db: Database session
        recipes: List of (Recipe, similarity_score) tuples
        filters: Dictionary of filter criteria

    Returns:
        Filtered list of (Recipe, similarity_score) tuples
    """
    if not recipes:
        return []

    filtered = recipes.copy()

    # Filter by max prep time
    if "max_prep_time" in filters:
        max_time = filters["max_prep_time"]
        filtered = [
            (r, s) for r, s in filtered
            if r.prepTime and r.prepTime <= max_time
        ]

    # Filter by difficulty
    if "difficulty" in filters:
        allowed_difficulties = filters["difficulty"]
        if isinstance(allowed_difficulties, str):
            allowed_difficulties = [allowed_difficulties]
        filtered = [
            (r, s) for r, s in filtered
            if r.difficulty in allowed_difficulties
        ]

    # Filter by servings
    if "servings" in filters:
        min_servings, max_servings = filters["servings"]
        filtered = [
            (r, s) for r, s in filtered
            if r.servings and min_servings <= r.servings <= max_servings
        ]

    # Filter by tags
    if "required_tags" in filters:
        required_tags = set(filters["required_tags"])
        filtered = [
            (r, s) for r, s in filtered
            if _has_all_tags(db, r.id, required_tags)
        ]

    # Filter by seasonality
    if "seasonalities" in filters:
        allowed_seasonalities = set(filters["seasonalities"])
        filtered = [
            (r, s) for r, s in filtered
            if _has_any_seasonality(db, r.id, allowed_seasonalities)
        ]

    return filtered


def _has_all_tags(db: Session, recipe_id: str, required_tags: set) -> bool:
    """Check if recipe has all required tags"""
    tags = db.execute(
        text("""
            SELECT t.name FROM tag t
            JOIN recipe_tags_tag rt ON t.id = rt."tagId"
            WHERE rt."recipeId" = :recipe_id
        """),
        {"recipe_id": str(recipe_id)}
    ).fetchall()

    recipe_tags = {row[0] for row in tags}
    return required_tags.issubset(recipe_tags)


def _has_any_seasonality(db: Session, recipe_id: str, allowed_seasonalities: set) -> bool:
    """Check if recipe has any of the allowed seasonalities"""
    seasonalities = db.query(Seasonality).join(
        RecipeSeasonality, RecipeSeasonality.seasonalityId == Seasonality.id
    ).filter(
        RecipeSeasonality.recipeId == recipe_id,
        Seasonality.isActive == True,
        Seasonality.type.in_(allowed_seasonalities)
    ).all()

    return len(seasonalities) > 0


def rank_recipes(
    db: Session,
    recipes: List[Tuple[Recipe, float]],
    user_uid: Optional[str] = None
) -> List[Tuple[Recipe, float]]:
    """
    Rank recipes by relevance, applying personalization if available.

    Args:
        db: Database session
        recipes: List of (Recipe, similarity_score) tuples
        user_uid: Optional user UID for personalization

    Returns:
        Ranked list of (Recipe, similarity_score) tuples
    """
    if not recipes:
        return []

    # Start with similarity scores
    ranked = list(recipes)

    # Boost liked recipes if user_uid provided
    if user_uid:
        for i, (recipe, score) in enumerate(ranked):
            is_liked = db.query(UserLikesRecipe).filter(
                UserLikesRecipe.userUid == user_uid,
                UserLikesRecipe.recipeId == recipe.id
            ).first()

            if is_liked:
                # Boost score by 10%
                ranked[i] = (recipe, score * 1.1)

    # Sort by adjusted score
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


# =====================================================
# Nutrition Tools
# =====================================================

def get_recipe_nutrition(
    db: Session,
    recipe_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get nutritional information for a recipe.

    Args:
        db: Database session
        recipe_id: Recipe UUID

    Returns:
        Dictionary with nutritional info or None
    """
    # Get recipe ingredients
    ingredients = db.query(RecipeIngredient).filter(
        RecipeIngredient.recipeId == recipe_id
    ).all()

    if not ingredients:
        return None

    total_nutrition = {
        "energyKcal": 0,
        "protein": 0,
        "carbohydrates": 0,
        "totalFiber": 0,
        "totalSugars": 0,
        "totalFat": 0,
        "saturatedFat": 0,
        "monounsaturatedFat": 0,
        "polyunsaturatedFat": 0,
        "cholesterol": 0,
        "sodium": 0,
        "calcium": 0,
        "iron": 0,
        "potassium": 0,
        "vitaminA": 0,
        "vitaminC": 0,
        "vitaminD": 0,
        "vitaminB12": 0,
    }

    ingredient_details = []

    for ri in ingredients:
        if not ri.ingredientId:
            continue

        # Get ingredient macros
        macros = db.query(IngredientMacros).filter(
            IngredientMacros.ingredientId == ri.ingredientId
        ).first()

        if macros:
            # Scale by amount (assuming macros are per 100g)
            factor = (ri.amount or 100) / 100.0

            nutrition = {
                "ingredient_id": str(ri.ingredientId),
                "amount": ri.amount,
                "energyKcal": float(macros.energyKcal or 0) * factor,
                "protein": float(macros.protein or 0) * factor,
                "carbohydrates": float(macros.carbohydrates or 0) * factor,
                "totalFiber": float(macros.totalFiber or 0) * factor,
                "totalSugars": float(macros.totalSugars or 0) * factor,
                "totalFat": float(macros.totalFat or 0) * factor,
                "saturatedFat": float(macros.saturatedFat or 0) * factor,
                "monounsaturatedFat": float(macros.monounsaturatedFat or 0) * factor,
                "polyunsaturatedFat": float(macros.polyunsaturatedFat or 0) * factor,
                "cholesterol": float(macros.cholesterol or 0) * factor,
            }

            ingredient_details.append(nutrition)

            # Add to totals
            for key in total_nutrition:
                if key in nutrition:
                    total_nutrition[key] += nutrition[key]

    return {
        "recipe_id": str(recipe_id),
        "total": total_nutrition,
        "per_ingredient": ingredient_details
    }


def get_ingredient_nutrition(
    db: Session,
    ingredient_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get nutritional information for a single ingredient.

    Args:
        db: Database session
        ingredient_id: Ingredient UUID

    Returns:
        Dictionary with nutritional info or None
    """
    ingredient = db.query(Ingredient).filter(Ingredient.id == ingredient_id).first()
    if not ingredient:
        return None

    macros = db.query(IngredientMacros).filter(
        IngredientMacros.ingredientId == ingredient_id
    ).first()

    micros = db.query(IngredientMicros).filter(
        IngredientMicros.ingredientId == ingredient_id
    ).first()

    nutrition_info = {
        "ingredient_id": str(ingredient.id),
        "name": ingredient.name,
        "serving_size": 100,  # Per 100g
        "macros": {},
        "micros": {}
    }

    if macros:
        nutrition_info["macros"] = {
            "energyKcal": float(macros.energyKcal or 0),
            "energyKj": float(macros.energyKj or 0),
            "protein": float(macros.protein or 0),
            "carbohydrates": float(macros.carbohydrates or 0),
            "totalFiber": float(macros.totalFiber or 0),
            "solubleFiber": float(macros.solubleFiber or 0),
            "insolubleFiber": float(macros.insolubleFiber or 0),
            "totalSugars": float(macros.totalSugars or 0),
            "addedSugar": float(macros.addedSugar or 0),
            "starch": float(macros.starch or 0),
            "totalFat": float(macros.totalFat or 0),
            "saturatedFat": float(macros.saturatedFat or 0),
            "transFat": float(macros.transFat or 0),
            "monounsaturatedFat": float(macros.monounsaturatedFat or 0),
            "polyunsaturatedFat": float(macros.polyunsaturatedFat or 0),
            "cholesterol": float(macros.cholesterol or 0),
        }

    if micros:
        nutrition_info["micros"] = {
            "vitaminA": float(micros.vitaminA or 0),
            "vitaminC": float(micros.vitaminC or 0),
            "vitaminD": float(micros.vitaminD or 0),
            "vitaminE": float(micros.vitaminE or 0),
            "vitaminK": float(micros.vitaminK or 0),
            "thiamineB1": float(micros.thiamineB1 or 0),
            "riboflavinB2": float(micros.riboflavinB2 or 0),
            "niacinB3": float(micros.niacinB3 or 0),
            "pantothenicAcidB5": float(micros.pantothenicAcidB5 or 0),
            "vitaminB6": float(micros.vitaminB6 or 0),
            "biotinB7": float(micros.biotinB7 or 0),
            "folateB9": float(micros.folateB9 or 0),
            "vitaminB12": float(micros.vitaminB12 or 0),
            "choline": float(micros.choline or 0),
            "calcium": float(micros.calcium or 0),
            "iron": float(micros.iron or 0),
            "magnesium": float(micros.magnesium or 0),
            "phosphorus": float(micros.phosphorus or 0),
            "potassium": float(micros.potassium or 0),
            "sodium": float(micros.sodium or 0),
            "zinc": float(micros.zinc or 0),
            "copper": float(micros.copper or 0),
            "manganese": float(micros.manganese or 0),
            "selenium": float(micros.selenium or 0),
            "fluoride": float(micros.fluoride or 0),
        }

    return nutrition_info


def search_ingredients_by_name(
    db: Session,
    name_query: str,
    limit: int = 10
) -> List[Ingredient]:
    """
    Search ingredients by name using fuzzy matching.

    Args:
        db: Database session
        name_query: Ingredient name to search for
        limit: Maximum number of results

    Returns:
        List of Ingredient objects
    """
    # Simple ILIKE search (can be enhanced with pgvector)
    ingredients = db.query(Ingredient).filter(
        Ingredient.name.ilike(f"%{name_query}%")
    ).limit(limit).all()

    return ingredients


def search_similar_ingredients(
    db: Session,
    ingredient_name: str,
    limit: int = 8,
    threshold: float = 0.5
) -> List[Dict[str, Any]]:
    """
    Find ingredients semantically similar to given ingredient.

    Useful for:
    - Substitutions: "What can I use instead of basil?"
    - Discovery: "What herbs are similar to thyme?"
    - Exploration: "If I like garlic, what else might I like?"
    - Regional: "What's the Thai equivalent of this ingredient?"

    Args:
        db: Database session
        ingredient_name: Name of ingredient to find similarities for
        limit: Maximum number of results
        threshold: Minimum similarity score (0-1)

    Returns:
        List of ingredient dicts with name and similarity score
    """
    from apps.fastapi.src.services.embedding_service import EmbeddingService

    embedding_service = EmbeddingService(db)
    results = embedding_service.search_ingredients_by_embedding(
        query_text=ingredient_name,
        limit=limit,
        threshold=threshold
    )

    return [
        {
            "id": str(ingr.id),
            "name": ingr.name,
            "similarity": round(score, 3)
        }
        for ingr, score in results
    ]


# =====================================================
# Ingredient Pricing Tools
# =====================================================

def get_ingredient_pricing(
    db: Session,
    ingredient_name: str,
    country_code: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get pricing information for an ingredient.

    Args:
        db: Database session
        ingredient_name: Name of the ingredient
        country_code: Optional country code (e.g., 'US', 'NO', 'IN', 'IND')

    Returns:
        Dictionary with pricing info or None if no pricing data found
    """
    # Find ingredient by name - prioritize exact matches, then prefix matches, then contains
    # This prevents "carrot" from matching "rainbow carrots" first
    ingredient = None

    # Try exact match first (case-insensitive)
    ingredient = db.query(Ingredient).filter(
        func.lower(Ingredient.name) == func.lower(ingredient_name)
    ).first()

    # If no exact match, try prefix match (e.g., "carrot" matches "carrots" but not "rainbow carrots")
    # Order by name length to prefer shorter/more common names
    if not ingredient:
        ingredient = db.query(Ingredient).filter(
            Ingredient.name.ilike(f"{ingredient_name}%")
        ).order_by(func.length(Ingredient.name).asc()).first()

    # If no prefix match, try contains match as last resort
    # Order by name length and similarity to prefer closer matches
    if not ingredient:
        ingredient = db.query(Ingredient).filter(
            Ingredient.name.ilike(f"%{ingredient_name}%")
        ).order_by(
            func.length(Ingredient.name).asc(),
            Ingredient.name.asc()
        ).first()

    if not ingredient:
        return None

    # Build query for pricing
    pricing_query = db.query(IngredientPricing, Country, Currency).join(
        Country, IngredientPricing.countryId == Country.id
    ).join(
        Currency, IngredientPricing.currencyId == Currency.id
    ).filter(
        IngredientPricing.ingredientId == ingredient.id
    )

    # Filter by country if specified - match exact code or partial (e.g., "India" -> "IND")
    if country_code:
        # Try exact match first, then partial match
        pricing_query = pricing_query.filter(
            (Country.code.ilike(country_code)) |
            (Country.code.ilike(f"%{country_code}%"))
        )

    # Get all pricing records
    pricing_records = pricing_query.all()

    # Return None instead of empty dict to trigger LLM fallback
    if not pricing_records:
        return None

    pricing_list = []
    for pricing, country, currency in pricing_records:
        unit_name = None
        if pricing.measuringUnitId:
            # Get the unit name from translation table
            unit_translation = db.execute(text("""
                SELECT name FROM measuring_unit_translation
                WHERE "measuringUnitId" = :unit_id
                ORDER BY CASE WHEN "languageId" = 'en' THEN 0 ELSE 1 END
                LIMIT 1
            """), {"unit_id": str(pricing.measuringUnitId)}).fetchone()

            if unit_translation and unit_translation[0]:
                unit_name = unit_translation[0]
            else:
                # Fallback to unit ID if no translation found
                unit = db.query(MeasuringUnit).filter(MeasuringUnit.id == pricing.measuringUnitId).first()
                if unit:
                    # Try to get a readable name from the unit itself
                    unit_name = str(unit.id).split('-')[-1] if '-' in str(unit.id) else str(unit.id)

        pricing_list.append({
            "country": country.code,
            "country_name": country.name,
            "currency": currency.code,
            "currency_symbol": currency.symbol,
            "price_per_unit": float(pricing.pricePerUnit),
            "quantity": pricing.quantity,
            "unit": unit_name or "unit",
            "total_price": float(pricing.pricePerUnit * pricing.quantity) if pricing.quantity else float(pricing.pricePerUnit)
        })

    return {
        "ingredient_id": str(ingredient.id),
        "name": ingredient.name,
        "pricing": pricing_list
    }


def get_recipe_cost(
    db: Session,
    recipe_id: str,
    country_code: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Calculate the cost of a recipe based on ingredient prices.

    Args:
        db: Database session
        recipe_id: Recipe UUID
        country_code: Optional country code for pricing (e.g., 'US', 'NO', 'IN')

    Returns:
        Dictionary with cost breakdown or None
    """
    # Get recipe
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        return None

    # Get recipe ingredients
    ingredients = db.query(RecipeIngredient).filter(
        RecipeIngredient.recipeId == recipe_id,
        RecipeIngredient.deletedAt == None
    ).all()

    if not ingredients:
        return None

    total_cost = 0
    ingredient_costs = []

    for ri in ingredients:
        if not ri.ingredientId:
            continue

        ingredient = db.query(Ingredient).filter(Ingredient.id == ri.ingredientId).first()
        if not ingredient:
            continue

        # Get pricing for this ingredient
        pricing_query = db.query(IngredientPricing, Currency).join(
            Currency, IngredientPricing.currencyId == Currency.id
        ).join(
            Country, IngredientPricing.countryId == Country.id
        ).filter(
            IngredientPricing.ingredientId == ri.ingredientId
        )

        # Filter by country if specified
        if country_code:
            pricing_query = pricing_query.filter(Country.code.ilike(f"%{country_code}%"))

        # Get first matching price
        pricing_result = pricing_query.first()

        if pricing_result:
            pricing, currency = pricing_result
            # Calculate cost: price per unit * (recipe amount / pricing quantity)
            recipe_quantity = ri.amount or 1
            pricing_quantity = pricing.quantity or 1
            cost_per_base_unit = float(pricing.pricePerUnit)
            cost = cost_per_base_unit * (recipe_quantity / pricing_quantity)

            ingredient_costs.append({
                "ingredient": ingredient.name,
                "amount": recipe_quantity,
                "cost": round(cost, 2),
                "currency": currency.code,
                "currency_symbol": currency.symbol
            })

            total_cost += cost
        else:
            ingredient_costs.append({
                "ingredient": ingredient.name,
                "amount": ri.amount,
                "cost": None,
                "note": "No pricing data available"
            })

    # Get currency for total (use first available)
    currency_code = ingredient_costs[0].get("currency") if ingredient_costs else "USD"
    currency_symbol = ingredient_costs[0].get("currency_symbol") if ingredient_costs else "$"

    return {
        "recipe_id": str(recipe.id),
        "recipe_name": recipe.name,
        "total_cost": round(total_cost, 2) if total_cost > 0 else None,
        "currency": currency_code,
        "currency_symbol": currency_symbol,
        "ingredient_costs": ingredient_costs,
        "servings": recipe.servings or 1,
        "cost_per_serving": round(total_cost / (recipe.servings or 1), 2) if total_cost > 0 else None
    }
