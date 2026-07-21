import requests
import pandas as pd
import sqlalchemy as db
import os

from google import genai


my_api_key = os.getenv('GENAI_KEY')
spoonacular_key = (os.getenv('SPOONACULAR_KEY') or '').strip()


client = genai.Client(api_key=my_api_key)

foodurl = "https://api.spoonacular.com/food/products/search"
drugurl = "https://api.fda.gov/drug/label.json"
cosmeticurl = "https://world.openbeautyfacts.org/cgi/search.pl"
engine = db.create_engine('sqlite:///item_status.db')

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

def get_recipe_details(recipe_id):
  spoonacular_key = (os.getenv("SPOONACULAR_KEY") or '').strip()

  if not spoonacular_key:
    raise RuntimeError("SPOONACULAR_KEY is not configured")

  url = "https://api.spoonacular.com/recipes/findByIngredients"

  response = requests.get(
    url,
    params={
      "apiKey": spoonacular_key,
      "includeNutrition": False
    },
    timeout=10
  )

  response.raise_for_status()

  return response.json()


def search_food(food):
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
        "Price": "Price not available",
        "Image": image
      })
    return results
  

def get_food_price(food_id):
  response = requests.get(f"https://api.spoonacular.com/food/products/{food_id}",
                          params={"apiKey": spoonacular_key})
  
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

  resp = client.models.generate_content(
      model="gemini-2.5-flash",
      contents=prompt
  )

  foods = resp.text.split(",")
  foods = [food.strip() for food in foods if food.strip()][:6]

  all_results = []
  spoonacular_available = True

  for food in foods:
    result = None
    if spoonacular_available:
      try:
        result = search_food(food)
      except (requests.RequestException, RuntimeError):
        spoonacular_available = False
    if result:
      all_results.append(result[0])
    else:
      all_results.append({
        "Food": food,
        "Price": "Price not available",
        "Image": None
      })
  
  return all_results

def generate_drug_remedies(issue):
  #prompt for gemini, modify it here
  prompt = f"""
  Given an issue, generate a list of the 10 best drugs in a comma separated list to help the person with that issue.
  Issue: {issue}
  """

  resp = client.models.generate_content(
      model="gemini-2.5-flash",
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

  resp = client.models.generate_content(
      model="gemini-2.5-flash",
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

  resp = client.models.generate_content(
      model="gemini-2.5-flash",
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
  Compare these two drugs for educational purposes: {drug1} and {drug2}.
  Give pros and cons. Do not recommend a dosage and you are not replacing medical advice.
  Format it like this:

  {drug1.capitalize()}
  Pros:
  Cons:

  {drug2.capitalize()}
  Pros:
  Cons:
  """

  resp = client.models.generate_content(
      model="gemini-2.5-flash",
      contents=prompt
  )

  return resp.text
