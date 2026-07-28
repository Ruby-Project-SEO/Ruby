from backend.services import products


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_medication_search_normalizes_strengths(monkeypatch):
    payload = [
        1,
        ['metFORMIN (Oral Pill)'],
        {
            'RXCUIS': [['861007']],
            'STRENGTHS_AND_FORMS': [['  500 mg Tab', '1,000 mg Tab']],
        },
    ]
    monkeypatch.setattr(
        products.requests,
        'get',
        lambda *args, **kwargs: FakeResponse(payload),
    )

    results = products.search_medications('metformin')

    assert results == [{
        'name': 'metFORMIN (Oral Pill)',
        'rxcui': '861007',
        'strengths': ['500 mg Tab', '1,000 mg Tab'],
        'source': 'RxNorm',
    }]


def test_cosmetic_search_keeps_brand_and_product_image(monkeypatch):
    payload = {
        'products': [{
            'code': '3337875597180',
            'product_name': 'Hydrating Cleanser',
            'brands': 'CeraVe',
            'image_front_small_url': 'https://images.example/cleanser.jpg',
        }],
    }
    monkeypatch.setattr(
        products.requests,
        'get',
        lambda *args, **kwargs: FakeResponse(payload),
    )
    monkeypatch.setattr(products, 'LOGO_DEV_KEY', 'publishable-test-key')

    result = products.search_cosmetics('cleanser')[0]

    assert result['name'] == 'Hydrating Cleanser'
    assert result['brand'] == 'CeraVe'
    assert result['image_url'] == 'https://images.example/cleanser.jpg'
    assert '/name/CeraVe?' in result['logo_url']
