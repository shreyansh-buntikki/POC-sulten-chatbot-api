"""
Nutritional Agent
Provides nutritional information for recipes and ingredients
"""
import json
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from apps.fastapi.src.agents.base_agent import BaseAgent
from models import (
    Recipe, RecipeIngredient, Ingredient,
    IngredientMacros, IngredientMicros
)


class NutritionalAgent(BaseAgent):
    """
    Nutritional Information Agent

    Provides nutritional data for:
    - Individual recipes (sum of ingredients)
    - Single ingredients
    - Meal planning suggestions
    """

    def __init__(self, db: Session, openai_client=None):
        super().__init__(db, openai_client)
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the system prompt for nutritional queries"""
        return self._format_system_prompt("""You are a nutrition expert helping users understand the nutritional content of recipes and ingredients.

Your task is to:
1. Provide accurate nutritional information based on the data available
2. Explain nutritional concepts in simple, accessible terms
3. Offer practical health insights without being overly prescriptive
4. Acknowledge limitations when data is incomplete

When presenting nutrition info:
- Start with a direct answer
- Break down key macronutrients (calories, protein, carbs, fat, fiber)
- Highlight notable micronutrients (vitamins, minerals)
- Provide context (daily values, health benefits)
- Keep responses clear and educational

Remember:
- You're providing information, not medical advice
- Values are typically per 100g serving unless specified
- Be helpful but acknowledge data limitations
""")

    async def process(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a nutritional query

        Args:
            query: User's question about nutrition
            context: Additional context including entities from NLID agent

        Returns:
            Dictionary with nutritional information and formatted response
        """
        entities = context.get("entities", {})

        # Identify what the user is asking about
        target_type, target_id = self._identify_target(query, entities)

        if target_type == "recipe" and target_id:
            result = self._get_recipe_nutrition(target_id)
        elif target_type == "ingredient" and target_id:
            result = self._get_ingredient_nutrition(target_id)
        elif entities.get("ingredients"):
            # Get nutrition for specific ingredients mentioned
            result = self._get_multi_ingredient_nutrition(entities["ingredients"])
        else:
            result = self._generate_general_nutrition_response(query)

        return {
            "response": result.get("response", ""),
            "metadata": result.get("metadata", {}),
            "next_agent": None
        }

    def _identify_target(self, query: str, entities: Dict) -> tuple:
        """
        Identify what the query is asking about

        Returns:
            Tuple of (target_type, target_id) where target_type is 'recipe', 'ingredient', or 'general'
        """
        # Check for recipe context
        recipe_context = entities.get("recipe_id")
        if recipe_context:
            return "recipe", recipe_context

        # Check for ingredient mentions
        ingredients = entities.get("ingredients", [])
        if len(ingredients) == 1:
            # Try to find the ingredient
            ingredient = self.db.query(Ingredient).filter(
                Ingredient.name.ilike(f"%{ingredients[0]}%")
            ).first()
            if ingredient:
                return "ingredient", ingredient.id

        return "general", None

    def _get_recipe_nutrition(self, recipe_id: str) -> Dict[str, Any]:
        """Get nutritional information for a recipe"""
        # Get recipe ingredients
        recipe_ingredients = self.db.query(RecipeIngredient).filter(
            RecipeIngredient.recipeId == recipe_id,
            RecipeIngredient.deletedAt == None
        ).all()

        if not recipe_ingredients:
            return self._generate_general_nutrition_response("No ingredients found for this recipe")

        # Aggregate nutrition from all ingredients
        total_nutrition = self._aggregate_ingredient_nutrition(recipe_ingredients)

        # Format the nutrition data
        nutrition_text = self._format_nutrition_summary(total_nutrition)

        # Generate natural language response
        response = self._generate_nutrition_response("recipe", nutrition_text, total_nutrition)

        return {
            "response": response,
            "metadata": {
                "type": "recipe",
                "nutrition": total_nutrition
            }
        }

    def _get_ingredient_nutrition(self, ingredient_id: str) -> Dict[str, Any]:
        """Get nutritional information for a single ingredient"""
        ingredient = self.db.query(Ingredient).filter(Ingredient.id == ingredient_id).first()
        if not ingredient:
            return {"response": "Ingredient not found.", "metadata": {}}

        macros = self.db.query(IngredientMacros).filter(
            IngredientMacros.ingredientId == ingredient_id
        ).first()

        micros = self.db.query(IngredientMicros).filter(
            IngredientMicros.ingredientId == ingredient_id
        ).first()

        nutrition = {
            "ingredient_name": ingredient.name,
            "serving_size": macros.servingSize if macros else 100,
            "macros": self._serialize_macros(macros) if macros else {},
            "micros": self._serialize_micros(micros) if micros else {}
        }

        nutrition_text = self._format_nutrition_summary(nutrition)
        response = self._generate_nutrition_response(ingredient.name, nutrition_text, nutrition)

        return {
            "response": response,
            "metadata": {
                "type": "ingredient",
                "ingredient_id": ingredient_id,
                "nutrition": nutrition
            }
        }

    def _get_multi_ingredient_nutrition(self, ingredient_names: List[str]) -> Dict[str, Any]:
        """Get nutrition for multiple ingredients"""
        ingredients_data = []

        for name in ingredient_names:
            ingredient = self.db.query(Ingredient).filter(
                Ingredient.name.ilike(f"%{name}%")
            ).first()

            if ingredient:
                macros = self.db.query(IngredientMacros).filter(
                    IngredientMacros.ingredientId == ingredient.id
                ).first()

                ingredients_data.append({
                    "name": ingredient.name,
                    "macros": self._serialize_macros(macros) if macros else {}
                })

        if not ingredients_data:
            return {"response": "No nutritional information found for those ingredients.", "metadata": {}}

        # Generate response for multiple ingredients
        response = self._generate_multi_ingredient_response(ingredients_data)

        return {
            "response": response,
            "metadata": {
                "type": "multi_ingredient",
                "ingredients": ingredients_data
            }
        }

    def _aggregate_ingredient_nutrition(self, recipe_ingredients: List[RecipeIngredient]) -> Dict:
        """Aggregate nutrition from all recipe ingredients"""
        total = {
            "serving_size": 100,
            "energy_kcal": 0,
            "protein": 0,
            "carbohydrates": 0,
            "total_fiber": 0,
            "total_fat": 0,
            "saturated_fat": 0
        }

        for ri in recipe_ingredients:
            if not ri.ingredientId:
                continue

            macros = self.db.query(IngredientMacros).filter(
                IngredientMacros.ingredientId == ri.ingredientId
            ).first()

            if macros:
                # Scale by amount if available
                scale = (ri.amount or 1) / macros.servingSize if macros.servingSize else 1

                total["energy_kcal"] += (macros.energyKcal or 0) * scale
                total["protein"] += (macros.protein or 0) * scale
                total["carbohydrates"] += (macros.carbohydrates or 0) * scale
                total["total_fiber"] += (macros.totalFiber or 0) * scale
                total["total_fat"] += (macros.totalFat or 0) * scale
                total["saturated_fat"] += (macros.saturatedFat or 0) * scale

        return total

    def _serialize_macros(self, macros: IngredientMacros) -> Dict:
        """Convert macros object to dictionary"""
        return {
            "serving_size": macros.servingSize,
            "energy_kcal": float(macros.energyKcal) if macros.energyKcal else 0,
            "energy_kj": float(macros.energyKj) if macros.energyKj else 0,
            "protein": float(macros.protein) if macros.protein else 0,
            "carbohydrates": float(macros.carbohydrates) if macros.carbohydrates else 0,
            "total_fiber": float(macros.totalFiber) if macros.totalFiber else 0,
            "total_sugars": float(macros.totalSugars) if macros.totalSugars else 0,
            "total_fat": float(macros.totalFat) if macros.totalFat else 0,
            "saturated_fat": float(macros.saturatedFat) if macros.saturatedFat else 0,
            "trans_fat": float(macros.transFat) if macros.transFat else 0,
            "cholesterol": float(macros.cholesterol) if macros.cholesterol else 0,
        }

    def _serialize_micros(self, micros: IngredientMicros) -> Dict:
        """Convert micros object to dictionary"""
        return {
            "vitamin_a": float(micros.vitaminA) if micros.vitaminA else 0,
            "vitamin_c": float(micros.vitaminC) if micros.vitaminC else 0,
            "vitamin_d": float(micros.vitaminD) if micros.vitaminD else 0,
            "calcium": float(micros.calcium) if micros.calcium else 0,
            "iron": float(micros.iron) if micros.iron else 0,
            "potassium": float(micros.potassium) if micros.potassium else 0,
            "sodium": float(micros.sodium) if micros.sodium else 0,
        }

    def _format_nutrition_summary(self, nutrition: Dict) -> str:
        """Format nutrition data as text for LLM context"""
        lines = ["Nutritional Information (per serving):"]

        if nutrition.get("energy_kcal"):
            lines.append(f"- Calories: {int(nutrition['energy_kcal'])} kcal")

        if nutrition.get("protein"):
            lines.append(f"- Protein: {nutrition['protein']:.1f}g")

        if nutrition.get("carbohydrates"):
            lines.append(f"- Carbohydrates: {nutrition['carbohydrates']:.1f}g")

        if nutrition.get("total_fiber"):
            lines.append(f"- Fiber: {nutrition['total_fiber']:.1f}g")

        if nutrition.get("total_fat"):
            lines.append(f"- Total Fat: {nutrition['total_fat']:.1f}g")

        if nutrition.get("saturated_fat"):
            lines.append(f"- Saturated Fat: {nutrition['saturated_fat']:.1f}g")

        return "\n".join(lines)

    def _generate_nutrition_response(self, item_name: str, nutrition_text: str, nutrition_data: Dict) -> str:
        """Generate natural language response for nutritional query"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""User is asking about nutrition for: {item_name}

{nutrition_text}

Provide a helpful, informative response about the nutritional content."""}
        ]

        return self._call_openai(messages, temperature=0.7, max_tokens=350)

    def _generate_multi_ingredient_response(self, ingredients: List[Dict]) -> str:
        """Generate response for multiple ingredients"""
        ingredient_info = []
        for ing in ingredients:
            name = ing["name"]
            macros = ing["macros"]
            info = f"- {name}: {macros.get('energy_kcal', 0):.0f} kcal, {macros.get('protein', 0):.1f}g protein"
            ingredient_info.append(info)

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""User is asking about nutrition for these ingredients:
{chr(10).join(ingredient_info)}

Provide a helpful comparison or summary."""}
        ]

        return self._call_openai(messages, temperature=0.7, max_tokens=300)

    def _generate_general_nutrition_response(self, query: str) -> Dict[str, Any]:
        """Generate response for general nutritional questions"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""User question: "{query}"

Provide helpful nutritional information or ask clarifying questions."""}
        ]

        response = self._call_openai(messages, temperature=0.7, max_tokens=250)

        return {
            "response": response,
            "metadata": {"type": "general"}
        }
