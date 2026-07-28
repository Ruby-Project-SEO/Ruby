import os
from urllib.parse import quote

import requests


RXTERMS_SEARCH_URL = 'https://clinicaltables.nlm.nih.gov/api/rxterms/v3/search'
BEAUTY_SEARCH_URL = 'https://world.openbeautyfacts.org/cgi/search.pl'
OPENFDA_API_KEY = os.environ.get('OPENFDA_API_KEY', '').strip()
LOGO_DEV_KEY = os.environ.get('LOGO_DEV_PUBLISHABLE_KEY', '').strip()


def _logo_url(brand):
    if not brand or not LOGO_DEV_KEY:
        return ''
    return (
        f'https://img.logo.dev/name/{quote(brand, safe="")}'
        f'?token={quote(LOGO_DEV_KEY)}&size=96&format=png'
    )


def search_medications(query, limit=10):
    response = requests.get(
        RXTERMS_SEARCH_URL,
        params={
            'terms': query,
            'ef': 'RXCUIS,STRENGTHS_AND_FORMS',
            'maxList': min(max(limit, 1), 20),
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    names = payload[1] if len(payload) > 1 else []
    extra = payload[2] if len(payload) > 2 else {}
    rxcuis = extra.get('RXCUIS') or []
    strengths = extra.get('STRENGTHS_AND_FORMS') or []
    results = []
    for index, name in enumerate(names):
        choices = strengths[index] if index < len(strengths) else []
        ids = rxcuis[index] if index < len(rxcuis) else []
        results.append({
            'name': name,
            'rxcui': str(ids[0]) if ids else '',
            'strengths': [item.strip() for item in choices[:12] if item.strip()],
            'source': 'RxNorm',
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
