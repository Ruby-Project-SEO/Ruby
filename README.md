# Ruby

Ruby is a server-rendered Flask application that brings nutrition tracking, medication schedules, cosmetic routines, food-safety monitoring, and AI-assisted wellness discovery into one dashboard.

The application combines public product and safety datasets with user-specific tracking. Users can log foods from USDA FoodData Central, monitor FDA recall matches, schedule medications, save recipes and routines, browse food-safety news, and ask questions across food, medication, and personal-care categories.

> Ruby provides general wellness information and is not a substitute for professional medical advice, diagnosis, or treatment.

## Core capabilities

- **Unified daily dashboard** for food, medication, cosmetic, hydration, and routine data
- **Nutrition tracking** with calories, macronutrients, micronutrients, configurable targets, and saved recipes
- **Food-safety monitoring** that checks logged products against openFDA recall data
- **Medication schedules** with configurable frequency and dose completion tracking
- **Cosmetic product tracking** with product search and safety information
- **Ask Ruby** for Gemini-powered answers and category-specific suggestions
- **Recipe discovery** and nutrition details through Spoonacular
- **Drug comparison** and saved wellness routines
- **Food-safety news feed** with article summaries, comments, and current products to review
- **Authentication** through Supabase email/password and Google Identity Services
- **Abuse controls** including CSRF validation, login throttling, search throttling, and security headers

## Architecture

Ruby uses a conventional server-rendered architecture. Flask owns routing, authentication, session management, persistence, and third-party integrations; Jinja templates render the interface; and browser-side JavaScript handles interactive dashboard behavior.

```text
Browser
   |
   v
Flask / Jinja2
   |-- Supabase Auth + Google Sign-In
   |-- SQLite application data
   |-- Google Gemini
   |-- USDA FoodData Central
   |-- openFDA
   |-- Spoonacular
   |-- NewsData.io
   `-- Logo.dev
```

## Technology

| Layer | Technology |
| --- | --- |
| Application | Python 3.13, Flask, Jinja2 |
| Frontend | HTML, CSS, JavaScript |
| Persistence | SQLite, Flask-SQLAlchemy |
| Authentication | Supabase Auth, Google Identity Services |
| AI | Google Gemini |
| Product data | USDA FoodData Central, openFDA, Spoonacular |
| Content | NewsData.io |
| Brand assets | Logo.dev |
| Delivery | GitHub Actions, PythonAnywhere |

## Repository layout

```text
.
|-- app.py                         # Local and WSGI application entry point
|-- backend/
|   |-- app.py                     # Routes, auth, sessions, and persistence
|   `-- services/
|       |-- food_safety.py         # Recall matching and safety profiles
|       |-- news.py                # Food-safety news and FDA watch data
|       |-- nutrition.py           # USDA food search and nutrient calculations
|       |-- products.py            # Medication and cosmetic integrations
|       `-- wellness.py            # Gemini, recipes, and wellness services
|-- frontend/
|   |-- templates/                 # Jinja2 views
|   `-- static/                    # Stylesheets and image assets
|-- database/                      # Runtime SQLite storage and documentation
|-- tests/                         # Service and recommendation tests
|-- .github/workflows/deploy.yml   # CI and PythonAnywhere deployment
|-- .env.example                   # Environment variable template
`-- requirements.txt               # Python dependencies
```

## Local development

### Requirements

- Python 3.13 recommended
- `pip`
- A Supabase project for authentication
- API credentials for the integrations you intend to enable

### 1. Clone the repository

```bash
git clone https://github.com/Ruby-Project-SEO/Ruby.git
cd Ruby
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Configure the environment

```bash
cp .env.example .env
```

Populate `.env` with the credentials for your environment:

| Variable | Purpose | Required for |
| --- | --- | --- |
| `SECRET_KEY` | Flask session signing | Stable sessions in production |
| `SUPABASE_URL` | Supabase project URL | Email/password and Google authentication |
| `SUPABASE_PUBLISHABLE_KEY` | Browser-safe Supabase project key | Authentication |
| `GOOGLE_CLIENT_ID` | Google OAuth web client ID | Google Sign-In |
| `GEMINI_API_KEY` | Google AI Studio key | Ask Ruby and AI recommendations |
| `SPOONACULAR_KEY` | Spoonacular API key | Recipe search and recipe nutrition |
| `USDA_FDC_API_KEY` | FoodData Central key | Food search and nutrition tracking |
| `OPENFDA_API_KEY` | openFDA key | Product search and recall monitoring |
| `NEWSDATA_API_KEY` | NewsData.io key | Food-safety news |
| `LOGO_DEV_PUBLISHABLE_KEY` | Logo.dev publishable key | Brand imagery |

Never commit `.env`, service-role keys, client secrets, or other private credentials. The repository ignores `.env` and local database files.

### 5. Start the application

```bash
python app.py
```

Open the local address printed by Flask. If port `5000` is already occupied, stop the existing process or configure Flask to use another port.

## Authentication configuration

Ruby sends email/password operations to Supabase Auth and validates Google ID tokens on the server.

For Google Sign-In:

1. Create a Web OAuth client in Google Cloud.
2. Add each local and deployed origin to **Authorized JavaScript origins**.
3. Enable Google in **Supabase Authentication > Providers** using that client ID and client secret.
4. Set `GOOGLE_CLIENT_ID` to the same web client ID used by the application.
5. Add local and production URLs to the Supabase redirect URL allowlist.

Only the Supabase publishable key belongs in this application configuration. Do not use a Supabase service-role key for client authentication.

## Testing

Run the test suite from the repository root:

```bash
pytest
```

The current suite covers nutrition normalization, product integrations, and Ruby recommendation selection. CI also imports the Flask application on Python 3.13 to catch dependency and startup failures.

## Continuous delivery

The GitHub Actions workflow runs for pull requests targeting `main` and pushes to `main`:

1. Install dependencies on Python 3.13.
2. Verify that the Flask application imports successfully.
3. On a successful push to `main`, call the protected PythonAnywhere deployment webhook.

Production configuration is supplied through PythonAnywhere. Deployment credentials are stored as GitHub environment secrets and are not part of the repository.

## Data and safety model

- User-generated application data is stored in SQLite and scoped by the authenticated Supabase user ID.
- Database files are runtime artifacts and are excluded from Git.
- Recall matches are informational text matches and should be verified against the linked official FDA source.
- AI responses are labeled as generated wellness information and should not be treated as medical advice.

## Contributors

- Laali Nembot
- Angel Rodriguez
- Haithem Salmi
- Christopher Hernandez

## License

Ruby is currently maintained as an educational project. No open-source license has been declared; all rights remain with the project contributors unless a license is added.
