# Ruby

A Flask wellness web application that helps users find personalized solutions — recipes, drug comparisons, or cosmetic products — for health-related issues, with AI-suggested product recommendations powered by Google Gemini.

## Overview

Finding trustworthy guidance for everyday health concerns can be overwhelming, with results scattered across too many sources. Ruby lets users enter a health-related issue (like dry skin or a headache) and choose how they'd like to solve it — through medicine, cosmetics, or food — then filters by budget and returns an AI-generated summary along with a ranked list of the top suggested products. Ruby also lets users browse recipes, compare drugs, and build out a personalized routine.

## Features

- 🔍 **Issue-based search** — enter a health concern and get tailored results
- 🧴💊🍎 **Multiple solution paths** — choose to address an issue via medicine, cosmetics, or food
- 🤖 **AI-powered recommendations** — Gemini API generates a summary plus a ranked top-10 list of products/foods/cosmetics for the issue
- 📖 **Recipes** — browse and explore recipe content
- 💊 **Drug comparison** — compare drug options side by side
- 🗓️ **Routine** — build and track a personal wellness routine
- 🛒 **Cart** — save items of interest for later
- 🔐 **Login/Sign up** — user accounts and authentication
- 🌐 **Web-based interface** — built with Flask and server-rendered templates

## Tech Stack

- **Backend:** Python, Flask
- **Templating:** Jinja2
- **Frontend:** HTML, CSS, JavaScript
- **APIs:**
  - [Google Gemini API](https://ai.google.dev/) — AI-generated summaries and product/recipe/food suggestions
  - [Spoonacular API] (https://spoonacular.com/food-api) - Specific meals/recipes given ingredients
- **Version Control:** Git / GitHub

## Getting Started

### Prerequisites
- Python 3.x
- pip

### Installation

1. Clone the repository
```bash
   git clone https://github.com/10sChris/Ruby.git
   cd Ruby
```

2. Create and activate a virtual environment
```bash
   python -m venv venv
   source venv/bin/activate
```bash
   # On Windows:
   venv\Scripts\activate


3. Install dependencies
```bash
   pip install -r requirements.txt


4. Run the app
```bash
   python app.py

5. Open your browser to the given port address

## How It Works

1. A user enters a health-related issue (e.g. dry skin, headache) on the search page.
2. A modal prompts the user to choose how they'd like the issue solved — medicine, cosmetics, or food while giving an AI powered summary/remedy for their issue
4. The Gemini API generates an AI summary along with a ranked list of the top 10 suggested products, foods, or cosmetics for that issue.
5. Users can then naviagte to food, drugs or cosmetic specific pages to search through different recipes, routines and drug comparisons.
6. Results are rendered back to the user through Flask/Jinja2 templates, with the option to add items to their cart.

## Navigation

Home · Recipes · Drug Comparison · Routine · MyRuby · Login/Logout

## Contributors

- Laali Nembot
- Angel Rodriguez
- Haithem Salmi
- Christopher Hernandez

## License

This project is for educational purposes.
