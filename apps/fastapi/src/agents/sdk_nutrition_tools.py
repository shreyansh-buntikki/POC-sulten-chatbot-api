"""
Nutrition Tools for SDK NutritionalAgent
These are SDK-compatible function tools that wrap database operations
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from agents import function_tool

from models import (
    Recipe, Ingredient, RecipeIngredient,
    RecipeTagsTag, UserLikesRecipe,
    IngredientMacros, IngredientMicros
)


# =====================================================
# Nutrition Tools for NutritionalAgent
# =====================================================

@function_tool
def get_recipe_nutrition(
    recipe_id: str
) -> Dict[str, Any]:
    """
    Get nutritional information for a recipe.

    Provides complete nutritional breakdown including:
    - Calories (energyKcal)
    - Macronutrients (protein, carbs, fat, fiber, sugars)
    - Micronutrients (vitamins and minerals)
    - Per-ingredient breakdown

    Args:
        recipe_id: Recipe UUID to get nutrition information for

    Returns:
        Dictionary with total nutrition and per-ingredient details
    """
    from database import get_db

    db = next(get_db())

    try:
        # Get recipe ingredients
        ingredients = db.query(RecipeIngredient).filter(
            RecipeIngredient.recipeId == recipe_id
        ).all()

        if not ingredients:
            return {"error": "No ingredients found for this recipe"}

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

            # Get ingredient info
            ingredient = db.query(Ingredient).filter(
                Ingredient.id == ri.ingredientId
            ).first()

            if not ingredient:
                continue

            # Get ingredient macros
            macros = db.query(IngredientMacros).filter(
                IngredientMacros.ingredientId == ri.ingredientId
            ).first()

            if macros:
                # Scale by amount (assuming macros are per 100g)
                factor = (ri.amount or 100) / 100.0

                nutrition = {
                    "ingredient_name": ingredient.name,
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

    finally:
        db.close()


@function_tool
def get_ingredient_nutrition(
    ingredient_name: str
) -> Dict[str, Any]:
    """
    Get nutritional information for a single ingredient by name.

    Provides comprehensive nutritional data per 100g serving:
    - Macronutrients (calories, protein, carbs, fat, fiber)
    - Vitamins (A, C, D, E, K, B-complex)
    - Minerals (calcium, iron, magnesium, potassium, sodium, zinc, etc.)

    Args:
        ingredient_name: Name of the ingredient to look up

    Returns:
        Dictionary with complete nutritional profile per 100g
    """
    from database import get_db

    db = next(get_db())

    try:
        # Search for ingredient by name
        ingredient = db.query(Ingredient).filter(
            Ingredient.name.ilike(f"%{ingredient_name}%")
        ).first()

        if not ingredient:
            return {"error": f"Ingredient '{ingredient_name}' not found"}

        macros = db.query(IngredientMacros).filter(
            IngredientMacros.ingredientId == ingredient.id
        ).first()

        micros = db.query(IngredientMicros).filter(
            IngredientMicros.ingredientId == ingredient.id
        ).first()

        nutrition_info = {
            "ingredient_id": str(ingredient.id),
            "name": ingredient.name,
            "serving_size": "100g",
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

    finally:
        db.close()


@function_tool
def search_similar_ingredients(
    ingredient_name: str,
    limit: int = 8,
    threshold: float = 0.5
) -> List[Dict[str, Any]]:
    """
    Find ingredients semantically similar to a given ingredient.

    Uses vector embeddings to find ingredients based on:
    - Culinary context (used in similar recipes)
    - Flavor profiles and usage patterns
    - Semantic similarity, not just name matching

    Useful for:
    - Substitutions: "What can I use instead of basil?"
    - Discovery: "What herbs are similar to thyme?"
    - Exploration: "If I like garlic, what else might I like?"
    - Regional variations: "What's the Thai equivalent of this ingredient?"

    Args:
        ingredient_name: Name of ingredient to find similarities for
        limit: Maximum number of results to return (default: 8)
        threshold: Minimum similarity score from 0-1 (default: 0.5)

    Returns:
        List of ingredients with names and similarity scores
    """
    from database import get_db
    from apps.fastapi.src.services.embedding_service import EmbeddingService

    db = next(get_db())

    try:
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
                "similarity": round(score, 3),
                "match_level": "high" if score > 0.8 else "medium" if score > 0.65 else "low"
            }
            for ingr, score in results
        ]

    finally:
        db.close()


@function_tool
def search_ingredients_by_name(
    name_query: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Search ingredients by name using pattern matching.

    Finds ingredients that contain the search text in their name.
    Useful for finding exact or partial name matches.

    Args:
        name_query: Ingredient name or partial name to search for
        limit: Maximum number of results to return (default: 10)

    Returns:
        List of matching ingredients with their IDs and names
    """
    from database import get_db

    db = next(get_db())

    try:
        ingredients = db.query(Ingredient).filter(
            Ingredient.name.ilike(f"%{name_query}%")
        ).limit(limit).all()

        return [
            {
                "id": str(ingr.id),
                "name": ingr.name
            }
            for ingr in ingredients
        ]

    finally:
        db.close()


# Export tools list for agent
nutrition_tools = [
    get_recipe_nutrition,
    get_ingredient_nutrition,
    search_similar_ingredients,
    search_ingredients_by_name,
]
