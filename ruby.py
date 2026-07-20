import requests
import pandas as pd
import sqlalchemy as db
import os

from google import genai


my_api_key = os.getenv('GENAI_KEY')

spoonacular_key = "2b41e17276b34383903a15f04794a10f"

client = genai.Client(api_key=my_api_key)

foodurl = "https://api.spoonacular.com/food/products/search"
drugurl = "https://api.fda.gov/drug/label.json"
cosmeticurl = ""


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
  print(info)

  price_in_cents = info.get("price", None)

  if price_in_cents is None:
    return "Price not available"

  price_in_usd = price_in_cents / 100

  return f"${price_in_usd}"
  

def search_drug(drug):
  response = requests.get(drugurl,
                            params={"search": description,
                                    "limit": 1})

  drug_status = response.json()

  if "results" not in drug_status:
      return None

  items = drug_status["results"]

  results = []

  for item in items:
    results.append({
        "Drug": item.get("description", "N/A"),
    })

  return results
  

def search_cosmetics(cosmetic):
  return None


