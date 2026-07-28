import os

import requests


USDA_API_ROOT = 'https://api.nal.usda.gov/fdc/v1'
USDA_API_KEY = (
    os.environ.get('USDA_FDC_API_KEY')
    or os.environ.get('FDC_API_KEY')
    or 'DEMO_KEY'
)


def _serving_grams(food):
    try:
        size = float(food.get('servingSize') or 0)
    except (TypeError, ValueError):
        size = 0
    unit = str(food.get('servingSizeUnit') or '').lower()
    if size > 0 and unit in {'g', 'gram', 'grams'}:
        return round(size, 2)
    if size > 0 and unit in {'oz', 'ounce', 'ounces'}:
        return round(size * 28.3495, 2)

    for portion in food.get('foodPortions') or []:
        try:
            grams = float(portion.get('gramWeight') or 0)
        except (TypeError, ValueError):
            grams = 0
        if grams > 0:
            return round(grams, 2)
    return None


def search_usda_foods(query, limit=12):
    response = requests.get(
        f'{USDA_API_ROOT}/foods/search',
        params={
            'api_key': USDA_API_KEY,
            'query': query,
            'pageSize': min(max(limit, 1), 25),
        },
        timeout=10,
    )
    response.raise_for_status()
    results = []
    for food in response.json().get('foods', []):
        serving_grams = _serving_grams(food)
        results.append({
            'fdc_id': food.get('fdcId'),
            'name': food.get('description') or 'Unnamed food',
            'brand': food.get('brandOwner') or food.get('brandName') or '',
            'serving_grams': serving_grams,
            'serving_label': (
                food.get('householdServingFullText')
                or (f'{serving_grams:g} g' if serving_grams else '100 g')
            ),
        })
    return [result for result in results if result['fdc_id']]


def get_usda_food(fdc_id):
    response = requests.get(
        f'{USDA_API_ROOT}/food/{int(fdc_id)}',
        params={'api_key': USDA_API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def food_nutrients_per_100g(food):
    nutrients = {}
    for item in food.get('foodNutrients') or []:
        nutrient = item.get('nutrient') or {}
        name = str(nutrient.get('name') or '').lower()
        unit = str(nutrient.get('unitName') or '').lower()
        try:
            amount = max(0.0, float(item.get('amount') or 0))
        except (TypeError, ValueError):
            amount = 0.0
        nutrients[(name, unit)] = amount

    def find(names, units=None):
        for (name, unit), amount in nutrients.items():
            if name in names and (units is None or unit in units):
                return amount
        return 0.0

    return {
        'calories': find({'energy'}, {'kcal'}),
        'protein_g': find({'protein'}, {'g'}),
        'carbs_g': find({'carbohydrate, by difference'}, {'g'}),
        'fat_g': find({'total lipid (fat)'}, {'g'}),
        'fiber_g': find({'fiber, total dietary'}, {'g'}),
        'calcium_mg': find({'calcium, ca'}, {'mg'}),
        'iron_mg': find({'iron, fe'}, {'mg'}),
        'potassium_mg': find({'potassium, k'}, {'mg'}),
        'vitamin_c_mg': find(
            {'vitamin c, total ascorbic acid', 'vitamin c, ascorbic acid'},
            {'mg'},
        ),
        'vitamin_d_mcg': find(
            {'vitamin d (d2 + d3)', 'vitamin d3 (cholecalciferol)'},
            {'ug', 'µg'},
        ),
    }


def food_log_values(food, amount, unit):
    serving_grams = _serving_grams(food)
    if unit == 'serving':
        if not serving_grams:
            raise ValueError('A serving size is unavailable for this food.')
        grams = amount * serving_grams
    else:
        grams = amount

    if grams <= 0 or grams > 5000:
        raise ValueError('Enter an amount between 1 and 5,000 grams.')

    scale = grams / 100
    per_100g = food_nutrients_per_100g(food)
    values = {
        key: round(value * scale, 2)
        for key, value in per_100g.items()
    }
    values['amount_grams'] = round(grams, 2)
    values['title'] = food.get('description') or 'Unnamed food'
    return values
