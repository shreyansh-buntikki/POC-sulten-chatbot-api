"""
Nutrition and Pricing Data Fetcher
Fetches nutrition data from USDA FoodData Central and pricing data from various sources
"""
import os
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import date
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class NutritionData:
    """Data class for nutrition information"""
    servingSize: int = 100
    energyKcal: Optional[float] = None
    energyKj: Optional[float] = None
    protein: Optional[float] = None
    carbohydrates: Optional[float] = None
    totalFiber: Optional[float] = None
    solubleFiber: Optional[float] = None
    insolubleFiber: Optional[float] = None
    totalSugars: Optional[float] = None
    addedSugar: Optional[float] = None
    starch: Optional[float] = None
    totalFat: Optional[float] = None
    saturatedFat: Optional[float] = None
    transFat: Optional[float] = None
    monounsaturatedFat: Optional[float] = None
    polyunsaturatedFat: Optional[float] = None
    cholesterol: Optional[float] = None
    # Vitamins (micros)
    vitaminA: Optional[float] = None
    vitaminC: Optional[float] = None
    vitaminD: Optional[float] = None
    vitaminE: Optional[float] = None
    vitaminK: Optional[float] = None
    thiamineB1: Optional[float] = None
    riboflavinB2: Optional[float] = None
    niacinB3: Optional[float] = None
    pantothenicAcidB5: Optional[float] = None
    vitaminB6: Optional[float] = None
    biotinB7: Optional[float] = None
    folateB9: Optional[float] = None
    vitaminB12: Optional[float] = None
    choline: Optional[float] = None
    # Minerals
    calcium: Optional[float] = None
    iron: Optional[float] = None
    magnesium: Optional[float] = None
    phosphorus: Optional[float] = None
    potassium: Optional[float] = None
    sodium: Optional[float] = None
    zinc: Optional[float] = None
    copper: Optional[float] = None
    manganese: Optional[float] = None
    selenium: Optional[float] = None
    fluoride: Optional[float] = None


@dataclass
class PricingData:
    """Data class for pricing information"""
    countryId: str
    currencyId: str
    pricePerUnit: float
    quantity: int
    measuringUnitId: str
    statePriceIndia: Optional[float] = None
    statePriceNorway: Optional[float] = None
    statePriceUS: Optional[float] = None
    dateOfEntry: date = None
    source: str = "Web Scraping 2025"
    isVerified: bool = False


class USDAFoodDataClient:
    """Client for USDA FoodData Central API"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('USDA_API_KEY', 'DEMO_KEY')
        self.base_url = "https://api.nal.usda.gov/fdc/v1"

    async def search_food(self, query: str, session: aiohttp.ClientSession) -> List[Dict]:
        """Search for foods in USDA database"""
        url = f"{self.base_url}/foods/search"
        params = {
            "api_key": self.api_key,
            "query": query,
            "pageSize": 5,
            "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"],
            "sortBy": "dataType"
        }

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('foods', [])
                else:
                    logger.warning(f"USDA API returned status {response.status} for query: {query}")
                    return []
        except Exception as e:
            logger.error(f"Error searching USDA for '{query}': {e}")
            return []

    async def get_food_nutrition(self, fdc_id: str, session: aiohttp.ClientSession) -> Optional[NutritionData]:
        """Get detailed nutrition information for a food item"""
        url = f"{self.base_url}/food/{fdc_id}"
        params = {"api_key": self.api_key}

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    food_data = await response.json()
                    return self._parse_nutrition_data(food_data)
                else:
                    return None
        except Exception as e:
            logger.error(f"Error getting nutrition for FDC ID {fdc_id}: {e}")
            return None

    def _parse_nutrition_data(self, food_data: Dict) -> Optional[NutritionData]:
        """Parse USDA food data into NutritionData object"""
        nutrients = food_data.get('foodNutrients', [])

        def get_nutrient_value(names: List[str], unit: str = 'g') -> Optional[float]:
            """Get nutrient value by searching for various name variants"""
            for nutrient in nutrients:
                nutrient_name = nutrient.get('name', '').lower()
                nutrient_unit = nutrient.get('unitName', '')
                if any(name.lower() in nutrient_name for name in names):
                    value = nutrient.get('amount')
                    if value is not None:
                        # Convert to standard units if needed
                        if unit == 'g' and nutrient_unit == 'mg':
                            return value / 1000
                        elif unit == 'mg' and nutrient_unit == 'g':
                            return value * 1000
                        elif unit == 'mcg' and nutrient_unit == 'mg':
                            return value * 1000
                        elif unit == 'mg' and nutrient_unit == 'mcg':
                            return value / 1000
                        return float(value)
            return None

        return NutritionData(
            servingSize=100,
            energyKcal=get_nutrient_value(['Energy', 'Energy (Atwater)', 'kilocalorie'], 'kcal'),
            energyKj=get_nutrient_value(['Energy', 'kJ'], 'kJ'),
            protein=get_nutrient_value(['Protein'], 'g'),
            carbohydrates=get_nutrient_value(['Carbohydrate, by difference', 'Total carbohydrate'], 'g'),
            totalFiber=get_nutrient_value(['Fiber, total dietary', 'Total dietary fiber'], 'g'),
            totalSugars=get_nutrient_value(['Sugars, total', 'Total sugars'], 'g'),
            starch=get_nutrient_value(['Starch'], 'g'),
            totalFat=get_nutrient_value(['Total fat', 'Fat, total'], 'g'),
            saturatedFat=get_nutrient_value(['Saturated fat', 'Fatty acids, total saturated'], 'g'),
            transFat=get_nutrient_value(['Trans fat', 'Fatty acids, total trans'], 'g'),
            cholesterol=get_nutrient_value(['Cholesterol'], 'mg'),
            # Vitamins
            vitaminA=get_nutrient_value(['Vitamin A'], 'mcg'),
            vitaminC=get_nutrient_value(['Vitamin C', 'Ascorbic acid'], 'mg'),
            vitaminD=get_nutrient_value(['Vitamin D'], 'mcg'),
            vitaminE=get_nutrient_value(['Vitamin E'], 'mg'),
            vitaminK=get_nutrient_value(['Vitamin K'], 'mcg'),
            thiamineB1=get_nutrient_value(['Thiamin', 'Vitamin B-1'], 'mg'),
            riboflavinB2=get_nutrient_value(['Riboflavin', 'Vitamin B-2'], 'mg'),
            niacinB3=get_nutrient_value(['Niacin', 'Vitamin B-3'], 'mg'),
            pantothenicAcidB5=get_nutrient_value(['Pantothenic acid', 'Vitamin B-5'], 'mg'),
            vitaminB6=get_nutrient_value(['Vitamin B-6'], 'mg'),
            folateB9=get_nutrient_value(['Folate', 'Vitamin B-12'], 'mcg'),
            vitaminB12=get_nutrient_value(['Vitamin B-12', 'Cobalamin'], 'mcg'),
            choline=get_nutrient_value(['Choline'], 'mg'),
            # Minerals
            calcium=get_nutrient_value(['Calcium'], 'mg'),
            iron=get_nutrient_value(['Iron'], 'mg'),
            magnesium=get_nutrient_value(['Magnesium'], 'mg'),
            phosphorus=get_nutrient_value(['Phosphorus'], 'mg'),
            potassium=get_nutrient_value(['Potassium'], 'mg'),
            sodium=get_nutrient_value(['Sodium'], 'mg'),
            zinc=get_nutrient_value(['Zinc'], 'mg'),
            copper=get_nutrient_value(['Copper'], 'mg'),
            manganese=get_nutrient_value(['Manganese'], 'mg'),
            selenium=get_nutrient_value(['Selenium'], 'mcg'),
        )


class FatSecretClient:
    """Client for FatSecret API (alternative nutrition data source)"""

    def __init__(self):
        # FatSecret requires OAuth - using as placeholder for future implementation
        self.base_url = "https://platform.fatsecret.com/rest/server.api"


class OpenFoodFactsClient:
    """Client for Open Food Facts API"""

    def __init__(self):
        self.base_url = "https://world.openfoodfacts.org/cgi/search.pl"

    async def search_food(self, query: str, session: aiohttp.ClientSession) -> List[Dict]:
        """Search for foods in Open Food Facts database"""
        params = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": 5
        }

        try:
            async with session.get(self.base_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('products', [])
                return []
        except Exception as e:
            logger.error(f"Error searching Open Food Facts for '{query}': {e}")
            return []

    def _parse_nutrition_data(self, product_data: Dict) -> Optional[NutritionData]:
        """Parse Open Food Facts product data into NutritionData object"""
        nutriments = product_data.get('nutriments', {})

        def get_value(key: str) -> Optional[float]:
            val = nutriments.get(key)
            return float(val) if val is not None else None

        return NutritionData(
            servingSize=100,
            energyKcal=get_value('energy-kcal_100g') or get_value('energy-kcal'),
            energyKj=get_value('energy-kj_100g') or get_value('energy-kj'),
            protein=get_value('proteins_100g') or get_value('proteins'),
            carbohydrates=get_value('carbohydrates_100g') or get_value('carbohydrates'),
            totalFiber=get_value('fiber_100g') or get_value('fiber'),
            totalSugars=get_value('sugars_100g') or get_value('sugars'),
            totalFat=get_value('fat_100g') or get_value('fat'),
            saturatedFat=get_value('saturated-fat_100g') or get_value('saturated-fat'),
            transFat=get_value('trans-fat_100g') or get_value('trans-fat'),
            cholesterol=get_value('cholesterol_100g') or get_value('cholesterol'),
            # Vitamins (Open Food Facts has limited vitamin data)
            vitaminA=get_value('vitamin-a_100g'),
            vitaminC=get_value('vitamin-c_100g'),
            vitaminD=get_value('vitamin-d_100g'),
            vitaminE=get_value('vitamin-e_100g'),
            vitaminK=get_value('vitamin-k_100g'),
            # Minerals
            calcium=get_value('calcium_100g') or get_value('calcium'),
            iron=get_value('iron_100g') or get_value('iron'),
            magnesium=get_value('magnesium_100g') or get_value('magnesium'),
            potassium=get_value('potassium_100g') or get_value('potassium'),
            sodium=get_value('sodium_100g') or get_value('sodium'),
            zinc=get_value('zinc_100g') or get_value('zinc'),
        )


class IngredientCategorizer:
    """Categorizes ingredients by type for appropriate unit mapping"""

    # Ingredient categories and their typical base units
    DRY_INGREDIENTS = ['flour', 'sugar', 'salt', 'spice', 'powder', 'meal', 'grain',
                       'rice', 'oats', 'nuts', 'seeds', 'beans', 'lentils', 'starch',
                       'mel', 'sukker', 'salt', 'krydder', 'pulver', 'mjøl', 'gryn',
                       'nøtter', 'frø', 'bønner', 'linser', 'melis', 'kakao']

    LIQUID_INGREDIENTS = ['milk', 'water', 'juice', 'oil', 'sauce', 'syrup', 'vinegar',
                          'cream', 'yogurt', 'buttermilk', 'kefir', 'extract', 'essence',
                          'melk', 'vann', 'saft', 'olje', 'saus', 'sirup', 'eddik',
                          'krem', 'yoghurt', 'ekstrakt']

    PIECE_BASED = ['egg', 'fruit', 'vegetable', 'onion', 'garlic', 'ginger', 'lime', 'lemon',
                   'egg', 'frukt', 'grønnsak', 'løk', 'hvitløk', 'ingefær', 'lime', 'sitron',
                   'tomat', 'agurk', 'paprika', 'chili', 'pepper', 'potato', 'avocado']

    @classmethod
    def categorize_ingredient(cls, name: str) -> Tuple[str, str]:
        """
        Categorize ingredient and return (category, default_unit_id)

        Returns: (category_name, measuring_unit_id)
        """
        name_lower = name.lower()

        # Check for dry ingredients
        for keyword in cls.DRY_INGREDIENTS:
            if keyword in name_lower:
                return ('dry', '396cab8c-5d3b-49b0-b946-b96a26f84af1')  # gram

        # Check for liquid ingredients
        for keyword in cls.LIQUID_INGREDIENTS:
            if keyword in name_lower:
                return ('liquid', '396cab8c-5d3b-49b0-b946-b96a26f84af1')  # gram (liquids often measured by weight too)

        # Check for piece-based ingredients
        for keyword in cls.PIECE_BASED:
            if keyword in name_lower:
                return ('piece', '7d25ed2b-9f5f-4d95-8ee8-4617e756ff68')  # stykk/piece

        # Default to grams for unknown ingredients
        return ('default', '396cab8c-5d3b-49b0-b946-b96a26f84af1')  # gram


class PricingEstimator:
    """Estimates pricing for ingredients across different countries"""

    # Base price multipliers by ingredient category
    PRICE_CATEGORIES = {
        'basic': {'india': 50, 'norway': 30, 'us': 2},  # Basic grains, flour
        'vegetable': {'india': 60, 'norway': 40, 'us': 3},
        'fruit': {'india': 150, 'norway': 50, 'us': 5},
        'dairy': {'india': 80, 'norway': 60, 'us': 5},
        'meat': {'india': 400, 'norway': 200, 'us': 15},
        'seafood': {'india': 500, 'norway': 250, 'us': 20},
        'spices': {'india': 100, 'norway': 50, 'us': 5},
        'nuts': {'india': 500, 'norway': 100, 'us': 10},
        'oil': {'india': 150, 'norway': 80, 'us': 8},
        'sweetener': {'india': 80, 'norway': 40, 'us': 3},
    }

    @classmethod
    def categorize_for_pricing(cls, name: str) -> str:
        """Categorize ingredient for pricing estimation"""
        name_lower = name.lower()

        if any(kw in name_lower for kw in ['flour', 'mel', 'rice', 'ris', 'grain', 'gryn']):
            return 'basic'
        elif any(kw in name_lower for kw in ['milk', 'melk', 'cheese', 'ost', 'yogurt', 'cream', 'krem']):
            return 'dairy'
        elif any(kw in name_lower for kw in ['meat', 'kjøtt', 'beef', 'storfe', 'pork', 'svin', 'chicken', 'kylling']):
            return 'meat'
        elif any(kw in name_lower for kw in ['fish', 'fisk', 'salmon', 'laks', 'shrimp', 'reke', 'seafood']):
            return 'seafood'
        elif any(kw in name_lower for kw in ['spice', 'krydder', 'powder', 'pulver']):
            return 'spices'
        elif any(kw in name_lower for kw in ['nut', 'nøtt', 'almond', 'mandel', 'cashew']):
            return 'nuts'
        elif any(kw in name_lower for kw in ['oil', 'olje', 'oil']):
            return 'oil'
        elif any(kw in name_lower for kw in ['sugar', 'sukker', 'syrup', 'sirup', 'honey', 'honning']):
            return 'sweetener'
        elif any(kw in name_lower for kw in ['fruit', 'frukt', 'apple', 'eple', 'banana', 'banan']):
            return 'fruit'
        elif any(kw in name_lower for kw in ['vegetable', 'grønnsak', 'tomat', 'onion', 'løk', 'carrot', 'gulrot']):
            return 'vegetable'

        return 'basic'  # Default category

    @classmethod
    def estimate_price(cls, name: str, quantity: int = 100) -> Dict[str, float]:
        """Estimate price per 100g for an ingredient across countries"""
        category = cls.categorize_for_pricing(name)
        prices = cls.PRICE_CATEGORIES.get(category, cls.PRICE_CATEGORIES['basic'])

        # Adjust for quantity (base is per 100g)
        multiplier = quantity / 100

        return {
            'india': round(prices['india'] * multiplier, 2),
            'norway': round(prices['norway'] * multiplier, 2),
            'us': round(prices['us'] * multiplier, 2),
        }


# Country and Currency IDs (from the database schema)
COUNTRY_IDS = {
    'india': '11111111-1111-1111-1111-111111111111',
    'norway': '22222222-2222-2222-2222-222222222222',
    'us': '33333333-3333-3333-3333-333333333333',
}

CURRENCY_IDS = {
    'india': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',  # INR
    'norway': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',  # NOK
    'us': 'cccccccc-cccc-cccc-cccc-cccccccccccc',  # USD
}
