"""
Cost and Nutrition Filter Utilities
Extracts cost and nutrition filters from user queries for direct SQL filtering.
These filters bypass embedding search and query recipe_metadata directly.
"""
import re
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum


class CountryCode(str, Enum):
    US = "US"
    INDIA = "India"
    NORWAY = "Norway"


# Currency to Country mapping
CURRENCY_COUNTRY_MAP = {
    # USD patterns
    "$": "US",
    "usd": "US",
    "dollar": "US",
    "dollars": "US",
    "us": "US",
    "usa": "US",
    "america": "US",
    "american": "US",

    # INR patterns
    "₹": "India",
    "inr": "India",
    "rs": "India",
    "rs.": "India",
    "rupee": "India",
    "rupees": "India",
    "india": "India",
    "indian": "India",

    # NOK patterns
    "kr": "Norway",
    "nok": "Norway",
    "krone": "Norway",
    "kroner": "Norway",
    "norway": "Norway",
    "norwegian": "Norway",
}


# Currency symbols by country
CURRENCY_SYMBOLS = {
    "US": "$",
    "India": "₹",
    "Norway": "kr",
}


# Nutrition keyword normalization
NUTRITION_KEYWORDS = {
    # Protein
    "protein": "protein",
    "proteins": "protein",

    # Carbohydrates
    "carb": "carbohydrates",
    "carbs": "carbohydrates",
    "carbohydrate": "carbohydrates",
    "carbohydrates": "carbohydrates",

    # Fat
    "fat": "totalFat",
    "fats": "totalFat",
    "fatty": "totalFat",

    # Calories
    "calorie": "energyKcal",
    "calories": "energyKcal",
    "kcal": "energyKcal",
    "energy": "energyKcal",

    # Fiber
    "fiber": "totalFiber",
    "fibre": "totalFiber",
    "fibers": "totalFiber",

    # Sugar
    "sugar": "totalSugars",
    "sugars": "totalSugars",

    # Sodium
    "sodium": "sodium",
    "salt": "sodium",

    # Cholesterol
    "cholesterol": "cholesterol",
}


# Level keywords mapping to sort order
NUTRITION_LEVELS = {
    # High/rich - sort descending
    "high": "DESC",
    "rich": "DESC",
    "lots": "DESC",
    "more": "DESC",
    "most": "DESC",
    "packed": "DESC",
    "loaded": "DESC",
    "heavy": "DESC",

    # Low/less - sort ascending
    "low": "ASC",
    "less": "ASC",
    "least": "ASC",
    "little": "ASC",
    "minimal": "ASC",
    "light": "ASC",
    "reduced": "ASC",
}


def detect_country_from_currency(query: str) -> Tuple[str, str]:
    """
    Detect country and currency from query text.

    Args:
        query: User's query text

    Returns:
        Tuple of (country_code, currency_symbol)
    """
    query_lower = query.lower()

    # Check for explicit currency symbols
    if "$" in query:
        return "US", "$"
    if "₹" in query:
        return "India", "₹"
    if "kr" in query_lower:
        return "Norway", "kr"

    # Check for currency/country keywords
    for keyword, country in CURRENCY_COUNTRY_MAP.items():
        if keyword in query_lower:
            return country, CURRENCY_SYMBOLS[country]

    # Default to Norway (primary market)
    return "Norway", "kr"


def extract_cost_filter(query: str) -> Optional[Dict[str, Any]]:
    """
    Extract cost filter from user query.

    Patterns supported:
    - "under $20", "below $20", "less than $20"
    - "$20 or less", "20$ max", "budget of $20"
    - "recipes under 500 rupees", "meals under 100 kr"

    Args:
        query: User's query text

    Returns:
        Dictionary with cost filter details or None
    """
    query_lower = query.lower()

    # Detect country and currency
    country, currency_symbol = detect_country_from_currency(query)

    # Patterns for cost extraction
    patterns = [
        # "under $20", "below $20", "less than $20", "cheaper than $20"
        r'(?:under|below|less\s+than|cheaper\s+than|within)\s*[\$₹]?\s*(\d+(?:\.\d+)?)\s*(?:[$₹]|dollars?|rupees?|rs\.?|kr|nok)?',

        # "$20 or less", "20$ max", "20 dollars maximum"
        r'[\$₹]?\s*(\d+(?:\.\d+)?)\s*(?:[$₹]|dollars?|rupees?|rs\.?|kr|nok)?\s*(?:or\s+less|max|maximum|budget)',

        # "budget of $20", "budget 20$"
        r'budget\s*(?:of)?\s*[\$₹]?\s*(\d+(?:\.\d+)?)\s*(?:[$₹]|dollars?|rupees?|rs\.?|kr|nok)?',

        # "for under 20", "for less than 20"
        r'for\s+(?:under|less\s+than)\s*[\$₹]?\s*(\d+(?:\.\d+)?)',

        # "20 dollar recipes", "500 rupee meals"
        r'(\d+(?:\.\d+)?)\s*(?:dollar|rupee|kr)\s*(?:recipes?|meals?|dishes?|options?)',

        # "can I make under 20", "make for under 20"
        r'(?:can\s+i\s+)?make\s+(?:for\s+)?(?:under|less\s+than)\s*[\$₹]?\s*(\d+(?:\.\d+)?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            try:
                value = float(match.group(1))
                return {
                    "operator": "<=",
                    "value": value,
                    "country": country,
                    "currency_symbol": currency_symbol
                }
            except (ValueError, IndexError):
                continue

    return None


def extract_nutrition_filter(query: str) -> Optional[Dict[str, Any]]:
    """
    Extract nutrition filter from user query for recipe search.

    Patterns supported:
    - "high protein recipes", "low carb meals"
    - "protein rich", "carb light"
    - "recipes with high protein", "low sugar options"

    Args:
        query: User's query text

    Returns:
        Dictionary with nutrition filter details or None
    """
    query_lower = query.lower()

    result = {
        "nutrients": [],
        "sort_by": None,
        "order": None,
        "nutrient_key": None  # The actual JSON key in recipe_metadata
    }

    # Check for level + nutrient patterns
    for level_word, order in NUTRITION_LEVELS.items():
        for keyword, nutrient_key in NUTRITION_KEYWORDS.items():
            # Patterns like "high protein", "low carb", "protein rich"
            patterns = [
                rf'\b{level_word}\s+{keyword}\b',
                rf'\b{keyword}\s+{level_word}\b',
                rf'\b{level_word}\s+in\s+{keyword}\b',
                rf'\b{keyword}[-\s]?{level_word}\b',
            ]

            for pattern in patterns:
                if re.search(pattern, query_lower):
                    nutrient_info = {
                        "name": keyword,
                        "key": nutrient_key,
                        "level": level_word,
                        "order": order
                    }

                    # Avoid duplicates
                    if not any(n["key"] == nutrient_key for n in result["nutrients"]):
                        result["nutrients"].append(nutrient_info)

                    # Set primary sort (first match wins)
                    if not result["sort_by"]:
                        result["sort_by"] = keyword
                        result["order"] = order
                        result["nutrient_key"] = nutrient_key

    if result["nutrients"]:
        return result

    return None


def extract_ingredient_query_info(query: str) -> Optional[Dict[str, Any]]:
    """
    Extract ingredient-specific price or nutrition query information.

    Patterns:
    - "price of eggs in India"
    - "how much does chicken cost"
    - "carbs in banana"
    - "protein content in eggs"

    Args:
        query: User's query text

    Returns:
        Dictionary with query type and ingredient info or None
    """
    query_lower = query.lower()

    result = {
        "type": None,  # "price" or "nutrition"
        "ingredient": None,
        "country": None,
        "nutrient": None,
        "nutrient_key": None
    }

    # Price query patterns
    price_patterns = [
        # "price of X in Y", "cost of X in Y"
        r'(?:price|cost)\s+(?:of\s+)?([a-z]+(?:\s+[a-z]+)?)\s+(?:in\s+)?(india|us|usa|norway|america)?',

        # "how much does X cost", "what is the price of X"
        r'(?:how\s+much|what)\s+(?:does|is)\s+(?:the\s+)?(?:price\s+of\s+)?([a-z]+(?:\s+[a-z]+)?)\s+(?:cost|price)?\s*(?:in\s+)?(india|us|usa|norway|america)?',

        # "X price in Y"
        r'([a-z]+(?:\s+[a-z]+)?)\s+price\s+(?:in\s+)?(india|us|usa|norway|america)?',
    ]

    for pattern in price_patterns:
        match = re.search(pattern, query_lower)
        if match:
            result["type"] = "price"
            result["ingredient"] = match.group(1).strip()
            if len(match.groups()) > 1 and match.group(2):
                country_lower = match.group(2).lower()
                country_map = {
                    "india": "India",
                    "us": "US",
                    "usa": "US",
                    "america": "US",
                    "norway": "Norway"
                }
                result["country"] = country_map.get(country_lower, "Norway")
            return result

    # Nutrition query patterns for ingredients
    nutrition_patterns = [
        # "how many carbs in X", "how much protein in X"
        r'(?:how\s+many|how\s+much|what)\s+(?:is\s+the\s+)?([a-z]+)\s+(?:content\s+)?(?:in|of)\s+([a-z]+(?:\s+[a-z]+)?)',

        # "protein content in X", "carb amount in X"
        r'([a-z]+)\s+(?:content|amount|value)\s+(?:in|of)\s+([a-z]+(?:\s+[a-z]+)?)',

        # "nutrition of X", "nutritional info for X"
        r'(?:nutrition|nutritional|macros?|micros?)\s+(?:info(?:rmation)?\s+)?(?:of|in|for)\s+([a-z]+(?:\s+[a-z]+)?)',
    ]

    for pattern in nutrition_patterns:
        match = re.search(pattern, query_lower)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                potential_nutrient = groups[0].lower()
                if potential_nutrient in NUTRITION_KEYWORDS:
                    result["type"] = "nutrition"
                    result["nutrient"] = potential_nutrient
                    result["nutrient_key"] = NUTRITION_KEYWORDS[potential_nutrient]
                    result["ingredient"] = groups[1].strip()
                    return result
            elif len(groups) == 1:
                # General nutrition query
                result["type"] = "nutrition"
                result["ingredient"] = groups[0].strip()
                return result

    return None


def is_cost_nutrition_filter_query(intent: str, query: str) -> bool:
    """
    Check if the query should skip embedding search and go directly to SQL.

    Args:
        intent: Detected intent from NLID
        query: User's query text

    Returns:
        True if query should use direct SQL (no embedding search)
    """
    # Intents that always skip embedding
    skip_embedding_intents = [
        "cost_filter",
        "price_filter",
        "nutrition_filter",
        "budget_filter",
        "ingredient_price",
        "ingredient_nutrition",
    ]

    if intent in skip_embedding_intents:
        return True

    # Check query text for cost/nutrition patterns
    cost_filter = extract_cost_filter(query)
    if cost_filter:
        return True

    nutrition_filter = extract_nutrition_filter(query)
    if nutrition_filter:
        return True

    return False


def get_recipe_metadata_nutrition_key(nutrient_name: str) -> str:
    """
    Get the JSON key path for a nutrient in recipe_metadata.

    Args:
        nutrient_name: Common nutrient name (protein, carbs, etc.)

    Returns:
        JSON key for recipe_metadata->nutrition
    """
    return NUTRITION_KEYWORDS.get(nutrient_name.lower(), nutrient_name)


def get_recipe_metadata_pricing_key(country: str) -> str:
    """
    Get the JSON key path for pricing in recipe_metadata.

    Args:
        country: Country code (US, India, Norway)

    Returns:
        JSON key for recipe_metadata->pricing
    """
    return country
