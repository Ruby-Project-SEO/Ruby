import os
from urllib.parse import quote

import requests


USDA_API_ROOT = 'https://api.nal.usda.gov/fdc/v1'
USDA_API_KEY = (
    os.environ.get('USDA_FDC_API_KEY')
    or os.environ.get('FDC_API_KEY')
    or 'DEMO_KEY'
)
LOGO_DEV_PUBLISHABLE_KEY = os.environ.get('LOGO_DEV_PUBLISHABLE_KEY', '').strip()


def _nutrient_value(food, names, units=None):
    for item in food.get('foodNutrients') or []:
        nutrient = item.get('nutrient') or {}
        name = str(
            nutrient.get('name')
            or item.get('nutrientName')
            or ''
        ).lower()
        unit = str(
            nutrient.get('unitName')
            or item.get('unitName')
            or ''
        ).lower()
        if not any(name == candidate or name.startswith(f'{candidate} (') for candidate in names):
            continue
        if units is not None and unit not in units:
            continue
        try:
            return max(0.0, float(item.get('amount', item.get('value', 0)) or 0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _brand_logo_url(brand):
    if not brand or not LOGO_DEV_PUBLISHABLE_KEY:
        return ''
    return (
        f'https://img.logo.dev/name/{quote(brand, safe="")}'
        f'?token={quote(LOGO_DEV_PUBLISHABLE_KEY)}&size=80&format=png'
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
            'calories_per_100g': round(
                _nutrient_value(food, {'energy'}, {'kcal'}),
            ),
            'serving_grams': serving_grams,
            'serving_label': (
                food.get('householdServingFullText')
                or (f'{serving_grams:g} g' if serving_grams else '100 g')
            ),
        })
        results[-1]['logo_url'] = _brand_logo_url(results[-1]['brand'])
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
    def find(names, units=None):
        return _nutrient_value(food, names, units)

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
