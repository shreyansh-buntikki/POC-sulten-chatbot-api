"""
Ingredient Data Population Script
Fetches nutrition and pricing data from web APIs and populates the database
"""
import asyncio
import os
import aiohttp
from typing import Dict, List, Optional, Set
from datetime import date
import logging
from tqdm import tqdm

from sqlalchemy.orm import Session
from database import SessionLocal, engine
from models import (
    Ingredient, IngredientMacros, IngredientMicros, IngredientPricing,
    Country, Currency, MeasuringUnit
)
from scripts.initial_data_fetching.nutrition_api_client import (
    USDAFoodDataClient,
    OpenFoodFactsClient,
    IngredientCategorizer,
    PricingEstimator,
    NutritionData,
    COUNTRY_IDS,
    CURRENCY_IDS
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Measuring unit IDs
UNIT_GRAM = '396cab8c-5d3b-49b0-b946-b96a26f84af1'
UNIT_KILO = '45da9c48-2a89-47a9-8d7c-6146a9dde4d9'
UNIT_PIECE = '7d25ed2b-9f5f-4d95-8ee8-4617e756ff68'
UNIT_LITER = '3db0f7a7-1b23-4528-be58-9c13b50d5629'

# Ingredient name translation mapping (Norwegian/English -> English search terms)
INGREDIENT_TRANSLATION = {
    # Norwegian to English mappings
    'hvitløkspulver': 'garlic powder',
    'banan': 'banana',
    'mandler': 'almonds',
    'chevre': 'goat cheese',
    'kiwi': 'kiwi fruit',
    'sammalt hvetemel': 'whole wheat flour',
    'maizena': 'cornstarch',
    'hønsebuljong': 'chicken bouillon',
    'kefir': 'kefir',
    'persillerot': 'parsley root',
    'nektarin': 'nectarine',
    'mørbrad av storfe': 'beef tenderloin',
    'chilimajones': 'chili mayonnaise',
    'granateple': 'pomegranate',
    'sukkerfri mørk sjokolade': 'sugar free dark chocolate',
    'kikertvann': 'aquafaba',
    'kvedekjerner': 'quince seeds',
    'asiatisk kyllingbuljongpulver': 'asian chicken bouillon powder',
    'mini marshmallows': 'mini marshmallows',
    'wonton pastry wrappers': 'wonton wrappers',
    'meierismør': 'dairy butter',
    'romanosalat': 'romaine lettuce',
    'taco salsa': 'taco salsa',
    'havrekli': 'oat bran',
    'løk': 'onion',
    'havrefiber': 'oat fiber',
    'peanøttsmør': 'peanut butter',
    'mascarpone': 'mascarpone cheese',
    'mørk sjokolade av høy kvalitet': 'dark chocolate',
    'frosne bær': 'frozen berries',
    'berberisbær': 'barberry berries',
    'glutenfritt brød': 'gluten free bread',
    'honning': 'honey',
    'sopp': 'mushrooms',
    'ferdig pizzadeig': 'pizza dough',
    'teriyakisaus': 'teriyaki sauce',
    'kadayif': 'kadaifi noodles',
    'lakrisruller': 'licorice rolls',
    'potetmos': 'mashed potatoes',
    'koriander': 'coriander',
    'paprikapulver': 'paprika powder',
    'svinenakke': 'pork shoulder',
    'sukrinsirup maple': 'sugar free maple syrup',
    'pepper': 'black pepper',
    'brune bønner i lake': 'brown beans in brine',
    'kokos': 'coconut',
    'roastbiff': 'roast beef',
    'solsikkekjerner': 'sunflower seeds',
    'hvitvinseddik': 'white wine vinegar',
    'kikerter': 'chickpeas',
    'grillribbe': 'pork ribs',
    'rødbete': 'beetroot',
    'mandelflak': 'sliced almonds',
    'gelatinpulver': 'gelatin powder',
    'pistasjsmør': 'pistachio butter',
    'solsikkeolje': 'sunflower oil',
    'argentinske villreker': 'argentine shrimp',
    'smør': 'butter',
    'regnbuegulrot': 'rainbow carrot',
    'kikertlake': 'chickpea brine',
    'pastinakk': 'parsnip',
    'vannmelon': 'watermelon',
    'vaniljepulver': 'vanilla powder',
    'storfekjøtt': 'beef',
    'hvitløksalt': 'garlic salt',
    'malt nellik': 'ground cloves',
    'fibersirup clear': 'fiber syrup clear',
    'kokoskrem': 'coconut cream',
    'espresso': 'espresso',
    'fersk fiken': 'fresh fig',
    'fersken': 'peach',
    'timian': 'thyme',
    'libanesisk brød': 'lebanese bread',
    'frosne grønnsaker': 'frozen vegetables',
    'gulrot': 'carrot',
    'kaffe': 'coffee',
    'indrefilet av svin': 'pork tenderloin',
    'palmesukker': 'palm sugar',
    'rug sammalt grov': 'whole grain rye flour',
    'østersopp': 'oyster mushrooms',
    'pasta fettuccine': 'fettuccine pasta',
    'fransk baguette': 'french baguette',
    'hjemmelaget pesto': 'homemade pesto',
    'tipo-00 pizzamel': '00 flour',
    'spisskål': 'savoy cabbage',
    'ferdigrevet kylling': 'shredded chicken',
    'sushiris': 'sushi rice',
    'purreløk': 'leek',
    'pølsebrød': 'hotdog buns',
    'liten aubergine': 'small aubergine',
    'sukrin gold': 'sukrin gold',
    'rettish': 'radish',
    'tørket tranebær': 'dried cranberries',
    'jalapeño': 'jalapeno',
    'pinjekjerner': 'pine nuts',
    'klementin': 'clementine',
    'sort te': 'black tea',
    'frisk basilikum': 'fresh basil',
    'pastarester': 'pasta leftovers',
    'sukrin sirup caramel': 'sugar free caramel syrup',
    'coca cola': 'coca cola',
    'råkostsalat': 'raw vegetable salad',
    'tapiokamel': 'tapioca flour',
    'delikatesseløk': 'shallot',
    'revet ingefær': 'grated ginger',
    'brokkoliris': 'broccoli rice',
    'ansjosfilet': 'anchovy fillet',
    'tørr hvitvin': 'dry white wine',
    'ruccola': 'arugula',
    'pasta rigatoni': 'rigatoni pasta',
    'bondebønner': 'fava beans',
    'fryst rosenkål': 'frozen brussels sprouts',
    'stjerneanis': 'star anise',
    'babymais': 'baby corn',
    'risotto': 'risotto rice',
    'romtemperert smør': 'room temperature butter',
    'pepperoni': 'pepperoni',
    'frosne bringebær': 'frozen raspberries',
    'kyllingfilet': 'chicken breast',
    'natron': 'baking soda',
    'tunfisk': 'tuna',
    'skinke': 'ham',
    'multebær': 'cloudberries',
    'grønnsakskraft': 'vegetable stock',
    'hel pepper': 'whole pepper',
    'fisk i terninger': 'cubed fish',
    'sjalottløk': 'shallot',
    'tørket mynte': 'dried mint',
    'trøffelolje': 'truffle oil',
    'malt muskat': 'ground nutmeg',
    'grønn pesto': 'green pesto',
    'surdeigsstarter': 'sourdough starter',
    'vårrullplater': 'spring roll sheets',
    'chiliolje': 'chili oil',
    'grovkvernet pepper': 'coarsely ground pepper',
    'tacoskjell': 'taco shells',
    'daddel': 'date',
    'vegansk rømme': 'vegan sour cream',
    'kirsebær': 'cherry',
    'rundkornet ris': 'short grain rice',
    'næringsgjær': 'nutritional yeast',
    'sylteagurk': 'pickled cucumber',
    'egg': 'egg',
    'kiwi gelé': 'kiwi jelly',
    'gul fargepaste': 'yellow food coloring',
    'havregryn': 'oats',
    'fersk estragon': 'fresh tarragon',
    'frisk babyspinat': 'fresh baby spinach',
    'rødkål': 'red cabbage',
    'sukkererter': 'sugar snap peas',
    'shiitake sopp': 'shiitake mushrooms',
    'sukkerfri kakestrøssel regnbue': 'sugar free rainbow sprinkles',
    'ferske maiskolber': 'fresh corn cobs',
    'gul pulverfarge (gurkemeie)': 'turmeric powder',
    'chilikrydder': 'chili powder',
    'aprikos': 'apricot',
    'kokosmel': 'coconut flour',
    'kokosyoghurt': 'coconut yogurt',
    'korianderfrø': 'coriander seeds',
    'mimolette': 'mimolette cheese',
    'barberry berries': 'barberry berries',
    'brown beans in brine': 'brown beans in brine',
    'sliced almonds': 'sliced almonds',
    'garlic salt': 'garlic salt',
    'frozen vegetables': 'frozen vegetables',
    'whole milk': 'whole milk',
    'strawberries': 'strawberries',
    'spring wheat': 'spring wheat',
    'yellow red bell pepper': 'yellow red bell pepper',
    'norwegian gastromat spice': 'norwegian gastromat spice',
    'brown rice': 'brown rice',
    'small aubergine': 'small aubergine',
    'phyllo dough': 'phyllo dough',
    'whole wheat flour': 'whole wheat flour',
    'psyllium husk fiber': 'psyllium husk fiber',
    'rum essence': 'rum essence',
    'whole grain flour': 'whole grain flour',
    'olivenolje': 'olive oil',
    'thai chili': 'thai chili',
    'ytrefilet av okse': 'beef sirloin',
    'potet': 'potato',
    'glutenfri spagetti': 'gluten free spaghetti',
    'ristede sesamfrø': 'toasted sesame seeds',
    'dill': 'dill',
    'tørre linser': 'dried lentils',
    'philadelphia ost naturell': 'cream cheese',
    'speltmel': 'spelt flour',
    'endive': 'endive',
    'eple': 'apple',
    'mandellikør': 'amaretto',
    'rosmarinstilker': 'rosemary sprigs',
    'reinsdyrkjøtt': 'reindeer meat',
    'kondensert melk': 'condensed milk',
    'glutenfritt grovt mel': 'gluten free coarse flour',
    'spisskummen': 'cumin',
    'brokkolini': 'broccolini',
    'udon nudler': 'udon noodles',
    'yoghurt naturell': 'plain yogurt',
    'knutekål': 'kohlrabi',
    'kylling lårfilet': 'chicken thigh',
    'rødløk': 'red onion',
    'bakepulver': 'baking powder',
    'brokkoli': 'broccoli',
    'rød curry paste': 'red curry paste',
    'salmalaks': 'salmon',
    'plantebasert smør': 'vegan butter',
    'sitrongress': 'lemongrass',
    'vaniljekesam': 'vanilla kesam',
    'dumle': 'dumle candy',
    'sukker': 'sugar',
    'lys kokesjokolade': 'white chocolate',
    'kokte linser': 'cooked lentils',
    'kidneybønner': 'kidney beans',
    'limejuice': 'lime juice',
    'mandelpoteter': 'almond potatoes',
    'smøreost med bacon': 'bacon cream cheese',
    'bulgur': 'bulgur',
    'vegansk smør': 'vegan butter',
    'plommer': 'plums',
    'aromasopp': 'aromatic mushrooms',
    'fersk salvie': 'fresh sage',
    'flytende margarin': 'liquid margarine',
    'kesam': 'kesam',
    'dumplingark': 'dumpling wrappers',
    'iskjeks': 'ice cream cones',
    'frosne rundstykker': 'frozen bread rolls',
    'krydderurt': 'herb',
    'vannmelonskall': 'watermelon rind',
    'nduja': 'nduja sausage',
    'cashewnøtter': 'cashew nuts',
    'søt spisspaprika': 'sweet paprika',
    'frosne erter': 'frozen peas',
    'brunt sukker': 'brown sugar',
    'hjemmelaget grytebrød': 'homemade bread',
    'mini tomater': 'cherry tomatoes',
    'pavlova-kake': 'pavlova',
    'nesquick pulver': 'nesquick powder',
    'perlesukker': 'pearl sugar',
    'ristede frø': 'toasted seeds',
    'peanøttsaus': 'peanut sauce',
    'chorizo': 'chorizo',
    'basilikum': 'basil',
    'tørket japchae/koreanske nudler': 'dried sweet potato noodles',
    'nudler': 'noodles',
    'glutenfri soyasaus': 'gluten free soy sauce',
    'seifilet': 'coalfish',
    'kalkun': 'turkey',
    'kidneybønner i chili': 'chili kidney beans',
    'blå druer': 'blue grapes',
    'bein av kongekrabbe': 'king crab legs',
    'sukkerlake': 'sugar syrup',
    'harissa': 'harissa',
    'hvit rom': 'white rum',
    'lebanese bread': 'lebanese bread',
    'scampi': 'shrimp',
    'fresh spaghetti': 'fresh spaghetti',
    'dried dill': 'dried dill',
    'green chili': 'green chili',
    'rice porridge': 'rice porridge',
    'mature cheese': 'mature cheese',
    'chicken seasoning': 'chicken seasoning',
    'greek yogurt': 'greek yogurt',
    'pumpkin puree': 'pumpkin puree',
    'date syrup': 'date syrup',
    'clementine': 'clementine',
    'fersk spagetti': 'fresh spaghetti',
    'gul paprika': 'yellow bell pepper',
    'gastromat': 'gastromat spice',
    'appelsin': 'orange',
    'høyrygg av storfe': 'beef chuck',
    'entrecôte': 'entrecote',
    'baja chicken spice': 'baja chicken spice',
    'stilker fersk mint': 'fresh mint stems',
    'bourbon vaniljeekstrakt': 'bourbon vanilla extract',
    'chili': 'chili',
    'fiskesuperester': 'fish soup leftovers',
    'pinnekjøttrester': 'pinnekjøtt leftovers',
    'griljermel': 'griljermel',
    'basmatiris': 'basmati rice',
    'butternut gresskar': 'butternut squash',
    'chiafrø': 'chia seeds',
    'avokado': 'avocado',
    'rosmarin': 'rosemary',
    'veganske burgere': 'vegan burgers',
    'hasselnøtter': 'hazelnuts',
    'chiliflak': 'chili flakes',
    'ingefær': 'ginger',
    'sesamolje': 'sesame oil',
    'sylteagurk lake': 'pickle brine',
    'muskatnøtt': 'nutmeg',
    'fiskekraft': 'fish stock',
    'kjøttboller i tomatsaus': 'meatballs in tomato sauce',
    'kardemommefrø': 'cardamom seeds',
    'syltet rødkål': 'pickled red cabbage',
    'sukrin marsipan': 'sugar free marzipan',
    'laurbærblad': 'bay leaf',
    'fullkornsris': 'brown rice',
    'kokosmelk': 'coconut milk',
    'isbergsalat': 'iceberg lettuce',
    'kjøttbuljong': 'beef bouillon',
    'holy basil': 'holy basil',
    'panert fiskefilet': 'breaded fish',
    'pepperkaker': 'gingerbread cookies',
    'lasagneplater': 'lasagna sheets',
    'lodderogn': 'cod roe',
    'svinekoteletter': 'pork cutlets',
    'sukrinsirup gold': 'sukrin gold syrup',
    'malt ingefær': 'ground ginger',
    'brownies': 'brownies',
    'vegansk parmesan': 'vegan parmesan',
    'chili sin carne': 'chili sin carne',
    'kål': 'cabbage',
    'sjokolade': 'chocolate',
    'lys soyasaus': 'light soy sauce',
    'chinkiang eddik': 'chinkiang vinegar',
    'nøtteblanding': 'nut mix',
    'sukrin': 'sukrin sweetener',
    'panko': 'panko breadcrumbs',
    'matfløte': 'cooking cream',
    'rosenkål': 'brussels sprouts',
    'seterrømme': 'sour cream',
    'lønnesirup': 'maple syrup',
    'knuste linfrø': 'crushed flaxseed',
    'gochujang': 'gochujang',
    'cherrytomater': 'cherry tomatoes',
    'paprikakrydder': 'paprika powder',
    'kjøttkraft': 'meat broth',
    'sitronjuice': 'lemon juice',
    'sake': 'sake',
    'ricottakrem': 'ricotta cream',
    'jordbærsyltetøy': 'strawberry jam',
    'blåbær': 'blueberries',
    'tofu (fast)': 'firm tofu',
    'guacamole spice mix': 'guacamole spice mix',
    'grønn paprika': 'green bell pepper',
    'vegansk kaviar': 'vegan caviar',
    'kjørvel': 'chervil',
    'reinsdyrskav': 'reindeer mince',
    'gurkemeie': 'turmeric',
    'chicken bouillon': 'chicken bouillon',
    'pomegranate': 'pomegranate',
    'mushroom': 'mushrooms',
    'pork shoulder': 'pork shoulder',
    'pork ribs': 'pork ribs',
    'aquafaba': 'aquafaba',
    'thyme': 'thyme',
    'oyster mushrooms': 'oyster mushrooms',
    'tipo-00 flour': 'tipo 00 flour',
    'sesame seeds': 'sesame seeds',
    'coconut sugar': 'coconut sugar',
    'chicken thighs': 'chicken thighs',
    'orange': 'orange',
    'chuck roll': 'chuck roll',
    'barbecue seasoning': 'barbecue seasoning',
    'diced bacon': 'diced bacon',
    'crème fraîche': 'creme fraiche',
    'cream cheese': 'cream cheese',
    'vegan sausages': 'vegan sausages',
    'agurk': 'cucumber',
    'vaniljesaus': 'vanilla sauce',
    'granateplekjerner': 'pomegranate seeds',
    'kalamata oliven': 'kalamata olives',
    'couscous': 'couscous',
    'blackstrap melasse': 'blackstrap molasses',
    'rismelk': 'rice milk',
    'tørket kikerter': 'dried chickpeas',
    'kalkunkraft': 'turkey stock',
    'halloumi': 'halloumi',
    'brokkolistilk': 'broccoli stalk',
    'gulrotblader': 'carrot tops',
    'oystersaus': 'oyster sauce',
    'sukkerfri kakestrøssel sjokolade': 'sugar free chocolate sprinkles',
    'kalkunfilet': 'turkey breast',
    'rosevann': 'rose water',
    'spinat': 'spinach',
    'sichuanpepper (mortet)': 'sichuan pepper',
    'thai basillikum': 'thai basil',
    'kruskakli': 'rye bran',
    'medjool dadler': 'medjool dates',
    'plantebasert yoghurt': 'plant based yogurt',
    'bokhvetemel': 'buckwheat flour',
    'glassnudler': 'glass noodles',
    'krem av tartar': 'cream of tartar',
    'gresskarfrø': 'pumpkin seeds',
    'pistasjnøtter': 'pistachios',
    'makrellfilet': 'mackerel fillet',
    'rugmel': 'rye flour',
    'revet ost': 'shredded cheese',
    'kaki': 'persimmon',
    'aleppo chili': 'aleppo pepper',
    'økologisk sitron': 'organic lemon',
    'hockey pulver': 'hockey powder',
    'kakestrøssel': 'sprinkles',
    'gin': 'gin',
    'mørk kokesjokolade': 'dark chocolate',
    'vaniljesukker': 'vanilla sugar',
    'vegetardeig': 'vegetable dough',
    'vann': 'water',
    'kulturmelk': 'cultured milk',
    'blåskjell': 'mussels',
    'salte kjeks': 'salted crackers',
    'eplesidereddik': 'apple cider vinegar',
    'pinnekjøtt': 'pinnekjøtt',
    'hvit sjokolade': 'white chocolate',
    'pitabrød': 'pita bread',
    'nakkekotelett': 'neck fillet',
    'pasta penne': 'penne pasta',
    'nutella': 'nutella',
    'syltede rødbeter': 'pickled beets',
    'røde epler': 'red apples',
    'hvetemel': 'wheat flour',
    'tacokrydder': 'taco seasoning',
    'hakkede tomater': 'diced tomatoes',
    'frosne jordbær': 'frozen strawberries',
    'einerrøkt laks': 'smoked salmon',
    'laksefilet': 'salmon fillet',
    'havreflarn': 'oat flakes',
    'melk': 'milk',
    'cheddar': 'cheddar',
    'søt havrekjeks': 'sweet oat crackers',
    'fryst ananas': 'frozen pineapple',
    'mørk sirup': 'dark syrup',
    'glutenfrie pepperkaker': 'gluten free gingerbread',
    'tørrgjær for søt bakst': 'yeast for sweet baking',
    'nori': 'nori',
    'plantebasert melk': 'plant based milk',
    'tranebærjuice': 'cranberry juice',
    'ras el hanout': 'ras el hanout',
    'linfrø': 'flaxseed',
    'hoisinsaus': 'hoisin sauce',
    'brie': 'brie',
    'nøytral olje': 'neutral oil',
    'gulrotsuppe': 'carrot soup',
    'gjendekjeks': 'gingerbread',
    'beef sirloin': 'beef sirloin',
    'yellow onion': 'yellow onion',
    'fresh coriander': 'fresh coriander',
    'gelatin powder': 'gelatin powder',
    'vanilla powder': 'vanilla powder',
}


class IngredientDataPopulator:
    """Main class to populate ingredient nutrition and pricing data"""

    def __init__(self, db: Session):
        self.db = db
        self.usda_client = USDAFoodDataClient()
        self.off_client = OpenFoodFactsClient()
        self.categorizer = IngredientCategorizer()
        self.pricing_estimator = PricingEstimator()

        # Cache for API results to avoid duplicate calls
        self._nutrition_cache: Dict[str, NutritionData] = {}

    def translate_ingredient_name(self, name: str) -> str:
        """Translate ingredient name to English for API search"""
        name_lower = name.lower().strip()
        return INGREDIENT_TRANSLATION.get(name_lower, name)

    async def fetch_nutrition_data(self, ingredient_name: str) -> Optional[NutritionData]:
        """Fetch nutrition data from multiple sources"""
        translated_name = self.translate_ingredient_name(ingredient_name)

        # Check cache first
        if translated_name in self._nutrition_cache:
            return self._nutrition_cache[translated_name]

        async with aiohttp.ClientSession() as session:
            # Try USDA first
            try:
                foods = await self.usda_client.search_food(translated_name, session)
                if foods:
                    # Get nutrition data for the first (most relevant) result
                    fdc_id = foods[0].get('fdcId')
                    if fdc_id:
                        nutrition = await self.usda_client.get_food_nutrition(str(fdc_id), session)
                        if nutrition and (nutrition.protein or nutrition.carbohydrates or nutrition.totalFat):
                            self._nutrition_cache[translated_name] = nutrition
                            logger.info(f"✓ Found nutrition data for '{ingredient_name}' via USDA")
                            return nutrition
            except Exception as e:
                logger.warning(f"USDA lookup failed for '{ingredient_name}': {e}")

            # Try Open Food Facts as fallback
            try:
                products = await self.off_client.search_food(translated_name, session)
                if products:
                    nutrition = self.off_client._parse_nutrition_data(products[0])
                    if nutrition and (nutrition.protein or nutrition.carbohydrates or nutrition.totalFat):
                        self._nutrition_cache[translated_name] = nutrition
                        logger.info(f"✓ Found nutrition data for '{ingredient_name}' via Open Food Facts")
                        return nutrition
            except Exception as e:
                logger.warning(f"Open Food Facts lookup failed for '{ingredient_name}': {e}")

        logger.warning(f"✗ No nutrition data found for '{ingredient_name}'")
        return None

    def get_pricing_data(self, ingredient_name: str, ingredient_id: str) -> List[Dict]:
        """Generate pricing data for all countries"""
        category, unit_id = self.categorizer.categorize_ingredient(ingredient_name)
        prices = self.pricing_estimator.estimate_price(ingredient_name, quantity=100)

        pricing_list = []

        # India pricing
        pricing_list.append({
            'ingredientId': ingredient_id,
            'countryId': COUNTRY_IDS['india'],
            'currencyId': CURRENCY_IDS['india'],
            'pricePerUnit': prices['india'],
            'quantity': 100,
            'measuringUnitId': UNIT_GRAM,
            'statePriceIndia': None,
            'statePriceNorway': None,
            'statePriceUS': None,
            'dateOfEntry': date.today(),
            'source': 'Estimated pricing 2025',
            'isVerified': False
        })

        # Norway pricing
        pricing_list.append({
            'ingredientId': ingredient_id,
            'countryId': COUNTRY_IDS['norway'],
            'currencyId': CURRENCY_IDS['norway'],
            'pricePerUnit': prices['norway'],
            'quantity': 100,
            'measuringUnitId': UNIT_GRAM,
            'statePriceIndia': None,
            'statePriceNorway': None,
            'statePriceUS': None,
            'dateOfEntry': date.today(),
            'source': 'Estimated pricing 2025',
            'isVerified': False
        })

        # US pricing
        pricing_list.append({
            'ingredientId': ingredient_id,
            'countryId': COUNTRY_IDS['us'],
            'currencyId': CURRENCY_IDS['us'],
            'pricePerUnit': prices['us'],
            'quantity': 100,
            'measuringUnitId': UNIT_GRAM,
            'statePriceIndia': None,
            'statePriceNorway': None,
            'statePriceUS': None,
            'dateOfEntry': date.today(),
            'source': 'Estimated pricing 2025',
            'isVerified': False
        })

        return pricing_list

    async def populate_ingredient(self, ingredient: Ingredient) -> bool:
        """Populate nutrition and pricing data for a single ingredient"""
        try:
            # Check if data already exists
            existing_macros = self.db.query(IngredientMacros).filter(
                IngredientMacros.ingredientId == ingredient.id
            ).first()

            existing_micros = self.db.query(IngredientMicros).filter(
                IngredientMicros.ingredientId == ingredient.id
            ).first()

            existing_pricing = self.db.query(IngredientPricing).filter(
                IngredientPricing.ingredientId == ingredient.id
            ).count()

            # Skip if data already exists
            if existing_macros and existing_micros and existing_pricing >= 3:
                return False

            # Fetch nutrition data
            nutrition = await self.fetch_nutrition_data(ingredient.name)

            if nutrition:
                # Add macros if not exists
                if not existing_macros:
                    macros = IngredientMacros(
                        ingredientId=ingredient.id,
                        servingSize=nutrition.servingSize,
                        energyKcal=nutrition.energyKcal,
                        energyKj=nutrition.energyKj,
                        protein=nutrition.protein,
                        carbohydrates=nutrition.carbohydrates,
                        totalFiber=nutrition.totalFiber,
                        solubleFiber=nutrition.solubleFiber,
                        insolubleFiber=nutrition.insolubleFiber,
                        totalSugars=nutrition.totalSugars,
                        addedSugar=nutrition.addedSugar,
                        starch=nutrition.starch,
                        totalFat=nutrition.totalFat,
                        saturatedFat=nutrition.saturatedFat,
                        transFat=nutrition.transFat,
                        monounsaturatedFat=nutrition.monounsaturatedFat,
                        polyunsaturatedFat=nutrition.polyunsaturatedFat,
                        cholesterol=nutrition.cholesterol,
                    )
                    self.db.add(macros)

                # Add micros if not exists
                if not existing_micros:
                    micros = IngredientMicros(
                        ingredientId=ingredient.id,
                        servingSize=nutrition.servingSize,
                        vitaminA=nutrition.vitaminA,
                        vitaminC=nutrition.vitaminC,
                        vitaminD=nutrition.vitaminD,
                        vitaminE=nutrition.vitaminE,
                        vitaminK=nutrition.vitaminK,
                        thiamineB1=nutrition.thiamineB1,
                        riboflavinB2=nutrition.riboflavinB2,
                        niacinB3=nutrition.niacinB3,
                        pantothenicAcidB5=nutrition.pantothenicAcidB5,
                        vitaminB6=nutrition.vitaminB6,
                        biotinB7=nutrition.biotinB7,
                        folateB9=nutrition.folateB9,
                        vitaminB12=nutrition.vitaminB12,
                        choline=nutrition.choline,
                        calcium=nutrition.calcium,
                        iron=nutrition.iron,
                        magnesium=nutrition.magnesium,
                        phosphorus=nutrition.phosphorus,
                        potassium=nutrition.potassium,
                        sodium=nutrition.sodium,
                        zinc=nutrition.zinc,
                        copper=nutrition.copper,
                        manganese=nutrition.manganese,
                        selenium=nutrition.selenium,
                        fluoride=nutrition.fluoride,
                    )
                    self.db.add(micros)

            # Add pricing data if not exists
            if existing_pricing < 3:
                pricing_data_list = self.get_pricing_data(ingredient.name, ingredient.id)
                for pricing_data in pricing_data_list:
                    # Check if pricing already exists for this country
                    existing = self.db.query(IngredientPricing).filter(
                        IngredientPricing.ingredientId == ingredient.id,
                        IngredientPricing.countryId == pricing_data['countryId']
                    ).first()

                    if not existing:
                        pricing = IngredientPricing(**pricing_data)
                        self.db.add(pricing)

            self.db.commit()
            return True

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error populating ingredient '{ingredient.name}': {e}")
            return False

    async def populate_all_ingredients(self, limit: Optional[int] = None, batch_size: int = 50):
        """Populate all ingredients in the database"""
        # Get all ingredients
        query = self.db.query(Ingredient)
        if limit:
            query = query.limit(limit)

        ingredients = query.all()
        total = len(ingredients)

        logger.info(f"Starting data population for {total} ingredients...")

        # Process in batches to avoid overwhelming the API
        populated = 0
        skipped = 0

        for i in range(0, total, batch_size):
            batch = ingredients[i:i + batch_size]
            logger.info(f"Processing batch {i//batch_size + 1}/{(total + batch_size - 1)//batch_size}")

            for ingredient in batch:
                result = await self.populate_ingredient(ingredient)
                if result:
                    populated += 1
                else:
                    skipped += 1

                # Add a small delay to avoid rate limiting
                await asyncio.sleep(0.1)

            logger.info(f"Batch complete: {populated} populated, {skipped} skipped")

        logger.info(f"Data population complete: {populated} ingredients updated, {skipped} skipped")


async def main():
    """Main function to run the population script"""
    db = SessionLocal()

    try:
        populator = IngredientDataPopulator(db)

        # Check how many ingredients need data
        total_ingredients = db.query(Ingredient).count()
        ingredients_without_macros = db.query(Ingredient).outerjoin(
            IngredientMacros, Ingredient.id == IngredientMacros.ingredientId
        ).filter(IngredientMacros.id.is_(None)).count()

        ingredients_without_pricing = db.query(Ingredient).outerjoin(
            IngredientPricing, Ingredient.id == IngredientPricing.ingredientId
        ).filter(IngredientPricing.id.is_(None)).count()

        logger.info(f"Database status:")
        logger.info(f"  Total ingredients: {total_ingredients}")
        logger.info(f"  Without macros: {ingredients_without_macros}")
        logger.info(f"  Without pricing: {ingredients_without_pricing}")

        # Populate all ingredients
        await populator.populate_all_ingredients(limit=100, batch_size=20)

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
