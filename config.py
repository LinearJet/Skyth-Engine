import os
from flask import Flask
from dotenv import load_dotenv
from threading import Lock
from authlib.integrations.flask_client import OAuth
from tinydb import TinyDB
from flask_session import Session

# ==============================================================================
# INITIAL SETUP
# ==============================================================================
load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'this-is-a-super-secret-key-for-dev')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET')


# ==============================================================================
# DATABASE SETUP
# ==============================================================================
DATABASE = 'memory.db'
USER_DB = TinyDB('user_db.json')


# ==============================================================================
# OAUTH SETUP
# ==============================================================================
Session(app)
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)
# --- MODEL CONFIGURATION ---
CONVERSATIONAL_MODEL = os.getenv("CONVERSATIONAL_MODEL", "gemini/gemini-2.5-flash-lite-preview-06-17")
VISUALIZATION_MODEL = os.getenv("VISUALIZATION_MODEL", "gemini/gemini-2.5-flash")
REASONING_MODEL = os.getenv("REASONING_MODEL", "gemini/gemini-2.5-pro")
UTILITY_MODEL = os.getenv("UTILITY_MODEL", "gemini/gemini-2.5-flash-lite-preview-06-17")
IMAGE_GENERATION_MODEL = "gemini-2.0-flash-preview-image-generation"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CONVERSATIONAL_API_KEY = GEMINI_API_KEY
REASONING_API_KEY = GEMINI_API_KEY
VISUALIZATION_API_KEY = GEMINI_API_KEY
UTILITY_API_KEY = GEMINI_API_KEY
IMAGE_GENERATION_API_KEY = GEMINI_API_KEY

print("🔑 Gemini Key:", "Loaded" if GEMINI_API_KEY else "NOT FOUND")
if not GEMINI_API_KEY:
    print("CRITICAL WARNING: GEMINI_API_KEY environment variable not found. The application will not function.")

_cached_popular_topics = []
_last_popular_topics_update = 0
_popular_topics_cache_lock = Lock()
_POPULAR_TOPICS_CACHE_DURATION = 60 * 12 * 60

EDGE_TTS_VOICE_MAPPING = {
    "default": "en-US-AvaMultilingualNeural",
    "academic": "en-US-AndrewMultilingualNeural",
    "coding": "en-US-BrianMultilingualNeural",
    "unhinged": "en-US-AndrewMultilingualNeural",
    "custom": "en-US-AvaMultilingualNeural"
}

CACHE = {
    'articles': {},
    'content': {}
}
ARTICLE_LIST_CACHE_DURATION = 600
CONTENT_CACHE_DURATION = 3600

CATEGORIES = [
    "For You", "Sports", "Entertainment", "Technology", "Top",
    "Around the World", "Science", "Business"
]

# --- Site-Specific Parsers ---
def _parse_bbc(soup):
    main_content = soup.find('main', {'id': 'main-content'})
    if main_content:
        article_blocks = main_content.find_all('div', {'data-component': 'text-block'})
        return '\n\n'.join(block.get_text(strip=True) for block in article_blocks)
    return None

def _parse_techcrunch(soup):
    content_div = soup.find('div', class_='article-content')
    return content_div.get_text(strip=True) if content_div else None

def _parse_reuters(soup):
    article_body = soup.find('div', {'data-testid': 'ArticleBody'})
    return article_body.get_text(strip=True) if article_body else None
    
SITE_PARSERS = {
    'www.bbc.com': _parse_bbc,
    'www.bbc.co.uk': _parse_bbc,
    'techcrunch.com': _parse_techcrunch,
    'www.reuters.com': _parse_reuters,
}

# --- Generic Scraper Selectors ---
GENERIC_SELECTORS = [
    'article', 'main', '.post-content', '.entry-content',
    '.article-body', '#content', '.content'
]
