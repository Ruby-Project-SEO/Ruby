import os
import re
import threading
import time

import requests


OPENFDA_FOOD_ENDPOINT = 'https://api.fda.gov/food/enforcement.json'
CACHE_SECONDS = 30 * 60
GENERIC_TOKENS = {
    'and', 'boneless', 'brand', 'breast', 'food', 'foods', 'fresh',
    'frozen', 'greek', 'meat', 'organic', 'product', 'raw', 'skinless',
    'the', 'whole', 'with',
}
CORE_FOOD_TOKENS = {
    'beef', 'blueberries', 'cheese', 'chicken', 'eggs', 'lettuce',
    'milk', 'oysters', 'peanuts', 'rice', 'salad', 'spinach',
    'strawberry', 'yogurt',
}

_cache = {'expires_at': 0, 'recalls': []}
_cache_lock = threading.Lock()


def _tokens(value):
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or '').casefold())
        if len(token) >= 4 and token not in GENERIC_TOKENS
    }


def _active_recalls():
    now = time.monotonic()
    with _cache_lock:
        if _cache['expires_at'] > now:
            return _cache['recalls']

    params = {
        'search': 'status:"Ongoing"',
        'sort': 'report_date:desc',
        'limit': 100,
    }
    api_key = os.environ.get('OPENFDA_API_KEY', '').strip()
    if api_key:
        params['api_key'] = api_key

    response = requests.get(
        OPENFDA_FOOD_ENDPOINT,
        params=params,
        timeout=12,
    )
    response.raise_for_status()
    recalls = response.json().get('results', [])

    with _cache_lock:
        _cache['recalls'] = recalls
        _cache['expires_at'] = now + CACHE_SECONDS
    return recalls


def _possible_matches(title, recalls):
    title_tokens = _tokens(title)
    matches = []
    for recall in recalls:
        product_description = recall.get('product_description') or ''
        recall_tokens = _tokens(product_description)
        overlap = title_tokens & recall_tokens
        distinctive_overlap = overlap - CORE_FOOD_TOKENS
        core_overlap = overlap & CORE_FOOD_TOKENS
        # A generic food word alone (for example "strawberry" or "chicken")
        # is not enough to connect someone's food to a specific recalled item.
        if not distinctive_overlap and len(core_overlap) < 2:
            continue

        matches.append({
            'product': ' '.join(product_description.split())[:180],
            'company': ' '.join(
                (recall.get('recalling_firm') or '').split()
            )[:90],
            'reason': ' '.join(
                (recall.get('reason_for_recall') or '').split()
            )[:220],
        })
        if len(matches) == 1:
            break
    return matches


def build_food_recall_profile(food_log, dismissed_food_ids=None):
    dismissed_food_ids = set(dismissed_food_ids or ())
    if not food_log:
        return {
            'available': True,
            'foods': [],
            'score': None,
            'status': 'Log food to begin',
            'matched_count': 0,
        }

    recalls = _active_recalls()
    foods = []
    matched_count = 0
    for row in food_log:
        food = dict(row)
        food['recall_matches'] = _possible_matches(food['title'], recalls)
        if food['id'] in dismissed_food_ids:
            food['recall_matches'] = []
            food['recall_dismissed'] = True
        else:
            food['recall_dismissed'] = False
        food['recall_status'] = (
            'Possible recall match'
            if food['recall_matches']
            else ('Checked by you' if food['recall_dismissed'] else 'No current match')
        )
        if food['recall_matches']:
            matched_count += 1
        foods.append(food)

    score = round((len(foods) - matched_count) / len(foods) * 100)
    if matched_count == 0:
        status = 'No current matches'
    elif matched_count == 1:
        status = 'Review 1 food'
    else:
        status = f'Review {matched_count} foods'
    return {
        'available': True,
        'foods': foods,
        'score': score,
        'status': status,
        'matched_count': matched_count,
    }
