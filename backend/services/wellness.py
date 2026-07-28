import requests
import pandas as pd
import sqlalchemy as db
import os
import shutil
from pathlib import Path

from google import genai


spoonacular_key = (os.getenv('SPOONACULAR_KEY') or '').strip()

def get_genai_client():
  api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GENAI_KEY')
  if not api_key:
    raise RuntimeError('GEMINI_API_KEY or GENAI_KEY is not configured')
  return genai.Client(api_key=api_key)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_ROOT = PROJECT_ROOT / "database"
DATABASE_ROOT.mkdir(parents=True, exist_ok=True)
ITEM_DATABASE = DATABASE_ROOT / "item_status.db"
LEGACY_ITEM_DATABASE = PROJECT_ROOT / "item_status.db"
if not ITEM_DATABASE.exists() and LEGACY_ITEM_DATABASE.exists():
  shutil.copy2(LEGACY_ITEM_DATABASE, ITEM_DATABASE)

foodurl = "https://api.spoonacular.com/food/products/search"
drugurl = "https://api.fda.gov/drug/label.json"
cosmeticurl = "https://world.openbeautyfacts.org/cgi/search.pl"
engine = db.create_engine(f"sqlite:///{ITEM_DATABASE}")

def search_recipes(ingredients):
  spoonacular_key = (os.getenv("SPOONACULAR_KEY") or '').strip()

  if not spoonacular_key:
    raise RuntimeError("SPOONACULAR_KEY is not configured")

  url = "https://api.spoonacular.com/recipes/findByIngredients"

  response = requests.get(
    url,
    params={
      "apiKey": spoonacular_key,
      "ingredients": ingredients,
      "number": 12,
      "ranking": 1,
      "ignorePantry": True
    },
    timeout=10
  )

  response.raise_for_status()

  return response.json()

def get_recipe_details(recipe_id, include_nutrition=False):
  spoonacular_key = (os.getenv("SPOONACULAR_KEY") or '').strip()

  if not spoonacular_key:
    raise RuntimeError("SPOONACULAR_KEY is not configured")

  url = f"https://api.spoonacular.com/recipes/{recipe_id}/information"

  response = requests.get(
    url,
    params={
        "apiKey": spoonacular_key,
        "includeNutrition": include_nutrition
    },
    timeout=10
  )

  response.raise_for_status()

  return response.json()


def search_food(food, price):
    if not spoonacular_key:
      raise RuntimeError("SPOONACULAR_KEY is not configured")

    response = requests.get(foodurl,
                            params={"apiKey": spoonacular_key,
                                    "query": food,
                                    "number": 1},
                            timeout=10)
    response.raise_for_status()

    food_status = response.json()

    if "products" not in food_status:
      return None
    
    results = []
    
    items = food_status["products"]
    for item in items:
      image = item.get("image")
      if image and not image.startswith(("http://", "https://")):
        image = f"https://img.spoonacular.com/products/{image}"
      results.append({
        "Food": item.get("title", "N/A"),
        "Price": price ,
        "Image": image
      })
    return results
  

def get_food_price(food_id):
  response = requests.get(f"https://api.spoonacular.com/food/products/{food_id}",
                          params={"apiKey": spoonacular_key},
                          timeout=10)
  

  if response.status_code != 200:
    return "Price not available"

  info = response.json()
  price = info.get("price", None)

  if price is None or price <= 0:
    return "Price not available"

  return f"${price:.2f}"

  

def search_drug(drug, price):
    response = requests.get(drugurl,
                            params={"search": f"openfda.brand_name:{drug}",
                                    "limit": 1})

    drug_status = response.json()


    if "results" not in drug_status:
        return None

    items = drug_status["results"]

    results = []

    for item in items:
      results.append({
          "Drug": item.get("openfda", {}).get("brand_name", ["N/A"])[0],
          "Price": price
      })

    return results


  

def search_cosmetics(cosmetic, price):
  response = requests.get(cosmeticurl,
                          params={"search_terms": cosmetic,
                                  "json": 1,
                                  "page_size": 1})
  
  cosmetic_status = response.json()


  if "products" not in cosmetic_status:
    return None
  
  items = cosmetic_status["products"]
  
  results = []

  for item in items:
    results.append({
      "Cosmetic": item.get("product_name", "N/A"),
      "Price": price
    })

  return results


def select_item(item, table, key):

    item_status = pd.DataFrame([item])

    with engine.connect() as connection:

        connection.execute(db.text
                            (f"DELETE FROM {table} WHERE {key} = :val"),
                            {"val": item[key]})
        connection.commit()

    item_status.to_sql(table, con=engine,
                            if_exists='append', index=False)



def show_db():
    rows = []
    tables = ["food_status", "drug_status", "cosmetic_status"]
    with engine.connect() as connection:
        for table in tables:
            try:
                query_result = connection.execute(
                    db.text(f"SELECT rowid, * FROM {table}")).fetchall()
                for row in query_result:
                    item = dict(row._mapping)
                    item["table"] = table
                    if table == "food_status":
                        item["Type"] = "food"
                    elif table == "drug_status":
                        item["Type"] = "drug"
                    elif table == "cosmetic_status":
                        item["Type"] = "cosmetic"
                    rows.append(item)
            except:
                pass
    if not rows:
        return None
    return rows


def delete_saved(table, row_num):
    if table not in ["food_status", "drug_status", "cosmetic_status"]:
        return

    with engine.connect() as connection:
        connection.execute(
            db.text(f"DELETE FROM {table} WHERE rowid = :row"),
            {"row": row_num})
        connection.commit()

def get_link(item):
  query = item.replace(" ", "+")
  return f"https://www.google.com/search?q=buy+{query}"



def generate_food_remedies(issue):
  #prompt for gemini, modify it here
  prompt = f"""
  Given an issue, generate a list of 6 foods in a comma separated list to help the person with that issue.
  These foods must be available from the spoonacular API.
  Issue: {issue}
  """

  resp = get_genai_client().models.generate_content(
      model="gemini-3.5-flash",
      contents=prompt
  )

  foods = resp.text.split(",")
  foods = [food.strip() for food in foods if food.strip()][:6]

  all_results = []
  spoonacular_available = True

  prices = generate_price(foods)

  for food in foods:
    price = prices.get(food, 'N/A')
    result = None
    if spoonacular_available:
      try:
        result = search_food(food, price)
      except (requests.RequestException, RuntimeError):
        spoonacular_available = False
    if result:
      all_results.append(result[0])
    else:
      all_results.append({
        "Food": food,
        "Price": price,
        "Image": None
      })
  
  return all_results

def generate_drug_remedies(issue):
  #prompt for gemini, modify it here
  prompt = f"""
  Given an issue, generate a list of the 10 best drugs in a comma separated list to help the person with that issue.
  Issue: {issue}
  """

  resp = get_genai_client().models.generate_content(
      model="gemini-3.5-flash",
      contents=prompt
  )
  drugs = resp.text.split(",")
  drugs = [drug.strip() for drug in drugs]

  all_results = []

  prices = generate_price(drugs)

  for drug in drugs:
    price = prices.get(drug, "N/A")
    result = search_drug(drug, price)
    if result:
      all_results.extend(result)
  
  return all_results

def generate_cosmetic_remedies(issue):
  #prompt for gemini, modify it here
  prompt = f"""
  Given an issue, generate the 10 best cosmetics in a comma separated list to help the person with that issue.
  Don't give any explanation or anything else besides the comma separated list.
  These cosmetics must be available from the open beauty facts API.
  Issue: {issue}
  """

  resp = get_genai_client().models.generate_content(
      model="gemini-3.5-flash",
      contents=prompt
  )
  cosmetics = resp.text.split(",")
  cosmetics = [cosmetic.strip() for cosmetic in cosmetics]

  all_results = []

  prices = generate_price(cosmetics)

  for cosmetic in cosmetics:
    price = prices.get(cosmetic, "N/A")
    result = search_cosmetics(cosmetic, price)
    if result:
      all_results.extend(result)
  
  return all_results


def generate_price(items):
  prompt = f"""
  Given an item, generate the most common price this item would be listed at in USD.
  Answer with only a comma separated list of numbers, in the exact same order as the 
  items given with no dollar signs, no other words, and no item names
  Item: {", ".join(items)}
  """

  resp = get_genai_client().models.generate_content(
      model="gemini-3.5-flash",
      contents=prompt
  )

  prices = resp.text.split(",")
  prices = [price.strip() for price in prices]

  price_list = {}

  for item, price in zip(items, prices): 
    try:
      price_list[item] = float(price)
    except ValueError:
      price_list[item] = "N/A"
  return price_list


def generate_drug_comparison(drug1, drug2):
    prompt = f"""
Compare {drug1} and {drug2} for educational purposes.

Return only the comparison. Do not include:
- a disclaimer
- an introduction
- a conclusion
- Markdown headings
- bold formatting
- any additional sections

Use exactly this structure:

{drug1.capitalize()}
Pros:
- First pro
- Second pro
- Third pro
Cons:
- First con
- Second con
- Third con

{drug2.capitalize()}
Pros:
- First pro
- Second pro
- Third pro
Cons:
- First con
- Second con
- Third con

Each drug must be one continuous section.
Include exactly one blank line between the two drugs.
Do not place blank lines inside either drug section.
"""

    resp = get_genai_client().models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt
    )

    return resp.text
