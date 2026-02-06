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
3. Always filter out: private=true recipes (unless user is creator), deletedAt IS NOT NULL
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

When in doubt, use double quotes around ALL column names: r."name", r."id", etc.

COMMON PATTERNS:
- Time filter: (r."prepTime" + r."cookTime") <= :max_time
- Ingredient exclusion: NOT EXISTS (SELECT 1 FROM recipe_ingredient ri2 JOIN ingredient i2 ON ri2."ingredientId" = i2."id" WHERE ri2."recipeId" = r."id" AND i2."name" ILIKE '%peanut%')
- Bundle access check: LEFT JOIN bundle_recipe br ON r."id" = br."recipeId" LEFT JOIN user_purchase up ON br."bundleId" = up."bundleId" AND up."userUid" = :user_uid
- Tag filter: JOIN recipe_tags_tag rtt ON r."id" = rtt."recipeId" JOIN tag t ON rtt."tagId" = t."id" WHERE t."name" IN (:tags)

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
            parts.append(f"- Include ingredients: {', '.join(sql_filters['included_ingredients'])}")

        if session_context.get("user_uid"):
            parts.append(f"- User ID: {session_context['user_uid']}")
            parts.append("- Include user's liked recipes and created recipes in results")
        else:
            parts.append("- Anonymous user (no personalization)")

        # Add candidate restriction for hybrid
        if candidate_ids:
            parts.append(f"\n## Candidate Restriction")
            parts.append(f"- ONLY these recipe IDs: {', '.join(candidate_ids[:10])}")
            parts.append(f"- Use WHERE r.id IN (...) with these IDs")

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
        sql_generation_result: SQLGenerationResult
    ) -> Dict[str, Any]:
        """
        Execute SQL with automatic fallback on error

        Args:
            sql_generation_result: Result from SQL generation stage

        Returns:
            Execution results or fallback results
        """
        if not sql_generation_result.is_safe:
            # Return error result
            return {
                "success": False,
                "error": "SQL validation failed",
                "explanation": sql_generation_result.explanation,
                "fallback_used": True
            }

        # Try executing generated SQL
        result = self.execute_sql(sql_generation_result.sql)

        if not result["success"]:
            # Fallback: simple SELECT
            fallback_sql = self._generate_fallback_sql()
            fallback_result = self.execute_sql(fallback_sql)

            return {
                **fallback_result,
                "original_error": result["error"],
                "fallback_used": True,
                "explanation": f"Original query failed, using fallback. {sql_generation_result.explanation}"
            }

        result["explanation"] = sql_generation_result.explanation
        result["fallback_used"] = False

        return result

    def _generate_fallback_sql(self) -> str:
        """Generate safe fallback SQL query"""
        return """
        SELECT DISTINCT r."id", r."name", r."ingress", r."difficulty",
               (r."prepTime" + r."cookTime") as total_time,
               r."image", r."servings"
        FROM recipe r
        WHERE r."deletedAt" IS NULL
          AND r."private" = false
          AND r.embedding IS NOT NULL
        LIMIT 20
        """
