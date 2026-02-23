"""
SQL Generation and Validation Service
Generates SQL queries based on natural language and schema
Validates generated SQL before execution
"""
import os
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
from agents import Agent, Runner

from apps.fastapi import logger
from apps.fastapi.src.services.schema_understanding import get_schema_service, RelevantSchema

load_dotenv()

# Model configuration from environment
SQL_GENERATOR_MODEL = os.getenv('SQL_GENERATOR_MODEL')


@dataclass
class SQLGenerationResult:
    """Result of SQL generation"""
    sql: str
    explanation: str
    params: Dict[str, Any]
    estimated_rows: int
    is_safe: bool


@dataclass
class ValidationResult:
    """Result of SQL validation"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    sanitized_sql: Optional[str] = None


class SQLValidator:
    """
    SQL Validation Stage

    Validates generated SQL for:
    - Syntax correctness (basic check)
    - Safety (read-only, no DML/DML)
    - Table references
    - Maximum row limits
    """

    # Forbidden SQL keywords (DML, DDL, DCL)
    FORBIDDEN_KEYWORDS = [
        "DROP", "DELETE", "TRUNCATE", "INSERT", "UPDATE",
        "ALTER", "CREATE", "GRANT", "REVOKE",
        "COMMIT", "ROLLBACK", "CALL", "EXEC"
    ]

    # Allowed SQL patterns (read-only queries)
    ALLOWED_PATTERNS = [
        r"^\s*SELECT",  # Must start with SELECT
        r"WITH\s+\w+\s+AS\s*\(",  # CTEs allowed
    ]

    MAX_ROW_LIMIT = 100  # Maximum rows per query

    def __init__(self, allowed_tables: Optional[set] = None):
        """
        Initialize validator

        Args:
            allowed_tables: Set of allowed table names (None = all tables allowed)
        """
        self.allowed_tables = allowed_tables

    def validate(self, sql: str, schema: RelevantSchema) -> ValidationResult:
        """
        Validate SQL query

        Args:
            sql: SQL query to validate
            schema: Relevant schema for table reference validation

        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []
        sanitized_sql = sql.strip()

        # Check 1: Forbidden keywords
        upper_sql = sanitized_sql.upper()
        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", upper_sql):
                errors.append(f"Forbidden keyword detected: {keyword}")

        # Check 2: Must start with SELECT (or WITH for CTE)
        if not re.match(r"^\s*(SELECT|WITH)", upper_sql, re.IGNORECASE):
            errors.append("Query must start with SELECT or WITH (CTE)")

        # Check 3: Table references
        table_names = {table.name for table in schema.tables}
        referenced_tables = self._extract_table_names(sanitized_sql)

        for table in referenced_tables:
            if table not in table_names:
                warnings.append(f"Referenced table '{table}' not in provided schema")

        # Check 4: LIMIT clause
        limit_match = re.search(r"LIMIT\s+(\d+)", upper_sql)
        if limit_match:
            limit = int(limit_match.group(1))
            if limit > self.MAX_ROW_LIMIT:
                warnings.append(f"LIMIT {limit} exceeds maximum of {self.MAX_ROW_LIMIT}")
                sanitized_sql = re.sub(
                    rf"LIMIT\s+{limit}",
                    f"LIMIT {self.MAX_ROW_LIMIT}",
                    sanitized_sql,
                    flags=re.IGNORECASE
                )
        else:
            # Add LIMIT if not present
            sanitized_sql += f" LIMIT {self.MAX_ROW_LIMIT}"

        # Check 5: Multiple statements (prevent injection)
        if ";" in sanitized_sql.rstrip(";"):
            errors.append("Multiple statements detected (semicolon injection risk)")

        # Check 6: Comment injection
        if "--" in sanitized_sql or "/*" in sanitized_sql:
            errors.append("SQL comments detected (potential injection)")

        # Check 7: Single-quoted identifiers (syntax error)
        # Pattern: FROM 'tablename' or JOIN 'tablename' causes syntax error
        # Single quotes are for string literals, not table identifiers
        single_quoted_from = re.search(r"\bFROM\s+'[^']+'(?:\s+[a-zA-Z_][a-zA-Z0-9_]*)?(?!\s+AS)", sanitized_sql, re.IGNORECASE)
        if single_quoted_from:
            errors.append("Single-quoted table name after FROM (use double quotes or no quotes for lowercase tables)")

        single_quoted_join = re.search(r"\b(?:LEFT|RIGHT|INNER|FULL|CROSS)\s+JOIN\s+'[^']+'(?:\s+[a-zA-Z_][a-zA-Z0-9_]*)?", sanitized_sql, re.IGNORECASE)
        if single_quoted_join:
            errors.append("Single-quoted table name after JOIN (use double quotes or no quotes for lowercase tables)")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            sanitized_sql=sanitized_sql if len(errors) == 0 else None
        )

    def _extract_table_names(self, sql: str) -> set:
        """Extract table names from SQL query"""
        tables = set()

        # FROM clauses
        from_matches = re.finditer(r"\bFROM\s+([\w.]+)", sql, re.IGNORECASE)
        for match in from_matches:
            tables.add(match.group(1).split(".")[-1])  # Handle schema.table

        # JOIN clauses
        join_matches = re.finditer(r"\b(?:JOIN|INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN)\s+([\w.]+)", sql, re.IGNORECASE)
        for match in join_matches:
            tables.add(match.group(1).split(".")[-1])

        return tables


class SQLGenerator:
    """
    SQL Generation Stage

    Generates SQL queries based on:
    - Natural language query intent
    - SQL filters
    - Database schema
    - Business rules
    """

    def __init__(self, openai_client):
        """
        Initialize SQL generator

        Args:
            openai_client: OpenAI client for LLM-based generation
        """
        self.client = openai_client
        self.validator = SQLValidator()
        self.schema_service = get_schema_service()

    async def generate_sql(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        sql_filters: Dict[str, Any],
        session_context: Dict[str, Any],
        candidate_ids: Optional[List[str]] = None
    ) -> SQLGenerationResult:
        """
        Generate SQL query based on intent and filters

        Args:
            query: Original user query
            nlid_result: NLID agent output
            sql_filters: Compiled SQL filters
            session_context: Session context
            candidate_ids: Optional list of recipe IDs from embedding search (for hybrid)

        Returns:
            SQLGenerationResult with SQL query and metadata
        """
        intent = nlid_result.get("intent", "recipe_search")

        # Get relevant schema
        relevant_schema = self.schema_service.get_relevant_schema(
            intent, sql_filters, session_context
        )

        # Build SQL generation prompt
        prompt = self._build_generation_prompt(
            query, nlid_result, sql_filters, session_context, candidate_ids, relevant_schema
        )


        # DEBUG: Log the sql_filters to verify ingredients are excluded
        logger.info(f"[SQL GENERATOR] sql_filters keys: {list(sql_filters.keys())}")
        logger.info(f"[SQL GENERATOR] included_ingredients in filters: {'included_ingredients' in sql_filters}")
        if 'included_ingredients' in sql_filters:
            logger.info(f"[SQL GENERATOR] included_ingredients value: {sql_filters['included_ingredients']}")

        # Log nutrition and pricing filters
        logger.info(f"[SQL GENERATOR] nutrition filters: {sql_filters.get('nutrition_filters', {})}")
        logger.info(f"[SQL GENERATOR] pricing filters: {sql_filters.get('pricing_filters', {})}")
        logger.info(f"[SQL GENERATOR] currency: {sql_filters.get('currency', 'KR')}")

        # Generate SQL using Agents SDK
        sql_agent = Agent(
            name="SQLGeneratorAgent",
            instructions=self._get_system_prompt(),
            model=SQL_GENERATOR_MODEL
        )

        result = await Runner.run(sql_agent, prompt)
        generated_text = result.final_output

        # Extract SQL from response
        sql = self._extract_sql_from_response(generated_text)

        # Fix common SQL syntax errors (e.g., single-quoted table names)
        sql = self._fix_common_sql_errors(sql)

        # Substitute placeholders with actual values (includes allergen injection)
        sql = self._substitute_placeholders(sql, candidate_ids, session_context, sql_filters)

        # Final validation: ensure SQL is syntactically correct
        sql = self._ensure_valid_sql(sql)

        # Log final SQL after substitution
        logger.info(f"[SQL GENERATOR] Final SQL (after substitution):\n{sql}")

        # Validate SQL
        validation = self.validator.validate(sql, relevant_schema)

        if not validation.is_valid:
            # Return with error info
            return SQLGenerationResult(
                sql="",
                explanation=f"SQL validation failed: {', '.join(validation.errors)}",
                params=sql_filters,
                estimated_rows=0,
                is_safe=False
            )

        # Use sanitized SQL if warnings were fixed
        final_sql = validation.sanitized_sql or sql

        # Log if sanitization changed the SQL
        if validation.sanitized_sql and validation.sanitized_sql != sql:
            logger.info(f"[SQL GENERATOR] Sanitized SQL (after validation):\n{final_sql}")

        return SQLGenerationResult(
            sql=final_sql,
            explanation=self._generate_explanation(sql_filters, candidate_ids),
            params=sql_filters,
            estimated_rows=self._estimate_rows(sql_filters, candidate_ids),
            is_safe=True
        )

    def _get_system_prompt(self) -> str:
        """Get system prompt for SQL generation - optimized for conciseness and speed"""
        return """You are a PostgreSQL expert for a recipe database. Generate queries based on user request and schema.

CRITICAL RULES:
1. Column names: Use double quotes for mixed-case columns: r."id", r."name", r."userUid", r."languageId", r."deletedAt", r."status", r."prepTime", r."cookTime"
2. Table names: NEVER use quotes around lowercase table names. Use "bundle" or "recipe" (with double quotes) only if needed. NEVER use single quotes for tables.
3. String literals: Use single quotes ONLY for string values: 'published', 'en', :language_id
4. WRONG: LEFT JOIN 'bundle' b  ← This uses single quotes (string literal), causes syntax error
5. CORRECT: LEFT JOIN "bundle" b  OR  LEFT JOIN bundle b  ← Double quotes or no quotes for lowercase tables
6. ALWAYS include: r."deletedAt" IS NULL AND r."status" = 'published'
7. Access control: r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL
8. ALWAYS add LIMIT clause (default 20) at the very end
9. CRITICAL: When candidate_ids placeholder (:recipe_ids) is present:
   - DO NOT add included_ingredients EXISTS clauses (embedding search already handled ingredient matching semantically)
   - DO NOT add excluded_ingredients (allergies) - these are handled programmatically
10. For tags (vegetarian, vegan, dessert, etc.): AND EXISTS (SELECT 1 FROM recipe_tags_tag rtt JOIN tag t ON rtt."tagId" = t.id WHERE rtt."recipeId" = r."id" AND t.name ILIKE '%tag_name%')
11. When filtering by multiple tags, use OR: AND EXISTS (SELECT 1 FROM recipe_tags_tag rtt JOIN tag t ON rtt."tagId" = t.id WHERE rtt."recipeId" = r."id" AND (t.name ILIKE '%vegetarian%' OR t.name ILIKE '%vegan%'))
12. BUNDLE TABLE: When joining bundle table, ALWAYS add: AND b."deletedAt" IS NULL. The column is mixed-case so MUST be quoted.

SUPER IMPORTANT - PARENTHESES BALANCE:
- EVERY opening parenthesis ( MUST have a matching closing parenthesis )
- EXISTS clauses MUST be closed: AND EXISTS (SELECT ... WHERE ...)  ← the ) at the end closes EXISTS
- Example CORRECT: AND EXISTS (SELECT 1 FROM recipe_tags_tag rtt WHERE rtt."recipeId" = r."id")
- Example WRONG: AND EXISTS (SELECT 1 FROM recipe_tags_tag rtt WHERE rtt."recipeId" = r."id"   ← missing closing )

QUOTING CHEATSHEET:
- Mixed-case columns: r."id", r."name", r."userUid"  ← double quotes
- Lowercase tables: recipe, ingredient, bundle_recipe, tag, recipe_tags_tag  ← no quotes needed
- Mixed-case tables: "bundle", "recipe"  ← double quotes if mixed case
- String values: 'published', 'en', 'easy', 'vegetarian'  ← single quotes
- NEVER single quotes for identifiers: 'bundle' is WRONG

STANDARD QUERY TEMPLATE:
```sql
SELECT r."id", r."name", r."ingress", r."image", (r."prepTime" + r."cookTime") as total_time, r."difficulty", r."servings"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id" AND b."deletedAt" IS NULL
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL AND r."status" = 'published' AND r."languageId" = :language_id
  AND (r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL)
  AND r."id" IN (:recipe_ids)
LIMIT 20
```

TAG FILTERING TEMPLATE (for dessert, vegetarian, etc.):
```sql
SELECT r."id", r."name", r."ingress", r."image", (r."prepTime" + r."cookTime") as total_time, r."difficulty", r."servings"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id" AND b."deletedAt" IS NULL
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL AND r."status" = 'published' AND r."languageId" = :language_id
  AND (r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL)
  AND r."id" IN (:recipe_ids)
  AND EXISTS (SELECT 1 FROM recipe_tags_tag rtt JOIN tag t ON rtt."tagId" = t.id WHERE rtt."recipeId" = r."id" AND t.name ILIKE '%dessert%')
LIMIT 20
```

CREATOR FILTER TEMPLATE (for filtering by recipe creator/author):
When a creator_uid is provided, filter recipes by the creator's userUid.
IMPORTANT: The creator_uid is always a UUID string (e.g., 'abc123-def456-...'), NOT a username.
NEVER use a username or display name in the userUid filter.
```sql
SELECT r."id", r."name", r."ingress", r."image", (r."prepTime" + r."cookTime") as total_time, r."difficulty", r."servings"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id" AND b."deletedAt" IS NULL
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL AND r."status" = 'published' AND r."languageId" = :language_id
  AND (r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL)
  AND r."userUid" = ':creator_uid'
LIMIT 20
```

Return ONLY the SQL query wrapped in ```sql ... ``` blocks."""

    def _build_generation_prompt(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        sql_filters: Dict[str, Any],
        session_context: Dict[str, Any],
        candidate_ids: Optional[List[str]],
        relevant_schema: RelevantSchema
    ) -> str:
        """Build prompt for SQL generation"""
        schema_service = get_schema_service()
        schema_text = schema_service.format_schema_for_prompt(relevant_schema)

        parts = [
            f"User Query: {query}",
            f"Intent: {nlid_result.get('intent')}",
            "",
            "## Available Schema",
            schema_text,
            "",
            "## Filters to Apply",
        ]

        # Add filters
        if sql_filters.get("max_time"):
            parts.append(f"- Max time: {sql_filters['max_time']} minutes")

        if sql_filters.get("difficulty"):
            parts.append(f"- Difficulty: {sql_filters['difficulty']}")

        if sql_filters.get("tags"):
            parts.append(f"- Tags: {', '.join(sql_filters['tags'])}")
            # Vegetarian context: make clear eggs/dairy are vegetarian so the LLM
            # does not generate ingredient exclusions for eggs or dairy products.
            if "vegetarian" in sql_filters.get("tags", []):
                parts.append(
                    "  NOTE: Vegetarian means NO meat/fish/poultry. "
                    "Eggs and dairy products (milk, cheese, butter, yogurt) ARE vegetarian. "
                    "Do NOT exclude egg or dairy ingredients from vegetarian recipes."
                )

        if sql_filters.get("cuisines"):
            parts.append(f"- Cuisines: {', '.join(sql_filters['cuisines'])}")

        if sql_filters.get("excluded_ingredients"):
            parts.append(f"- Exclude ingredients: {', '.join(sql_filters['excluded_ingredients'])}")

        # Add nutrition filters
        if sql_filters.get("nutrition_filters"):
            nutrition_filters = sql_filters['nutrition_filters']
            parts.append("\n## Nutrition Filters")
            for nutrient, constraint in nutrition_filters.items():
                if isinstance(constraint, str):
                    # Text-based constraints (high, low, under, over)
                    parts.append(f"- {nutrient}: {constraint} (e.g., high = >20g, low = <10g)")
                else:
                    # Numeric constraints
                    parts.append(f"- {nutrient}: {constraint}g")

            parts.append("""
   CRITICAL: For filter-only nutrition queries, use ORDER BY sorting instead of WHERE filter clauses
   This allows dynamic sorting by actual nutrition values in recipe_metadata

   SORTING LOGIC:
   - "high protein" → ORDER BY protein DESC (higher protein first)
   - "low carb" → ORDER BY carbohydrates ASC (lower carbs first)
   - "high calorie" → ORDER BY calories DESC
   - "under 300 calories" → ORDER BY calories ASC (under 300)

   DO NOT use hardcoded WHERE clauses like "> 20" or "< 10"
   Instead, rely on ORDER BY to sort results by actual values""")

        # Add pricing filters
        if sql_filters.get("pricing_filters"):
            pricing_filters = sql_filters['pricing_filters']
            currency = sql_filters.get('currency', 'USD')
            parts.append(f"\n## Pricing Filters (Currency: {currency})")

            for constraint, amount in pricing_filters.items():
                if constraint == 'max_price':
                    parts.append(f"- Max price: {amount} {currency}")
                    parts.append(f"  CRITICAL: Use r.recipe_metadata->'pricing'->>'{currency}' for {currency} pricing")
                    parts.append(f"  Example: AND CAST(r.recipe_metadata->'pricing'->>'{currency}' AS FLOAT) <= {amount}")
                elif constraint == 'min_price':
                    parts.append(f"- Min price: {amount} {currency}")
                    parts.append(f"  CRITICAL: Use r.recipe_metadata->'pricing'->>'{currency}' for {currency} pricing")
                    parts.append(f"  Example: AND CAST(r.recipe_metadata->'pricing'->>'{currency}' AS FLOAT) >= {amount}")

            parts.append("  Use appropriate ORDER BY clauses for sorting by price")

        # IMPORTANT: Only show included_ingredients if we DON'T have candidate_ids
        # When candidate_ids are provided, embedding search already handled ingredient matching semantically
        if not candidate_ids and sql_filters.get("included_ingredients"):
            ingredients = sql_filters['included_ingredients']
            parts.append(f"- Include ingredients: {', '.join(ingredients)}")
            parts.append(f"  CRITICAL: These are SYNONYMS (alternative names for the same ingredient).")
            parts.append(f"  Use EXISTS to find recipes that CONTAIN these ingredients")

            # Build example with available ingredients (handle single ingredient case)
            if len(ingredients) >= 2:
                example = f"  Use OR within a single EXISTS clause: EXISTS (... WHERE i.\"name\" ILIKE '%{ingredients[0]}%' OR i.\"name\" ILIKE '%{ingredients[1]}%' ...)"
            else:
                example = f"  Use EXISTS clause: EXISTS (... WHERE i.\"name\" ILIKE '%{ingredients[0]}%')"
            parts.append(example)
            parts.append(f"  DO NOT use multiple EXISTS with AND - that requires ALL ingredients to be present!")
            parts.append(f"  DO NOT use NOT EXISTS - that would exclude recipes with these ingredients!")
        elif candidate_ids and sql_filters.get("included_ingredients"):
            # candidate_ids present but included_ingredients in filters - log warning

            logger.warning(f"[SQL GENERATOR] candidate_ids present but included_ingredients in filters - will ignore included_ingredients")
            parts.append(f"  NOTE: included_ingredients ignored - candidate_ids provided, embedding search already handled ingredient matching")
        elif not candidate_ids and not sql_filters.get("included_ingredients"):
            # No candidate_ids and no included_ingredients - standalone search with no ingredients
            pass  # No special instructions needed

        if session_context.get("language"):
            language = session_context['language']
            parts.append(f"- Language ID: {language} (CRITICAL: filter r.\"languageId\" = :language_id)")
            parts.append(f"  MANDATORY: Recipe's \"languageId\" column MUST match '{language}' exactly")

        if session_context.get("user_uid"):
            parts.append(f"- User ID: {session_context['user_uid']}")
            parts.append("- Include user's liked recipes and created recipes in results")
        else:
            parts.append("- Anonymous user (no personalization)")

        # Add candidate restriction for hybrid
        if candidate_ids:
            parts.append(f"\n## Candidate Restriction")
            parts.append(f"- ONLY these recipe IDs: {', '.join(candidate_ids[:10])}")
            parts.append(f"- CRITICAL: Use WHERE r.\"id\" IN (:recipe_ids) placeholder (with parentheses)")
            parts.append(f"- The (:recipe_ids) placeholder will be automatically replaced with ('uuid1', 'uuid2', ...)")

        # Add creator filter (CRITICAL for @username and "by Name" queries)
        if sql_filters.get("creator_uid"):
            creator_uid = sql_filters["creator_uid"]
            # Normalise: guard against a list slipping through from the NLID agent.
            if isinstance(creator_uid, list):
                creator_uid = creator_uid[0] if creator_uid else None
            if creator_uid and isinstance(creator_uid, str):
                parts.append(f"\n## Creator Filter (CRITICAL)")
                parts.append(f"- Filter recipes by creator: r.\"userUid\" = '{creator_uid}'")
                parts.append(f"- IMPORTANT: This is a UUID, NOT a username. Use it exactly as provided.")
                parts.append(f"- DO NOT use r.\"name\" ILIKE for creator filtering - that filters recipe names, not creators!")
                parts.append(f"- Add: AND r.\"userUid\" = '{creator_uid}'")
        # Add excluded ingredients (allergies) - ALWAYS apply even with candidate_ids
        if sql_filters.get("excluded_ingredients"):
            if candidate_ids:
                parts.append(f"\n## Allergy Exclusion (CRITICAL)")
                parts.append(f"- EXCLUDE recipes containing these ingredients: {', '.join(sql_filters['excluded_ingredients'])}")
                parts.append(f"- Check in THREE places: recipe name (r.\"name\"), description (r.\"ingress\"), AND ingredients")
                parts.append(f"- For EACH excluded ingredient, add a combined NOT condition like:")
                parts.append(f"- Example for chocolate:")
                parts.append(f"  AND (")
                parts.append(f"    r.\"name\" NOT ILIKE '%chocolate%'")
                parts.append(f"    AND r.\"ingress\" NOT ILIKE '%chocolate%'")
                parts.append(f"    AND NOT EXISTS (SELECT 1 FROM recipe_ingredient ri JOIN ingredient i ON ri.\"ingredientId\" = i.\"id\" WHERE ri.\"recipeId\" = r.\"id\" AND i.\"name\" ILIKE '%chocolate%')")
                parts.append(f"  )")
            else:
                parts.append(f"- Exclude ingredients: {', '.join(sql_filters['excluded_ingredients'])}")
                parts.append(f"- Check in: recipe name, description (ingress), and ingredients table")

        parts.append("\n## Task")
        parts.append("Generate a PostgreSQL SELECT query to retrieve recipes matching the criteria above.")
        parts.append("Return only the SQL query wrapped in ```sql ``` blocks.")

        return "\n".join(parts)

    def _fix_common_sql_errors(self, sql: str) -> str:
        """
        Fix common SQL syntax errors generated by LLM
        This is a safety net to handle cases where the LLM generates incorrect SQL
        """
        import re


        original_sql = sql

        # Fix 1: Single-quoted table names in JOIN/FROM clauses
        def fix_single_quoted_table(match):
            quote_type = match.group(1)
            table_name = match.group(2)
            alias = match.group(3) if match.group(3) else ""
            return f'{quote_type} "{table_name}"{alias}'

        sql = re.sub(
            r'\b(FROM|JOIN)\s+\'([a-zA-Z_][a-zA-Z0-9_]*)\'(\s+[a-zA-Z_][a-zA-Z0-9_]*)?(?!\s*AS)',
            fix_single_quoted_table,
            sql,
            flags=re.IGNORECASE
        )

        # Fix 2: Single-quoted table names in LEFT/RIGHT/INNER JOIN
        def fix_join_single_quotes(match):
            join_type = match.group(1)
            table_name = match.group(2)
            alias = match.group(3) if match.group(3) else ""
            return f'{join_type} "{table_name}"{alias}'

        sql = re.sub(
            r'\b(LEFT|RIGHT|INNER|FULL|CROSS)\s+JOIN\s+\'([a-zA-Z_][a-zA-Z0-9_]*)\'(\s+[a-zA-Z_][a-zA-Z0-9_]*)?',
            fix_join_single_quotes,
            sql,
            flags=re.IGNORECASE
        )

        # Fix 3: Remove any trailing semicolon before LIMIT
        sql = re.sub(r';\s*LIMIT', '\nLIMIT', sql, flags=re.IGNORECASE)

        # Fix 4: Remove stray empty parentheses
        sql = re.sub(r'\bAND\s*\(\s*\)', '', sql, flags=re.IGNORECASE)

        # Fix 5: Remove orphaned closing parentheses
        sql = re.sub(r'\)\s*\n\s*\)', ')', sql)
        sql = re.sub(r'\)\s{2,}\)', ')', sql)

        if sql != original_sql:
            logger.info(f"[SQL FIXER] Applied fixes to SQL syntax")

        return sql

    def _ensure_valid_sql(self, sql: str) -> str:
        """
        Ensure SQL is syntactically valid by fixing any remaining issues.
        This is the final validation step before execution.
        """
        import re

        # Strip SQL comments — the validator rejects them as injection risk.
        # Remove -- line comments (but preserve the newline so line structure stays intact)
        sql = re.sub(r'--[^\n]*', '', sql)
        # Remove /* ... */ block comments
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        # Collapse extra blank lines left behind by comment removal
        sql = re.sub(r'\n{3,}', '\n\n', sql).strip()

        # ------------------------------------------------------------------
        # Fix unclosed EXISTS/NOT EXISTS clauses before r."name" references
        # This happens when LLM generates allergen filters inside EXISTS
        # Pattern: EXISTS (... WHERE ...) followed by AND (r."name" ...) at same level
        # ------------------------------------------------------------------
        def fix_unclosed_exists_before_rname(sql_str: str) -> str:
            """
            Find unclosed EXISTS clauses and close them before r."name" references.
            The pattern we're fixing is:
              AND EXISTS (SELECT ... WHERE ...condition...)
              AND (r."name" ...)  <-- This AND should be OUTSIDE the EXISTS

            We need to close the EXISTS before the AND that has r."name".
            """
            # Pattern: EXISTS ( followed by content, then AND (r."name" at depth 1
            # We need to insert ) before the AND that has r."name"
            result = []
            i = 0
            n = len(sql_str)

            while i < n:
                # Look for EXISTS ( or NOT EXISTS (
                upper_from_i = sql_str[i:i+20].upper()
                if 'EXISTS (' in upper_from_i or 'EXISTS(' in upper_from_i:
                    # Find the opening paren of EXISTS
                    exists_start = i
                    paren_pos = sql_str.find('(', i)
                    if paren_pos == -1:
                        result.append(sql_str[i])
                        i += 1
                        continue

                    # Track depth and look for r."name" at depth 1
                    depth = 0
                    in_str = False
                    j = paren_pos
                    rname_pos = -1

                    while j < n:
                        c = sql_str[j]
                        if c == "'" and not in_str:
                            in_str = True
                        elif c == "'" and in_str:
                            in_str = False
                        elif not in_str:
                            if c == '(':
                                depth += 1
                            elif c == ')':
                                depth -= 1
                                if depth == 0:
                                    # EXISTS properly closed
                                    break
                            # Check for r."name" or r."ingress" at depth 1
                            # This means we're still inside EXISTS but referencing outer table
                            if depth == 1:
                                remaining = sql_str[j:j+20]
                                if remaining.startswith('r."name"') or remaining.startswith('r."ingress"'):
                                    rname_pos = j
                                    break
                        j += 1

                    if rname_pos != -1:
                        # Found r."name" inside unclosed EXISTS
                        # Look backwards for the AND that starts this block
                        # Pattern: newline + whitespace + AND + whitespace + (
                        and_pattern = re.compile(r'\n(\s*)AND\s*\(', re.IGNORECASE)
                        # Search in the segment from paren_pos to rname_pos
                        segment = sql_str[paren_pos:rname_pos]
                        matches = list(and_pattern.finditer(segment))
                        if matches:
                            # Use the last match (the AND just before r."name")
                            last_match = matches[-1]
                            # Position in original string
                            and_pos = paren_pos + last_match.start()
                            indent = last_match.group(1)
                            # Insert ) before the AND with proper indentation
                            result.append(sql_str[i:and_pos])
                            result.append(f'\n{indent})\n')
                            result.append(sql_str[and_pos:])
                            logger.info(
                                f"[SQL FIXER] Closed unclosed EXISTS clause before r.\"name\" at position {and_pos}"
                            )
                            return ''.join(result)

                result.append(sql_str[i])
                i += 1

            return sql_str

        sql = fix_unclosed_exists_before_rname(sql)

        # Find LIMIT clause
        limit_match = re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE)
        if not limit_match:
            return sql

        sql_before_limit = sql[:limit_match.start()].rstrip()

        # Check if there are unclosed parentheses by counting
        depth = 0
        in_string = False

        for char in sql_before_limit:
            if char == "'" and not in_string:
                in_string = True
            elif char == "'" and in_string:
                in_string = False
            elif not in_string:
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1

        if depth == 0:
            # Parentheses are balanced, return as-is
            return sql

        if depth > 0:
            # Unclosed parentheses - most likely an unclosed EXISTS clause
            # Find the last AND EXISTS or AND NOT EXISTS and close it properly
            logger.warning(f"[SQL FIXER] Found {depth} unclosed parentheses, attempting to fix")

            # Simple fix: add closing parentheses before LIMIT
            sql = sql_before_limit + '\n' + ')' * depth + '\n' + sql[limit_match.start():]

        elif depth < 0:
            # Too many closing parentheses - remove extras
            logger.warning(f"[SQL FIXER] Found {-depth} extra closing parentheses, removing")

            # Find and remove extra ) before LIMIT
            extra_count = -depth
            result = []
            close_removed = 0

            for char in sql_before_limit:
                if char == ')' and close_removed < extra_count:
                    close_removed += 1
                    continue
                result.append(char)

            sql = ''.join(result) + '\n' + sql[limit_match.start():]

        return sql

    def _find_sql_injection_point(self, sql: str) -> int:
        """
        Return the character offset of the first top-level trailing clause
        (GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET) so that AND conditions
        we inject land inside the WHERE clause, not after ORDER BY.

        Uses character-by-character paren depth tracking (skips content inside
        single-quoted strings) and regex to locate keyword boundaries, so
        nested subqueries with LIMIT/ORDER BY are correctly skipped.
        Falls back to len(sql) when no trailing clause is found.
        """
        import re

        # Build a list of (start, keyword) for all candidate keyword matches
        pattern = re.compile(
            r'\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET)\b',
            re.IGNORECASE
        )

        # Walk the SQL char by char tracking paren depth and skipping
        # single-quoted string literals (which may contain keywords / parens).
        depth = 0
        in_string = False
        i = 0
        n = len(sql)

        # Pre-collect all keyword match positions for fast lookup
        keyword_starts = {m.start(): m for m in pattern.finditer(sql)}

        while i < n:
            ch = sql[i]

            # Toggle string mode on unescaped single-quote
            if ch == "'" and not in_string:
                in_string = True
                i += 1
                continue
            if in_string:
                if ch == "'" and (i + 1 < n and sql[i + 1] == "'"):
                    # Escaped quote inside string literal — skip both
                    i += 2
                    continue
                if ch == "'":
                    in_string = False
                i += 1
                continue

            if ch == '(':
                depth += 1
                i += 1
                continue
            if ch == ')':
                depth -= 1
                i += 1
                continue

            # Check if a keyword starts at this position (only at depth 0)
            if depth == 0 and i in keyword_starts:
                return i

            i += 1

        return len(sql)

    def _close_unclosed_exists(self, sql: str) -> str:
        """
        Find and close any unclosed EXISTS/NOT EXISTS clauses.
        This is critical before injecting exclusion clauses.

        The problem: LLM sometimes generates SQL like:
            AND EXISTS (SELECT ... WHERE ...)
            AND (r."name" NOT ILIKE '%chicken%' ...)

        Where the EXISTS is not closed before the next AND clause.
        This causes r."name" references to be inside the EXISTS subquery,
        which is invalid (r is not in scope inside the subquery).

        The fix: Detect unclosed EXISTS and insert closing ) before the
        AND that starts with r."name" or r."ingress" references.
        """
        import re

        # Find all EXISTS ( and NOT EXISTS ( positions
        result = []
        i = 0
        n = len(sql)
        modified = False

        while i < n:
            # Check for EXISTS ( pattern (case insensitive)
            remaining = sql[i:]
            exists_match = re.search(r'\bEXISTS\s*\(', remaining, re.IGNORECASE)
            not_exists_match = re.search(r'\bNOT\s+EXISTS\s*\(', remaining, re.IGNORECASE)

            # Use whichever comes first
            if not_exists_match and (not exists_match or not_exists_match.start() < exists_match.start()):
                match = not_exists_match
                is_not = True
            elif exists_match:
                match = exists_match
                is_not = False
            else:
                # No more EXISTS found, append rest and exit
                result.append(sql[i:])
                break

            # Add content up to and including EXISTS (
            match_start = i + match.start()
            match_end = i + match.end()
            result.append(sql[i:match_end])
            i = match_end

            # Track parentheses depth to find where EXISTS should close
            # Also look for r."name" references which indicate we've exited the subquery scope
            depth = 1  # Already inside the EXISTS (
            in_str = False
            j = i
            found_rname = -1
            proper_close = -1

            while j < n:
                c = sql[j]
                if c == "'" and not in_str:
                    in_str = True
                elif c == "'" and in_str:
                    in_str = False
                elif not in_str:
                    if c == '(':
                        depth += 1
                    elif c == ')':
                        depth -= 1
                        if depth == 0:
                            # EXISTS properly closed
                            proper_close = j
                            break
                    # Check for r."name" or r."ingress" at depth 1
                    # This indicates we're still inside EXISTS but referencing outer table
                    if depth == 1:
                        # Look for pattern: newline + whitespace + AND + whitespace + ( + r."
                        lookahead = sql[j:j+50]
                        rname_pattern = re.match(r'\s*AND\s*\(\s*r\."', lookahead, re.IGNORECASE)
                        if rname_pattern:
                            found_rname = j
                            break
                j += 1

            if found_rname != -1 and proper_close == -1:
                # EXISTS not properly closed, but found r."name" reference
                # Insert closing ) before the AND that has r."name"
                # Find the exact position of AND
                and_match = re.search(r'\s*AND\s*\(', sql[found_rname:found_rname+50], re.IGNORECASE)
                if and_match:
                    insert_pos = found_rname + and_match.start()
                    result.append(sql[i:insert_pos])
                    result.append('\n)\n')
                    result.append(sql[insert_pos:])
                    logger.info(
                        f"[SQL FIXER] Closed unclosed EXISTS clause before r.\"name\" reference"
                    )
                    modified = True
                    break

            if proper_close != -1:
                # EXISTS properly closed, continue from there
                result.append(sql[i:proper_close+1])
                i = proper_close + 1
            else:
                # No proper close and no r."name" found - add rest and let _ensure_valid_sql handle it
                result.append(sql[i:])
                break

        if modified:
            return ''.join(result)
        return sql

    def _build_allergen_exclusion_clause(
        self,
        excluded_ingredients: List[str]
    ) -> str:
        """
        Build a complete allergen exclusion SQL clause programmatically.
        This ensures ALL expanded allergen variants are included.

        Generates a clause like:
        AND (
            r."name" NOT ILIKE '%apple%' AND r."ingress" NOT ILIKE '%apple%'
            AND NOT EXISTS (SELECT 1 FROM recipe_ingredient ri JOIN ingredient i ON ri."ingredientId" = i."id" WHERE ri."recipeId" = r."id" AND i."name" ILIKE '%apple%')
        )
        AND (
            r."name" NOT ILIKE '%strawberry%' AND r."ingress" NOT ILIKE '%strawberry%'
            AND NOT EXISTS (SELECT 1 FROM recipe_ingredient ri JOIN ingredient i ON ri."ingredientId" = i."id" WHERE ri."recipeId" = r."id" AND i."name" ILIKE '%strawberry%')
        )
        ...
        """
        if not excluded_ingredients:
            return ""

        clauses = []
        for ingredient in excluded_ingredients:
            # Escape single quotes in ingredient name
            safe_ing = ingredient.replace("'", "''")
            clause = f"""(
    r."name" NOT ILIKE '%{safe_ing}%'
    AND r."ingress" NOT ILIKE '%{safe_ing}%'
    AND NOT EXISTS (
      SELECT 1
      FROM recipe_ingredient ri
      JOIN ingredient i ON ri."ingredientId" = i."id"
      WHERE ri."recipeId" = r."id"
        AND i."name" ILIKE '%{safe_ing}%'
    )
  )"""
            clauses.append(clause)

        return "AND " + "\n  AND ".join(clauses)

    def _strip_trailing_clauses(self, sql: str) -> tuple:
        """
        Strip trailing ORDER BY / LIMIT / OFFSET / GROUP BY clauses from the
        end of a top-level SQL statement and return (body, trailing_text).

        Locates the last top-level ORDER BY or LIMIT line (identified by
        starting a line with ≤2 spaces of indentation) and cuts there.
        This correctly handles multi-line ORDER BY blocks whose sort-column
        lines are indented — those lines are included in the trailing text
        because they come after the ORDER BY keyword line.

        Returns:
            (body_sql, trailing_sql) — trailing_sql is re-appended after
            WHERE-clause injections.
        """
        import re

        if '\n' in sql:
            # Multi-line SQL: require newline + ≤2-space indent before clause
            # (avoids matching ORDER BY / LIMIT inside subquery lines)
            order_by_pat = re.compile(
                r'\n[ \t]{0,2}ORDER\s+BY\b', re.IGNORECASE
            )
            limit_pat = re.compile(
                r'\n[ \t]{0,2}LIMIT\b', re.IGNORECASE
            )
        else:
            # Single-line SQL (whitespace-normalised): match on word boundaries.
            # Using last match ensures we pick up the top-level clause, not one
            # inside a subquery.
            order_by_pat = re.compile(r'\bORDER\s+BY\b', re.IGNORECASE)
            limit_pat = re.compile(r'\bLIMIT\b', re.IGNORECASE)

        order_by_matches = list(order_by_pat.finditer(sql))
        limit_matches = list(limit_pat.finditer(sql))

        cut_pos = None

        if order_by_matches:
            # Use the last ORDER BY — it is the top-level trailing clause
            cut_pos = order_by_matches[-1].start()
        elif limit_matches:
            cut_pos = limit_matches[-1].start()

        if cut_pos is None:
            return sql, ""

        body = sql[:cut_pos].rstrip()
        trailing = '\n' + sql[cut_pos:].lstrip('\n')
        return body, trailing


    def _remove_ilike_blocks_for_ingredient(
        self, sql: str, ingredient: str
    ) -> str:
        """
        Remove any top-level AND (...) or AND NOT (...) block from the SQL
        WHERE clause that contains an ILIKE reference to *ingredient*.

        Uses a character-level balanced-paren walker so nested subqueries
        (which may contain their own parens) are handled correctly.

        This is the ingredient-aware catch-all that removes the LLM's
        positive-inclusion form:
            AND ( (r."name" ILIKE '%potato%') OR ... OR EXISTS(...) IS FALSE )
        as well as any other unusual form not matched by the regex patterns.
        """
        import re

        # Quick bail-out: if the ingredient doesn't appear in the SQL at all
        if ingredient.lower() not in sql.lower():
            return sql

        result = []
        i = 0
        n = len(sql)
        in_string = False

        while i < n:
            ch = sql[i]

            # Track single-quoted string literals
            if ch == "'" and not in_string:
                in_string = True
                result.append(ch)
                i += 1
                continue
            if in_string:
                result.append(ch)
                if ch == "'" and i + 1 < n and sql[i + 1] == "'":
                    # Escaped quote — consume both
                    result.append(sql[i + 1])
                    i += 2
                elif ch == "'":
                    in_string = False
                    i += 1
                else:
                    i += 1
                continue

            # Look for "AND" (+ optional "NOT") followed by "(" at depth 0
            and_match = re.match(
                r'(AND\s+(?:NOT\s+)?)\(',
                sql[i:],
                re.IGNORECASE,
            )
            if and_match:
                prefix = and_match.group(1)  # "AND " or "AND NOT "
                paren_start = i + len(prefix)  # position of the "("
                # Walk forward to find the matching closing paren
                depth = 0
                j = paren_start
                block_contains_ingredient = False
                in_str_inner = False
                while j < n:
                    c = sql[j]
                    if c == "'" and not in_str_inner:
                        in_str_inner = True
                        # Check if ingredient appears here (in a string literal)
                        # by peeking ahead for ILIKE '%ingredient%'
                        j += 1
                        continue
                    if in_str_inner:
                        if c == "'" and j + 1 < n and sql[j + 1] == "'":
                            j += 2
                            continue
                        if c == "'":
                            in_str_inner = False
                        j += 1
                        continue
                    if c == '(':
                        depth += 1
                    elif c == ')':
                        depth -= 1
                        if depth == 0:
                            # Closing paren found — check if block contains ingredient
                            block_text = sql[paren_start: j + 1].lower()
                            if ingredient.lower() in block_text:
                                block_contains_ingredient = True
                            break
                    j += 1

                if block_contains_ingredient and j < n:
                    # Skip the whole AND [NOT] (...) block
                    logger.info(
                        f"[ALLERGEN INJECTION] Removed LLM block referencing "
                        f"'{ingredient}' at offset {i}"
                    )
                    i = j + 1
                    continue
                # Block does not reference ingredient — keep it
                result.append(sql[i])
                i += 1
                continue

            result.append(ch)
            i += 1

        return ''.join(result)

    def _inject_allergen_exclusion(
        self,
        sql: str,
        excluded_ingredients: List[str]
    ) -> str:
        """
        Inject allergen exclusion clause into SQL.

        Strategy:
        1. Remove ALL existing LLM-generated allergen filter blocks
           (both exclusion forms and the broken IS FALSE form).
        2. Strip trailing ORDER BY / LIMIT clauses to a side buffer.
        3. Append our programmatic exclusion to the WHERE body.
        4. Re-attach the trailing clauses.

        This avoids all paren-depth tracking for the injection point.
        """
        import re

        if not excluded_ingredients:
            return sql

        # ------------------------------------------------------------------
        # Step 1: Remove ALL existing LLM-generated allergen filter blocks
        # ------------------------------------------------------------------

        sql_cleaned = sql

        # Pattern F (ingredient-aware): Run FIRST while ingredient names are still
        # present in the SQL. Removes any top-level AND (...) or AND NOT (...) block
        # that references one of the excluded ingredient names via ILIKE.
        # Uses a balanced-paren walker — safe for nested subqueries.
        # Must run before C/D/E which strip the NOT ILIKE lines and would hide
        # the ingredient name from this search.
        for ing in excluded_ingredients:
            ing_lower = ing.lower()
            sql_cleaned = self._remove_ilike_blocks_for_ingredient(
                sql_cleaned, ing_lower
            )

        # Pattern A: AND NOT (...) blocks — the clean negation form
        # AND NOT ( r."name" ILIKE '%X%' OR r."ingress" ILIKE '%X%' OR EXISTS(...) )
        sql_cleaned = re.sub(
            r"AND\s+NOT\s*\(\s*(?:r\.\"name\"\s+ILIKE\s+'%[^']*%'|"
            r"r\.\"ingress\"\s+ILIKE\s+'%[^']*%'|"
            r"EXISTS\s*\(.*?\))"
            r"(?:\s*(?:OR|AND)\s*(?:r\.\"name\"\s+ILIKE\s+'%[^']*%'|"
            r"r\.\"ingress\"\s+ILIKE\s+'%[^']*%'|"
            r"EXISTS\s*\(.*?\)))*\s*\)",
            "",
            sql_cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Pattern B: AND (...ILIKE... OR ...ILIKE... OR EXISTS(...) IS FALSE/NOT TRUE)
        # This is the LLM's broken "exclusion via IS FALSE / IS NOT TRUE" form
        sql_cleaned = re.sub(
            r"AND\s*\(\s*(?:\(r\.\"name\"\s+ILIKE\s+'%[^']*%'\)\s*OR\s*)?"
            r"(?:\(r\.\"ingress\"\s+ILIKE\s+'%[^']*%'\)\s*OR\s*)?"
            r"EXISTS\s*\(.*?\)\s+IS\s+(?:NOT\s+TRUE|FALSE)\s*\)",
            "",
            sql_cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Pattern C: NOT ILIKE standalone lines
        # AND r."name" NOT ILIKE '%X%'
        sql_cleaned = re.sub(
            r"AND\s+r\.\s*\"(?:name|ingress)\"\s+NOT\s+ILIKE\s+'%[^']*%'",
            "",
            sql_cleaned,
            flags=re.IGNORECASE,
        )

        # Pattern D: AND NOT EXISTS (...ingredient ILIKE...) blocks
        sql_cleaned = re.sub(
            r"AND\s+NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+recipe_ingredient\b.*?"
            r"ILIKE\s+'%[^']*%'.*?\)",
            "",
            sql_cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Pattern E: Compound AND (r."name" NOT ILIKE ... AND ... AND NOT EXISTS ...)
        sql_cleaned = re.sub(
            r"AND\s*\(\s*r\.\s*\"name\"\s+NOT\s+ILIKE\s+'%[^']*%'.*?NOT\s+EXISTS\s*\(.*?\)\s*\)",
            "",
            sql_cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Pattern F: MALFORMED NOT EXISTS with r."name"/r."ingress" nested inside
        # This handles the case where LLM puts r."name" checks INSIDE a NOT EXISTS subquery:
        # AND NOT EXISTS (SELECT ... WHERE ... AND (r."name" NOT ILIKE '%X%' ...))
        # This is syntactically wrong - r."name" is from outer query, not the subquery
        # We need to remove the entire NOT EXISTS block and the nested AND (...) blocks
        for ing in excluded_ingredients:
            ing_escaped = re.escape(ing.lower())
            # Pattern for malformed NOT EXISTS containing r."name" checks
            malformed_pattern = (
                r"AND\s+NOT\s+EXISTS\s*\(\s*SELECT\s+[^)]+?"
                r"WHERE\s+[^)]*?"
                r"AND\s*\(\s*"
                r"r\.\"name\"\s+NOT\s+ILIKE\s+'%" + ing_escaped + r"%'"
                r"[^)]*\)"
                r"[^)]*\)"
            )
            sql_cleaned = re.sub(
                malformed_pattern,
                "",
                sql_cleaned,
                flags=re.IGNORECASE | re.DOTALL,
            )

        # Pattern G: Aggressive cleanup - remove ANY r."name" or r."ingress" NOT ILIKE
        # that references an excluded ingredient, regardless of context
        # This catches edge cases the above patterns miss
        for ing in excluded_ingredients:
            ing_escaped = re.escape(ing.lower())
            # Remove r."name" NOT ILIKE '%ingredient%'
            sql_cleaned = re.sub(
                rf"AND\s+r\.\s*\"name\"\s+NOT\s+ILIKE\s+'%{ing_escaped}%'",
                "",
                sql_cleaned,
                flags=re.IGNORECASE,
            )
            # Remove r."ingress" NOT ILIKE '%ingredient%'
            sql_cleaned = re.sub(
                rf"AND\s+r\.\s*\"ingress\"\s+NOT\s+ILIKE\s+'%{ing_escaped}%'",
                "",
                sql_cleaned,
                flags=re.IGNORECASE,
            )
            # Remove nested AND (...) blocks containing r."name" NOT ILIKE
            sql_cleaned = re.sub(
                rf"AND\s*\(\s*r\.\s*\"name\"\s+NOT\s+ILIKE\s+'%{ing_escaped}%'[^)]*\)",
                "",
                sql_cleaned,
                flags=re.IGNORECASE | re.DOTALL,
            )

        # Clean up orphaned empty AND () wrappers left by the removals above.
        # Run in a loop to handle nested cases (inner removed first, then outer).
        for _ in range(5):
            cleaned = re.sub(r'\bAND\s*\(\s*\)', '', sql_cleaned, flags=re.IGNORECASE | re.DOTALL)
            if cleaned == sql_cleaned:
                break
            sql_cleaned = cleaned

        # Clean up stray empty parens / extra blank lines
        sql_cleaned = re.sub(r'\bAND\s*\(\s*\)', '', sql_cleaned, flags=re.IGNORECASE)
        sql_cleaned = re.sub(r'\n{3,}', '\n\n', sql_cleaned)

        if sql_cleaned != sql:
            logger.info(
                "[ALLERGEN INJECTION] Removed existing LLM-generated "
                "allergen filter blocks"
            )

        # ------------------------------------------------------------------
        # CRITICAL: Close any unclosed EXISTS/NOT EXISTS clauses BEFORE
        # injecting the exclusion clause. Otherwise the exclusion will be
        # injected inside the EXISTS where r."name" references are invalid.
        # ------------------------------------------------------------------
        sql_cleaned = self._close_unclosed_exists(sql_cleaned)

        # ------------------------------------------------------------------
        # Step 2: Build the programmatic exclusion clause
        # ------------------------------------------------------------------
        exclusion_clause = self._build_allergen_exclusion_clause(
            excluded_ingredients
        )
        logger.info(
            f"[ALLERGEN INJECTION] Built exclusion clause for "
            f"{len(excluded_ingredients)} ingredients"
        )

        # ------------------------------------------------------------------
        # Step 3: Strip trailing ORDER BY / LIMIT to a side buffer,
        #         append exclusion to WHERE body, re-attach trailing clauses.
        #         This is 100% reliable — no paren counting needed.
        # ------------------------------------------------------------------
        body, trailing = self._strip_trailing_clauses(sql_cleaned)
        if trailing.strip():
            sql = body + "\n" + exclusion_clause + trailing
            logger.info(
                "[ALLERGEN INJECTION] Injected allergen exclusion before "
                "trailing clause"
            )
        else:
            sql = body + "\n" + exclusion_clause
            logger.info(
                "[ALLERGEN INJECTION] Appended allergen exclusion at end "
                "(no trailing clause found)"
            )

        return sql

    def _inject_creator_filter(
        self,
        sql: str,
        creator_uid: str
    ) -> str:
        """
        Inject creator filter into SQL WHERE clause.
        This ensures recipes are filtered by the specified creator.

        IMPORTANT: Also removes any LLM-generated incorrect filters:
        - Recipe name filters (r."name" ILIKE '%username%')
        - Incorrect userUid filters with non-UUID values (r."userUid" = '@username')

        Args:
            sql: The SQL query to modify
            creator_uid: The user UID of the recipe creator (must be a UUID)

        Returns:
            Modified SQL with creator filter
        """
        import re

        if not creator_uid:
            return sql

        # Step 1: Remove any LLM-generated recipe name filters
        # When user asks "recipes by mammapia", LLM might generate:
        # AND r."name" ILIKE '%mammapia%' (WRONG - mammapia is a username, not a recipe name)
        recipe_name_filter_pattern = r'AND\s+r\."name"\s+(I?LIKE)\s+\'%[^%\']+%\''
        sql_cleaned = re.sub(recipe_name_filter_pattern, '', sql, flags=re.IGNORECASE)

        if sql_cleaned != sql:
            logger.info("[CREATOR INJECTION] Removed LLM-generated recipe name filter (r.name ILIKE)")
            sql = sql_cleaned

        # Step 2: Remove any LLM-generated userUid filters with non-UUID values
        # The LLM might generate: AND r."userUid" = '@mammapia' (WRONG - username is not a UID)
        # We only keep userUid filters that look like UUIDs or are part of access control

        # UUID pattern: 8-4-4-4-12 hex characters
        uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'

        # Find all standalone userUid = 'value' patterns (not part of OR access control)
        # We need to preserve the access control pattern: (r."private" = false OR r."userUid" = 'value')
        def remove_invalid_useruid_filters(match):
            full_match = match.group(0)
            value = match.group(1)

            # Keep it if it exactly matches the creator_uid we intend to inject
            # (covers Firebase UIDs which are alphanumeric but not hyphenated UUIDs)
            if value == creator_uid:
                return full_match

            # Check if the value looks like a PostgreSQL UUID (case insensitive)
            if re.match(uuid_pattern, value, re.IGNORECASE):
                # Keep it - it's a valid UUID
                return full_match

            # Remove it - it's not a valid UID (probably a username like '@mammapia')
            logger.info(f"[CREATOR INJECTION] Removed invalid userUid filter with value: {value}")
            return ''

        # Pattern to match: AND r."userUid" = 'value' (standalone, not in OR clause)
        # We need to be careful not to match the access control pattern
        # First, let's check if there's an access control pattern
        access_control_pattern = r'\(r\."private"\s*=\s*false\s+OR\s+r\."userUid"\s*=\s*\'[^\']+\'\)'

        # Find and protect access control patterns
        access_control_matches = list(re.finditer(access_control_pattern, sql, re.IGNORECASE))

        # Replace standalone userUid filters that aren't UUIDs
        # Pattern: AND r."userUid" = 'value' where value is NOT a UUID
        standalone_useruid_pattern = r"AND\s+r\.\"userUid\"\s*=\s*'([^']+)'"

        # Process each match
        sql_cleaned = re.sub(standalone_useruid_pattern, remove_invalid_useruid_filters, sql, flags=re.IGNORECASE)

        if sql_cleaned != sql:
            sql = sql_cleaned
            logger.info("[CREATOR INJECTION] Cleaned up invalid userUid filters")

        # Clean up any resulting double spaces or empty AND clauses
        sql = re.sub(r'\bAND\s+AND\b', 'AND', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\s+', ' ', sql)  # Normalize whitespace

        # Step 3: Check if we already have the correct creator filter
        correct_filter = f"r.\"userUid\" = '{creator_uid}'"
        if correct_filter in sql:
            logger.info(f"[CREATOR INJECTION] Correct creator filter already exists in SQL")
            return sql

        # Step 4: Inject the correct creator filter
        # Defensive: coerce to string in case a list slipped through
        if isinstance(creator_uid, list):
            creator_uid = creator_uid[0] if creator_uid else ""
        if not creator_uid:
            return sql
        safe_uid = creator_uid.replace("'", "''")
        creator_clause = f'AND r."userUid" = \'{safe_uid}\''
        logger.info(f"[CREATOR INJECTION] Adding creator filter for uid: {creator_uid}")

        # Inject before trailing SQL clauses (ORDER BY / LIMIT)
        # Use strip-and-reattach strategy (no paren-depth counting needed)
        body, trailing = self._strip_trailing_clauses(sql)
        if trailing.strip():
            sql = body + "\n" + creator_clause + trailing
            logger.info("[CREATOR INJECTION] Injected creator filter before trailing clause")
        else:
            sql = body + "\n" + creator_clause
            logger.info("[CREATOR INJECTION] Appended creator filter at end")

        return sql

    def _inject_time_order_by(
        self,
        sql: str,
        sort_order: str
    ) -> str:
        """
        Inject ORDER BY clause for time-based sorting.

        When user asks for "quick" or "long" recipes, we sort by total_time
        instead of filtering by a hard time limit.

        Args:
            sql: The SQL query to modify
            sort_order: "ASC" for quickest first, "DESC" for longest first

        Returns:
            Modified SQL with time-based ORDER BY
        """
        import re

        # Validate sort_order
        if sort_order.upper() not in ["ASC", "DESC"]:
            return sql

        sort_order = sort_order.upper()
        logger.info(f"[TIME ORDER] Injecting ORDER BY total_time {sort_order}")

        # Remove any existing max_time filter clause (since we're sorting, not filtering)
        # Pattern: AND (r."prepTime" + r."cookTime") <= X
        sql = re.sub(
            r'AND\s+\(r\."prepTime"\s*\+\s*r\."cookTime"\)\s*<=\s*\d+',
            '',
            sql,
            flags=re.IGNORECASE
        )

        # Also remove any time comparison in WHERE clause
        sql = re.sub(
            r'AND\s+\(r\."prepTime"\s*\+\s*r\."cookTime"\)\s*[<>=]+\s*\d+',
            '',
            sql,
            flags=re.IGNORECASE
        )

        # Build the ORDER BY clause
        order_clause = f'ORDER BY (r."prepTime" + r."cookTime") {sort_order}'

        # Find LIMIT clause
        limit_match = re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE)

        if limit_match:
            # Check if there's already an ORDER BY clause
            existing_order_match = re.search(r'\bORDER\s+BY\b', sql, re.IGNORECASE)

            if existing_order_match:
                # Replace existing ORDER BY with our time-based one
                # Find everything between ORDER BY and LIMIT
                pattern = r'\bORDER\s+BY\s+[^L]+?(?=LIMIT)'
                sql = re.sub(pattern, f'{order_clause}\n', sql, flags=re.IGNORECASE)
                logger.info(f"[TIME ORDER] Replaced existing ORDER BY with time sorting")
            else:
                # Insert ORDER BY before LIMIT
                sql = sql[:limit_match.start()] + order_clause + "\n" + sql[limit_match.start():]
                logger.info(f"[TIME ORDER] Inserted ORDER BY before LIMIT")
        else:
            # No LIMIT, append at the end
            sql = sql.rstrip() + "\n" + order_clause
            logger.info(f"[TIME ORDER] Appended ORDER BY at end")

        return sql

    def _substitute_placeholders(
        self,
        sql: str,
        candidate_ids: Optional[List[str]],
        session_context: Dict[str, Any],
        sql_filters: Dict[str, Any]
    ) -> str:
        """Substitute placeholders in SQL with actual values"""
        import re


        # Debug: Log original SQL before substitution
        logger.debug(f"[SQL SUBSTITUTION] Before: {sql[:200]}...")
        logger.debug(f"[SQL SUBSTITUTION] max_time in filters: {'max_time' in sql_filters}")
        if 'max_time' in sql_filters:
            logger.debug(f"[SQL SUBSTITUTION] max_time value: {sql_filters['max_time']}")

        # Substitute :recipe_ids placeholder
        if candidate_ids and ":recipe_ids" in sql:
            formatted_ids = ", ".join(f"'{cid}'" for cid in candidate_ids)
            sql = re.sub(r":recipe_ids\b", formatted_ids, sql)

        # Substitute :user_uid placeholder (or %(user_uid)s fallback)
        user_uid = session_context.get("user_uid")
        if user_uid:
            if ":user_uid" in sql:
                sql = re.sub(r":user_uid\b", f"'{user_uid}'", sql)
            elif "%(user_uid)" in sql:
                sql = re.sub(r"%\(user_uid\)s?", f"'{user_uid}'", sql)

        # Substitute :max_time placeholder (or %(max_time)s fallback)
        max_time = sql_filters.get("max_time")

        # Handle list values (from filter merging) - take the last non-None value
        if isinstance(max_time, list):
            # Filter out None values and take the last one
            non_none_values = [v for v in max_time if v is not None]
            max_time = non_none_values[-1] if non_none_values else None

        # Handle None case by using a very large default value (effectively no limit)
        # This is simpler than removing the entire AND clause
        actual_max_time = max_time if max_time else 999999

        # Try :max_time format first
        if ":max_time" in sql:
            sql = re.sub(r":max_time\b", str(actual_max_time), sql)
        # Fallback for %(max_time)s format (LLM sometimes generates this)
        # Note: No \b at end since ) is not a word character
        elif "%(max_time)" in sql:
            sql = re.sub(r"%\(max_time\)s?", str(actual_max_time), sql)

        # Handle time_sort_order (for "quick", "short", "long" queries)
        # Instead of filtering by max_time, we ORDER BY total_time
        time_sort_order = sql_filters.get("time_sort_order")
        if time_sort_order:
            sql = self._inject_time_order_by(sql, time_sort_order)

        # Substitute :language_id placeholder (or %(language_id)s fallback)
        language = session_context.get("language")
        if language:
            if ":language_id" in sql:
                sql = re.sub(r":language_id\b", f"'{language}'", sql)
            elif "%(language_id)" in sql:
                sql = re.sub(r"%\(language_id\)s?", f"'{language}'", sql)

        # Handle difficulty parameter (optional)
        # When None, substitute with all possible values (effectively no filter)
        difficulty = sql_filters.get("difficulty")
        if not difficulty:
            # When no difficulty specified, use all values (no filter)
            # The LLM may have generated: AND r."difficulty" IN ('easy', 'medium', 'hard')
            # We can leave it as is since it's a complete list, or remove it entirely
            # For simplicity, we leave it - it's harmless
            pass
        else:
            # Convert to list if it's a single value
            if isinstance(difficulty, str):
                difficulty = [difficulty]
            # Format as SQL IN clause values
            difficulty_values = ", ".join(f"'{d}'" for d in difficulty)
            if ":difficulty" in sql:
                sql = re.sub(r":difficulty\b", difficulty_values, sql)
            elif "%(difficulty)" in sql:
                sql = re.sub(r"%\(difficulty\)s?", difficulty_values, sql)

        # Fix unquoted UUIDs in IN clauses (LLM sometimes forgets to quote them)
        # Pattern matches: r."id" IN (uuid1, uuid2, ...) where uuids are NOT quoted
        # We quote each UUID individually (only strings with dashes, like xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
        def quote_uuids_in_in(match):
            content = match.group(1)
            if not content:
                return match.group(0)
            # Split by comma, trim whitespace, and quote each UUID if not already quoted
            items = [item.strip() for item in content.split(',')]
            quoted_items = []
            for item in items:
                # Skip if already quoted or empty
                if not item:
                    quoted_items.append(item)
                    continue
                # Check if already quoted
                if (item.startswith("'") and item.endswith("'")) or (item.startswith('"') and item.endswith('"')):
                    quoted_items.append(item)
                # Only quote if it looks like a UUID (contains dashes)
                elif '-' in item and not item.startswith(':'):
                    quoted_items.append(f"'{item}'")
                else:
                    quoted_items.append(item)
            return f'IN ({", ".join(quoted_items)})'

        # Match IN (...) followed by optional whitespace, capturing the content
        sql = re.sub(
            r'\bIN\s*\(\s*([^)]+?)\s*\)',
            quote_uuids_in_in,
            sql,
            flags=re.IGNORECASE
        )

        # Normalize / balance SQL before programmatic injections.
        # If LLM output contains unclosed parentheses, later injections can
        # land inside nested OR/EXISTS blocks and become ineffective.
        sql = self._ensure_valid_sql(sql)

        # Inject programmatically-built allergen exclusion clause
        # This ensures ALL expanded allergen variants are included
        excluded_ingredients = sql_filters.get("excluded_ingredients") or sql_filters.get("exclude_ingredients")
        if excluded_ingredients:
            sql = self._inject_allergen_exclusion(sql, excluded_ingredients)

        # Inject creator filter (filter recipes by specific user)
        creator_uid = sql_filters.get("creator_uid")
        # Normalize: NLID or pipeline may produce a list instead of a plain string
        if isinstance(creator_uid, list):
            creator_uid = creator_uid[0] if creator_uid else None
        if creator_uid and isinstance(creator_uid, str):
            sql = self._inject_creator_filter(sql, creator_uid)

        return sql

    def _extract_sql_from_response(self, response: str) -> str:
        """Extract SQL from LLM response"""
        # Try to find SQL in markdown blocks
        sql_match = re.search(r"```sql\s*(.*?)\s*```", response, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()

        # Try to find SQL without markdown
        sql_match = re.search(r"SELECT.*?(?:;|$)", response, re.DOTALL | re.IGNORECASE)
        if sql_match:
            return sql_match.group(0).strip()

        # Fallback: return entire response
        return response.strip()

    def _generate_explanation(
        self,
        sql_filters: Dict[str, Any],
        candidate_ids: Optional[List[str]]
    ) -> str:
        """Generate explanation of the SQL query"""
        parts = ["SQL query to retrieve recipes"]

        if candidate_ids:
            parts.append(f"restricted to {len(candidate_ids)} candidates from semantic search")

        filters_applied = []
        if sql_filters.get("max_time"):
            filters_applied.append(f"max time {sql_filters['max_time']}min")
        if sql_filters.get("difficulty"):
            filters_applied.append(f"difficulty {sql_filters['difficulty']}")
        if sql_filters.get("tags"):
            filters_applied.append(f"tags {', '.join(sql_filters['tags'])}")

        if filters_applied:
            parts.append(f"with filters: {', '.join(filters_applied)}")

        return ". ".join(parts) + "."

    def _estimate_rows(
        self,
        sql_filters: Dict[str, Any],
        candidate_ids: Optional[List[str]]
    ) -> int:
        """Estimate number of rows returned"""
        if candidate_ids:
            return len(candidate_ids)

        # Rough estimate based on filters
        estimate = 50  # Default

        if sql_filters.get("difficulty"):
            estimate //= 2
        if sql_filters.get("max_time"):
            estimate //= 2
        if sql_filters.get("tags"):
            estimate //= 3

        return max(5, min(estimate, 100))


class SQLExecutionService:
    """
    SQL Execution Stage

    Executes validated SQL queries and returns results
    """

    def __init__(self, db: Session):
        """
        Initialize SQL execution service

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def execute_sql(self, sql: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute validated SQL query

        Args:
            sql: SQL query to execute
            params: Query parameters to substitute in the SQL

        Returns:
            Dictionary with results and metadata
        """
        try:
            if params:
                # Substitute parameters using string formatting for compatibility
                # This is safe because the SQL has been validated and sanitized
                formatted_sql = sql
                for key, value in params.items():
                    formatted_sql = formatted_sql.replace(f":{key}", f"'{value}'")
                result = self.db.execute(text(formatted_sql))
            else:
                logger.info(f"[SQL EXECUTOR] Final SQL (no params):\n{sql}")
                result = self.db.execute(text(sql))

            rows = result.fetchall()
            columns = result.keys()

            return {
                "success": True,
                "rows": [dict(zip(columns, row)) for row in rows],
                "row_count": len(rows),
                "columns": list(columns),
                "sql": sql
            }

        except SQLAlchemyError as e:
            # Rollback to clear the failed transaction state
            self.db.rollback()
            return {
                "success": False,
                "error": str(e),
                "sql": sql
            }
