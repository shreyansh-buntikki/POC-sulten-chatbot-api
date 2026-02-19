"""
Database Schema Profiler
Extracts complete schema information from PostgreSQL database for AI agent consumption.
Includes intelligent column descriptions based on naming conventions and context.
"""
import os
import json
import re
from typing import Dict, List, Any, Optional
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import reflection
from dotenv import load_dotenv
from libs.utils.logger import setup_logger

# Load environment variables
load_dotenv()

logger = setup_logger("schema_profiler", True, False, False, False)

# Database configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')

# Create database URL
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Intelligent column description mappings
COLUMN_DESCRIPTIONS = {
    # Common identifiers
    'id': 'Unique identifier (primary key)',
    'uid': 'Unique user identifier',
    'userUid': 'Reference to user (foreign key to user.uid)',
    'languageId': 'Reference to language (foreign key to language.id)',

    # User-related
    'username': "User's chosen username for login/display",
    'name': "Full display name",
    'bio': "User biography or profile description",
    'image': "URL/path to user's profile image",
    'dob': "Date of birth",
    'gender': "User's gender (male, female, other)",
    'role': "User role/permission level (admin, vip, team_sulten, verified, community)",
    'tag': "User tag or label for categorization",
    'termsAccepted': "Whether user has accepted terms of service",
    'messagingTokens': "Push notification tokens for sending messages",
    'lastSeen': "Last timestamp user was active",

    # Timestamps
    'createdAt': "Timestamp when record was created",
    'updatedAt': "Timestamp when record was last updated",
    'deletedAt': "Soft delete timestamp (null if not deleted)",
    'publishedAt': "Timestamp when content was published",
    'viewedAt': "Timestamp when item was viewed",
    'purchasedAt': "Timestamp when purchase was made",
    'purchasedAtMillis': "Purchase timestamp in milliseconds since epoch",
    'lastUpdatedAt': "Last update timestamp for tracking changes",

    # Recipe-related
    'recipeId': "Reference to recipe (foreign key to recipe.id)",
    'ingredientId': "Reference to ingredient (foreign key to ingredient.id)",
    'unitId': "Reference to measuring unit (foreign key to measuring_unit.id)",
    'section': "Recipe section or category the ingredient belongs to",
    'amount': "Quantity or amount of ingredient",
    'order': "Sorting order for display",
    'description': "Text description or instruction",
    'slug': "URL-friendly identifier for SEO",
    'ingress': "Short summary or introduction text",
    'status': "Current status of the item (draft, published, etc.)",
    'difficulty': "Recipe difficulty level (easy, medium, hard)",
    'servings': "Number of servings the recipe yields",
    'prepTime': "Preparation time in minutes",
    'cookTime': "Cooking time in minutes",
    'private': "Whether content is private (not public)",

    # Pricing-related
    'price': "Price value",
    'pricePerUnit': "Price per single unit of measurement",
    'quantity': "Amount or count",
    'currencyId': "Reference to currency (foreign key to currency.id)",
    'countryId': "Reference to country (foreign key to country.id)",
    'measuringUnitId': "Reference to measuring unit (foreign key to measuring_unit.id)",
    'bundleId': "Reference to bundle (foreign key to bundle.id)",

    # Content-related
    'text': "Main text content",
    'caption': "Caption or title text",
    'duration': "Duration in seconds or minutes",
    'data': "JSON data storage",
    'messageId': "Unique message identifier",

    # Comment/Like system
    'entityId': "ID of the entity being commented/liked",
    'entityType': "Type of entity (recipe, video, etc.)",
    'parentCommentId': "ID of parent comment for nested replies",
    'read': "Whether notification has been read",

    # Translation-related
    'longName': "Full/long form name of the item",
    'menuTypeId': "Reference to menu type (foreign key to menu_type.id)",
    'menuId': "Reference to menu (foreign key to menu.id)",
    'measuringUnitId': "Reference to measuring unit (foreign key to measuring_unit.id)",

    # Preference/Type/Tag relationships
    'recipePreferenceId': "Reference to recipe preference (foreign key to recipe_preference.id)",
    'recipeTypeId': "Reference to recipe type (foreign key to recipe_type.id)",
    'tagId': "Reference to tag (foreign key to tag.id)",
    'seasonalityId': "Reference to seasonality (foreign key to seasonality.id)",
    'videoId': "Reference to video (foreign key to video.id)",

    # Bundle/Pricing
    'pricingDetailId': "Reference to pricing details (foreign key to bundle_price.id)",
    'isActive': "Whether the item is currently active",
    'iosName': "Display name for iOS platform",
    'androidName': "Display name for Android platform",

    # Subscription/Purchase
    'orderId': "Order identifier from payment provider",
    'provider': "Payment provider name (e.g., apple, google, stripe)",
    'recipt': "Purchase receipt data (JSON)",
    'expiresAt': "Subscription expiration timestamp (milliseconds)",
    'transactionId': "Unique transaction identifier",
    'purchaseType': "Type of purchase (iap=in-app purchase, web)",
    'platform': "Purchase platform (ios, android, web)",

    # Video
    'video_status_enum': "Video processing status (uploading, processing, ready, failed)",

    # Priority
    'is_priority': "Whether item is marked as priority",

    # Recipe-specific
    'search_vector': "Full-text search vector for fast searching",
    'recipeInstructionId': "Reference to recipe instruction (foreign key to recipe_instruction.id)",

    # Seasonality
    'isActive': "Whether the seasonality tag is currently active",
    'type': "Type or category of seasonality",

    # Cache
    'identifier': "Cache key identifier",
    'time': "Timestamp of cache entry",
    'duration': "Cache duration in milliseconds",
    'query': "SQL query that was cached",
    'result': "Cached query result",

    # Migration
    'timestamp': "Migration timestamp",
    'name': "Migration name/identifier",
}


TABLE_DESCRIPTIONS = {
    'user': 'User account information including profile details, role, and preferences',
    'recipe': 'Recipe details including name, ingredients, instructions, and metadata',
    'ingredient': 'Ingredient base information with name and language',
    'ingredient_macros': 'Macronutrient information for ingredients (calories, protein, carbs, fats, etc.)',
    'ingredient_micros': 'Micronutrient information for ingredients (vitamins and minerals)',
    'ingredient_pricing': 'Pricing information for ingredients across different countries and currencies',
    'recipe_ingredient': 'Junction table linking recipes to their ingredients with amounts',
    'recipe_instruction': 'Step-by-step cooking instructions for recipes',
    'country': 'Country reference table with codes and names',
    'currency': 'Currency reference table with codes and symbols',
    'language': 'Language reference table with ISO codes',
    'measuring_unit': 'Standard units of measurement (cups, grams, etc.)',
    'measuring_unit_translation': 'Translated names for measuring units',
    'bundle': 'Recipe bundles/packages available for purchase',
    'bundle_price': 'Pricing details for bundles across platforms',
    'bundle_recipe': 'Recipes included in each bundle',
    'collection': 'User-created collections to organize recipes',
    'collection_recipes_recipe': 'Junction table linking collections to recipes',
    'menu': 'Meal plans or menus created by users',
    'menu_type': 'Categories or types of menus',
    'menu_type_translation': 'Translated names for menu types',
    'menu_recipe': 'Recipes included in menus',
    'menu_view': 'Tracking user views of menus',
    'comment': 'User comments on recipes and other content',
    'like': 'Generic like tracking for any entity type',
    'tag': 'Tags for categorizing content',
    'recipe_tags_tag': 'Junction table linking recipes to tags',
    'video': 'Video content related to recipes',
    'video_tags_tag': 'Junction table linking videos to tags',
    'video_recipes_recipe': 'Junction table linking videos to recipes',
    'recipe_type': 'Categories or types of recipes',
    'recipe_type_translation': 'Translated names for recipe types',
    'recipe_recipe_types_recipe_type': 'Junction table linking recipes to types',
    'recipe_preference': 'Dietary preferences (vegan, gluten-free, etc.)',
    'recipe_preference_translation': 'Translated names for recipe preferences',
    'recipe_recipe_preferences_recipe_preference': 'Junction table linking recipes to preferences',
    'recipe_view': 'Tracking user views of recipes',
    'recipe_liked_by_user': 'Junction table linking users to liked recipes',
    'recipe_seasonality': 'Seasonal availability information for recipes',
    'seasonality': 'Seasonality categories (spring, summer, fall, winter)',
    'seasonality_translation': 'Translated names for seasonality categories',
    'user_purchase': 'User purchase history for subscriptions and bundles',
    'user_stored_ingredient': 'Pantry ingredients stored by users',
    'user_likes_recipe': 'User liked recipes tracking',
    'subscription': 'User subscription status and details',
    'push_token': 'Push notification tokens for user devices',
    'notification': 'User notifications with read status',
    'recipe_instruction_selected_ingredients_ingredient': 'Ingredients highlighted in recipe instructions',
    'query-result-cache': 'Cache for frequently executed database queries',
    'migrations': 'Database migration tracking',
}


class SchemaProfiler:
    """Extracts and formats database schema information"""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.inspector = inspect(self.engine)

    def get_column_type_info(self, column_info: Dict[str, Any]) -> Dict[str, Any]:
        """Extract detailed column type information"""
        col_type = {
            'name': column_info['name'],
            'type': str(column_info['type']),
            'nullable': column_info.get('nullable', True),
            'default': str(column_info.get('default', '')) if column_info.get('default') else None,
            'autoincrement': column_info.get('autoincrement', False),
            'is_primary_key': False,  # Will be updated later
        }
        return col_type

    def generate_column_description(self, table_name: str, column_name: str, column_type: str) -> str:
        """Generate intelligent description for a column based on name and context"""
        # Direct lookup
        if column_name in COLUMN_DESCRIPTIONS:
            return COLUMN_DESCRIPTIONS[column_name]

        # Pattern-based descriptions
        col_lower = column_name.lower()

        # Foreign key patterns
        if col_lower.endswith('id') and col_lower != 'id':
            ref_entity = col_lower[:-2]
            return f"Reference to {ref_entity} (foreign key)"

        # Boolean patterns
        if 'is_' in col_lower or 'has_' in col_lower:
            return f"Boolean flag indicating {col_lower.replace('_', ' ')}"

        # Timestamp patterns
        if 'at' in col_lower or 'date' in col_lower or 'time' in col_lower:
            return f"Timestamp for {col_lower.replace('_', ' ')}"

        # Translation tables
        if table_name.endswith('_translation') and column_name in ['name', 'description']:
            lang = table_name.replace('_translation', '').replace('_', ' ')
            return f"Translated {column_name} for {lang}"

        # Junction tables
        if column_name in [f'{t}Id' for t in ['recipe', 'ingredient', 'tag', 'video', 'menu', 'collection', 'bundle']]:
            return f"Reference to {column_name.replace('Id', '').replace('Id', '')} (foreign key)"

        # Name fields
        if column_name == 'name':
            return f"Display name or title of the {table_name.rstrip('s')}"

        # Fallback: convert camelCase/pascalCase to readable text
        readable = re.sub(r'([A-Z])', r' \1', column_name).strip().lower()
        return readable if readable else column_name

    def get_enum_values(self, enum_name: str) -> List[str]:
        """Get enum values from PostgreSQL"""
        query = f"""
        SELECT e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = '{enum_name}'
        ORDER BY e.enumsortorder;
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query)
                return [row[0] for row in result]
        except Exception:
            return []

    def get_table_comment(self, table_name: str) -> str:
        """Get table comment from PostgreSQL"""
        query = f"""
        SELECT obj_description('{table_name}'::regclass, 'pg_class') as comment;
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query)
                row = result.fetchone()
                if row and row[0]:
                    return row[0]
        except Exception:
            pass

        # Use predefined description
        return TABLE_DESCRIPTIONS.get(table_name, f"Table: {table_name}")

    def get_column_comment(self, table_name: str, column_name: str, column_type: str) -> str:
        """Get column comment from PostgreSQL, or generate intelligent description"""
        query = f"""
        SELECT col_description('{table_name}'::regclass, ordinal_position) as comment
        FROM information_schema.columns
        WHERE table_name = '{table_name}' AND column_name = '{column_name}';
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query)
                row = result.fetchone()
                if row and row[0]:
                    return row[0]
        except Exception:
            pass

        # Generate intelligent description
        return self.generate_column_description(table_name, column_name, column_type)

    def get_check_constraints(self, table_name: str) -> List[Dict[str, Any]]:
        """Get check constraints for a table"""
        try:
            constraints = self.inspector.get_check_constraints(table_name)
            return [{'name': c['name'], 'sqltext': c.get('sqltext', '')} for c in constraints]
        except Exception:
            return []

    def get_foreign_keys(self, table_name: str) -> List[Dict[str, Any]]:
        """Get foreign key relationships for a table"""
        try:
            fks = self.inspector.get_foreign_keys(table_name)
            result = []
            for fk in fks:
                result.append({
                    'columns': fk['constrained_columns'],
                    'referred_table': fk['referred_table'],
                    'referred_columns': fk['referred_columns'],
                    'name': fk.get('name', ''),
                })
            return result
        except Exception:
            return []

    def get_indexes(self, table_name: str) -> List[Dict[str, Any]]:
        """Get indexes for a table"""
        try:
            indexes = self.inspector.get_indexes(table_name)
            result = []
            for idx in indexes:
                result.append({
                    'name': idx['name'],
                    'columns': idx['column_names'],
                    'unique': idx.get('unique', False),
                })
            return result
        except Exception:
            return []

    def get_primary_keys(self, table_name: str) -> List[str]:
        """Get primary key columns for a table"""
        try:
            pk = self.inspector.get_pk_constraint(table_name)
            return pk.get('constrained_columns', [])
        except Exception:
            return []

    def get_all_tables(self) -> List[str]:
        """Get all table names in the database"""
        return self.inspector.get_table_names()

    def get_all_enums(self) -> List[str]:
        """Get all custom enum types in the database"""
        query = """
        SELECT typname
        FROM pg_type
        WHERE typtype = 'e'
        ORDER BY typname;
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query)
                return [row[0] for row in result]
        except Exception:
            return []

    def profile_table(self, table_name: str) -> Dict[str, Any]:
        """Profile a single table with all its details"""
        columns = self.inspector.get_columns(table_name)
        primary_keys = self.get_primary_keys(table_name)
        foreign_keys = self.get_foreign_keys(table_name)
        indexes = self.get_indexes(table_name)
        check_constraints = self.get_check_constraints(table_name)

        table_comment = self.get_table_comment(table_name)

        column_details = []
        for col in columns:
            col_info = self.get_column_type_info(col)
            col_info['is_primary_key'] = col['name'] in primary_keys
            col_info['description'] = self.get_column_comment(table_name, col['name'], str(col['type']))

            # Check if this column is a foreign key
            fk_info = None
            for fk in foreign_keys:
                if col['name'] in fk['columns']:
                    fk_info = {
                        'references_table': fk['referred_table'],
                        'references_column': fk['referred_columns'][fk['columns'].index(col['name'])],
                    }
                    break
            col_info['foreign_key'] = fk_info

            column_details.append(col_info)

        # Build column descriptions for AI
        column_descriptions = []
        for col in column_details:
            desc = f"- {col['name']}: {col['description']}"
            desc += f" ({col['type']})"
            if col['is_primary_key']:
                desc += " [PRIMARY KEY]"
            if not col['nullable']:
                desc += " [NOT NULL]"
            if col['default']:
                desc += f" [DEFAULT: {col['default']}]"
            if col['foreign_key']:
                desc += f" -> {col['foreign_key']['references_table']}.{col['foreign_key']['references_column']}"
            column_descriptions.append(desc)

        return {
            'table_name': table_name,
            'description': table_comment,
            'columns': column_details,
            'primary_keys': primary_keys,
            'foreign_keys': foreign_keys,
            'indexes': indexes,
            'check_constraints': check_constraints,
            'column_descriptions': column_descriptions,
        }

    def profile_database(self, schema: str = 'public') -> Dict[str, Any]:
        """Profile the entire database schema"""
        logger.info(f"Connecting to database: {DB_NAME}")
        logger.info(f"Host: {DB_HOST}:{DB_PORT}")

        # Get all custom enum types
        enum_types = {}
        for enum_name in self.get_all_enums():
            enum_types[enum_name] = self.get_enum_values(enum_name)

        # Profile all tables
        tables = {}
        table_names = self.get_all_tables()

        logger.info(f"Found {len(table_names)} tables")

        for table_name in table_names:
            logger.info(f"Profiling table: {table_name}")
            tables[table_name] = self.profile_table(table_name)

        # Build relationships summary
        relationships = []
        for table_name, table_info in tables.items():
            for fk in table_info['foreign_keys']:
                relationships.append({
                    'from_table': table_name,
                    'from_columns': fk['columns'],
                    'to_table': fk['referred_table'],
                    'to_columns': fk['referred_columns'],
                })

        return {
            'database_name': DB_NAME,
            'schema': schema,
            'enum_types': enum_types,
            'tables': tables,
            'relationships': relationships,
        }

    def generate_ai_prompt(self, schema: Dict[str, Any]) -> str:
        """Generate a formatted prompt for AI agents"""
        lines = [
            "# Database Schema for SQL Query Generation",
            f"## Database: {schema['database_name']} (Schema: {schema['schema']})",
            "",
            "This schema describes a recipe and meal management platform with users, recipes, ingredients,",
            "bundles (collections of recipes for purchase), menus (meal plans), and various supporting tables.",
            "",
        ]

        # Add enum types
        if schema['enum_types']:
            lines.append("## Custom Enum Types:")
            for enum_name, values in schema['enum_types'].items():
                lines.append(f"- `{enum_name}`: {', '.join(repr(v) for v in values)}")
            lines.append("")

        # Add tables
        lines.append("## Tables:")
        for table_name, table_info in schema['tables'].items():
            lines.append(f"\n### `{table_name}`")
            lines.append(f"> {table_info['description']}")
            lines.append("")
            lines.append("| Column | Type | Description |")
            lines.append("|--------|------|-------------|")
            for col in table_info['columns']:
                pk = " **PK**" if col['is_primary_key'] else ""
                nullable = "" if col['nullable'] else " **NOT NULL**"
                fk_ref = ""
                if col['foreign_key']:
                    fk_ref = f" → `{col['foreign_key']['references_table']}.{col['foreign_key']['references_column']}`"

                table_row = f"| `{col['name']}`{pk} | `{col['type']}`{nullable} | {col['description']}{fk_ref} |"
                lines.append(table_row)
            lines.append("")

        # Add relationships
        if schema['relationships']:
            lines.append("## Relationships:")
            for rel in schema['relationships']:
                lines.append(
                    f"- `{rel['from_table']}`.`{', '.join(rel['from_columns'])}` "
                    f"→ `{rel['to_table']}`.`{', '.join(rel['to_columns'])}`"
                )

        return "\n".join(lines)


def main():
    """Main function to generate schema profiler"""
    profiler = SchemaProfiler(DATABASE_URL)

    # Profile the database
    schema = profiler.profile_database()

    # Generate AI-friendly prompt
    ai_prompt = profiler.generate_ai_prompt(schema)

    # Save to files
    with open('schema_profiler.json', 'w') as f:
        json.dump(schema, f, indent=2, default=str)

    with open('schema_profiler.md', 'w') as f:
        f.write(ai_prompt)

    # Create Python module for importing
    with open('schema_profiler_data.py', 'w') as f:
        f.write("# Database Schema Profiler Data\n")
        f.write("# Auto-generated from schema_profiler.py\n\n")
        f.write("SCHEMA_DATA = ")
        f.write(json.dumps(schema, indent=2, default=str))

    logger.info("\n" + "="*60)
    logger.info("Schema profiler generated successfully!")
    logger.info("="*60)
    logger.info(f"Tables profiled: {len(schema['tables'])}")
    logger.info(f"Relationships found: {len(schema['relationships'])}")
    logger.info(f"Enum types: {len(schema['enum_types'])}")
    logger.info("\nOutput files:")
    logger.info("  - schema_profiler.json  (JSON format)")
    logger.info("  - schema_profiler.md    (Markdown for AI prompts)")
    logger.info("  - schema_profiler_data.py (Python module)")

    # Print summary
    logger.info("\n" + "="*60)
    logger.info("TABLES SUMMARY")
    logger.info("="*60)
    for table_name, table_info in schema['tables'].items():
        col_count = len(table_info['columns'])
        fk_count = len(table_info['foreign_keys'])
        logger.info(f"{table_name}: {col_count} columns, {fk_count} foreign keys")


if __name__ == "__main__":
    main()
