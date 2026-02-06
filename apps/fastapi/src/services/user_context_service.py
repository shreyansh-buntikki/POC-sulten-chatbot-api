"""
User Context Service
Manages user-specific context, preferences, and interactions for personalized recipe recommendations
"""
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from models import (
    User, UserLikesRecipe, UserPurchase, BundleRecipe,
    Recipe, RecipeSeasonality, Seasonality, Tag, RecipeTagsTag
)


class UserContextService:
    """
    Manages user-specific context for recipe recommendations

    Maintains:
    - User's liked recipes
    - User's purchased bundles/recipes
    - User's created recipes
    - User's dietary restrictions and allergies
    - User's regional/country preferences
    """

    def __init__(self, db: Session):
        self.db = db
        self._context_cache = {}  # Simple in-memory cache with TTL

    def get_user_context(self, user_uid: Optional[str]) -> Dict[str, Any]:
        """
        Get complete user context for recommendations

        Args:
            user_uid: Optional user identifier (None for anonymous users)

        Returns:
            Dictionary with user context data
        """
        if not user_uid:
            return self._get_anonymous_context()

        # Check cache first
        if user_uid in self._context_cache:
            cached_data, expiry = self._context_cache[user_uid]
            if datetime.utcnow() < expiry:
                return cached_data

        context = {
            "user_uid": user_uid,
            "is_authenticated": True,
            "liked_recipe_ids": self._get_liked_recipes(user_uid),
            "purchased_recipe_ids": self._get_purchased_recipes(user_uid),
            "created_recipe_ids": self._get_created_recipes(user_uid),
            "allergies": self._get_user_allergies(user_uid),
            "dietary_restrictions": self._get_dietary_restrictions(user_uid),
            "preferred_tags": self._get_preferred_tags(user_uid),
            "preferred_seasonality": self._get_preferred_seasonality(user_uid),
            "country_id": self._get_user_country(user_uid),
        }

        # Cache for 5 minutes
        self._context_cache[user_uid] = (context, datetime.utcnow() + timedelta(minutes=5))

        return context

    def _get_anonymous_context(self) -> Dict[str, Any]:
        """Get context for anonymous users"""
        return {
            "user_uid": None,
            "is_authenticated": False,
            "liked_recipe_ids": set(),
            "purchased_recipe_ids": set(),
            "created_recipe_ids": set(),
            "allergies": [],
            "dietary_restrictions": [],
            "preferred_tags": [],
            "preferred_seasonality": [],
            "country_id": None,
        }

    def _get_liked_recipes(self, user_uid: str) -> Set[str]:
        """Get set of recipe IDs the user has liked"""
        likes = self.db.query(UserLikesRecipe).filter(
            UserLikesRecipe.userUid == user_uid
        ).all()

        return {str(like.recipeId) for like in likes}

    def _get_purchased_recipes(self, user_uid: str) -> Set[str]:
        """Get set of recipe IDs the user has purchased via bundles"""
        # Get user's bundle purchases
        purchases = self.db.query(UserPurchase).filter(
            and_(
                UserPurchase.userUid == user_uid,
                UserPurchase.bundleId.isnot(None)
            )
        ).all()

        bundle_ids = [p.bundleId for p in purchases]

        if not bundle_ids:
            return set()

        # Get all recipes in purchased bundles
        bundle_recipes = self.db.query(BundleRecipe).filter(
            and_(
                BundleRecipe.bundleId.in_(bundle_ids),
                BundleRecipe.deletedAt.is_(None)
            )
        ).all()

        return {str(br.recipeId) for br in bundle_recipes}

    def _get_created_recipes(self, user_uid: str) -> Set[str]:
        """Get set of recipe IDs created by the user"""
        recipes = self.db.query(Recipe).filter(
            and_(
                Recipe.userUid == user_uid,
                Recipe.deletedAt.is_(None)
            )
        ).all()

        return {str(r.id) for r in recipes}

    def _get_user_allergies(self, user_uid: str) -> List[str]:
        """
        Get user's allergen ingredients

        Note: This would typically come from a user profile or preferences table.
        For POC, returning empty list. Implement based on your user schema.
        """
        # TODO: Implement based on your user profile schema
        # This could query a user_allergies or user_dietary_preferences table
        return []

    def _get_dietary_restrictions(self, user_uid: str) -> List[str]:
        """
        Get user's dietary restrictions (vegan, vegetarian, gluten-free, etc.)

        Note: This would typically come from user profile or tags.
        For POC, returning empty list. Implement based on your user schema.
        """
        # TODO: Implement based on your user profile schema
        # This could query a user_dietary_preferences or user_tags table
        return []

    def _get_preferred_tags(self, user_uid: str) -> List[str]:
        """
        Get user's preferred recipe tags based on their interaction history
        """
        # Get liked recipes
        liked_recipe_ids = self._get_liked_recipes(user_uid)

        if not liked_recipe_ids:
            return []

        # Get tags for liked recipes and count occurrences
        tag_counts = {}

        recipe_tags = self.db.query(RecipeTagsTag).filter(
            RecipeTagsTag.recipeId.in_(liked_recipe_ids)
        ).all()

        for rt in recipe_tags:
            tag = self.db.query(Tag).filter(Tag.id == rt.tagId).first()
            if tag:
                tag_counts[tag.name] = tag_counts.get(tag.name, 0) + 1

        # Return top 5 most common tags
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        return [tag[0] for tag in sorted_tags[:5]]

    def _get_preferred_seasonality(self, user_uid: str) -> List[str]:
        """
        Get user's preferred seasonality based on liked recipes
        """
        liked_recipe_ids = self._get_liked_recipes(user_uid)

        if not liked_recipe_ids:
            return []

        # Get seasonality for liked recipes
        seasonality_counts = {}

        recipe_seasonalities = self.db.query(RecipeSeasonality).filter(
            RecipeSeasonality.recipeId.in_(liked_recipe_ids)
        ).all()

        for rs in recipe_seasonalities:
            seasonality = self.db.query(Seasonality).filter(
                Seasonality.id == rs.seasonalityId
            ).first()

            if seasonality:
                seasonality_counts[seasonality.type] = seasonality_counts.get(seasonality.type, 0) + 1

        # Return top 3 most common seasonality types
        sorted_seasonalities = sorted(seasonality_counts.items(), key=lambda x: x[1], reverse=True)
        return [s[0] for s in sorted_seasonalities[:3]]

    def _get_user_country(self, user_uid: str) -> Optional[str]:
        """
        Get user's country for regional preferences

        Note: Implement based on your user profile schema
        """
        # TODO: Implement based on your user profile schema
        # This would typically come from user.profile.countryId
        return None

    def get_recipe_access_level(
        self,
        recipe_id: str,
        user_uid: Optional[str]
    ) -> str:
        """
        Determine the user's access level for a specific recipe

        Args:
            recipe_id: Recipe UUID
            user_uid: Optional user identifier

        Returns:
            Access level: 'full', 'name_only', or 'none'
        """
        if not user_uid:
            # Anonymous users can see public non-bundle recipes
            return 'full'

        context = self.get_user_context(user_uid)

        # Check if user created this recipe
        if str(recipe_id) in context["created_recipe_ids"]:
            return 'full'

        # Check if recipe is in a purchased bundle
        if str(recipe_id) in context["purchased_recipe_ids"]:
            return 'full'

        # Check if recipe is part of any bundle
        bundle_recipe = self.db.query(BundleRecipe).filter(
            and_(
                BundleRecipe.recipeId == recipe_id,
                BundleRecipe.deletedAt.is_(None)
            )
        ).first()

        if bundle_recipe:
            # Recipe is in a bundle but user hasn't purchased it
            return 'name_only'

        # Public recipe - full access
        return 'full'

    def is_user_liked_recipe(self, recipe_id: str, user_uid: Optional[str]) -> bool:
        """Check if user has liked a specific recipe"""
        if not user_uid:
            return False

        context = self.get_user_context(user_uid)
        return str(recipe_id) in context["liked_recipe_ids"]

    def should_exclude_recipe(
        self,
        recipe: Recipe,
        user_context: Dict[str, Any],
        excluded_ingredients: List[str] = None
    ) -> bool:
        """
        Determine if a recipe should be excluded from results

        Args:
            recipe: Recipe object
            user_context: User context from get_user_context()
            excluded_ingredients: List of ingredient names to exclude (from user query)

        Returns:
            True if recipe should be excluded
        """
        # Exclude private recipes
        if recipe.private:
            return True

        # Exclude deleted recipes
        if recipe.deletedAt:
            return True

        # Check for excluded ingredients (allergies from user query)
        if excluded_ingredients:
            recipe_ingredients = self.db.query(RecipeIngredient).filter(
                and_(
                    RecipeIngredient.recipeId == recipe.id,
                    RecipeIngredient.deletedAt.is_(None)
                )
            ).all()

            for ri in recipe_ingredients:
                if ri.ingredientId:
                    ingredient = self.db.query(Ingredient).filter(
                        Ingredient.id == ri.ingredientId
                    ).first()

                    if ingredient and any(
                        excluded.lower() in ingredient.name.lower()
                        for excluded in excluded_ingredients
                    ):
                        return True

        return False

    def prioritize_recipes(
        self,
        recipes: List[tuple],
        user_uid: Optional[str]
    ) -> List[tuple]:
        """
        Sort recipes by priority:
        1. User has liked the recipe
        2. Recipe created by user
        3. Other recipes

        Args:
            recipes: List of (recipe, similarity_score) tuples
            user_uid: Optional user identifier

        Returns:
            Prioritized list of (recipe, similarity_score, priority_score) tuples
        """
        if not user_uid:
            # No user context, return as-is
            return [(r, s, 0) for r, s in recipes]

        context = self.get_user_context(user_uid)

        prioritized = []
        for recipe, similarity in recipes:
            recipe_id = str(recipe.id)

            # Calculate priority score
            priority_score = 0

            if recipe_id in context["liked_recipe_ids"]:
                priority_score = 100  # Highest priority
            elif recipe_id in context["created_recipe_ids"]:
                priority_score = 50   # Medium priority

            prioritized.append((recipe, similarity, priority_score))

        # Sort by: priority_score (desc), then similarity (desc)
        prioritized.sort(key=lambda x: (x[2], x[1]), reverse=True)

        return prioritized

    def invalidate_cache(self, user_uid: Optional[str] = None):
        """
        Invalidate cached user context

        Args:
            user_uid: Specific user to invalidate, or None to invalidate all
        """
        if user_uid:
            self._context_cache.pop(user_uid, None)
        else:
            self._context_cache.clear()

    def add_user_like(self, user_uid: str, recipe_id: str):
        """Add a like and update context"""
        existing = self.db.query(UserLikesRecipe).filter(
            and_(
                UserLikesRecipe.userUid == user_uid,
                UserLikesRecipe.recipeId == recipe_id
            )
        ).first()

        if not existing:
            like = UserLikesRecipe(userUid=user_uid, recipeId=recipe_id)
            self.db.add(like)
            self.db.commit()

        self.invalidate_cache(user_uid)

    def remove_user_like(self, user_uid: str, recipe_id: str):
        """Remove a like and update context"""
        self.db.query(UserLikesRecipe).filter(
            and_(
                UserLikesRecipe.userUid == user_uid,
                UserLikesRecipe.recipeId == recipe_id
            )
        ).delete()

        self.db.commit()
        self.invalidate_cache(user_uid)
