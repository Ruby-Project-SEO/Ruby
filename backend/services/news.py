import os
import hashlib
import threading
import time
from datetime import datetime

import requests


NEWSDATA_ENDPOINT = 'https://newsdata.io/api/1/latest'
OPENFDA_FOOD_ENDPOINT = 'https://api.fda.gov/food/enforcement.json'
NEWS_CACHE_SECONDS = 30 * 60
NEWS_QUERY = 'recall OR outbreak OR salmonella OR listeria OR "food safety"'
SAFETY_TERMS = {
    'allergen',
    'contaminated',
    'contamination',
    'cyclospora',
    'e. coli',
    'food safety',
    'foodborne',
    'listeria',
    'outbreak',
    'recall',
    'recalled',
    'salmonella',
}
FOOD_TERMS = {
    'beef',
    'chicken',
    'dairy',
    'egg',
    'food',
    'lettuce',
    'meal',
    'meat',
    'milk',
    'oyster',
    'produce',
    'product',
    'restaurant',
    'salad',
    'supplement',
}

_cache = {
    'expires_at': 0,
    'articles': [],
    'watch_items': [],
}
_cache_lock = threading.Lock()


def _plain_text(value):
    return ' '.join((value or '').split())


def _published_label(value):
    if not value:
        return ''
    try:
        published = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            published = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return value
    return published.strftime('%b %-d, %Y')


def _is_food_safety_article(article):
    searchable = ' '.join((
        article.get('title') or '',
        article.get('description') or '',
        article.get('content') or '',
    )).lower()
    return (
        any(term in searchable for term in SAFETY_TERMS)
        and any(term in searchable for term in FOOD_TERMS)
    )


def _normalize_article(article):
    summary = _plain_text(
        article.get('description')
        or article.get('content')
    )
    article_id = article.get('article_id') or article.get('link')
    return {
        'id': article_id,
        'anchor': hashlib.sha1(article_id.encode()).hexdigest()[:10],
        'title': _plain_text(article.get('title')),
        'summary': summary[:520],
        'url': article.get('link'),
        'image_url': article.get('image_url'),
        'source': (
            article.get('source_name')
            or article.get('source_id')
            or 'News source'
        ),
        'source_icon': article.get('source_icon'),
        'published': _published_label(article.get('pubDate')),
    }


def _short_product_name(description):
    name = _plain_text(description).split(',', 1)[0].strip(' .:-')
    return name[:72] or 'Recalled food product'


def _get_fda_watch_items(limit=5):
    params = {
        'search': 'status:"Ongoing"',
        'sort': 'report_date:desc',
        'limit': 30,
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
    results = response.json().get('results', [])

    watch_items = []
    seen_names = set()
    for recall in results:
        name = _short_product_name(recall.get('product_description'))
        normalized_name = name.casefold()
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        watch_items.append({
            'name': name,
            'company': _plain_text(recall.get('recalling_firm'))[:80],
            'reason': _plain_text(recall.get('reason_for_recall'))[:180],
            'level': recall.get('classification') or 'Recall',
            'reported': _published_label(recall.get('report_date')),
            'recall_number': recall.get('recall_number'),
        })
        if len(watch_items) == limit:
            break
    return watch_items


def get_food_safety_feed(article_limit=5, watch_limit=5):
    now = time.monotonic()
    with _cache_lock:
        if _cache['expires_at'] > now:
            return {
                'articles': _cache['articles'][:article_limit],
                'watch_items': _cache['watch_items'][:watch_limit],
            }

    api_key = os.environ.get('NEWSDATA_API_KEY', '').strip()
    articles = []
    if api_key:
        response = requests.get(
            NEWSDATA_ENDPOINT,
            params={
                'apikey': api_key,
                'qInTitle': NEWS_QUERY,
                'language': 'en',
                'image': 1,
            },
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get('status') != 'success':
            raise RuntimeError(payload.get('message') or 'NewsData request failed')

        articles = [
            _normalize_article(article)
            for article in payload.get('results', [])
            if _is_food_safety_article(article)
            and article.get('title')
            and article.get('link')
        ]

    unique_articles = []
    seen_urls = set()
    for article in articles:
        if article['url'] in seen_urls:
            continue
        seen_urls.add(article['url'])
        unique_articles.append(article)

    watch_items = _get_fda_watch_items(watch_limit)

    with _cache_lock:
        _cache['articles'] = unique_articles
        _cache['watch_items'] = watch_items
        _cache['expires_at'] = now + NEWS_CACHE_SECONDS

    return {
        'articles': unique_articles[:article_limit],
        'watch_items': watch_items,
    }
