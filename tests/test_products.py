from backend.services import products


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_medication_search_normalizes_strengths(monkeypatch):
    payload = {
        'results': [{
            'product_ndc': '68788-8528',
            'brand_name': 'Metformin Hydrochloride',
            'generic_name': 'Metformin Hydrochloride',
            'labeler_name': 'Preferred Pharmaceuticals Inc.',
            'dosage_form': 'TABLET',
            'active_ingredients': [
                {'name': 'METFORMIN HYDROCHLORIDE', 'strength': '850 mg/1'},
            ],
        }],
    }
    monkeypatch.setattr(
        products.requests,
        'get',
        lambda *args, **kwargs: FakeResponse(payload),
    )

    results = products.search_medications('metformin')

    assert results == [{
        'name': 'Metformin Hydrochloride',
        'generic_name': 'Metformin Hydrochloride',
        'brand': 'Metformin Hydrochloride',
        'labeler': 'Preferred Pharmaceuticals Inc.',
        'rxcui': '68788-8528',
        'strengths': ['850 mg/1'],
        'dosage_form': 'TABLET',
        'logo_url': products._logo_url('Preferred Pharmaceuticals Inc.'),
        'source': 'FDA NDC',
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
