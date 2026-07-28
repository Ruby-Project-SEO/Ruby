import os
from urllib.parse import quote

import requests


BEAUTY_SEARCH_URL = 'https://world.openbeautyfacts.org/cgi/search.pl'
OPENFDA_API_KEY = os.environ.get('OPENFDA_API_KEY', '').strip()
LOGO_DEV_KEY = os.environ.get('LOGO_DEV_PUBLISHABLE_KEY', '').strip()


def _logo_url(brand):
    if not brand or not LOGO_DEV_KEY:
        return ''
    return (
        f'https://img.logo.dev/name/{quote(brand, safe="")}'
        f'?token={quote(LOGO_DEV_KEY)}&size=96&format=png&fallback=404'
    )


def search_medications(query, limit=10):
    clean_query = query.replace('"', '').replace(':', ' ').strip()
    params = {
        'search': (
            f'(brand_name:"{clean_query}" OR generic_name:"{clean_query}")'
        ),
        'limit': min(max(limit, 1), 20),
    }
    if OPENFDA_API_KEY:
        params['api_key'] = OPENFDA_API_KEY
    response = requests.get(
        'https://api.fda.gov/drug/ndc.json',
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    results = []
    for product in response.json().get('results', []):
        brand = (product.get('brand_name') or '').strip()
        generic_name = (product.get('generic_name') or '').strip()
        labeler = (product.get('labeler_name') or '').strip()
        strengths = []
        for ingredient in product.get('active_ingredients') or []:
            strength = (ingredient.get('strength') or '').strip()
            if strength and strength not in strengths:
                strengths.append(strength)
        results.append({
            'name': brand or generic_name or 'Unnamed medication',
            'generic_name': generic_name,
            'brand': brand,
            'labeler': labeler,
            'rxcui': str(product.get('product_ndc') or ''),
            'strengths': strengths[:12],
            'dosage_form': (product.get('dosage_form') or '').strip(),
            'logo_url': _logo_url(labeler),
            'source': 'FDA NDC',
        })
    return results


def search_cosmetics(query, limit=10):
    response = requests.get(
        BEAUTY_SEARCH_URL,
        params={
            'search_terms': query,
            'search_simple': 1,
            'action': 'process',
            'json': 1,
            'page_size': min(max(limit, 1), 20),
            'fields': 'code,product_name,brands,image_front_small_url,image_front_url',
        },
        headers={'User-Agent': 'Ruby Wellness/1.0'},
        timeout=12,
    )
    response.raise_for_status()
    results = []
    for product in response.json().get('products', []):
        name = (product.get('product_name') or '').strip()
        if not name:
            continue
        brand = (product.get('brands') or '').split(',')[0].strip()
        results.append({
            'code': str(product.get('code') or ''),
            'name': name,
            'brand': brand,
            'image_url': (
                product.get('image_front_small_url')
                or product.get('image_front_url')
                or ''
            ),
            'logo_url': _logo_url(brand),
            'source': 'Open Beauty Facts',
        })
    return results


def _openfda_result(endpoint, search):
    params = {'search': search, 'limit': 1}
    if OPENFDA_API_KEY:
        params['api_key'] = OPENFDA_API_KEY
    response = requests.get(
        f'https://api.fda.gov/{endpoint}.json',
        params=params,
        timeout=8,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    results = response.json().get('results') or []
    return results[0] if results else None


def medication_safety_note(name):
    clean_name = name.split(' (', 1)[0].replace('"', '').strip()
    try:
        match = _openfda_result(
            'drug/enforcement',
            f'openfda.generic_name:"{clean_name}"',
        )
    except requests.RequestException:
        return 'Safety check temporarily unavailable'
    if not match:
        return 'No current FDA recall match found'
    classification = match.get('classification') or 'FDA recall'
    return f'{classification} recall match — check details'


def cosmetic_safety_note(name, brand):
    search_term = (brand or name).replace('"', '').strip()
    try:
        match = _openfda_result(
            'cosmetic/event',
            f'products.name_brand:"{search_term}"',
        )
    except requests.RequestException:
        return 'Safety check temporarily unavailable'
    if not match:
        return 'No current FDA event match found'
    return 'FDA adverse-event report match — check details'
