"""
Retrieval Strategy Decision Stage
Decides whether to use SQL-only, Embeddings-only, or Hybrid retrieval
"""
from typing import Dict, Any, Literal, Optional, List
from enum import Enum
from dataclasses import dataclass


class RetrievalStrategy(str, Enum):
    """Retrieval strategy types"""
    SQL_ONLY = "sql_only"
    EMBEDDINGS_ONLY = "embeddings_only"
    HYBRID_VECTOR_TO_SQL = "hybrid_vector_to_sql"  # Vector search first, then SQL filter
    HYBRID_SQL_TO_VECTOR = "hybrid_sql_to_vector"  # SQL filter first, then vector re-rank


@dataclass
class RetrievalPlan:
    """
    Retrieval execution plan

    Created by Decision Stage, consumed by Execution Stage
    """
    strategy: RetrievalStrategy
    reasoning: str
    vector_query: Optional[str] = None  # Query for embedding search
    sql_filters: Dict[str, Any] = None  # Filters for SQL
    top_k: int = 20  # Number of candidates to retrieve
    use_reranking: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "strategy": self.strategy.value,
            "reasoning": self.reasoning,
            "vector_query": self.vector_query,
            "sql_filters": self.sql_filters or {},
            "top_k": self.top_k,
            "use_reranking": self.use_reranking
        }


class RetrievalStrategyDecider:
    """
    Decision Stage for Retrieval Strategy

    Determines the best retrieval approach based on:
    - Query structure (structured vs fuzzy)
    - Available filters
    - Intent type
    """

    # Keywords that indicate structured queries
    STRUCTURED_KEYWORDS = [
        "under", "less than", "more than", "minutes", "easy", "medium", "hard",
        "servings", "portion", "vegetarian", "vegan", "gluten-free", "dairy-free",
        "quick", "slow", "no ", "without", "except", "allergic"
    ]

    # Keywords that indicate fuzzy/semantic queries
    FUZZY_KEYWORDS = [
        "healthy", "delicious", "tasty", "yummy", "comforting", "refreshing",
        "light", "heavy", "rich", "simple", "elegant", "festive", "hearty",
        "ideas", "suggestions", "recommend", "inspiration"
    ]

    # Direct recipe search patterns
    DIRECT_RECIPE_PATTERNS = [
        "how to make", "how do i make", "recipe for", "show me recipe",
        "i want to cook", "i want to make", "can you teach me"
    ]

    def __init__(self):
        pass

    def decide_strategy(
        self,
        query: str,
        nlid_result: Dict[str, Any],
        session_context: Dict[str, Any]
    ) -> RetrievalPlan:
        """
        Decide the retrieval strategy based on query analysis

        Args:
            query: User's query text
            nlid_result: Result from NLID agent
            session_context: Session context with filters

        Returns:
            RetrievalPlan with strategy and execution details
        """
        query_lower = query.lower()

        # Extract components
        intent = nlid_result.get("intent", "general_chat")
        entities = nlid_result.get("entities", {})
        parameters = nlid_result.get("parameters", {})
        filters = nlid_result.get("filters", {})

        # Check for direct recipe search
        if self._is_direct_recipe_search(query):
            return RetrievalPlan(
                strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                reasoning="Direct recipe request - using semantic search with SQL filters",
                vector_query=query,
                sql_filters=self._build_sql_filters(parameters, filters, session_context),
                top_k=20
            )

        # Check for structured query with multiple constraints
        structured_score = self._calculate_structured_score(query, parameters, filters)
        fuzzy_score = self._calculate_fuzzy_score(query)

        # Decision logic
        if structured_score >= 3 and fuzzy_score >= 2:
            # Both structured and fuzzy elements → Hybrid
            # Use vector first for candidate selection, SQL for hard constraints
            return RetrievalPlan(
                strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                reasoning=f"Query has both structured (score:{structured_score}) and fuzzy (score:{fuzzy_score}) elements. Using vector search for relevance, SQL for constraints.",
                vector_query=query,
                sql_filters=self._build_sql_filters(parameters, filters, session_context),
                top_k=20
            )

        elif structured_score >= 3:
            # Primarily structured → SQL-only (or SQL → Vector rerank)
            # If we have tight filters, SQL can efficiently narrow down
            if self._has_tight_filters(parameters, filters):
                return RetrievalPlan(
                    strategy=RetrievalStrategy.SQL_ONLY,
                    reasoning=f"Query has tight structured filters (score:{structured_score}). SQL-only is sufficient.",
                    sql_filters=self._build_sql_filters(parameters, filters, session_context),
                    top_k=20
                )
            else:
                # Has structure but filters are loose → Hybrid (SQL → Vector)
                return RetrievalPlan(
                    strategy=RetrievalStrategy.HYBRID_SQL_TO_VECTOR,
                    reasoning=f"Query has structured elements (score:{structured_score}) but filters are loose. Using SQL to narrow, vector to re-rank.",
                    vector_query=query,
                    sql_filters=self._build_sql_filters(parameters, filters, session_context),
                    top_k=50,  # Get more candidates for re-ranking
                    use_reranking=True
                )

        elif fuzzy_score >= 2:
            # Primarily fuzzy/semantic → Embeddings-only
            return RetrievalPlan(
                strategy=RetrievalStrategy.EMBEDDINGS_ONLY,
                reasoning=f"Query is primarily fuzzy/semantic (score:{fuzzy_score}). Using embedding search for relevance.",
                vector_query=query,
                sql_filters={},  # Minimal filtering
                top_k=20
            )

        else:
            # Unclear → Default to Hybrid (safest approach)
            return RetrievalPlan(
                strategy=RetrievalStrategy.HYBRID_VECTOR_TO_SQL,
                reasoning="Query intent is unclear. Using hybrid approach for best results.",
                vector_query=query,
                sql_filters=self._build_sql_filters(parameters, filters, session_context),
                top_k=20
            )

    def _is_direct_recipe_search(self, query: str) -> bool:
        """Check if query is a direct recipe search"""
        query_lower = query.lower()
        return any(pattern in query_lower for pattern in self.DIRECT_RECIPE_PATTERNS)

    def _calculate_structured_score(
        self,
        query: str,
        parameters: Dict[str, Any],
        filters: Dict[str, Any]
    ) -> int:
        """
        Calculate how structured the query is

        Returns score from 0-5
        """
        score = 0
        query_lower = query.lower()

        # Check for structured keywords
        for keyword in self.STRUCTURED_KEYWORDS:
            if keyword in query_lower:
                score += 1
                break  # Count once for presence of structured keywords

        # Check for explicit time constraints
        if parameters.get("max_time"):
            score += 1

        # Check for difficulty constraint
        if parameters.get("difficulty"):
            score += 1

        # Check for excluded ingredients (allergies)
        if filters.get("excluded_ingredients"):
            score += 1

        # Check for dietary restrictions (structured)
        dietary = filters.get("tags", [])
        structured_dietary = ["vegetarian", "vegan", "gluten-free", "dairy-free", "keto"]
        if any(d in dietary for d in structured_dietary):
            score += 1

        return min(score, 5)

    def _calculate_fuzzy_score(self, query: str) -> int:
        """
        Calculate how fuzzy/semantic the query is

        Returns score from 0-5
        """
        score = 0
        query_lower = query.lower()

        # Check for fuzzy keywords
        for keyword in self.FUZZY_KEYWORDS:
            if keyword in query_lower:
                score += 1

        # Check for concept-based queries
        concept_indicators = ["like", "similar to", "something", "anything", "ideas"]
        for indicator in concept_indicators:
            if indicator in query_lower:
                score += 1
                break

        # Check for ingredient-only queries (fuzzy - "chicken recipes")
        # Not structured if no constraints
        words = query_lower.split()
        if len(words) <= 3:  # Short query likely fuzzy
            score += 1

        return min(score, 5)

    def _has_tight_filters(
        self,
        parameters: Dict[str, Any],
        filters: Dict[str, Any]
    ) -> bool:
        """
        Check if filters are tight enough for SQL-only

        Tight filters = multiple constraints that SQL can handle efficiently
        """
        tight_filter_count = 0

        if parameters.get("max_time"):
            tight_filter_count += 1

        if parameters.get("difficulty"):
            tight_filter_count += 1

        if parameters.get("servings"):
            tight_filter_count += 1

        if filters.get("tags"):
            tight_filter_count += 1

        if filters.get("excluded_ingredients"):
            tight_filter_count += 1

        # Need at least 2 tight filters for SQL-only
        return tight_filter_count >= 2

    def _build_sql_filters(
        self,
        parameters: Dict[str, Any],
        filters: Dict[str, Any],
        session_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build SQL filters from parameters, filters, and session context

        Returns dictionary of filters for SQL generation
        """
        sql_filters = {}

        # From parameters
        if parameters.get("max_time"):
            sql_filters["max_time"] = parameters["max_time"]

        if parameters.get("difficulty"):
            sql_filters["difficulty"] = parameters["difficulty"]

        if parameters.get("servings"):
            sql_filters["servings"] = parameters["servings"]

        # From NLID filters
        if filters.get("tags"):
            sql_filters["tags"] = filters["tags"]

        if filters.get("include_ingredients"):
            sql_filters["included_ingredients"] = filters["include_ingredients"]

        if filters.get("included_ingredients"):
            sql_filters["included_ingredients"] = filters["included_ingredients"]

        if filters.get("excluded_ingredients"):
            sql_filters["excluded_ingredients"] = filters["excluded_ingredients"]

        if filters.get("cuisines"):
            sql_filters["cuisines"] = filters["cuisines"]

        if filters.get("seasonality"):
            sql_filters["seasonality"] = filters["seasonality"]

        if filters.get("regional"):
            sql_filters["region"] = filters["regional"]

        # From session context (persistent across turns)
        session_filters = session_context.get("filters", {})

        if session_filters.get("tags"):
            existing = sql_filters.get("tags", [])
            sql_filters["tags"] = list(set(existing + session_filters["tags"]))

        if session_filters.get("cuisines"):
            existing = sql_filters.get("cuisines", [])
            sql_filters["cuisines"] = list(set(existing + session_filters["cuisines"]))

        if session_filters.get("max_time"):
            # Use the stricter (smaller) time constraint
            existing = sql_filters.get("max_time")
            if existing:
                sql_filters["max_time"] = min(existing, session_filters["max_time"])
            else:
                sql_filters["max_time"] = session_filters["max_time"]

        if session_filters.get("difficulty"):
            existing = sql_filters.get("difficulty")
            if existing:
                # Keep the first specified difficulty
                sql_filters["difficulty"] = existing
            else:
                sql_filters["difficulty"] = session_filters["difficulty"]

        # Session excluded ingredients (allergies) - always apply
        session_excluded = session_context.get("excluded_ingredients", [])
        if session_excluded:
            existing = sql_filters.get("excluded_ingredients", [])
            sql_filters["excluded_ingredients"] = list(set(existing + session_excluded))

        # Session included ingredients
        session_included = session_context.get("included_ingredients", [])
        if session_included:
            sql_filters["included_ingredients"] = session_included

        return sql_filters


# Convenience function for quick decision making
def decide_retrieval_strategy(
    query: str,
    nlid_result: Dict[str, Any],
    session_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Convenience function to decide retrieval strategy

    Returns retrieval plan as dictionary
    """
    decider = RetrievalStrategyDecider()
    plan = decider.decide_strategy(query, nlid_result, session_context)
    return plan.to_dict()
