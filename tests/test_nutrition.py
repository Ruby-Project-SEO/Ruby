from backend.services.nutrition import food_nutrients_per_100g


def test_reads_current_usda_atwater_energy_name():
    food = {
        'foodNutrients': [
            {
                'nutrient': {
                    'name': 'Energy (Atwater General Factors)',
                    'unitName': 'kcal',
                },
                'amount': 106.034,
            },
            {
                'nutrient': {'name': 'Protein', 'unitName': 'g'},
                'amount': 22.525,
            },
        ],
    }

    nutrients = food_nutrients_per_100g(food)

    assert nutrients['calories'] == 106.034
    assert nutrients['protein_g'] == 22.525


def test_reads_flat_nutrients_from_usda_search_results():
    food = {
        'foodNutrients': [
            {
                'nutrientName': 'Energy',
                'unitName': 'KCAL',
                'value': 165,
            },
        ],
    }

    assert food_nutrients_per_100g(food)['calories'] == 165
