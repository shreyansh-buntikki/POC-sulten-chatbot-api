"""
SQL Generation and Validation Service
Generates SQL queries based on natural language and schema
Validates generated SQL before execution
"""
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from apps.fastapi.src.services.schema_understanding import get_schema_service, RelevantSchema


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

        # Generate SQL using LLM
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
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
            max_tokens=800
        )

        generated_text = response.choices[0].message.content

        # Extract SQL from response
        sql = self._extract_sql_from_response(generated_text)

        # Substitute placeholders with actual values
        sql = self._substitute_placeholders(sql, candidate_ids, session_context, sql_filters)

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
        """Get system prompt for SQL generation"""
        return """You are a SQL expert for a recipe and cooking platform database.

Generate PostgreSQL SQL queries based on the user's request and provided schema.

IMPORTANT RULES:
1. ALWAYS include LIMIT clause (default 20, max 100)
2. Use table JOINs for related data
3. Access Control: ALLOW bundle recipes (private=true but in bundle_recipe table), exclude other private recipes
4. Use ILIKE for case-insensitive string matching
5. Use parameterized patterns (do not hardcode values)
6. Return ONLY the SQL query, no explanations
7. Use proper PostgreSQL syntax

CRITICAL: Column names are case-sensitive in PostgreSQL. You MUST use double quotes for column names that contain mixed case:
- "prepTime" (not prepTime or preptime)
- "cookTime" (not cookTime or cooktime)
- "createdAt" (not createdAt or createdat)
- "updatedAt" (not updatedAt or updatedat)
- "deletedAt" (not deletedAt or deletedat)
- "publishedAt" (not publishedAt or publishedat)
- "userUid" (not userUid or useruid)
- "languageId" (not languageId or languageid)
- "recipeId" (not recipeId or recipeid)

CRITICAL JOIN RULE: In ALL JOIN conditions, you MUST quote EVERY column name from BOTH tables:
- WRONG: LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr.userUid = '...'
- CORRECT: LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = '...'

When in doubt, use double quotes around ALL column names: r."name", r."id", ulr."userUid", etc.

ACCESS CONTROL PATTERN (CRITICAL - Bundle recipes must be included):
```sql
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN bundle b ON br."bundleId" = b."id"
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'  -- ONLY return published recipes, never draft
  AND (
    -- Public recipes (not private)
    r."private" = false
    -- OR user created this recipe
    OR r."userUid" = :user_uid
    -- OR recipe is in a bundle owned by user (b."userUid" = :user_uid)
    -- OR recipe is in a bundle (show name even if not owned - access level handled in app)
    OR br."bundleId" IS NOT NULL
  )
```

INGREDIENT FILTERING (CRITICAL):
When candidate_ids are provided (from semantic embedding search), DO NOT add ingredient EXISTS clauses.
The semantic search already found relevant recipes - adding ingredient filtering would be redundant.

ONLY use ingredient EXISTS clauses when:
- No candidate_ids are provided (pure SQL search)
- Filtering by excluded_ingredients (allergies)

IMPORTANT: Synonyms are alternative names for the SAME ingredient. Use OR, not AND:
- WRONG: EXISTS (... ILIKE '%chole%') AND EXISTS (... ILIKE '%chickpeas%')
- CORRECT: EXISTS (... WHERE i."name" ILIKE '%chole%' OR i."name" ILIKE '%chickpeas%')

EXAMPLE: When candidate_ids are provided (from semantic search), DO NOT add ingredient EXISTS:
```sql
SELECT r."id", r."name", r."slug", r."ingress", r."image"
FROM recipe r
LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
LEFT JOIN bundle b ON br."bundleId" = b."id"
LEFT JOIN user_likes_recipe ulr ON r."id" = ulr."recipeId" AND ulr."userUid" = :user_uid
WHERE r."deletedAt" IS NULL
  AND r."status" = 'published'
  AND r."languageId" = :language_id
  AND (
    r."private" = false
    OR r."userUid" = :user_uid
    OR br."bundleId" IS NOT NULL
  )
  AND r."id" IN (:recipe_ids)  -- Only these semantic matches, NO ingredient EXISTS needed!
LIMIT 20;
```

Pattern for single ingredient or its synonyms:
```sql
JOIN recipe_ingredient ri ON r."id" = ri."recipeId" AND ri."deletedAt" IS NULL
JOIN ingredient i ON ri."ingredientId" = i."id"
WHERE i."name" ILIKE '%ingredient_name%'
```

For ingredient with synonyms (use OR within a single EXISTS):
```sql
WHERE EXISTS (
  SELECT 1 FROM recipe_ingredient ri
  JOIN ingredient i ON ri."ingredientId" = i."id"
  WHERE ri."recipeId" = r."id"
    AND (i."name" ILIKE '%chole%' OR i."name" ILIKE '%chickpeas%' OR i."name" ILIKE '%garbanzo beans%')
)
```

For DIFFERENT ingredients (must have ALL present), use multiple EXISTS with AND:
```sql
WHERE EXISTS (
  SELECT 1 FROM recipe_ingredient ri1 JOIN ingredient i1 ON ri1."ingredientId" = i1."id"
  WHERE ri1."recipeId" = r."id" AND i1."name" ILIKE '%ingredient1%'
)
AND EXISTS (
  SELECT 1 FROM recipe_ingredient ri2 JOIN ingredient i2 ON ri2."ingredientId" = i2."id"
  WHERE ri2."recipeId" = r."id" AND i2."name" ILIKE '%ingredient2%'
)
```

OTHER COMMON PATTERNS:
- Time filter: (r."prepTime" + r."cookTime") <= :max_time
- Language filter (CRITICAL): r."languageId" = :language_id -- MUST match the header value exactly
- Status filter (CRITICAL): r."status" = 'published' -- NEVER return draft recipes
- Ingredient exclusion: NOT EXISTS (SELECT 1 FROM recipe_ingredient ri2 JOIN ingredient i2 ON ri2."ingredientId" = i2."id" WHERE ri2."recipeId" = r."id" AND i2."name" ILIKE '%peanut%')
- Tag filter: JOIN recipe_tags_tag rtt ON r."id" = rtt."recipeId" JOIN tag t ON rtt."tagId" = t."id" WHERE t."name" IN (:tags)

MANDATORY FILTERS (ALWAYS INCLUDE IN EVERY QUERY):
1. r."deletedAt" IS NULL
2. r."status" = 'published' (ONLY published recipes, NEVER draft)
3. Access control pattern as shown above
4. Language filter when :language_id is provided

Return the SQL query only, wrapped in ```sql ... ``` markdown blocks."""

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

        if sql_filters.get("included_ingredients"):
            ingredients = sql_filters['included_ingredients']
            parts.append(f"- Include ingredients: {', '.join(ingredients)}")
            parts.append(f"  CRITICAL: These are SYNONYMS (alternative names for the same ingredient).")

            # Only add EXISTS clause instruction if we DON'T have candidate_ids
            # When candidate_ids are provided, the semantic search already found relevant recipes
            # Adding EXISTS clause would be redundant and too restrictive
            if not candidate_ids:
                # Build example with available ingredients (handle single ingredient case)
                if len(ingredients) >= 2:
                    example = f"  Use OR within a single EXISTS clause: EXISTS (... WHERE i.\"name\" ILIKE '%{ingredients[0]}%' OR i.\"name\" ILIKE '%{ingredients[1]}%' ...)"
                else:
                    example = f"  Use EXISTS clause: EXISTS (... WHERE i.\"name\" ILIKE '%{ingredients[0]}%')"
                parts.append(example)
                parts.append(f"  DO NOT use multiple EXISTS with AND - that requires ALL ingredients to be present!")
            else:
                parts.append(f"  NOTE: Ingredient EXISTS clause NOT needed - candidate_ids already filtered by semantic search")

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
            parts.append(f"- DO NOT manually list UUIDs - use the placeholder!")

        parts.append("\n## Task")
        parts.append("Generate a PostgreSQL SELECT query to retrieve recipes matching the criteria above.")
        parts.append("Return only the SQL query wrapped in ```sql ``` blocks.")

        return "\n".join(parts)

    def _substitute_placeholders(
        self,
        sql: str,
        candidate_ids: Optional[List[str]],
        session_context: Dict[str, Any],
        sql_filters: Dict[str, Any]
    ) -> str:
        """Substitute placeholders in SQL with actual values"""
        import re

        # Substitute :recipe_ids placeholder
        if candidate_ids and ":recipe_ids" in sql:
            formatted_ids = ", ".join(f"'{cid}'" for cid in candidate_ids)
            sql = re.sub(r":recipe_ids\b", formatted_ids, sql)

        # Substitute :user_uid placeholder
        user_uid = session_context.get("user_uid")
        if user_uid and ":user_uid" in sql:
            sql = re.sub(r":user_uid\b", f"'{user_uid}'", sql)

        # Substitute :max_time placeholder
        max_time = sql_filters.get("max_time")
        if max_time and ":max_time" in sql:
            # Handle string values like "short", "quick" - convert to minutes
            if isinstance(max_time, str):
                time_map = {"quick": 20, "short": 30, "medium": 45, "long": 90}
                max_time = time_map.get(max_time.lower(), 30)
            sql = re.sub(r":max_time\b", str(max_time), sql)

        # Substitute :language_id placeholder
        language = session_context.get("language")
        if language and ":language_id" in sql:
            sql = re.sub(r":language_id\b", f"'{language}'", sql)

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
            params: Query parameters (for logging only, SQL is pre-parameterized)

        Returns:
            Dictionary with results and metadata
        """
        try:
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

    def execute_with_fallback(
        self,
        sql_generation_result: SQLGenerationResult,
        language_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute SQL with automatic fallback on error

        Args:
            sql_generation_result: Result from SQL generation stage
            language_id: Optional language ID filter (e.g., 'en', 'no')

        Returns:
            Execution results or fallback results
        """
        import logging
        logger = logging.getLogger(__name__)

        if not sql_generation_result.is_safe:
            # Return error result
            logger.error(f"[SQL EXECUTOR] SQL validation failed - using fallback. language_id={language_id}")
            return {
                "success": False,
                "error": "SQL validation failed",
                "explanation": sql_generation_result.explanation,
                "fallback_used": True
            }

        # Try executing generated SQL
        logger.warning(f"[SQL EXECUTOR] Executing generated SQL... language_id={language_id}")
        result = self.execute_sql(sql_generation_result.sql)

        if not result["success"]:
            # Log the failure
            logger.error(f"[SQL EXECUTOR] ❌ SQL EXECUTION FAILED - switching to FALLBACK")
            logger.error(f"[SQL EXECUTOR] Error: {result.get('error')}")
            logger.error(f"[SQL EXECUTOR] Failed SQL (first 200 chars): {sql_generation_result.sql[:200]}")

            # Fallback: simple SELECT with language filter
            fallback_sql = self._generate_fallback_sql(language_id)
            logger.warning(f"[SQL EXECUTOR] Using FALLBACK SQL... language_id={language_id}")
            logger.warning(f"[SQL EXECUTOR] Fallback SQL (first 200 chars): {fallback_sql[:200]}")

            fallback_result = self.execute_sql(fallback_sql)

            if not fallback_result["success"]:
                logger.error(f"[SQL EXECUTOR] ❌ FALLBACK SQL ALSO FAILED: {fallback_result.get('error')}")
            else:
                logger.warning(f"[SQL EXECUTOR] ✓ Fallback SQL succeeded - returned {fallback_result.get('row_count', 0)} rows")

            return {
                **fallback_result,
                "original_error": result["error"],
                "fallback_used": True,
                "explanation": f"Original query failed, using fallback. {sql_generation_result.explanation}"
            }

        logger.warning(f"[SQL EXECUTOR] ✓ Generated SQL succeeded - returned {result.get('row_count', 0)} rows")
        result["explanation"] = sql_generation_result.explanation
        result["fallback_used"] = False

        return result

    def _generate_fallback_sql(self, language_id: Optional[str] = None) -> str:
        """Generate safe fallback SQL query"""
        # Build WHERE clause conditions
        where_conditions = [
            'r."deletedAt" IS NULL',
            'r."status" = \'published\'',
            '(r."private" = false OR br."bundleId" IS NOT NULL)',
            'r.embedding IS NOT NULL'
        ]

        # Add language filter if provided
        if language_id:
            where_conditions.insert(1, f'r."languageId" = \'{language_id}\'')

        where_clause = '\n  AND '.join(where_conditions)

        return f"""
        SELECT DISTINCT r."id", r."name", r."ingress", r."difficulty",
               (r."prepTime" + r."cookTime") as total_time,
               r."image", r."servings"
        FROM recipe r
        LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" AND br."deletedAt" IS NULL
        WHERE {where_clause}
        LIMIT 20
        """
