"""
Recipe Retrieval Agent
Searches for recipes using semantic search and structured filters
"""
import json
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from apps.fastapi.src.agents.base_agent import BaseAgent
from apps.fastapi.src.services.embedding_service import EmbeddingService
from apps.fastapi.src.services.user_context_service import UserContextService
from models import (
    Recipe, RecipeIngredient, Ingredient, RecipeInstruction,
    RecipeTagsTag, Tag, RecipeSeasonality, Seasonality
)


class RecipeRetrievalAgent(BaseAgent):
    """
    Recipe Retrieval Agent

    Searches for recipes using:
    - Semantic search (embeddings via pgvector)
    - Structured filters (cuisine, difficulty, time, etc.)
    - User preferences and context
    - Personalized ranking (liked > created > others)
    """

    MAX_RECIPES = 5

    def __init__(self, db: Session, openai_client=None):
        super().__init__(db, openai_client)
        self.embedding_service = EmbeddingService(db)
        self.user_context = UserContextService(db)
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the system prompt for recipe search responses"""
        return self._format_system_prompt("""You are a recipe assistant helping users find recipes.

Your task is to:
1. Present recipe options in a clear, organized way
2. Highlight key details (time, difficulty, main ingredients)
3. Be helpful and suggest alternatives if no exact match is found
4. Handle bundle recipes appropriately (limited info for unpurchased)

When presenting recipes:
- Start with a brief acknowledgment
- List up to 5 top matching recipes with:
  - Recipe name
  - Brief description
  - Prep/cook time
  - Difficulty level
  - Key ingredients (2-3)
- For bundle recipes not purchased, only show name
- Offer to provide full details for any recipe
- If results are limited, suggest related searches

Keep responses friendly and concise (under 300 words when possible).

If no recipes are found:
- Acknowledge that we couldn't find exact matches
- Ask clarifying questions or suggest alternatives
- Encourage the user to try different search terms
""")

    async def process(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a recipe search query

        Args:
            query: User's search query
            context: Additional context including entities and filters from NLID agent

        Returns:
            Dictionary with search results and formatted response
        """
        user_uid = context.get("user_uid")
        entities = context.get("entities", {})
        parameters = context.get("parameters", {})
        filters = context.get("filters", {})

        # Get user context
        user_context_data = self.user_context.get_user_context(user_uid)

        # Step 1: Search using semantic embeddings
        semantic_results = self.embedding_service.search_recipes_by_embedding(
            query_text=query,
            limit=20,  # Get more to filter down
            threshold=0.65
        )

        # Step 2: If semantic search didn't yield results, try structured search
        if not semantic_results and entities:
            structured_results = self._structured_search(entities, parameters, filters)
            semantic_results = [(r, 0.7) for r in structured_results]

        # Step 3: Filter out excluded recipes (private, deleted, allergic ingredients)
        filtered_results = self._filter_recipes(
            semantic_results,
            user_context_data,
            filters.get("excluded_ingredients", [])
        )

        # Step 4: Apply additional structured filters (tags, seasonality, regional)
        filtered_results = self._apply_advanced_filters(
            filtered_results,
            filters,
            user_context_data
        )

        # Step 5: Apply parameter filters (time, difficulty, servings)
        filtered_results = self._apply_parameter_filters(filtered_results, parameters)

        # Step 6: Prioritize based on user context (liked > created > others)
        prioritized_results = self.user_context.prioritize_recipes(
            filtered_results,
            user_uid
        )

        # Step 7: Limit to max recipes
        top_results = prioritized_results[:self.MAX_RECIPES]

        # Step 8: Format results with bundle access check
        formatted_results = self._format_results_with_access(top_results, user_uid)

        # Step 9: Generate natural language response
        response = self._generate_response(query, formatted_results, entities)

        # Step 10: Prepare metadata
        metadata = {
            "num_results": len(formatted_results),
            "search_method": "semantic" if semantic_results else "structured",
            "recipes": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "similarity": round(r.get("similarity", 0), 3),
                    "access_level": r["access_level"],
                    "is_liked": r.get("is_liked", False),
                    "is_created_by_user": r.get("is_created_by_user", False),
                }
                for r in formatted_results
            ]
        }

        return {
            "response": response,
            "metadata": metadata,
            "next_agent": None
        }

    def _structured_search(
        self,
        entities: Dict,
        parameters: Dict,
        filters: Dict
    ) -> List[Recipe]:
        """
        Fallback structured search using database filters

        Args:
            entities: Extracted entities (ingredients, cuisines, etc.)
            parameters: Search parameters (time, servings, etc.)
            filters: Advanced filters (tags, seasonality, regional)

        Returns:
            List of matching recipes
        """
        query = self.db.query(Recipe).filter(
            Recipe.deletedAt == None,
            Recipe.private == False
        )

        # Filter by ingredients
        if entities.get("ingredients"):
            ingredient_names = [i.lower() for i in entities["ingredients"]]
            query = query.join(RecipeIngredient).join(Ingredient).filter(
                or_(*[Ingredient.name.ilike(f"%{name}%") for name in ingredient_names])
            )

        # Filter by tags
        if filters.get("tags"):
            tag_names = [t.lower() for t in filters["tags"]]
            query = query.join(RecipeTagsTag).join(Tag).filter(
                or_(*[Tag.name.ilike(f"%{tag}%") for tag in tag_names])
            )

        # Filter by seasonality
        if filters.get("seasonality"):
            seasonality_types = [s.lower() for s in filters["seasonality"]]
            query = query.join(RecipeSeasonality).join(Seasonality).filter(
                or_(*[Seasonality.type.ilike(f"%{stype}%") for stype in seasonality_types])
            )

        # Filter by difficulty
        if parameters.get("difficulty"):
            query = query.filter(Recipe.difficulty == parameters["difficulty"])

        # Filter by total time
        max_time = parameters.get("max_time")
        if max_time:
            query = query.filter(
                (Recipe.prepTime + Recipe.cookTime) <= max_time
            )

        # Filter by servings
        if parameters.get("servings"):
            query = query.filter(Recipe.servings == parameters["servings"])

        return query.limit(20).all()

    def _filter_recipes(
        self,
        results: List,
        user_context: Dict[str, Any],
        excluded_ingredients: List[str]
    ) -> List:
        """
        Filter out recipes based on:
        - Private status
        - Deleted status
        - Excluded ingredients (allergies)

        Args:
            results: List of (recipe, similarity) tuples
            user_context: User context data
            excluded_ingredients: List of ingredients to exclude

        Returns:
            Filtered list of results
        """
        filtered = []

        for recipe, similarity in results:
            # Check if recipe should be excluded
            should_exclude = self.user_context.should_exclude_recipe(
                recipe, user_context, excluded_ingredients
            )

            if not should_exclude:
                filtered.append((recipe, similarity))

        return filtered

    def _apply_advanced_filters(
        self,
        results: List,
        filters: Dict,
        user_context: Dict[str, Any]
    ) -> List:
        """
        Apply advanced filters: tags, seasonality, regional preferences

        Args:
            results: List of (recipe, similarity) tuples
            filters: Filters from NLID agent
            user_context: User context data

        Returns:
            Filtered list of results
        """
        if not filters and not user_context.get("preferred_tags"):
            return results

        filtered = []

        for recipe, similarity in results:
            recipe_id = recipe.id
            include_recipe = True

            # Check tag filters
            if filters.get("tags"):
                recipe_tags = self.db.query(RecipeTagsTag).filter(
                    RecipeTagsTag.recipeId == recipe_id
                ).all()

                tag_ids = [rt.tagId for rt in recipe_tags]
                matching_tags = self.db.query(Tag).filter(Tag.id.in_(tag_ids)).all()

                if not any(
                    any(filter_tag.lower() in tag.name.lower() for filter_tag in filters["tags"])
                    for tag in matching_tags
                ):
                    # No matching tags found
                    include_recipe = False

            # Check seasonality filters
            if include_recipe and filters.get("seasonality"):
                recipe_seasonalities = self.db.query(RecipeSeasonality).filter(
                    RecipeSeasonality.recipeId == recipe_id
                ).all()

                seasonality_ids = [rs.seasonalityId for rs in recipe_seasonalities]
                matching_seasonalities = self.db.query(Seasonality).filter(
                    Seasonality.id.in_(seasonality_ids)
                ).all()

                if not any(
                    any(filter_seas.lower() in s.type.lower() for filter_seas in filters["seasonality"])
                    for s in matching_seasonalities
                ):
                    # No matching seasonality found
                    include_recipe = False

            # Boost recipes matching user's preferred tags
            if include_recipe and user_context.get("preferred_tags"):
                recipe_tags = self.db.query(RecipeTagsTag).filter(
                    RecipeTagsTag.recipeId == recipe_id
                ).all()

                tag_ids = [rt.tagId for rt in recipe_tags]
                matching_tags = self.db.query(Tag).filter(Tag.id.in_(tag_ids)).all()

                for pref_tag in user_context["preferred_tags"]:
                    if any(pref_tag.lower() in tag.name.lower() for tag in matching_tags):
                        # Boost similarity score
                        similarity = min(similarity + 0.05, 1.0)
                        break

            if include_recipe:
                filtered.append((recipe, similarity))

        return filtered

    def _apply_parameter_filters(self, results: List, parameters: Dict) -> List:
        """
        Apply additional parameter filters to search results

        Args:
            results: List of (recipe, similarity) tuples
            parameters: Filter parameters

        Returns:
            Filtered list of results
        """
        if not parameters:
            return results

        filtered = []
        for recipe, similarity in results:
            # Time filter
            if parameters.get("max_time"):
                total_time = (recipe.prepTime or 0) + (recipe.cookTime or 0)
                if total_time > parameters["max_time"]:
                    continue

            # Difficulty filter
            if parameters.get("difficulty") and recipe.difficulty != parameters["difficulty"]:
                continue

            # Servings filter
            if parameters.get("servings") and recipe.servings != parameters["servings"]:
                continue

            filtered.append((recipe, similarity))

        return filtered

    def _format_results_with_access(
        self,
        results: List[tuple],
        user_uid: Optional[str]
    ) -> List[Dict[str, Any]]:
        """
        Format results with access level information

        Args:
            results: List of (recipe, similarity, priority_score) tuples
            user_uid: Optional user identifier

        Returns:
            List of formatted recipe dictionaries
        """
        formatted = []

        for recipe, similarity, priority_score in results:
            recipe_id = str(recipe.id)
            access_level = self.user_context.get_recipe_access_level(recipe_id, user_uid)

            formatted_recipe = {
                "id": recipe_id,
                "name": recipe.name,
                "slug": recipe.slug,
                "ingress": recipe.ingress,
                "image": recipe.image,
                "difficulty": recipe.difficulty,
                "prepTime": recipe.prepTime,
                "cookTime": recipe.cookTime,
                "servings": recipe.servings,
                "similarity": round(similarity, 3),
                "priority_score": priority_score,
                "access_level": access_level,
                "is_liked": self.user_context.is_user_liked_recipe(recipe_id, user_uid),
            }

            # Get user context to check if created by user
            if user_uid:
                user_context = self.user_context.get_user_context(user_uid)
                formatted_recipe["is_created_by_user"] = recipe_id in user_context.get("created_recipe_ids", set())

            formatted.append(formatted_recipe)

        return formatted

    def _generate_response(
        self,
        query: str,
        results: List[Dict],
        entities: Dict
    ) -> str:
        """
        Generate a natural language response with recipe suggestions

        Args:
            query: Original user query
            results: List of formatted recipe dictionaries
            entities: Extracted entities

        Returns:
            Formatted response text
        """
        if not results:
            return self._generate_no_results_response(query, entities)

        # Build context for LLM
        recipes_context = []
        for recipe in results:
            access_note = ""
            if recipe["access_level"] == "name_only":
                access_note = " (Premium recipe - upgrade for full details)"

            total_time = (recipe["prepTime"] or 0) + (recipe["cookTime"] or 0)

            context_parts = [
                f"- **{recipe['name']}**{access_note}"
            ]

            if recipe["ingress"]:
                context_parts.append(f"  {recipe['ingress']}")

            context_parts.append(
                f"  Time: {total_time} min | Difficulty: {recipe['difficulty'] or 'N/A'}"
            )

            if recipe.get("is_liked"):
                context_parts.append("  ⭐ You liked this recipe")

            recipes_context.append("\n".join(context_parts))

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""User query: "{query}"

Available recipes:
{chr(10).join(recipes_context)}

Provide a helpful response with recipe suggestions."""}
        ]

        return self._call_openai(messages, temperature=0.8, max_tokens=400)

    def _generate_no_results_response(self, query: str, entities: Dict) -> str:
        """Generate response when no recipes are found"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""User query: "{query}"

No recipes found matching the search criteria.

Provide a helpful response that:
1. Acknowledges that we couldn't find exact matches
2. Asks clarifying questions or suggests alternatives
3. Encourages the user to try different search terms"""}
        ]

        return self._call_openai(messages, temperature=0.8, max_tokens=200)

    def get_recipe_details(self, recipe_id: str, user_uid: Optional[str] = None) -> Dict[str, Any]:
        """
        Get detailed information about a specific recipe

        Args:
            recipe_id: Recipe UUID
            user_uid: Optional user identifier for access check

        Returns:
            Dictionary with recipe details including ingredients and instructions
        """
        recipe = self.db.query(Recipe).filter(Recipe.id == recipe_id).first()
        if not recipe:
            return None

        # Check access level
        access_level = self.user_context.get_recipe_access_level(recipe_id, user_uid)

        if access_level == "name_only":
            # Only return basic info for bundle recipes not purchased
            return {
                "id": str(recipe.id),
                "name": recipe.name,
                "access_level": "name_only",
                "message": "This is a premium recipe. Please purchase the bundle to view full details."
            }

        # Get ingredients
        ingredients = self.db.query(RecipeIngredient).filter(
            RecipeIngredient.recipeId == recipe_id,
            RecipeIngredient.deletedAt == None
        ).order_by(RecipeIngredient.order).all()

        # Get instructions
        instructions = self.db.query(RecipeInstruction).filter(
            RecipeInstruction.recipeId == recipe_id,
            RecipeInstruction.deletedAt == None
        ).order_by(RecipeInstruction.order).all()

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
            "access_level": access_level,
            "is_liked": self.user_context.is_user_liked_recipe(recipe_id, user_uid),
            "ingredients": [
                {
                    "amount": ing.amount,
                    "section": ing.section,
                    "ingredient_id": str(ing.ingredientId) if ing.ingredientId else None,
                }
                for ing in ingredients
            ],
            "instructions": [
                {
                    "order": ins.order,
                    "description": ins.description,
                    "image": ins.image
                }
                for ins in instructions
            ]
        }
