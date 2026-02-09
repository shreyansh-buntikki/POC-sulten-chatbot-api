"""
Schema Understanding Service
Provides database schema information for SQL generation stage
Optimized to inject only relevant tables based on intent
"""
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass

# Import auto-generated schema data
try:
    from schema_profiler_data import SCHEMA_DATA
except ImportError:
    SCHEMA_DATA = {}


@dataclass
class TableSchema:
    """Schema information for a single table"""
    name: str
    columns: List[Dict[str, str]]
    description: str
    relationships: List[Dict[str, str]] = None  # FK relationships


@dataclass
class RelevantSchema:
    """
    Schema subset relevant to a specific query

    Optimized for minimal token usage while providing complete context
    """
    tables: List[TableSchema]
    relationships: List[Dict[str, str]]
    business_rules: List[str]  # Important business constraints


class SchemaUnderstandingService:
    """
    Schema Understanding Service

    Provides schema information for SQL generation stage.
    Only injects tables relevant to the intent to optimize token usage.
    """

    # Intent to table mapping
    INTENT_TABLE_MAP = {
        "recipe_search": [
            "recipe",
            "recipe_ingredient",
            "ingredient",
            "recipe_instruction",
            "recipe_tags_tag",
            "tag",
            "recipe_seasonality",
            "seasonality",
            "bundle_recipe",
            "bundle",
            "user_likes_recipe",
        ],
        "nutritional_info": [
            "recipe",
            "recipe_ingredient",
            "ingredient",
            "ingredient_macros",
            "ingredient_micros",
        ],
        "ingredient_substitution": [
            "ingredient",
            "ingredient_macros",
        ],
        "recommendation": [
            "recipe",
            "recipe_ingredient",
            "ingredient",
            "user_likes_recipe",
            "recipe_tags_tag",
            "tag",
        ],
        "general_chat": [],  # No SQL needed
    }

    # Table descriptions for business context
    TABLE_DESCRIPTIONS = {
        "recipe": "Recipe table with core recipe data (name, difficulty, times, servings, privacy status, user who created it)",
        "recipe_ingredient": "Junction table linking recipes to ingredients with amounts and order",
        "ingredient": "Ingredient reference table with names and language",
        "recipe_instruction": "Cooking steps for recipes, ordered sequentially",
        "recipe_tags_tag": "Junction table linking recipes to tags (categories, dietary restrictions, etc.)",
        "tag": "Tag reference table (vegetarian, quick, healthy, etc.)",
        "recipe_seasonality": "Junction table linking recipes to seasonal occasions (summer, festive, etc.)",
        "seasonality": "Seasonality reference table with types (WEATHER, FESTIVAL, INGREDIENT_AVAILABILITY, etc.)",
        "bundle_recipe": "Junction table linking premium recipes to bundles (paid content)",
        "bundle": "Bundle table for recipe collections - bundle.userUid indicates the user who purchased/owns the bundle",
        "user_likes_recipe": "User-recipe like relationship for personalization",
        "ingredient_macros": "Nutritional macros for ingredients (calories, protein, carbs, fat, fiber)",
        "ingredient_micros": "Nutritional micros for ingredients (vitamins, minerals)",
        "chat_session": "Chat session for conversation tracking",
        "chat_message": "Individual messages in a chat session",
    }

    # Business rules that must be enforced in SQL
    BUSINESS_RULES = [
        "Always exclude recipes where private=true unless user is the creator",
        "Always exclude recipes where deletedAt is not null",
        "For bundle_recipe: check if bundle.userUid matches the current user (user owns/purchased the bundle)",
        "Bundle recipes where isFree=false and user doesn't own the bundle should return only name (name_only access)",
        "Recipe search should exclude allergic ingredients from user context",
        "Join user_likes_recipe to identify user's liked recipes for ranking",
        "Time filters should apply to (prepTime + cookTime) in minutes",
        "Difficulty values are: 'easy', 'medium', 'hard'",
    ]

    def __init__(self):
        self._schema_cache = {}

    def get_relevant_schema(
        self,
        intent: str,
        sql_filters: Dict[str, Any],
        session_context: Dict[str, Any]
    ) -> RelevantSchema:
        """
        Get schema relevant to the specific query

        Args:
            intent: Detected intent type
            sql_filters: SQL filters to be applied (determines which tables are needed)
            session_context: Session context (user-specific data)

        Returns:
            RelevantSchema with optimized table set
        """
        # Start with intent-based tables
        table_names = set(self.INTENT_TABLE_MAP.get(intent, []))

        # Add tables based on filters
        if sql_filters.get("included_ingredients"):
            table_names.update(["recipe", "recipe_ingredient", "ingredient"])

        if sql_filters.get("excluded_ingredients"):
            table_names.update(["recipe", "recipe_ingredient", "ingredient"])

        if sql_filters.get("tags"):
            table_names.update(["recipe", "recipe_tags_tag", "tag"])

        if sql_filters.get("cuisines"):
            table_names.update(["recipe", "recipe_tags_tag", "tag"])

        if sql_filters.get("seasonality"):
            table_names.update(["recipe", "recipe_seasonality", "seasonality"])

        # Add user-specific tables if user is authenticated
        if session_context.get("user_uid"):
            table_names.update(["user_likes_recipe"])
            # Add bundle tables only if we need to check access
            if intent == "recipe_search":
                table_names.update(["bundle_recipe", "bundle"])

        # Build table schemas
        tables = []
        for table_name in table_names:
            if table_name in SCHEMA_DATA:
                tables.append(self._build_table_schema(table_name))

        # Build relationships
        relationships = self._extract_relationships(list(table_names))

        # Filter business rules based on relevance
        business_rules = self._filter_business_rules(intent, sql_filters, session_context)

        return RelevantSchema(
            tables=tables,
            relationships=relationships,
            business_rules=business_rules
        )

    def _build_table_schema(self, table_name: str) -> TableSchema:
        """Build TableSchema from schema data"""
        table_data = SCHEMA_DATA.get(table_name, {})

        # Enrich columns with type information
        columns = []
        for col in table_data.get("columns", []):
            columns.append({
                "name": col["name"],
                "type": col["type"],
                "nullable": col.get("nullable", "unknown")
            })

        return TableSchema(
            name=table_name,
            columns=columns,
            description=self.TABLE_DESCRIPTIONS.get(table_name, ""),
            relationships=[]
        )

    def _extract_relationships(self, table_names: List[str]) -> List[Dict[str, str]]:
        """
        Extract foreign key relationships between relevant tables

        Uses schema_profiler.json as single source of truth for relationships.

        Returns list of: {from, from_column, to, to_column}
        """
        # Import relationships from schema profiler data
        try:
            from apps.fastapi.src.services.schema_profiler_data import get_all_relationships
            all_relationships = get_all_relationships()
        except ImportError:
            # Fallback to hardcoded relationships if profiler not available
            all_relationships = self._get_hardcoded_relationships()

        # Filter to only include relationships between relevant tables
        filtered = []
        table_set = set(table_names)
        for rel in all_relationships:
            if rel["from"] in table_set or rel["to"] in table_set:
                filtered.append(rel)

        return filtered

    def _get_hardcoded_relationships(self) -> List[Dict[str, str]]:
        """Fallback hardcoded relationships (used only if profiler unavailable)"""
        return [
            {"from": "recipe", "from_column": "id", "to": "recipe_ingredient", "to_column": "recipeId"},
            {"from": "recipe", "from_column": "id", "to": "recipe_instruction", "to_column": "recipeId"},
            {"from": "recipe", "from_column": "id", "to": "recipe_tags_tag", "to_column": "recipeId"},
            {"from": "recipe", "from_column": "id", "to": "recipe_seasonality", "to_column": "recipeId"},
            {"from": "recipe_ingredient", "from_column": "ingredientId", "to": "ingredient", "to_column": "id"},
            {"from": "recipe_tags_tag", "from_column": "tagId", "to": "tag", "to_column": "id"},
            {"from": "recipe_seasonality", "from_column": "seasonalityId", "to": "seasonality", "to_column": "id"},
            {"from": "bundle_recipe", "from_column": "recipeId", "to": "recipe", "to_column": "id"},
            {"from": "user_likes_recipe", "from_column": "recipeId", "to": "recipe", "to_column": "id"},
        ]

    def _filter_business_rules(
        self,
        intent: str,
        sql_filters: Dict[str, Any],
        session_context: Dict[str, Any]
    ) -> List[str]:
        """Filter business rules based on query context"""
        rules = []

        # Core rules for recipe search
        if intent == "recipe_search":
            rules.extend([
                "Always exclude recipes where private=true unless user_uid matches recipe.userUid",
                "Always exclude recipes where deletedAt IS NOT NULL",
                "Time filter: (prepTime + cookTime) <= max_time",
                "Difficulty filter: difficulty = 'easy' OR 'medium' OR 'hard'",
            ])

        # Bundle access rules
        if session_context.get("user_uid"):
            rules.extend([
                "Check bundle.userUid to see if user owns/purchased bundles containing the recipe",
                "If recipe is in bundle_recipe with isFree=false and bundle.userUid != user: return name_only access",
            ])
        else:
            rules.append("Anonymous users cannot access paid bundle-only recipes (return name_only)")

        # Allergen exclusion rules
        if sql_filters.get("excluded_ingredients") or session_context.get("excluded_ingredients"):
            rules.extend([
                "Exclude recipes that contain allergen ingredients",
                "Join through recipe_ingredient to check ingredient names",
            ])

        # Personalization rules
        if session_context.get("user_uid"):
            rules.extend([
                "Join user_likes_recipe to identify user's liked recipes",
                "Check if recipe.userUid equals user_uid for user-created recipes",
            ])

        return rules

    def format_schema_for_prompt(self, relevant_schema: RelevantSchema) -> str:
        """
        Format schema as text for LLM prompt

        Optimized for minimal tokens while providing complete context
        """
        sections = []

        # Tables section
        sections.append("## Relevant Tables")
        for table in relevant_schema.tables:
            sections.append(f"\n### {table.name}")
            sections.append(f"Description: {table.description}")
            sections.append("Columns:")
            for col in table.columns:
                nullable = "NULL" if col.get("nullable") == "YES" else "NOT NULL"
                sections.append(f"  - {col['name']}: {col['type']} ({nullable})")

        # Relationships section
        if relevant_schema.relationships:
            sections.append("\n## Relationships")
            for rel in relevant_schema.relationships:
                sections.append(
                    f"- {rel['from']}.{rel['from_column']} → {rel['to']}.{rel['to_column']}"
                )

        # Business rules section
        if relevant_schema.business_rules:
            sections.append("\n## Business Rules (Must Enforce)")
            for i, rule in enumerate(relevant_schema.business_rules, 1):
                sections.append(f"{i}. {rule}")

        return "\n".join(sections)

    def get_schema_prompt(
        self,
        intent: str,
        sql_filters: Dict[str, Any],
        session_context: Dict[str, Any]
    ) -> str:
        """
        Get complete schema prompt for SQL generation

        Convenience method that combines get_relevant_schema and format_schema_for_prompt
        """
        schema = self.get_relevant_schema(intent, sql_filters, session_context)
        return self.format_schema_for_prompt(schema)


# Singleton instance
_schema_service = None


def get_schema_service() -> SchemaUnderstandingService:
    """Get singleton instance of schema service"""
    global _schema_service
    if _schema_service is None:
        _schema_service = SchemaUnderstandingService()
    return _schema_service
