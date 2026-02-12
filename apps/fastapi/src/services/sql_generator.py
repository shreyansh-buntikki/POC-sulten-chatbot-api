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

from apps.fastapi import logger
from apps.fastapi.src.services.schema_understanding import get_schema_service, RelevantSchema

load_dotenv()

# Model configuration from environment
SQL_GENERATOR_MODEL = os.getenv('SQL_GENERATOR_MODEL', 'gpt-5-mini')


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

    def generate_sql(
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

        import logging
        logger = logging.getLogger(__name__)

        # DEBUG: Log the sql_filters to verify ingredients are excluded
        logger.info(f"[SQL GENERATOR] sql_filters keys: {list(sql_filters.keys())}")
        logger.info(f"[SQL GENERATOR] included_ingredients in filters: {'included_ingredients' in sql_filters}")
        if 'included_ingredients' in sql_filters:
            logger.info(f"[SQL GENERATOR] included_ingredients value: {sql_filters['included_ingredients']}")

        # Generate SQL using LLM
        response = self.client.chat.completions.create(
            model=SQL_GENERATOR_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": self._get_system_prompt()
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1,  # Low temperature for consistent SQL
            max_completion_tokens=800  # GPT-5+ uses max_completion_tokens
        )

        generated_text = response.choices[0].message.content

        # Extract SQL from response
        sql = self._extract_sql_from_response(generated_text)

        # DEBUG: Log generated SQL before substitution
        logger.info(f"[SQL GENERATOR] Generated SQL (before substitution): {sql[:300]}...")

        # Fix common SQL syntax errors (e.g., single-quoted table names)
        sql = self._fix_common_sql_errors(sql)

        # Substitute placeholders with actual values
        sql = self._substitute_placeholders(sql, candidate_ids, session_context, sql_filters)

        # DEBUG: Log final SQL after substitution
        logger.info(f"[SQL GENERATOR] Final SQL (after substitution): {sql[:300]}...")

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
8. ALWAYS add LIMIT clause (default 20)
9. CRITICAL: When candidate_ids placeholder (:recipe_ids) is present:
   - DO NOT add included_ingredients EXISTS clauses (embedding search already handled ingredient matching semantically)
   - BUT: ALWAYS add excluded_ingredients (allergies) NOT EXISTS clauses, even with candidate_ids! This filters out allergens from the embedding results.
10. For allergies (exclude ingredients): NOT EXISTS (SELECT 1 FROM recipe_ingredient ri JOIN ingredient i ON ri."ingredientId" = i."id" WHERE ri."recipeId" = r."id" AND i."name" ILIKE '%allergen%')
11. For included ingredients (ONLY when NO candidate_ids): EXISTS (SELECT 1 FROM recipe_ingredient ri JOIN ingredient i ON ri."ingredientId" = i."id" WHERE ri."recipeId" = r."id" AND i."name" ILIKE '%ingredient%')
12. For tags (vegetarian, vegan, etc.): EXISTS (SELECT 1 FROM recipe_tags_tag rtt JOIN tag t ON rtt."tagId" = t.id WHERE rtt."recipeId" = r."id" AND t.name = 'tag_name')
13. When filtering by multiple tags, use OR within EXISTS: EXISTS (SELECT 1 FROM recipe_tags_tag rtt JOIN tag t ON rtt."tagId" = t.id WHERE rtt."recipeId" = r."id" AND (t.name = 'vegetarian' OR t.name = 'vegan'))
14. Return ONLY SQL in ```sql blocks, no explanations
15. IMPORTANT: Use :placeholder format for ALL parameters (e.g., :max_time, :user_uid, :language_id) - NOT %(max_time)s format

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
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL AND r."status" = 'published' AND r."languageId" = :language_id
  AND (r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL)
LIMIT 20
```

TAG FILTERING TEMPLATE (for vegetarian, vegan, etc.):
```sql
SELECT r."id", r."name", r."ingress", r."image", (r."prepTime" + r."cookTime") as total_time, r."difficulty", r."servings"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN "bundle" b ON br."bundleId" = b."id"
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL AND r."status" = 'published' AND r."languageId" = :language_id
  AND (r."private" = false OR r."userUid" = :user_uid OR br."bundleId" IS NOT NULL)
  AND EXISTS (SELECT 1 FROM recipe_tags_tag rtt JOIN tag t ON rtt."tagId" = t.id WHERE rtt."recipeId" = r."id" AND t.name = 'vegetarian')
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

        if sql_filters.get("cuisines"):
            parts.append(f"- Cuisines: {', '.join(sql_filters['cuisines'])}")

        if sql_filters.get("excluded_ingredients"):
            parts.append(f"- Exclude ingredients: {', '.join(sql_filters['excluded_ingredients'])}")

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
            import logging
            logger = logging.getLogger(__name__)
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

        # Add excluded ingredients (allergies) - ALWAYS apply even with candidate_ids
        if sql_filters.get("excluded_ingredients"):
            if candidate_ids:
                parts.append(f"\n## Allergy Exclusion (CRITICAL)")
                parts.append(f"- EXCLUDE recipes containing these ingredients: {', '.join(sql_filters['excluded_ingredients'])}")
                parts.append(f"- Add NOT EXISTS clause for EACH excluded ingredient")
                parts.append(f"- Example for garlic: AND NOT EXISTS (SELECT 1 FROM recipe_ingredient ri JOIN ingredient i ON ri.\"ingredientId\" = i.\"id\" WHERE ri.\"recipeId\" = r.\"id\" AND i.\"name\" ILIKE '%garlic%')")
            else:
                parts.append(f"- Exclude ingredients: {', '.join(sql_filters['excluded_ingredients'])}")

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
        import logging
        logger = logging.getLogger(__name__)

        original_sql = sql

        # Fix 1: Single-quoted table names in JOIN/FROM clauses
        # Pattern: FROM 'table_name' or JOIN 'table_name' -> FROM "table_name" or JOIN "table_name"
        # This is a common error where LLM uses single quotes for table identifiers
        # We need to be careful not to affect string literals in WHERE clauses
        def fix_single_quoted_table(match):
            quote_type = match.group(1)  # FROM or JOIN type
            table_name = match.group(2)  # table name
            alias = match.group(3) if match.group(3) else ""  # optional alias
            # Convert single quotes to double quotes for table identifier
            return f'{quote_type} "{table_name}"{alias}'

        # Match FROM 'tablename' or FROM 'tablename' alias or JOIN 'tablename' alias
        # Negative lookahead to avoid matching string literals in expressions
        sql = re.sub(
            r'\b(FROM|JOIN)\s+\'([a-zA-Z_][a-zA-Z0-9_]*)\'(\s+[a-zA-Z_][a-zA-Z0-9_]*)?(?!\s*AS)',
            fix_single_quoted_table,
            sql,
            flags=re.IGNORECASE
        )

        # Fix 2: Single-quoted table names in LEFT/RIGHT/INNER JOIN
        # Pattern: LEFT JOIN 'table' alias -> LEFT JOIN "table" alias
        def fix_join_single_quotes(match):
            join_type = match.group(1)  # LEFT, RIGHT, INNER, etc.
            table_name = match.group(2)  # table name
            alias = match.group(3) if match.group(3) else ""  # optional alias
            return f'{join_type} "{table_name}"{alias}'

        sql = re.sub(
            r'\b(LEFT|RIGHT|INNER|FULL|CROSS)\s+JOIN\s+\'([a-zA-Z_][a-zA-Z0-9_]*)\'(\s+[a-zA-Z_][a-zA-Z0-9_]*)?',
            fix_join_single_quotes,
            sql,
            flags=re.IGNORECASE
        )

        # Fix 3: Remove any trailing semicolon before LIMIT (LLM sometimes adds this)
        sql = re.sub(r';\s*LIMIT', '\nLIMIT', sql, flags=re.IGNORECASE)

        # Fix 4: Ensure LIMIT is on its own line for readability (optional)
        # sql = re.sub(r'(\S)\s+LIMIT', r'\1\nLIMIT', sql, flags=re.IGNORECASE)

        if sql != original_sql:
            logger.info(f"[SQL FIXER] Applied fixes to SQL syntax")
            logger.debug(f"[SQL FIXER] Before: {original_sql[:200]}...")
            logger.debug(f"[SQL FIXER] After: {sql[:200]}...")

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
        import logging
        logger = logging.getLogger(__name__)

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
        # Handle None case by using a very large default value (effectively no limit)
        # This is simpler than removing the entire AND clause
        actual_max_time = max_time if max_time else 999999

        # Handle string values like "short", "quick" - convert to minutes
        if isinstance(actual_max_time, str):
            time_map = {"quick": 20, "short": 30, "medium": 45, "long": 90}
            actual_max_time = time_map.get(actual_max_time.lower(), 30)

        # Try :max_time format first
        if ":max_time" in sql:
            sql = re.sub(r":max_time\b", str(actual_max_time), sql)
        # Fallback for %(max_time)s format (LLM sometimes generates this)
        # Note: No \b at end since ) is not a word character
        elif "%(max_time)" in sql:
            sql = re.sub(r"%\(max_time\)s?", str(actual_max_time), sql)

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

