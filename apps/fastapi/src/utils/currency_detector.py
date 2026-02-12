"""
Currency Detection Utility
Detects currency mentions in user queries and maps to country codes for pricing queries.
"""
import re
from typing import Dict, Tuple, Optional, Any
from enum import Enum


class CountryCode(str, Enum):
    US = "US"
    INDIA = "India"
    NORWAY = "Norway"


class CurrencyInfo:
    def __init__(self, code: str, symbol: str, country: str, country_code: str):
        self.code = code
        self.symbol = symbol
        self.country = country
        self.country_code = country_code


# Currency mapping for the 3 supported countries
CURRENCY_MAP = {
    'USD': CurrencyInfo('USD', '$', 'United States', 'US'),
    'INR': CurrencyInfo('INR', '₹', 'India', 'India'),
    'NOK': CurrencyInfo('NOK', 'kr', 'Norway', 'Norway'),
}


# Pattern matching for currency detection
CURRENCY_PATTERNS = [
    # USD patterns
    (r'(?:\b|$)(20|50|100)?\s*\$[\d.,]+(?:\s*(dollars?|bucks?|usd))?', 'USD'),
    (r'\b(?:under|less than|below)\s*\$[\d.,]+', 'USD'),
    (r'\$(?:20|50|100)[\d.,]*', 'USD'),
    (r'\b(?:dollars?|bucks?|usd)\s*[\d.,]+', 'USD'),

    # INR patterns
    (r'(?:\b|$)(20|50|100)?\s*₹[\d.,]+(?:\s*(rupees?|rs\.?|inr))?', 'INR'),
    (r'\b(?:under|less than|below)\s*₹[\d.,]+', 'INR'),
    (r'₹(?:20|50|100)[\d.,]*', 'INR'),
    (r'\b(?:rupees?|rs\.?|inr)\s*[\d.,]+', 'INR'),

    # NOK patterns
    (r'(?:\b|$)(20|50|100)?\s*kr[\d.,]+(?:\s*(nok|norwegian\s+krone))?', 'NOK'),
    (r'\b(?:under|less than|below)\s*kr[\d.,]+', 'NOK'),
    (r'kr(?:20|50|100)[\d.,]*', 'NOK'),
    (r'\b(?:nok|norwegian\s+krone)\s*[\d.,]+', 'NOK'),
]


# Country name patterns
COUNTRY_PATTERNS = {
    'US': [r'\b(?:usa?|america?|united\s+states?)\b', r'\b(?:us|u\.s\.?)\b'],
    'India': [r'\bindia?', r'\b(?:in|india)\b'],
    'Norway': [r'\bnorway?', r'\b(?:no|norway)\b']
}


def detect_currency(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Detect currency from user query.

    Args:
        query: User's query text

    Returns:
        Tuple of (currency_code, country_code) or (None, None)
    """
    query_lower = query.lower()

    # Check for currency symbols and amounts
    for pattern, currency_code in CURRENCY_PATTERNS:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            return currency_code, CURRENCY_MAP[currency_code].country_code

    # Check for country mentions
    for country_code, patterns in COUNTRY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                # Default to USD if no currency specified but country mentioned
                return 'USD', country_code

    # Default to USD if no currency detected
    return 'USD', 'US'


def extract_price_amount(query: str, currency_code: str) -> Optional[float]:
    """
    Extract price amount from query based on currency.

    Args:
        query: User's query text
        currency_code: Currency code (USD, INR, NOK)

    Returns:
        Price amount as float or None
    """
    currency_info = CURRENCY_MAP.get(currency_code)
    if not currency_info:
        return None

    # Pattern for the specific currency
    if currency_code == 'USD':
        pattern = r'\$?([\d.,]+)'
    elif currency_code == 'INR':
        pattern = r'₹?([\d.,]+)'
    elif currency_code == 'NOK':
        pattern = r'kr?([\d.,]+)'
    else:
        pattern = r'([\d.,]+)'

    # Look for price keywords with amounts
    price_patterns = [
        rf'under\s+{currency_info.symbol}?([\d.,]+)',
        rf'less\s+than\s+{currency_info.symbol}?([\d.,]+)',
        rf'below\s+{currency_info.symbol}?([\d.,]+)',
        rf'{currency_info.symbol}?([\d.,]+)\s*(?:dollars?|rupees?|krone?|{currency_info.code})?',
    ]

    for pattern in price_patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(',', ''))
            except (ValueError, IndexError):
                continue

    # Look for standalone numbers after price keywords
    price_keywords = ['under', 'less than', 'below', 'budget', 'for']
    for keyword in price_keywords:
        pattern = rf'{keyword}\s+(\d+(?:\.\d+)?)'
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue

    return None


def detect_nutrition_filters(query: str) -> Dict[str, Any]:
    """
    Detect nutrition-based filters in user query.

    Args:
        query: User's query text

    Returns:
        Dictionary with nutrition filters
    """
    query_lower = query.lower()
    filters = {}

    # Nutrition keyword mapping
    nutrition_keywords = {
        'protein': ['high protein', 'protein rich', 'lots of protein', 'protein packed'],
        'carbs': ['high carb', 'carb rich', 'lots of carbs', 'carb loaded'],
        'fat': ['high fat', 'fat rich', 'fatty'],
        'calories': ['low calorie', 'high calorie', 'calorie dense'],
        'fiber': ['high fiber', 'fiber rich', 'lots of fiber'],
        'sugar': ['low sugar', 'sugar free', 'no sugar']
    }

    # Threshold keywords
    threshold_keywords = {
        'high': ['high', 'lots of', 'rich in', 'packed with', 'loaded with'],
        'low': ['low', 'little', 'minimal', 'less'],
        'under': ['under', 'less than', 'below'],
        'over': ['over', 'more than', 'above']
    }

    # Check for nutrition filters
    for nutrient, phrases in nutrition_keywords.items():
        for phrase in phrases:
            if phrase in query_lower:
                filters[nutrient] = 'high'  # Default to high
                break

        # Check for specific thresholds
        for threshold, threshold_phrases in threshold_keywords.items():
            for phrase in threshold_phrases:
                if f"{phrase} {nutrient}" in query_lower or phrase in query_lower and nutrient in query_lower:
                    filters[nutrient] = threshold
                    break

    # Extract numeric values
    import re
    number_pattern = r'(\d+(?:\.\d+)?)\s*(grams?|g|mg)'
    matches = re.findall(number_pattern, query_lower)

    for match in matches:
        value = float(match[0])
        unit = match[1].lower()

        if 'protein' in query_lower:
            filters['protein'] = value
        elif 'carb' in query_lower:
            filters['carbs'] = value
        elif 'calorie' in query_lower:
            filters['calories'] = value

    return filters


def is_filter_only_query(query: str, intent: str) -> bool:
    """
    Determine if a query should use filter-only approach (skip embeddings).

    Args:
        query: User's query text
        intent: Detected intent from NLID agent

    Returns:
        True if query should use filter-only approach
    """
    filter_intents = ['nutrition_filter', 'price_filter']

    if intent not in filter_intents:
        return False

    # Additional check for pure filter queries
    query_lower = query.lower()

    # If query contains specific filter keywords without semantic terms
    filter_keywords = [
        'high protein', 'low carb', 'high fat', 'low sugar',
        'under', 'budget', 'expensive', 'cheap', 'expensive',
        'calorie', 'protein', 'carbs', 'fat'
    ]

    has_filter_keyword = any(keyword in query_lower for keyword in filter_keywords)

    # If query has recipe names or dishes, it's not filter-only
    recipe_indicators = ['recipe', 'recipes', 'dish', 'dishes', 'meal', 'meals']
    has_recipe_indicator = any(indicator in query_lower for indicator in recipe_indicators)

    return has_filter_keyword and not has_recipe_indicator
