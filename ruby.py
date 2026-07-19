import requests
import pandas as pd
import sqlalchemy as db
import os

from google import genai


my_api_key = os.getenv('GENAI_KEY')
spoonacular_key = os.getenv('SPOONACULAR_KEY')


client = genai.Client(api_key=my_api_key)

foodurl = "https://api.spoonacular.com/food/products/search"
drugurl = "https://api.fda.gov/drug/label.json"
cosmeticurl = "https://world.openbeautyfacts.org/cgi/search.pl"


def search_food(food):

    response = requests.get(foodurl,
                            params={"apiKey": spoonacular_key,
                                    "query": food,
                                    "number": 1})
    
    food_status = response.json()

    if "products" not in food_status:
      return None
    
    results = []
    
    items = food_status["products"]
    for item in items:
      results.append({
        "Food": item.get("title", "N/A"),
        "Price": get_food_price(item.get("id", "N/A"))
      })
    return results
  

def get_food_price(food_id):
  response = requests.get(f"https://api.spoonacular.com/food/products/{food_id}",
                          params={"apiKey": spoonacular_key})
  
  info = response.json()

  price_in_cents = info.get("price", None)

  if price_in_cents is None:
    return "Price not available"

  price_in_usd = price_in_cents / 100

  return f"${price_in_usd}"

  

def search_drug(drug):
  response = requests.get(drugurl,
                            params={"search": f"openfda.brand_name:{drug}"
                                    "limit": 1})

    drug_status = response.json()

    if "results" not in drug_status:
        return None

    items = drug_status["results"]

    results = []

    for item in items:
      results.append({
          "Drug": item.get("openfda, {}").get("brand_name", ["N/A"])[0],
      })

    return results


  

def search_cosmetics(cosmetic):
  response = requests.get(cosmeticurl,
                          params={"search_terms": cosmetic,
                                  "json:" 1,
                                  "page_size": 1})
  
  cosmetic_status = response.json()

  if "products" not in cosmetic_status:
    return None
  
  items = cosmetic_status["products"]
  
  results = []

  for item in items:
    results.append({
      "Cosmetic": item.get("product_name", "N/A")
    })

  return results


def select_item(item, table, key):

    food_stats = pd.DataFrame([item])

    with engine.connect() as connection:

        connection.execute(db.text
                            (f"DELETE FROM {table} WHERE {key} = :val"),
                            {"val": item[key]})
        connection.commit()

    food_stats.to_sql('food_status', con=engine,
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



def delete_food(row_num):
    with engine.connect() as connection:
        connection.execute(
            db.text("DELETE FROM food_status WHERE rowid = :row"),
            {"row": row_num})
        connection.commit()


def delete_saved(table, row_num):
    if table not in ["food_status", "drug_status", "cosmetic_status"]:
        return

    with engine.connect() as connection:
        connection.execute(
            db.text(f"DELETE FROM {table} WHERE rowid = :row"),
            {"row": row_num})
        connection.commit()

