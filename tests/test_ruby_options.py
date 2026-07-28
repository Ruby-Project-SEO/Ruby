import backend.app as app_module


def test_cosmetic_options_use_product_images(monkeypatch):
    monkeypatch.setattr(
        app_module,
        'search_cosmetics',
        lambda term, limit: [{
            'name': 'Hydrating Eye Cream',
            'brand': 'Example Brand',
            'image_url': 'https://images.example/eye-cream.jpg',
            'logo_url': 'https://logos.example/brand.png',
        }],
    )

    options = app_module.ruby_category_options('cosmetics', ['eye cream'])

    assert options == [{
        'Cosmetic': 'Hydrating Eye Cream',
        'Price': 'N/A',
        'Image': 'https://images.example/eye-cream.jpg',
        'Detail': 'Example Brand',
    }]


def test_drug_options_require_model_supplied_medication_name(monkeypatch):
    monkeypatch.setattr(
        app_module,
        'search_medications',
        lambda term, limit: [{
            'name': 'Example Medication',
            'generic_name': 'Example Generic',
            'labeler': 'Example Labeler',
            'logo_url': 'https://logos.example/labeler.png',
        }],
    )

    options = app_module.ruby_category_options('drugs', ['example medication'])

    assert options[0]['Drug'] == 'Example Medication'
    assert options[0]['Detail'] == 'Example Labeler'
