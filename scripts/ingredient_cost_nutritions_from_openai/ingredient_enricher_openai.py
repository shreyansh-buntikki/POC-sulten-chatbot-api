#!/usr/bin/env python3
"""
OpenAI-Powered Ingredient Enricher
Uses OpenAI GPT to fetch nutritional data AND pricing for ingredients
Handles Norwegian ingredient names automatically
"""

import csv
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
import os

try:
    from openai import OpenAI
except ImportError:
    print("Installing openai package...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'openai'])
    from openai import OpenAI


class OpenAIIngredientEnricher:
    def __init__(self, openai_api_key: str):
        self.client = OpenAI(api_key=openai_api_key)
        
        self.macros_fields = [
            "servingSize", "energyKcal", "energyKj", "protein", "carbohydrates",
            "totalFiber", "solubleFiber", "insolubleFiber", "totalSugars",
            "addedSugar", "starch", "totalFat", "saturatedFat", "transFat",
            "monounsaturatedFat", "polyunsaturatedFat", "cholesterol"
        ]
        
        self.micros_fields = [
            "servingSize", "vitaminA", "vitaminC", "vitaminD", "vitaminE",
            "vitaminK", "thiamineB1", "riboflavinB2", "niacinB3",
            "pantothenicAcidB5", "vitaminB6", "biotinB7", "folateB9",
            "vitaminB12", "choline", "calcium", "iron", "magnesium",
            "phosphorus", "potassium", "sodium", "zinc", "copper",
            "manganese", "selenium", "fluoride"
        ]
        
        self.measuring_units = self.load_measuring_units()
        
    def load_measuring_units(self) -> Dict:
        """Load measuring units from CSV"""
        units = {}
        try:
            with open('measuring_unit_translation_202602101948.csv', 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    units[row['id']] = {
                        'name': row['name'],
                        'longName': row['longName'],
                        'languageId': row['languageId'],
                        'measuringUnitId': row['measuringUnitId']
                    }
        except Exception as e:
            print(f"Warning: Could not load measuring units: {e}")
        return units
    
    def read_ingredients(self, start: int = 0, limit: int = 20) -> List[Dict]:
        """Read ingredients from CSV"""
        ingredients = []
        try:
            with open('ingredient_202602101938.csv', 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if i < start:
                        continue
                    if i >= start + limit:
                        break
                    ingredients.append({
                        'id': row.get('id', ''),
                        'name': row['name'],
                        'languageId': row.get('languageId', 'no'),
                        'index': i
                    })
        except Exception as e:
            print(f"Error reading ingredients: {e}")
        
        return ingredients
    
    def create_prompt(self, ingredient_name: str, language: str = "no") -> str:
        """Create a detailed prompt for OpenAI"""
        return f"""You are a nutrition expert. Provide comprehensive nutritional data and pricing information for the ingredient: "{ingredient_name}" (language: {language}).

CRITICAL - Unit Selection by Country/Language:

NORWEGIAN UNITS (for Norway pricing) - Use exact "name" from system:
- stk - whole countable items: eggs, bananas, apples, onions, potatoes, tomatoes
- kg - solid ingredients by weight: flour, sugar, rice, meat, cheese
- g - smaller weight: spices, herbs, nuts, seeds
- l - larger liquids: milk, juice, oil, water
- ml - smaller liquids: vanilla extract, soy sauce
- dl - medium liquids: cream, cooking wine
- cl - small liquids
- pk - packaged items: bacon, pasta, cheese packs
- boks - canned items: tomatoes, tuna, coconut milk
- fedd - garlic clove
- skiver - bread, cheese slices
- ts - small measures
- ss - medium measures
- kopp - cup measures
- klype - pinch of spice
- håndfull - handful measure
- tommel - thumb measure
- Pose - bag/pouch
- kuler - scoops

ENGLISH UNITS (for India & USA pricing) - Use exact "name" from system:
- x - whole countable items (same as stk)
- kg - solid ingredients by weight
- g - smaller weight
- l - larger liquids
- ml - smaller liquids
- dl - medium liquids
- cl - small liquids
- pack - packaged items (same as pk)
- can - canned items (same as boks)
- clove - garlic clove (same as fedd)
- slices - bread, cheese slices (same as skiver)
- tsp - small measures (same as ts)
- tbsp - medium measures (same as ss)
- c - cup measures (same as kopp)
- pinch - pinch of spice (same as klype)
- handful - handful measure (same as håndfull)
- thumb - thumb measure (same as tommel)
- Bag - bag/pouch (same as Pose)
- scoops - scoops (same as kuler)

PRICING RULES:
- Select the most appropriate unit based on how ingredient is typically SOLD in stores
- Norway: Use exact Norwegian unit "name" from system (stk, kg, g, l, ml, dl, pk, boks, fedd, etc.)
- India & USA: Use exact English unit "name" from system (x, kg, g, l, ml, dl, pack, can, clove, etc.)
- Price should be for 1 unit (e.g., 1 kg, 1 liter, 1 piece)
- Use realistic average retail prices in local currency

Return ONLY a valid JSON object with this EXACT structure (use 0 for unknown values):

{{
  "englishName": "ingredient name in English",
  "macros": {{
    "servingSize": "100g",
    "energyKcal": 0,
    "energyKj": 0,
    "protein": 0,
    "carbohydrates": 0,
    "totalFiber": 0,
    "solubleFiber": 0,
    "insolubleFiber": 0,
    "totalSugars": 0,
    "addedSugar": 0,
    "starch": 0,
    "totalFat": 0,
    "saturatedFat": 0,
    "transFat": 0,
    "monounsaturatedFat": 0,
    "polyunsaturatedFat": 0,
    "cholesterol": 0
  }},
  "micros": {{
    "servingSize": "100g",
    "vitaminA": 0,
    "vitaminC": 0,
    "vitaminD": 0,
    "vitaminE": 0,
    "vitaminK": 0,
    "thiamineB1": 0,
    "riboflavinB2": 0,
    "niacinB3": 0,
    "pantothenicAcidB5": 0,
    "vitaminB6": 0,
    "biotinB7": 0,
    "folateB9": 0,
    "vitaminB12": 0,
    "choline": 0,
    "calcium": 0,
    "iron": 0,
    "magnesium": 0,
    "phosphorus": 0,
    "potassium": 0,
    "sodium": 0,
    "zinc": 0,
    "copper": 0,
    "manganese": 0,
    "selenium": 0,
    "fluoride": 0
  }},
  "pricing": {{
    "norway": {{
      "price": 0,
      "currency": "NOK",
      "unit": "Use NORWEGIAN unit name (stk, kg, g, l, ml, dl, cl, pk, boks, fedd, skiver, ts, ss, kopp, klype, håndfull, tommel, Pose, kuler)",
      "pricePerUnit": 0
    }},
    "india": {{
      "price": 0,
      "currency": "INR",
      "unit": "Use ENGLISH unit name (x, kg, g, l, ml, dl, cl, pack, can, clove, slices, tsp, tbsp, c, pinch, handful, thumb, Bag, scoops)",
      "pricePerUnit": 0
    }},
    "usa": {{
      "price": 0,
      "currency": "USD",
      "unit": "Use ENGLISH unit name (x, kg, g, l, ml, dl, cl, pack, can, clove, slices, tsp, tbsp, c, pinch, handful, thumb, Bag, scoops)",
      "pricePerUnit": 0
    }}
  }}
}}

Important:
- All numeric values should be numbers (not strings)
- Norway MUST use exact Norwegian unit names from system (stk, boks, pk, fedd, etc.)
- India & USA MUST use exact English unit names from system (x, can, pack, clove, etc.)
- The unit TYPE should be the same (e.g., if Norway uses "stk" for banana, India/USA use "x")
- Use typical/average retail prices in local currency for that specific unit
- Examples:
  * Banana: norway {{"unit": "stk", "price": 15}}, india {{"unit": "x", "price": 10}}, usa {{"unit": "x", "price": 0.50}}
  * Milk (1L): norway {{"unit": "l", "price": 20}}, india {{"unit": "l", "price": 60}}, usa {{"unit": "l", "price": 3.50}}
  * Flour (1kg): norway {{"unit": "kg", "price": 25}}, india {{"unit": "kg", "price": 50}}, usa {{"unit": "kg", "price": 3.00}}
  * Garlic clove: norway {{"unit": "fedd", "price": 2}}, india {{"unit": "clove", "price": 1}}, usa {{"unit": "clove", "price": 0.15}}
  * Canned tomatoes: norway {{"unit": "boks", "price": 18}}, india {{"unit": "can", "price": 40}}, usa {{"unit": "can", "price": 1.20}}
  * Bacon pack: norway {{"unit": "pk", "price": 45}}, india {{"unit": "pack", "price": 150}}, usa {{"unit": "pack", "price": 5.00}}
- energyKj should be energyKcal × 4.184
- Return ONLY the JSON, no explanations"""
    
    def fetch_data_from_openai(self, ingredient_name: str, language: str = "no") -> Optional[Dict]:
        """Fetch nutritional and pricing data using OpenAI"""
        print(f"  Querying OpenAI for: {ingredient_name}")
        
        try:
            prompt = self.create_prompt(ingredient_name, language)
            
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Using cost-effective model
                messages=[
                    {"role": "system", "content": "You are a nutrition and food pricing expert. Always return valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent results
                max_tokens=1500
            )
            
            content = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if content.startswith('```'):
                content = content.split('```')[1]
                if content.startswith('json'):
                    content = content[4:]
                content = content.strip()
            
            data = json.loads(content)
            print(f"    ✓ Data retrieved successfully")
            return data
            
        except json.JSONDecodeError as e:
            print(f"    ✗ JSON parsing error: {e}")
            print(f"    Response: {content[:200]}...")
            return None
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return None
    
    def enrich_ingredient(self, ingredient: Dict) -> Dict:
        """Enrich a single ingredient with all data using OpenAI"""
        name = ingredient['name']
        print(f"\n[{ingredient['index'] + 1}] Processing: {name}")
        
        # Fetch data from OpenAI
        ai_data = self.fetch_data_from_openai(name, ingredient['languageId'])
        
        # If OpenAI fails, create empty structure
        if not ai_data:
            ai_data = {
                'englishName': name,
                'macros': {field: 0 for field in self.macros_fields},
                'micros': {field: 0 for field in self.micros_fields},
                'pricing': {
                    'norway': {'price': None, 'currency': 'NOK', 'unit': 'kg', 'pricePerUnit': None},
                    'india': {'price': None, 'currency': 'INR', 'unit': 'kg', 'pricePerUnit': None},
                    'usa': {'price': None, 'currency': 'USD', 'unit': 'kg', 'pricePerUnit': None}
                }
            }
            ai_data['macros']['servingSize'] = '100g'
            ai_data['micros']['servingSize'] = '100g'
            print(f"    Using empty data structure")
        
        # Compile enriched data
        enriched = {
            'id': ingredient['id'],
            'name': name,
            'englishName': ai_data.get('englishName', name),
            'languageId': ingredient['languageId'],
            'macros': ai_data.get('macros', {}),
            'micros': ai_data.get('micros', {}),
            'pricing': ai_data.get('pricing', {}),
            'metadata': {
                'processedAt': datetime.now().isoformat(),
                'dataSource': 'OpenAI GPT-4',
                'hasNutritionData': bool(ai_data.get('macros', {}).get('energyKcal', 0) > 0),
                'hasPricingData': any(
                    ai_data.get('pricing', {}).get(country, {}).get('price') is not None
                    for country in ['india', 'norway', 'usa']
                )
            }
        }
        
        # Small delay to respect rate limits
        time.sleep(0.5)
        
        return enriched
    
    def process_batch(self, start: int = 0, limit: int = 20, batch_size: int = 10) -> List[Dict]:
        """Process a batch of ingredients"""
        print(f"\n{'='*70}")
        print(f"Reading ingredients {start+1} to {start+limit}...")
        print(f"{'='*70}")
        
        ingredients = self.read_ingredients(start, limit)
        
        print(f"\nProcessing {len(ingredients)} ingredients\n")
        
        enriched_ingredients = []
        success_nutrition = 0
        success_pricing = 0
        
        for i, ingredient in enumerate(ingredients):
            try:
                enriched = self.enrich_ingredient(ingredient)
                enriched_ingredients.append(enriched)
                
                if enriched['metadata']['hasNutritionData']:
                    success_nutrition += 1
                if enriched['metadata']['hasPricingData']:
                    success_pricing += 1
                
                # Save intermediate results every batch_size items
                if (i + 1) % batch_size == 0:
                    self.save_to_json(enriched_ingredients, f'enriched_ingredients_partial_{start}_{i+1}.json')
                    print(f"\n    💾 Saved intermediate results ({i+1} items)")
                
            except Exception as e:
                print(f"❌ Error processing {ingredient['name']}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        print(f"\n{'='*70}")
        print(f"✅ Nutrition data: {success_nutrition}/{len(enriched_ingredients)}")
        print(f"✅ Pricing data: {success_pricing}/{len(enriched_ingredients)}")
        print(f"{'='*70}")
        
        return enriched_ingredients
    
    def save_to_json(self, data: List[Dict], filename: str = 'enriched_ingredients.json'):
        """Save enriched data to JSON file"""
        output = {
            'metadata': {
                'generatedAt': datetime.now().isoformat(),
                'totalIngredients': len(data),
                'version': '3.0 - OpenAI Powered',
                'dataSource': 'OpenAI GPT-4o-mini',
                'dataFields': {
                    'macros': self.macros_fields,
                    'micros': self.micros_fields
                },
                'withNutrition': sum(1 for i in data if i['metadata']['hasNutritionData']),
                'withPricing': sum(1 for i in data if i['metadata']['hasPricingData'])
            },
            'ingredients': data
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        file_size_kb = len(json.dumps(output)) / 1024
        print(f"\n💾 Saved to: {filename}")
        print(f"   Size: {file_size_kb:.2f} KB")
        print(f"   Ingredients: {len(data)}")


def main():
    print("\n" + "="*70)
    print("🤖 OpenAI-Powered Ingredient Enricher v3.0")
    print("   Fetches Nutrition Data + Pricing from OpenAI")
    print("="*70)
    
    # Get OpenAI API key
    # Option 1: Hardcode your key here (NOT recommended for security)
 
    api_key = "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"  # <-- PUT YOUR KEY HERE
    
    # Option 2: Enter when prompted (comment out line above and uncomment below)
    # api_key = input("\nEnter OpenAI API key (or press Enter to use env var OPENAI_API_KEY): ").strip()
    # if not api_key:
    #     api_key = os.getenv('OPENAI_API_KEY')
    #     if not api_key:
    #         print("❌ No API key provided!")
    #         print("   Set OPENAI_API_KEY environment variable or enter key when prompted")
    #         return
    #     print("✓ Using API key from environment variable")
    
    enricher = OpenAIIngredientEnricher(openai_api_key=api_key)
    
    # Set defaults (modify these values as needed)
    start_index = 0
    num_ingredients = 2841  # Process all ingredients
    
    print(f"\n📋 Processing {num_ingredients} ingredients starting from #{start_index}")
    print(f"   (Edit lines 409-410 in script to change these values)")
    
    # Process ingredients
    enriched_data = enricher.process_batch(start=start_index, limit=num_ingredients)
    
    # Save to JSON
    enricher.save_to_json(enriched_data, 'enriched_ingredients_openai.json')
    
    print("\n" + "="*70)
    print("✅ Processing complete!")
    print("="*70)
    
    # Show summary
    with_nutrition = sum(1 for ing in enriched_data if ing['metadata']['hasNutritionData'])
    with_pricing = sum(1 for ing in enriched_data if ing['metadata']['hasPricingData'])
    
    print(f"\n📊 Summary:")
    print(f"   Total processed: {len(enriched_data)}")
    print(f"   With nutrition data: {with_nutrition} ({with_nutrition/len(enriched_data)*100:.1f}%)")
    print(f"   With pricing data: {with_pricing} ({with_pricing/len(enriched_data)*100:.1f}%)")
    print(f"\n   Output file: enriched_ingredients_openai.json")


if __name__ == "__main__":
    main()
