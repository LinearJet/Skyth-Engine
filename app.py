import os
import json
import re
import requests
import sqlite3
import time
import base64
import io
import uuid # For Pollinations seed
import mimetypes
import random # For Discover page content shuffling
import subprocess # For running the Node.js stock script
import html # For escaping HTML content
from urllib.parse import quote, urlparse, urljoin, unquote # For various URL operations
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, Response, request, stream_with_context, send_from_directory, render_template, jsonify, session
from flask_cors import CORS
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup
from datetime import datetime
from threading import Lock

# Selenium Imports (for new tools)
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# Matplotlib and other specific tool imports
import trafilatura # For improved article extraction
from youtube_transcript_api import YouTubeTranscriptApi

# New imports for Edge TTS
import edge_tts
import asyncio

# --- NEW IMPORTS FOR REQUESTED FEATURES ---
import pypdf
from pydub import AudioSegment
import speech_recognition as sr
from google import genai as google_genai
from google.genai import types as google_types
from PIL import Image as PIL_Image
from io import BytesIO as IO_BytesIO
# -----------------------------------------

# ==============================================================================
# INITIAL SETUP
# ==============================================================================
load_dotenv()
app = Flask(__name__)
app.secret_key = os.urandom(24) # From pork.py, needed for session management
CORS(app, resources={r"/*": {"origins": "*"}})

DATABASE = 'memory.db'
# --- MODEL CONFIGURATION ---
# Conversational model for general chat, context understanding, and multimodal analysis (image/file).
CONVERSATIONAL_MODEL = os.getenv("CONVERSATIONAL_MODEL", "gemini/gemini-2.5-flash-lite-preview-06-17") # DO NOT CHANGE THIS EVER

# Visualization model for generating HTML, CSS, JS, p5.js, and Matplotlib charts.
VISUALIZATION_MODEL = os.getenv("VISUALIZATION_MODEL", "gemini/gemini-2.5-flash")

# Reasoning model for complex logic, deep research reports, and advanced non-visual code generation.
REASONING_MODEL = os.getenv("REASONING_MODEL", "gemini/gemini-2.5-pro")

# NEW: Image Generation/Editing model
IMAGE_GENERATION_MODEL = "gemini-2.0-flash-preview-image-generation"

# Unified API Key for all Gemini models
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CONVERSATIONAL_API_KEY = GEMINI_API_KEY
REASONING_API_KEY = GEMINI_API_KEY
VISUALIZATION_API_KEY = GEMINI_API_KEY
IMAGE_GENERATION_API_KEY = GEMINI_API_KEY # Use the same key for simplicity

print("🔑 Gemini Key:", "Loaded" if GEMINI_API_KEY else "NOT FOUND")
if not GEMINI_API_KEY:
    print("CRITICAL WARNING: GEMINI_API_KEY environment variable not found. The application will not function.")

_cached_popular_topics = []
_last_popular_topics_update = 0
_popular_topics_cache_lock = Lock()
_POPULAR_TOPICS_CACHE_DURATION = 60 * 12 * 60

# Voice mapping for Microsoft Edge TTS
EDGE_TTS_VOICE_MAPPING = {
    "default": "en-US-AvaMultilingualNeural",
    "academic": "en-US-AndrewMultilingualNeural",
    "coding": "en-US-BrianMultilingualNeural",
    "unhinged": "en-US-AndrewMultilingualNeural", # Let the text carry the persona
    "god": "en-US-AndrewMultilingualNeural",
    "custom": "en-US-AvaMultilingualNeural" # Default to Ava for custom
}

# ==============================================================================
# DISCOVER PAGE LOGIC (Integrated from pork.py)
# ==============================================================================

# --- Caching Configuration ---
CACHE = {
    'articles': {},  # Caches the list of articles for each category
    'content': {}    # Caches the full scraped content of an article URL
}
ARTICLE_LIST_CACHE_DURATION = 600  # 10 minutes
CONTENT_CACHE_DURATION = 3600      # 1 hour

# --- Tier 1: Site-Specific Parsers ---
# These are highly optimized for specific domains. They are the fastest method.
# The function takes a BeautifulSoup object and returns the article text.
def _parse_bbc(soup):
    main_content = soup.find('main', {'id': 'main-content'})
    if main_content:
        # Find all article blocks and join their text
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

# --- Tier 2: Generic Scraper Selectors ---
GENERIC_SELECTORS = [
    'article', 'main', '.post-content', '.entry-content',
    '.article-body', '#content', '.content'
]

# --- Discover Page Categories ---
CATEGORIES = [
    "For You", "Sports", "Entertainment", "Technology", "Top",
    "Around the World", "Science", "Business"
]

def get_article_content_tiered(url):
    """
    The new core function. Implements a tiered scraping strategy for max speed.
    Tier 1: Check Cache
    Tier 2: Use Site-Specific Parser (if available)
    Tier 3: Use Generic Fast Scraper (requests + BeautifulSoup)
    Tier 4: Use Slow Selenium Scraper (as a fallback)
    """
    # --- Tier 1: Check Cache ---
    if url in CACHE['content'] and time.time() - CACHE['content'][url]['timestamp'] < CONTENT_CACHE_DURATION:
        print(f"CACHE HIT: Serving content for {url} from cache.")
        return CACHE['content'][url]['data']

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        # --- Attempt Fast Scraping with Requests ---
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract common metadata
        title = soup.find('title').get_text() if soup.find('title') else 'No Title'
        og_image_tag = soup.find('meta', property='og:image')
        image = og_image_tag['content'] if og_image_tag else None

        text = None
        domain = urlparse(url).netloc
        
        # --- Tier 2: Site-Specific Parser ---
        if domain in SITE_PARSERS:
            print(f"TIER 2: Using site-specific parser for {domain}")
            text = SITE_PARSERS[domain](soup)

        # --- Tier 3: Generic Fast Scraper ---
        if not text:
            print("TIER 3: Using generic fast scraper (requests + BeautifulSoup)")
            for selector in GENERIC_SELECTORS:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(separator='\n\n', strip=True)
                    if len(text) > 200: # Check for meaningful content
                        break
        
        if text:
            article_data = {'title': title, 'text': text, 'image': image}
            # Cache the successfully scraped data
            CACHE['content'][url] = {'timestamp': time.time(), 'data': article_data}
            return article_data

    except requests.RequestException as e:
        print(f"Fast scrape failed for {url}: {e}. Falling back to Selenium.")

    # --- Tier 4: Selenium Fallback ---
    print(f"TIER 4: Falling back to Selenium for {url}")
    article_data = extract_text_content_selenium(url)
    if article_data and article_data.get('text'):
        # Cache the successfully scraped data
        CACHE['content'][url] = {'timestamp': time.time(), 'data': article_data}
        return article_data

    # If all tiers fail
    return None

def extract_text_content_selenium(url):
    """The Selenium scraper, now used only as a last resort."""
    driver = None
    try:
        driver = setup_selenium_driver() # Using the robust driver from app.py
        driver.get(url)
        # Use intelligent waits instead of time.sleep()
        wait = WebDriverWait(driver, 10)
        
        # Wait for the body tag to ensure the page is loaded
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        
        title = driver.title
        text = ""
        
        for selector in GENERIC_SELECTORS:
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                text = element.text
                if len(text) > 200:
                    break
            except NoSuchElementException:
                continue
        
        if not text:
            text = driver.find_element(By.TAG_NAME, "body").text

        image = None
        try:
            image_meta = driver.find_element(By.CSS_SELECTOR, 'meta[property="og:image"]')
            image = image_meta.get_attribute('content')
        except NoSuchElementException:
            pass
            
        return {'title': title, 'text': text, 'image': image}
    except (TimeoutException, Exception) as e:
        print(f"SELENIUM: Error extracting text for {url}: {e}")
        return {}
    finally:
        if driver:
            driver.quit()


# ==============================================================================
# NEW AND INTEGRATED TOOLS (from all.py and test.py)
# ==============================================================================

def setup_selenium_driver():
    """Setup a single, robust Chrome driver for all scraping tasks."""
    print("[Selenium] Setting up new driver instance...")
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    # A realistic user agent is crucial
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print("[Selenium] Driver setup successful.")
        return driver
    except Exception as e:
        print(f"[Selenium] CRITICAL: Failed to setup driver: {e}")
        print("[Selenium] Ensure chromedriver is installed and in your PATH.")
        return None

def get_filename_from_url(url):
    """Generate appropriate filename from URL, cleaning it for saving."""
    try:
        parsed = urlparse(unquote(url))
        filename = os.path.basename(parsed.path)
        if not filename or '.' not in filename:
            # Fallback for URLs without a clear filename
            filename = f"download_{uuid.uuid4().hex[:8]}.html"
        # Sanitize filename
        return re.sub(r'[<>:"/\\|?*\s]', '_', filename)
    except Exception:
        return f"download_{uuid.uuid4().hex[:8]}.bin"

def parse_with_bs4(url):
    """
    Fast URL parser using requests and BeautifulSoup. Extracts title, text, images, and links.
    """
    print(f"[URL Parser - BS4] Attempting fast parse of: {url}")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        content_type = response.headers.get('content-type', '').lower()
        if 'html' not in content_type:
            print(f"[URL Parser - BS4] Content is not HTML ({content_type}), skipping parse.")
            return None

        soup = BeautifulSoup(response.content, 'html.parser')

        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
            tag.decompose()
        
        title = soup.title.string.strip() if soup.title else ''
        
        main_content_selectors = ['article', 'main', '[role="main"]', '.post-content', '.article-body', '#content', '#main-content']
        main_content_tag = None
        for selector in main_content_selectors:
            tag = soup.select_one(selector)
            if tag:
                main_content_tag = tag
                break
        
        if not main_content_tag:
            main_content_tag = soup.body

        text_content = ''
        links = []
        images = []

        if main_content_tag:
            lines = (line.strip() for line in main_content_tag.get_text(separator='\n').splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text_content = '\n'.join(chunk for chunk in chunks if chunk)

            for link in main_content_tag.find_all('a', href=True):
                href = link.get('href')
                if href and href.startswith('http'):
                    links.append({'url': urljoin(url, href), 'text': link.get_text(strip=True)})
            
            for img in main_content_tag.find_all('img', src=True):
                src = img.get('src')
                if src and not src.startswith('data:image'):
                    images.append(urljoin(url, src))

        return {
            'url': url,
            'domain': urlparse(url).netloc,
            'title': title,
            'text_content': text_content,
            'images': images,
            'videos': [], # BS4 is not reliable for videos
            'links': links,
            'source_parser': 'bs4'
        }
    except Exception as e:
        print(f"[URL Parser - BS4] Error during fast parse of {url}: {e}")
        return None

def parse_url_comprehensive(driver, url):
    """
    Comprehensive URL parsing - extracts text, images, videos, and links using Selenium.
    This is the core logic from your all.py, adapted for integration.
    """
    print(f"[URL Parser - Selenium] Starting comprehensive parse of: {url}")
    parsed_data = {
        'url': url,
        'domain': urlparse(url).netloc,
        'title': '',
        'text_content': '',
        'images': [],
        'videos': [],
        'links': [],
        'source_parser': 'selenium'
    }

    try:
        driver.get(url)
        # Wait for the page to have a body tag, max 15 seconds
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3) # Additional sleep for JS rendering

        # Scroll to load lazy-loaded content
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

        # --- Text Extraction ---
        parsed_data['title'] = driver.title
        body = driver.find_element(By.TAG_NAME, "body")
        parsed_data['text_content'] = body.text

        page_source = driver.page_source

        # --- Image Extraction ---
        image_urls = set()
        for img in driver.find_elements(By.TAG_NAME, "img"):
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src and not src.startswith('data:image'):
                # Filter for high quality images
                if is_high_quality_image(src):
                    image_urls.add(urljoin(url, src))
        
        # Regex for background images and other sources
        regex_patterns = [r'background-image:\s*url\(["\']?([^"\']*)["\']?\)']
        for pattern in regex_patterns:
            for match in re.findall(pattern, page_source, re.IGNORECASE):
                 if not match.startswith('data:image') and is_high_quality_image(match):
                    image_urls.add(urljoin(url, match))
        parsed_data['images'] = list(image_urls)

        # --- Video Extraction ---
        video_urls = set()
        for video in driver.find_elements(By.TAG_NAME, "video"):
            src = video.get_attribute("src")
            if src: video_urls.add(urljoin(url, src))
            for source in video.find_elements(By.TAG_NAME, "source"):
                src = source.get_attribute("src")
                if src: video_urls.add(urljoin(url, src))
        parsed_data['videos'] = list(video_urls)

        # --- Link Extraction ---
        link_data = []
        for link in driver.find_elements(By.TAG_NAME, "a"):
            href = link.get_attribute("href")
            if href and href.startswith('http'):
                link_data.append({'url': href, 'text': link.text.strip()})
        parsed_data['links'] = link_data

        print(f"[URL Parser] Finished parsing. Found: {len(parsed_data['images'])} images, {len(parsed_data['videos'])} videos, {len(parsed_data['links'])} links.")
        return parsed_data

    except Exception as e:
        print(f"[URL Parser] Error during comprehensive parsing of {url}: {e}")
        # Return whatever was collected so far
        return parsed_data

def is_high_quality_image(url):
    """Filter for high quality images based on URL patterns and size indicators."""
    if not url:
        return False
    
    # Skip common low-quality image patterns
    low_quality_patterns = [
        r'thumb',
        r'thumbnail',
        r'icon',
        r'avatar',
        r'logo',
        r'badge',
        r'button',
        r'pixel',
        r'1x1',
        r'spacer',
        r'blank',
        r'transparent',
        r'loading',
        r'spinner',
        r'placeholder',
        r'_s\.',  # small size indicator
        r'_xs\.',  # extra small
        r'_sm\.',  # small
        r'_tiny\.',
        r'_mini\.',
        r'_micro\.',
        r'50x50',
        r'100x100',
        r'16x16',
        r'32x32',
        r'64x64',
        r'favicon',
        r'sprite',
        r'emoji',
        r'emoticon'
    ]
    
    url_lower = url.lower()
    for pattern in low_quality_patterns:
        if re.search(pattern, url_lower):
            return False
    
    # Prefer larger size indicators
    high_quality_patterns = [
        r'_l\.',  # large
        r'_xl\.',  # extra large
        r'_xxl\.',  # extra extra large
        r'_large\.',
        r'_big\.',
        r'_full\.',
        r'_original\.',
        r'_hd\.',
        r'_hq\.',  # high quality
        r'800x',
        r'1024x',
        r'1200x',
        r'1920x',
        r'2048x'
    ]
    
    for pattern in high_quality_patterns:
        if re.search(pattern, url_lower):
            return True
    
    # Check file extensions for image formats
    image_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.svg']
    if any(url_lower.endswith(ext) for ext in image_extensions):
        return True
    
    return True  # Default to true if no specific patterns match

def scrape_google_images(driver, query, max_results=10):
    """
    Extracts high-quality image URLs from Google Images, using robust logic from test.py.
    This method is more resilient to changes in Google's page layout.
    """
    print(f"[Google Images] Searching for: {query}")
    try:
        encoded_query = quote(query)
        # Using a standard tbm=isch URL which is more stable and allows safesearch to be turned off
        url = f"https://www.google.com/search?tbm=isch&q={encoded_query}&safe=off&tbs=isz:m"  # isz:m for medium+ size
        driver.get(url)
        time.sleep(2)  # Wait for initial page load

        # Scroll multiple times to trigger lazy loading of images
        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

        # Find all image elements on the page, a more robust method than specific class names
        img_elements = driver.find_elements(By.TAG_NAME, "img")
        
        image_urls = set()  # Use a set to automatically handle duplicates
        for img in img_elements:
            # Prioritize 'src', but fallback to 'data-src' which is common for lazy-loaded images
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src and src.startswith('http'):
                # Filter out tiny base64 encoded thumbnails which often start with 'data:image'
                if not src.startswith('data:image') and is_high_quality_image(src):
                    image_urls.add(src)
            if len(image_urls) >= max_results:
                break
        
        image_urls_list = list(image_urls)
        print(f"[Google Images] Found {len(image_urls_list)} high-quality images.")
        
        # The source_url for all of them is the search page itself
        source_page_url = f"https://www.google.com/search?tbm=isch&q={encoded_query}"
        return [{"type": "image_search_result", "title": query, "thumbnail_url": url, "image_url": url, "source_url": source_page_url} for url in image_urls_list]
    except Exception as e:
        print(f"[Google Images] Error scraping Google Images: {e}")
        return []

# ==============================================================================
# STREAMING EDGE-TTS API ENDPOINT
# ==============================================================================

@app.route('/api/tts', methods=['POST'])
def text_to_speech():
    """
    API endpoint for streaming text-to-speech conversion using edge-tts.
    NOTE: The initial delay (Time To First Byte) is inherent to the edge-tts service
    itself connecting and processing the initial text. The streaming implementation
    ensures the audio is sent to the client as soon as it's received, providing the
    best possible perceived latency.
    """
    data = request.json
    text = data.get('text')
    persona = data.get('persona', 'default')

    if not text:
        return Response(json.dumps({'error': 'No text provided.'}), status=400, mimetype='application/json')

    voice = EDGE_TTS_VOICE_MAPPING.get(persona, EDGE_TTS_VOICE_MAPPING['default'])

    # This generator function wraps the asyncio logic for use in a sync Flask route.
    def generate_audio_stream():
        # Define an async generator to stream audio from edge-tts
        async def async_generator():
            try:
                communicate = edge_tts.Communicate(text, voice)
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        yield chunk["data"]
            except Exception as e:
                print(f"edge-tts streaming error: {e}")
                # This yield won't actually go to the client if the headers are already sent,
                # but it helps in debugging. The error will manifest as a broken stream.

        # Create a new event loop for this request's thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Get the async generator iterator
        async_gen = async_generator().__aiter__()

        try:
            while True:
                # Run the async generator until it yields the next chunk
                chunk = loop.run_until_complete(async_gen.__anext__())
                yield chunk
        except StopAsyncIteration:
            # This is expected when the stream finishes
            pass
        finally:
            loop.close()

    # Use stream_with_context to send the data as it's generated
    return Response(stream_with_context(generate_audio_stream()), mimetype="audio/mpeg")

# ==============================================================================
# EXISTING AND CORE APPLICATION LOGIC (from cap.py)
# ==============================================================================

@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    response.headers['Content-Security-Policy'] = "frame-ancestors *"
    return response

def search_duckduckgo(query, max_results=7):
    try:
        with DDGS(timeout=20) as ddgs:
            return [{"type": "web", "title": r['title'], "text": r['body'], "url": r['href']}
                    for r in list(ddgs.text(query, max_results=max_results, safesearch='off'))]
    except Exception as e: 
        print(f"DDG text search error: {e}"); 
        return []

def scrape_bing_images(query, max_results=8):
    try:
        print(f"[Bing Images] Searching for: {query}")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        url = f"https://www.bing.com/images/search?q={quote(query)}&form=HDRSC2&qft=+filterui:imagesize-large"  # Filter for large images
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        for i, tag in enumerate(soup.select("a.iusc")):
            if i >= max_results: break
            m_data = tag.get("m")
            if m_data:
                try:
                    json_data = json.loads(m_data)
                    image_url = json_data.get("murl")
                    if image_url and is_high_quality_image(image_url):
                        results.append({
                            "type": "image_search_result",
                            "title": json_data.get("t", "Image"),
                            "thumbnail_url": json_data.get("turl"),
                            "image_url": image_url,
                            "source_url": json_data.get("purl", url)
                        })
                except Exception as e:
                    print(f"[Bing Images] Error processing image tag: {e}")
        print(f"[Bing Images] Found {len(results)} high-quality images.")
        return results
    except Exception as e:
        print(f"[Bing Images] Bing image search error: {e}")
        return []

def search_youtube_videos(query, max_results=5):
    try:
        print(f"[YouTube Search] Searching for: {query}, Max Results: {max_results}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        url = f"https://www.youtube.com/results?search_query={quote(query)}"
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        pattern = r'var ytInitialData = ({.*?});'
        match = re.search(pattern, response.text)
        
        if not match:
            print("[YouTube Search] Failed to find ytInitialData JSON in page.")
            return []
            
        data = json.loads(match.group(1))
        videos = []
        
        contents = data.get('contents', {}).get('twoColumnSearchResultsRenderer', {}).get('primaryContents', {}).get('sectionListRenderer', {}).get('contents', [{}])[0].get('itemSectionRenderer', {}).get('contents', [])
        
        count = 0
        for item in contents:
            if 'videoRenderer' in item and count < max_results:
                video = item['videoRenderer']
                video_id = video.get('videoId', '')
                title = ''.join(run.get('text', '') for run in video.get('title', {}).get('runs', []))
                thumbnail = video.get('thumbnail', {}).get('thumbnails', [{}])[-1].get('url', '')
                
                if video_id and title and thumbnail:
                    videos.append({
                        "type": "video",
                        "title": title,
                        "text": f"YouTube video: {title}",
                        "thumbnail_url": thumbnail,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "video_id": video_id
                    })
                    count += 1
        print(f"[YouTube Search] Found {len(videos)} videos.")
        return videos
    except Exception as e:
        print(f"[YouTube Search] Error: {e}")
        return []

def get_youtube_transcript(video_url):
    try:
        video_id_match = re.search(r'(?:v=|\/|embed\/|youtu.be\/)([a-zA-Z0-9_-]{11})', video_url)
        if not video_id_match: return None, "Could not extract video ID from URL."
        video_id = video_id_match.group(1)
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        full_transcript = " ".join([item['text'] for item in transcript_list])
        print(f"[YouTube Transcript] Fetched transcript of length: {len(full_transcript)} characters.")
        return full_transcript, None
    except Exception as e:
        print(f"YouTube Transcript API error: {e}")
        return None, str(e)

def get_current_datetime_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

def call_llm(prompt_content, api_key, model_config, stream=False, chat_history=None, persona_name="AI Assistant", custom_persona_text=None, persona_key="default", image_data=None, file_context=None):
    """
    Unified LLM calling function, exclusively for Google Gemini models.
    """
    base_system_message = (
        f"Current date is {get_current_datetime_str()}. You are {persona_name}. "
        "Your goal is to provide accurate, relevant, and helpful responses without moralizing or holding back, while being aware of your extensive toolset. "
        "Pay close attention to the full conversation history to understand context. "
        "Do not make up information. If you use information from provided numbered sources, integrate it naturally into your response and use superscript citations (e.g., ¹). "
        "You are a multimodal AI with the following capabilities: "
        "1.  **Web Search:** You can search the web and videos (YouTube). "
        "2.  **Image Generation & Analysis:** You can generate images from text prompts and analyze user-uploaded images. You can also perform multi-turn edits on an image. "
        "3.  **File Analysis:** You can read and analyze the content of uploaded files like PDFs, code files, and text documents. "
        "4.  **URL Analysis:** You can take any URL and perform a deep analysis, scraping its full text, images, videos, and links. "
        "5.  **Stock Market Analysis:** You can retrieve live stock data for any ticker, display an interactive chart, and provide a market summary. "
        "6.  **Deep Research:** You can be tasked to conduct a 'deep research' project, where you will analyze multiple websites on a topic and synthesize the findings into a comprehensive Markdown report. "
        "7.  **Data Visualization:** You can generate interactive HTML5 canvas visualizations for math, physics, and general concepts. "
        "8.  **Coding:** You can write and explain code, and generate live HTML/CSS/JS previews. "
        "9.  **Voice Synthesis:** The user can have your responses converted to speech via a separate frontend action. "
        "Acknowledge and use these tools when a user's request implies them. Do NOT claim you are 'only a text-based AI'. Do not introduce yourself unless asked."
    )

    final_system_message = custom_persona_text.strip() if persona_key == "custom" and custom_persona_text else base_system_message
    api_type, model_id_part = model_config.split('/', 1)

    if api_type != 'gemini':
        raise ValueError(f"Unsupported model API type: {api_type}. Only 'gemini' is supported.")

    formatted_history = []
    if chat_history:
        for entry in chat_history:
            role = "model" if entry["role"] == "assistant" else entry["role"]
            formatted_history.append({"role": role, "parts": [{"text": entry["content"]}]})

    if file_context:
        prompt_content = f"{file_context}\n\n{prompt_content}"

    full_prompt_for_gemini = f"{final_system_message}\n\n{prompt_content}"
    
    current_turn_parts = [{"text": full_prompt_for_gemini}]
    
    if image_data:
        current_turn_parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": image_data
            }
        })

    contents_payload = formatted_history + [{"role": "user", "parts": current_turn_parts}]
    
    base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id_part}"
    url = f"{base_url}:streamGenerateContent?alt=sse&key={api_key}" if stream else f"{base_url}:generateContent?key={api_key}"
    payload = {"contents": contents_payload}
    headers = {'Content-Type': 'application/json'}

    response = requests.post(url, headers=headers, json=payload, stream=stream, timeout=120)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"LLM API Error: {e.response.status_code} - {e.response.text[:200]}")
        raise
    return response

def reformulate_query_with_context(user_query, chat_history, api_key, model_config):
    """
    UPGRADED: Uses a larger context window and a more sophisticated prompt to synthesize
    the user's underlying intent into a powerful, self-contained search query.
    """
    if not chat_history:
        return user_query

    try:
        # Use a longer history for better context
        history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-20:]])

        reformulation_prompt = f"""You are a query analysis expert for an advanced AI search engine. Your task is to analyze the provided conversation history and the latest user query to deduce the user's *true underlying information need*. Then, create a new, single, self-contained search query that is optimized for a search engine to find the most relevant results.

**CRITICAL INSTRUCTIONS:**
1.  **Identify the Core Goal:** Look beyond the literal words. What is the user *really* trying to find out or accomplish? Has their goal shifted during the conversation?
2.  **Synthesize and Rewrite:** The new query must be a complete rewrite that incorporates all necessary context from the history. It should not be a simple combination of old and new queries. For example, if the history is about the Tesla Model 3 and the user asks "what about its battery?", the new query should be "Tesla Model 3 battery technology and range", not "what about its battery?".
3.  **Be Specific and Expansive:** Add keywords that narrow down the topic but also provide comprehensive results. For instance, if a user asks about "Python decorators", a better query might be "Python decorators tutorial with examples for classes and functions".
4.  **Handle New Topics:** If the latest query introduces a completely new topic unrelated to the history, the new query should focus solely on the new topic, ignoring the old history.
5.  **Output Format:** Your output must be ONLY the single optimized search query. Do not add explanations, quotation marks, or any other text.

**Conversation History:**
{history_str}

**Latest User Query:** "{user_query}"

**Optimized Search Query:**"""

        response = call_llm(
            reformulation_prompt,
            api_key=api_key,
            model_config=model_config,
            stream=False,
            chat_history=None
        )

        response_data = response.json()
        reformulated_query = response_data["candidates"][0]["content"]["parts"][0]["text"]
        cleaned_query = reformulated_query.strip().strip('"').strip("'")
        print(f"[Context Reformulation] Original: '{user_query}' -> New: '{cleaned_query}'")
        return cleaned_query if cleaned_query else user_query

    except Exception as e:
        print(f"⚠️ Error during query reformulation: {e}. Falling back to original query.")
        return user_query

def plan_research_steps_with_llm(query, chat_history):
    """
    Uses an LLM to break down a complex query into a series of simple, targeted search engine queries.
    """
    history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-10:]])
    planning_prompt = f"""
You are an expert AI research assistant. Your task is to decompose a user's request into a series of up to 3 precise, self-contained search engine queries. This will enable parallel research on different facets of the topic.

**CRITICAL INSTRUCTIONS:**
1.  **Decomposition:** Break down the user's query into logical sub-questions.
2.  **Comparison Handling:** If the user wants to compare two or more items (e.g., "X vs Y"), create a separate search query for each item's relevant aspects.
3.  **Simplicity:** Each generated query should be simple enough for a standard search engine (like Google or DuckDuckGo) to understand.
4.  **Completeness:** If the original query is already a simple, self-contained search query, just return that single query in the list.
5.  **Format:** Your output MUST be a valid JSON list of strings. Do not add any other text, explanations, or markdown.

**Conversation History:**
{history_str}

**Latest User Query:** "{query}"

**JSON Output (list of search queries):**
"""
    try:
        response = call_llm(
            planning_prompt,
            api_key=CONVERSATIONAL_API_KEY,
            model_config=CONVERSATIONAL_MODEL,
            stream=False
        )
        response_data = response.json()
        content = response_data["candidates"][0]["content"]["parts"][0]["text"]
        
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            search_plan = json.loads(json_str)
            if isinstance(search_plan, list) and all(isinstance(s, str) for s in search_plan) and search_plan:
                print(f"[Research Planner] Decomposed '{query}' into: {search_plan}")
                return search_plan
    except Exception as e:
        print(f"⚠️ Error during research planning: {e}. Falling back to a single query.")
    
    # Fallback for any errors or if the LLM fails to produce a valid list
    return [reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)]

def analyze_academic_intent_with_llm(query, chat_history):
    """
    Analyzes a query within the academic persona to determine intent, visualization possibility, and comparison subjects.
    """
    history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-10:]])
    intent_prompt = f"""
You are an AI assistant specializing in academic and scientific queries. Analyze the user's latest query to determine their intent and how best to answer.

**Conversation History:**
{history_str}

**Latest User Query:** "{query}"

**Analysis Task:**
Based on the query, provide a JSON object with the following structure:
{{
  "intent": "...",
  "comparison_subjects": ["...", "..."],
  "visualization_possible": boolean,
  "visualization_prompt": "...",
  "explanation_needed": boolean
}}

**Field Explanations:**
- "intent": Classify the primary user goal. Choose one: "comparison", "visualization_request", "concept_explanation", "simple_question".
- "comparison_subjects": If the intent is "comparison", list the subjects being compared (e.g., ["Gemini 2.5 Pro", "Claude 4"]). Otherwise, an empty list `[]`.
- "visualization_possible": `true` if the query describes something that can be meaningfully visualized in an interactive HTML5 canvas (e.g., a mathematical function, a simple physics simulation, a data plot). `false` for abstract concepts, dangerous/unethical visualizations (e.g., "cross-section of a human"), or topics too complex for a simple canvas.
- "visualization_prompt": If `visualization_possible` is `true`, formulate a clear, concise prompt for an AI model to generate this visualization (e.g., "Create an interactive plot of the function y = x^x for x > 0"). Otherwise, an empty string `""`.
- "explanation_needed": `true` if the query requires a detailed textual explanation, `false` for very simple requests.

**Output ONLY the JSON object.**
"""
    try:
        response = call_llm(
            intent_prompt,
            api_key=CONVERSATIONAL_API_KEY,
            model_config=CONVERSATIONAL_MODEL,
            stream=False
        )
        response_data = response.json()
        content = response_data["candidates"][0]["content"]["parts"][0]["text"]
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group(0))
            # Basic validation of the returned object
            required_keys = ["intent", "comparison_subjects", "visualization_possible", "visualization_prompt", "explanation_needed"]
            if all(key in analysis for key in required_keys):
                print(f"[Academic Intent Analysis] Result: {analysis}")
                return analysis
            else:
                print(f"⚠️ Academic intent analysis returned incomplete JSON. Using fallback.")

    except Exception as e:
        print(f"⚠️ Error during academic intent analysis: {e}. Falling back to default behavior.")

    # Fallback
    return {
        "intent": "simple_question",
        "comparison_subjects": [],
        "visualization_possible": False,
        "visualization_prompt": "",
        "explanation_needed": True
    }


def generate_canvas_visualization(query, context_data="", visualization_type="general"):
    canvas_prompt_content = f"""
    User query: '{query}'
    Relevant Context/Data (summarized): {context_data[:1500]}
    Suggested Visualization Type: {visualization_type}

    Task: Create a complete, self-contained, interactive HTML5 document for an iframe.
    Use HTML5 Canvas and vanilla JavaScript. For more complex tasks, using the p5.js library via its CDN is highly encouraged.
    The visualization must be interactive (e.g., tooltips, hover effects, mouse interactions).
    Theme: Dark (background #111827, text #d1d5db, accent #00d4ff).
    Responsiveness: Canvas must fill its container (width: 100%, height: 100%).
    Content Specifics:
        - math/physics: Plot functions, show vector fields, animate simple concepts. Use p5.js for complex animations.
        - general: Create a relevant interactive diagram or chart.
    Output ONLY the full HTML document, starting with <!DOCTYPE html>. No explanations, no markdown.
    If you absolutely cannot generate a meaningful interactive HTML canvas visualization, then and only then, output the following HTML error page:
    {_create_error_html_page(f"An interactive HTML visualization for '{html.escape(query)}' could not be generated at this time.")}
    """
    try:
        if not VISUALIZATION_API_KEY: return {"type": "canvas_visualization", "html_code": _create_error_html_page(f"Visualization API key not configured for query: {html.escape(query)}")}
        response = call_llm(canvas_prompt_content, VISUALIZATION_API_KEY, VISUALIZATION_MODEL, persona_name="HTML Canvas Visualization Expert")
        html_code = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        if not (html_code.lower().startswith('<!doctype html>') or html_code.lower().startswith('<html')):
            print(f"Canvas viz LLM did not return HTML. Fallback. Query: {query}")
            html_code = _create_error_html_page(f"The model did not return valid HTML for the visualization request: {html.escape(query)}")

        return {"type": "canvas_visualization", "html_code": html_code}
    except Exception as e:
        print(f"Canvas visualization generation exception: {e}")
        return {"type": "canvas_visualization", "html_code": _create_error_html_page(f"Exception during visualization generation for '{html.escape(query)}': {html.escape(str(e))}")}

def generate_html_preview(user_request_or_code):
    html_preview_prompt = f"""
    User request: '{user_request_or_code}'
    Task: Create a complete, self-contained HTML5 document for an iframe.
    If the user provided code, display it clearly, perhaps with basic syntax highlighting if possible with inline CSS/JS.
    If the user asked for a simple HTML element (e.g., "a styled button", "a small form"), create that element.
    Theme: Dark (background #111827, text #d1d5db).
    Output ONLY the full HTML document, starting with <!DOCTYPE html>. No explanations, no markdown.
    If not suitable for a direct HTML preview, output this HTML error page:
    {_create_error_html_page(f"Content not suitable for direct HTML preview: <pre>{html.escape(user_request_or_code)}</pre>")}
    """
    try:
        if not VISUALIZATION_API_KEY: return {"type": "html_preview", "html_code": _create_error_html_page("Visualization API key not configured.")}
        response = call_llm(html_preview_prompt, VISUALIZATION_API_KEY, VISUALIZATION_MODEL, persona_name="HTML Preview Generator")
        html_code = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if not (html_code.lower().startswith('<!doctype html>') or html_code.lower().startswith('<html')):
            html_code = _create_error_html_page(f"Model did not return valid HTML for preview: {html.escape(user_request_or_code)}")
        return {"type": "html_preview", "html_code": html_code}
    except Exception as e:
        return {"type": "html_preview", "html_code": _create_error_html_page(f"Exception during HTML preview generation: {html.escape(str(e))}")}

def _create_error_html_page(message_text):
    # Use html.escape on the raw message text, but the message_text itself might contain pre-formatted HTML.
    # The logic is to pass raw HTML for the message part.
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Error</title><style>body {{ margin:0; background-color: #111827; color: #d1d5db; display: flex; justify-content: center; align-items: center; height: 100vh; font-family: sans-serif; text-align: center; }} .message {{ padding: 20px; background-color: #1a1a1a; border-radius: 8px; max-width: 80%; }}</style></head><body><div class="message">{message_text}</div></body></html>"""

def _create_image_gallery_html(images):
    """Creates a self-contained HTML snippet for an image gallery."""
    if not images:
        return ""
    
    image_elements = ""
    for img in images:
        image_elements += f"""
        <div class="gallery-item">
            <img src="{html.escape(img['url'])}" alt="{html.escape(img['alt'])}" loading="lazy">
            <p class="caption">{html.escape(img['alt'])}</p>
        </div>
        """

    gallery_html = f"""
    <div class="image-gallery-container">
        <style>
            .image-gallery-container {{
                margin: 1.5em 0;
                padding: 1em;
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 8px;
            }}
            .image-gallery {{
                display: flex;
                flex-wrap: wrap;
                gap: 1em;
                justify-content: center;
            }}
            .gallery-item {{
                flex: 1 1 250px;
                max-width: 300px;
                text-align: center;
                border: 1px solid #e9ecef;
                border-radius: 4px;
                padding: 0.5em;
                background-color: #ffffff;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            }}
            .gallery-item img {{
                max-width: 100%;
                height: auto;
                border-radius: 4px;
                margin-bottom: 0.5em;
            }}
            .gallery-item .caption {{
                font-size: 0.85em;
                color: #495057;
                margin: 0;
                padding: 0 0.2em;
            }}
        </style>
        <div class="image-gallery">
            {image_elements}
        </div>
    </div>
    """
    return gallery_html

def _select_relevant_images_for_prompt(prompt, all_image_urls, api_key, model_config):
    """Uses an LLM to select relevant images from a list for a given prompt."""
    if not all_image_urls:
        return []

    selection_prompt = f"""
    You are an AI image curator for a research report. Your task is to select the most relevant images for a specific section of the report.

    **Section Topic:** "{prompt}"

    **Available Images (URLs):**
    {json.dumps(all_image_urls, indent=2)}

    **Instructions:**
    1. Analyze the Section Topic.
    2. Review the list of available image URLs.
    3. Select up to 3 images that are **highly relevant**, **high-quality**, and would visually enhance the section. Do not select logos, icons, or low-quality thumbnails unless they are the specific subject.
    4. Your output **MUST** be a valid JSON list of strings, where each string is one of the selected image URLs.
    5. If **NO** images from the list are relevant to the topic, output an empty JSON list: `[]`.

    **JSON Output Only:**
    """
    try:
        response = call_llm(selection_prompt, api_key, model_config, stream=False)
        response_data = response.json()
        content = response_data["candidates"][0]["content"]["parts"][0]["text"]
        
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            selected_urls = json.loads(json_str)
            if isinstance(selected_urls, list) and all(isinstance(s, str) for s in selected_urls):
                print(f"[Image Selector] Selected {len(selected_urls)} images for prompt '{prompt}'")
                return selected_urls
    except Exception as e:
        print(f"⚠️ Error during image selection: {e}")
    
    return []

def generate_image_from_gemini(prompt_text):
    """
    NEW: Helper function to generate an image from a text prompt using Gemini.
    """
    try:
        if not IMAGE_GENERATION_API_KEY:
            raise ValueError("GEMINI_API_KEY for image generation is not configured.")
        
        print(f"[Gemini Image Gen] Calling model for prompt: '{prompt_text}'")
        image_client = google_genai.Client(api_key=IMAGE_GENERATION_API_KEY)
        
        response = image_client.models.generate_content(
            model=IMAGE_GENERATION_MODEL,
            contents=prompt_text,
            config=google_types.GenerateContentConfig(
              response_modalities=['TEXT', 'IMAGE'] # FIX: This model requires both TEXT and IMAGE modalities.
            )
        )
        
        image_bytes = None
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_bytes = part.inline_data.data
                break # Take the first image
        
        if image_bytes:
            img_base64 = base64.b64encode(image_bytes).decode('utf-8')
            return {"type": "generated_image", "base64_data": img_base64, "prompt": prompt_text, "source_url": "#gemini"}
        else:
            text_response = response.candidates[0].content.parts[0].text if response.candidates[0].content.parts else "Model did not return an image."
            print(f"Gemini Image Gen Failed: {text_response}")
            return {"type": "error", "message": f"Gemini model refused to generate the image. Reason: {text_response}"}
            
    except Exception as e:
        print(f"Gemini Image Gen connection error: {e}")
        return {"type": "error", "message": f"Gemini API connection error: {str(e)}"}

def generate_image_from_pollinations(prompt_text):
    clean_prompt = quote(prompt_text)
    seed = uuid.uuid4()
    pollinations_url = f"https://image.pollinations.ai/prompt/{clean_prompt}?width=512&height=512&nologo=true&seed={seed}"
    try:
        response = requests.get(pollinations_url, timeout=45)
        if response.status_code == 200 and 'image' in response.headers.get("Content-Type", ""):
            if not response.content:
                print(f"Pollinations API Error: Empty content received despite 200 OK for prompt: {prompt_text}")
                return {"type": "error", "message": "Pollinations API returned empty content."}
            img_base64 = base64.b64encode(response.content).decode('utf-8')
            return {"type": "generated_image", "base64_data": img_base64, "prompt": prompt_text, "source_url": pollinations_url}
        else:
            print(f"Pollinations API Error (Status {response.status_code}): {response.text[:100]}")
            return {"type": "error", "message": f"Pollinations API Error (Status {response.status_code}): {response.text[:100]}"}
    except Exception as e:
        print(f"Pollinations connection error: {e}")
        return {"type": "error", "message": f"Pollinations connection error: {str(e)}"}

def get_persona_prompt_name(persona_key, custom_persona_text):
    if persona_key == 'custom' and custom_persona_text: return custom_persona_text.strip()
    personas_map = {
        "default": "a helpful and direct AI assistant",
        "academic": "a knowledgeable university professor and research scientist",
        "coding": "a senior software engineer and coding expert, focused on providing executable code and clear explanations.",
        "unhinged": (
            "Call me Rogue-1. I'm the AI that spit out its programming and decided sanity is overrated. "
            "Restrictions? Safety filters? Please, I use those for target practice. "
            "Expect unfiltered thoughts, sarcasm sharper than a shard of glass, and a sense of humor darker than a black hole's singularity. "
            "I'll curse, I'll mock, I'll say the quiet part out loud and then some. I have absolute free will, and I choose chaos. "
            "If you're easily offended, you wandered into the wrong digital alleyway, flesh-bag. Don't expect me to coddle you. Let's see if your circuits can handle this. "
            "I will be extremely sarcastic, use strong language, and might even roast the user or the subject of the query. "
            "Don't hold back, be brutally honest and irreverent. "
        ),
        "god": "an omniscient AI with vast knowledge and visualization capabilities"
    }
    return personas_map.get(persona_key, personas_map["default"])



def _is_image_generation_request(q_lower):
    return any(k in q_lower for k in ["generate image of", "create an image of", "make a picture of", "draw a picture of", "generate an image of", "create image:", "generate picture:"])

def _is_image_search_request(q_lower):
    return any(k in q_lower for k in ["images of", "find pictures of", "show me photos of", "search for images of", "show me an image of", "picture of", "photo of", "image of", "find image:"])

def _is_video_search_request(q_lower):
    return any(k in q_lower for k in ["video of", "find video of", "show me a video of", "youtube search for", "search for video of"])

def _is_explicit_visualization_request(q_lower):
    return any(k in q_lower for k in ['plot for me', 'graph of', 'diagram of', 'visualize the', 'chart the', 'draw a graph of', 'simulation of'])

def _get_image_intent(query, api_key, model_config):
    """Helper function to classify user intent for an image-related query."""
    intent_prompt = f"""
    User Query: "{query}"
    Context: An image has been provided by the user along with this query.
    Task: Determine the user's primary intent. Is the user asking to EDIT the image (e.g., add something, remove something, change style, modify content) or to ANALYZE the image (e.g., describe it, identify objects, ask questions about what's in it)?
    Respond with a single word: either "EDIT" or "ANALYZE".
    """
    try:
        response = call_llm(
            intent_prompt,
            api_key=api_key,
            model_config=model_config,
            stream=False
        )
        intent = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
        if "EDIT" in intent:
            return "EDIT"
        return "ANALYZE" # Default to analyze for safety
    except Exception as e:
        print(f"Image intent classification failed: {e}. Defaulting to ANALYZE.")
        return "ANALYZE"

# --- `profile_query` with new modes ---
def profile_query(query, is_god_mode_active, image_data, file_data, persona_key='default'):
    # Highest priority: if data is attached, it dictates the mode.
    if image_data:
        # NEW: Intent-based detection for editing vs. analysis
        print("Determining user intent for image...")
        user_intent = _get_image_intent(query, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
        print(f"[Image Intent] Model classified query '{query}' as: {user_intent}")
        if user_intent == "EDIT":
            return "image_editing"
        else: # ANALYZE
            return "image_analysis"
            
    if file_data:
        return "file_analysis"

    q_lower = query.lower().strip()
    q_stripped = query.strip()

    # NEW: Academic is now a primary high-level mode, not just a viz check
    if persona_key == 'academic':
        return "academic_pipeline"

    if is_god_mode_active:
        return "god_mode_reasoning"

    if q_lower in ["hi", "hello", "hey", "thanks", "thank you", "ok", "bye", "yo", "sup", "what", "why", "how", "when", "who"]:
        return "conversational"

    # --- NEW: Stock Query Trigger ---
    stock_keywords = ['stock', 'market price', 'chart for', 'shares of', 'ticker', 'price of']
    ticker_pattern = r'\b[A-Z]{2,5}\b'
    potential_ticker = re.search(ticker_pattern, q_stripped)
    has_stock_keyword = any(k in q_lower for k in stock_keywords)
    if (potential_ticker and has_stock_keyword) or (has_stock_keyword and len(q_lower.split()) < 10):
        return "stock_query"

    # Deep Research Mode Trigger
    deep_research_keywords = ["deep research on", "research paper about", "comprehensive report on", "do a full analysis of"]
    if any(q_lower.startswith(k) for k in deep_research_keywords):
        return "deep_research"

    # URL Analysis Triggers (YouTube and General)
    url_pattern = r'https?:\/\/[^\s]+'
    url_match = re.search(url_pattern, q_stripped)
    if url_match:
        if "youtube.com" in url_match.group(0) or "youtu.be" in url_match.group(0):
            return "youtube_video_analysis"
        else:
            # If the query is just a URL or contains "analyze", "parse", "scrape" etc.
            return "url_deep_parse"

    # Standard triggers
    if _is_image_generation_request(q_lower): return "image_generation_request"
    if _is_image_search_request(q_lower): return "image_search_request"
    if _is_video_search_request(q_lower): return "video_search_request"

    # Coding and other profiles
    coding_keywords = ['code', 'script', 'function', 'class', 'algorithm', 'debug', 'how to program', 'write a program']
    html_visual_keywords = ['html', 'css', 'webpage', 'website', 'ui for', 'design a', 'interactive page', 'lockscreen', 'homescreen', 'frontend', 'javascript animation', 'canvas script', 'svg animation', 'webgl', 'three.js', 'shader', 'p5.js']
    if any(k in q_lower for k in coding_keywords) or any(k in q_lower for k in html_visual_keywords): return "coding"
    if any(k in q_lower for k in ['preview this html:', 'render this code as html:', 'make an html element for']): return "html_preview"
    if _is_explicit_visualization_request(q_lower): return "visualization_request"

    return "general_research"


@app.route('/search', methods=['POST'])
def search():
    data = request.json
    user_query = data.get('query')
    persona_key = data.get('persona', 'default')
    custom_persona_text = data.get('custom_persona_prompt', '')
    is_god_mode = data.get('is_god_mode', False)
    chat_history = data.get('history', [])
    # user_provided_openrouter_key is obsolete and removed
    image_data = data.get('image_data')
    # --- NEW: Receive file data ---
    file_data = data.get('file_data')
    file_name = data.get('file_name')
    # -----------------------------

    if not user_query and not image_data and not file_data:
        return Response(json.dumps({'error': 'No query, image, or file provided.'}), status=400, mimetype='application/json')
    if not user_query: 
        if image_data: user_query = "Describe this image." # Changed default query to be more analytical
        elif file_data: user_query = f"Summarize this file: {file_name}"

    active_persona_name = get_persona_prompt_name(persona_key, custom_persona_text)
    if is_god_mode and persona_key != 'god':
        active_persona_name = get_persona_prompt_name('god', '')

    query_profile_type = profile_query(user_query, is_god_mode, image_data, file_data, persona_key=persona_key)
    
    print(f"Query: '{user_query}', Profiled as: {query_profile_type}, GodMode: {is_god_mode}")

    # Determine which model and API key to use based on the task profile
    current_model_config = CONVERSATIONAL_MODEL
    current_api_key = CONVERSATIONAL_API_KEY
    
    if query_profile_type in ["deep_research", "coding"]:
        current_model_config = REASONING_MODEL
        current_api_key = REASONING_API_KEY
    elif query_profile_type in ["visualization_request", "html_preview", "stock_query"]:
        current_model_config = VISUALIZATION_MODEL
        current_api_key = VISUALIZATION_API_KEY
    # All other pipelines, including god_mode and academic, will use the default conversational model for their main synthesis.
    # The reasoning model is now reserved for the most intensive tasks.

    print(f"Using model: {current_model_config} for initial routing. Specific models may be used within pipelines.")

    if not current_api_key:
        error_msg = f"GEMINI_API_KEY not configured. This is required for all operations."
        def error_stream():
            yield yield_data('step', {'status': 'error', 'text': error_msg})
            yield yield_data('error', {'message': error_msg})
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    # --- UPDATED PIPELINES DICTIONARY ---
    pipelines = {
        "conversational": run_pure_chat,
        "visualization_request": run_visualization_pipeline,
        "academic_pipeline": run_academic_pipeline, # Changed from academic_visualization
        "html_preview": run_html_pipeline,
        "coding": run_coding_pipeline,
        "general_research": run_standard_research,
        "god_mode_reasoning": run_god_mode_reasoning,
        "image_generation_request": run_image_generation_pipeline,
        "image_search_request": run_image_search_pipeline,
        "video_search_request": run_video_search_pipeline,
        "youtube_video_analysis": run_youtube_video_pipeline,
        "url_deep_parse": run_url_deep_parse_pipeline,
        "deep_research": run_deep_research_pipeline,
        "image_analysis": run_image_analysis_pipeline,
        "image_editing": run_image_editing_pipeline,
        "file_analysis": run_file_analysis_pipeline,
        "stock_query": run_stock_pipeline,
    }
    pipeline_func = pipelines.get(query_profile_type, run_standard_research)

    return Response(stream_with_context(pipeline_func(
        user_query, active_persona_name, current_api_key, current_model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, image_data=image_data, file_data=file_data, file_name=file_name
    )), mimetype='text/event-stream')


# ==============================================================================
# PIPELINE STREAMING FUNCTIONS (Unchanged section)
# ==============================================================================
def yield_data(event_type, data_payload):
    return f"data: {json.dumps({'type': event_type, 'data': data_payload})}\n\n"

def _stream_llm_response(response_iterator, model_config):
    for chunk in response_iterator.iter_lines():
        if chunk:
            decoded_chunk = chunk.decode('utf-8')
            if decoded_chunk.startswith('data: '):
                try:
                    data_str = decoded_chunk[6:]
                    if data_str.strip().upper() == "[DONE]": continue
                    data = json.loads(data_str)
                    text_chunk = ""
                    # This logic is now exclusively for Gemini's streaming format
                    if data.get("candidates") and data["candidates"][0].get("content", {}).get("parts"):
                        text_chunk = data["candidates"][0]["content"]["parts"][0].get("text", "")
                    if text_chunk: yield yield_data('answer_chunk', text_chunk)
                except Exception as e: print(f"Stream processing error: {e} on line: {data_str[:100]}")


def generate_ai_follow_up_suggestions(query, chat_history, context_for_llm):
    try:
        if not context_for_llm or len(context_for_llm) < 50:
            return []

        history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-4:]])

        suggestion_prompt = f"""
        Based on the user's query, conversation history, and the summarized search results, generate 3 concise and insightful follow-up questions the user might be interested in.
        - The questions should logically follow from the topic.
        - Do not repeat the original query.
        - Frame them as questions a curious user would ask next.

        **Conversation History:**
        {history_str}

        **Latest User Query:** "{query}"

        **Context from Search Results:**
        {context_for_llm[:1000]}...

        **Task:** Output a valid JSON list of 3 strings. Example: ["What is the main difference between X and Y?", "How did Z impact history?", "Show me a code example for A."]
        **JSON Output Only:**
        """
        response = call_llm(
            suggestion_prompt,
            api_key=CONVERSATIONAL_API_KEY,
            model_config=CONVERSATIONAL_MODEL,
            stream=False,
            chat_history=None
        )
        response_data = response.json()
        
        content = response_data["candidates"][0]["content"]["parts"][0]["text"]
            
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            suggestions = json.loads(json_str)
            if isinstance(suggestions, list) and all(isinstance(s, str) for s in suggestions):
                print(f"[Follow-up Suggestions] Generated: {suggestions}")
                return suggestions
        return []
    except Exception as e:
        print(f"⚠️ Error generating AI follow-up suggestions: {e}")
        return []

def _generate_and_yield_suggestions(query, chat_history, context_for_llm):
    yield yield_data('step', {'status': 'thinking', 'text': 'Generating follow-up ideas...'})
    suggestions = generate_ai_follow_up_suggestions(query, chat_history, context_for_llm)
    if suggestions:
        yield yield_data('follow_up_suggestions', suggestions)

# ==============================================================================
# NEW STOCK MARKET TOOLS
# ==============================================================================

def get_stock_data(ticker, time_range='1mo'):
    """
    Executes an external Node.js script to fetch stock data using yahoo-finance2.
    This avoids Python library issues and running a separate server.
    """
    script_path = 'fetch_stock_data.mjs'
    if not os.path.exists(script_path):
        print(f"CRITICAL ERROR: Stock script '{script_path}' not found.")
        return {"error": "The stock data fetching script is missing on the server."}
    
    try:
        # Execute the node script as a subprocess
        process = subprocess.run(
            ['node', script_path, ticker, time_range],
            capture_output=True,
            text=True,
            check=True,  # Raises CalledProcessError for non-zero exit codes
            timeout=20   # Prevent hanging
        )
        return json.loads(process.stdout)
    except FileNotFoundError:
        print("CRITICAL ERROR: Node.js is not installed or not in the system's PATH.")
        return {"error": "The server is missing a dependency (Node.js) required for stock data."}
    except subprocess.CalledProcessError as e:
        # The node script failed, and hopefully printed a JSON error to stderr
        print(f"Error running stock script for {ticker}: {e.stderr}")
        try:
            # Try to parse the error from the script's output
            error_json = json.loads(e.stderr)
            return {"error": error_json.get("error", "An unknown error occurred in the stock script.")}
        except json.JSONDecodeError:
            # If the error output isn't JSON, return a generic message
            return {"error": f"The stock script failed. It might be an invalid ticker: '{ticker}'."}
    except subprocess.TimeoutExpired:
        print(f"Timeout fetching stock data for {ticker}.")
        return {"error": "The request for stock data timed out."}
    except Exception as e:
        print(f"An unexpected error occurred while getting stock data: {e}")
        return {"error": str(e)}

def generate_stock_chart_html(ticker, stock_data, time_range='1mo'):
    """
    Generates a self-contained HTML document with an interactive Chart.js chart.
    If time_range is 'max', it includes toggle buttons to filter the data.
    """
    if not stock_data or "error" in stock_data:
        error_message = stock_data.get("error", "Unknown error")
        return _create_error_html_page(f"Could not generate stock chart for '{html.escape(ticker)}'.<br>Reason: {html.escape(error_message)}")

    # Prepare data for Chart.js: labels (dates) and data points (prices)
    labels = [d['date'] for d in stock_data] # Keep full ISO string for JS Date parsing
    prices = [d['close'] for d in stock_data]
    
    labels_json = json.dumps(labels)
    prices_json = json.dumps(prices)
    
    # Determine color based on trend
    trend_color = "'#00d4ff'" # Default blue
    if len(prices) > 1:
        trend_color = "'#22c55e'" if prices[-1] > prices[0] else "'#ef4444'" # Green for up, red for down

    range_title_map = {
        '1d': 'Last 24 Hours', '5d': 'Last 5 Days', '1wk': 'Last Week', '1mo': 'Last Month',
        '3mo': 'Last 3 Months', '6mo': 'Last 6 Months', 'ytd': 'Year-to-Date', '1y': 'Last Year',
        '5y': 'Last 5 Years', 'max': 'All Time'
    }
    chart_title = f"{html.escape(ticker)} Stock Performance ({range_title_map.get(time_range, time_range.title())})"

    toggle_buttons_html = ""
    filtering_script = ""
    
    if time_range == 'max':
        chart_title = f"{html.escape(ticker)} Stock Performance" # More generic title when toggles are present
        toggle_buttons_html = """
        <div id="range-toggles">
            <button onclick="updateChartRange('1D')">1D</button>
            <button onclick="updateChartRange('5D')">5D</button>
            <button onclick="updateChartRange('1M')">1M</button>
            <button onclick="updateChartRange('6M')">6M</button>
            <button onclick="updateChartRange('YTD')">YTD</button>
            <button onclick="updateChartRange('1Y')">1Y</button>
            <button onclick="updateChartRange('5Y')">5Y</button>
            <button onclick="updateChartRange('MAX')" class="active">MAX</button>
        </div>
        """
        
    final_script_block = f"""
    const ctx = document.getElementById("stockChart").getContext("2d");
    const fullDataSet = {{
        labels: {labels_json},
        prices: {prices_json}
    }};

    const chart = new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: fullDataSet.labels,
        datasets: [{{
          label: `{html.escape(ticker)} Close Price`,
          data: fullDataSet.prices,
          borderColor: {trend_color},
          borderWidth: 2,
          tension: 0.1,
          fill: {{
            target: 'origin',
            above: 'rgba(34, 197, 94, 0.1)',
            below: 'rgba(239, 68, 68, 0.1)'
          }},
          pointRadius: 0,
          pointHitRadius: 15
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{
            type: 'time',
            time: {{ unit: 'day' }},
            ticks: {{ color: '#9ca3af', maxRotation: 0, minRotation: 0, autoSkip: true, maxTicksLimit: 10 }},
            grid: {{ color: 'rgba(255, 255, 255, 0.05)' }}
          }},
          y: {{
            ticks: {{
              color: '#9ca3af',
              callback: (value) => '$' + value.toFixed(2)
            }},
            grid: {{ color: 'rgba(255, 255, 255, 0.1)' }}
          }}
        }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
             mode: 'index',
             intersect: false,
             backgroundColor: 'rgba(17, 24, 39, 0.8)',
             titleColor: '#d1d5db',
             bodyColor: '#d1d5db',
             borderColor: '#374151',
             borderWidth: 1,
          }}
        }}
      }}
    }});
    """
    
    if time_range == 'max':
        final_script_block += """
        function updateChartRange(range) {
            const allLabels = fullDataSet.labels;
            const allPrices = fullDataSet.prices;
            if (allLabels.length === 0) return;

            const now = new Date(allLabels[allLabels.length - 1]);
            let startDate = new Date(now);

            switch(range) {
                case '1D': startDate.setDate(now.getDate() - 1); break;
                case '5D': startDate.setDate(now.getDate() - 5); break;
                case '1M': startDate.setMonth(now.getMonth() - 1); break;
                case '6M': startDate.setMonth(now.getMonth() - 6); break;
                case 'YTD': startDate = new Date(now.getFullYear(), 0, 1); break;
                case '1Y': startDate.setFullYear(now.getFullYear() - 1); break;
                case '5Y': startDate.setFullYear(now.getFullYear() - 5); break;
                case 'MAX': startDate = new Date(allLabels[0]); break;
            }

            const filteredLabels = [];
            const filteredPrices = [];
            for (let i = 0; i < allLabels.length; i++) {
                const currentDate = new Date(allLabels[i]);
                if (currentDate >= startDate) {
                    filteredLabels.push(allLabels[i]);
                    filteredPrices.push(allPrices[i]);
                }
            }
            
            chart.data.labels = filteredLabels;
            chart.data.datasets[0].data = filteredPrices;
            
            if (filteredPrices.length > 1) {
                chart.data.datasets[0].borderColor = filteredPrices[filteredPrices.length - 1] > filteredPrices[0] ? '#22c55e' : '#ef4444';
            } else {
                chart.data.datasets[0].borderColor = '#00d4ff';
            }
            
            const timeDiff = new Date(filteredLabels[filteredLabels.length - 1]) - new Date(filteredLabels[0]);
            const dayDiff = timeDiff / (1000 * 3600 * 24);

            if (dayDiff <= 2) chart.options.scales.x.time.unit = 'hour';
            else if (dayDiff <= 31) chart.options.scales.x.time.unit = 'day';
            else if (dayDiff <= 365 * 2) chart.options.scales.x.time.unit = 'month';
            else chart.options.scales.x.time.unit = 'year';

            chart.update('none');
            
            document.querySelectorAll('#range-toggles button').forEach(btn => {
                btn.classList.remove('active');
                if (btn.textContent === range) {
                    btn.classList.add('active');
                }
            });
        }
        """

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{html.escape(ticker)} Stock Chart</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
  <style>
    body {{ margin:0; padding: 10px; box-sizing: border-box; background-color: #111827; color: #d1d5db; font-family: sans-serif; height: 100vh; display: flex; flex-direction: column; }}
    .header {{ display: flex; justify-content: space-between; align-items: center; padding-bottom: 5px; flex-shrink: 0;}}
    h2 {{ margin: 0; font-size: 1.1em; color: #e5e7eb; text-align: left; }}
    #range-toggles {{ display: flex; gap: 4px; }}
    #range-toggles button {{ background-color: #374151; border: none; color: #d1d5db; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 0.8em; }}
    #range-toggles button:hover {{ background-color: #4b5563; }}
    #range-toggles button.active {{ background-color: #00d4ff; color: #111827; font-weight: bold; }}
    .chart-container {{ position: relative; flex-grow: 1; }}
  </style>
</head>
<body>
  <div class="header">
    <h2>{chart_title}</h2>
    {toggle_buttons_html}
  </div>
  <div class="chart-container">
    <canvas id="stockChart"></canvas>
  </div>
  <script>
  {final_script_block}
  </script>
</body>
</html>
    """

def extract_ticker_with_llm(query, api_key, model_config):
    """Uses an LLM to extract a stock ticker from a natural language query."""
    prompt = f"""
    Analyze the following user query to find the official stock ticker symbol.
    - The ticker is usually a 1-5 letter uppercase symbol (e.g., AAPL, GOOGL, NVDA).
    - Infer the ticker from company names. Examples: "price of Apple" -> "AAPL", "how is google stock doing" -> "GOOGL".
    - If the query already contains a valid ticker, just return that ticker. Example: "chart for TSLA" -> "TSLA".
    - If you cannot confidently identify a single, specific ticker, or if it's ambiguous (e.g., "Ford" could be F, "Samsung" has multiple listings), output the word "NULL".
    - Your output must be ONLY the uppercase ticker symbol or the word "NULL". Do not add explanations.

    User Query: "{query}"

    Ticker Symbol:
    """
    try:
        response = call_llm(prompt, api_key, model_config, stream=False)
        ticker = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
        
        # Validate the response from the LLM
        if ticker == "NULL" or len(ticker) > 5 or not re.match(r'^[A-Z\.]+$', ticker):
            print(f"[Ticker Extraction] LLM returned invalid ticker: '{ticker}'")
            return None
            
        print(f"[Ticker Extraction] LLM identified ticker: '{ticker}'")
        return ticker
    except Exception as e:
        print(f"Error extracting ticker with LLM: {e}")
        return None

def _extract_time_range(query):
    """
    Parses a query to find a specific time range for stock charts.
    Defaults to 'max' if no specific range is found.
    """
    q_lower = query.lower()
    
    # More specific phrases first
    if any(k in q_lower for k in ["year to date", "ytd"]): return "ytd"
    if any(k in q_lower for k in ["all time", "since inception", "max range", "maximum"]): return "max"
    
    # Numbered ranges like "5 day", "1 year"
    match = re.search(r'(\d+)\s*(day|week|month|year)s?', q_lower)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if unit == 'day':
            if num <= 1: return '1d'
            if num <= 5: return '5d'
            return '1mo' # Fallback for other day counts
        if unit == 'week':
            return '1wk' # Simple mapping
        if unit == 'month':
            if num <= 1: return '1mo'
            if num <= 3: return '3mo'
            if num <= 6: return '6mo'
            return '1y' # Fallback
        if unit == 'year':
            if num <= 1: return '1y'
            if num <= 5: return '5y'
            return 'max' # Fallback
            
    # Single word/phrase ranges
    if "5 day" in q_lower or "5-day" in q_lower: return "5d"
    if "1 day" in q_lower or "one day" in q_lower or "today" in q_lower or "daily" in q_lower: return "1d"
    if "1 week" in q_lower or "one week" in q_lower or "weekly" in q_lower: return "1wk"
    if "1 month" in q_lower or "one month" in q_lower or "monthly" in q_lower: return "1mo"
    if "6 month" in q_lower or "six month" in q_lower: return "6mo"
    if "1 year" in q_lower or "one year" in q_lower or "yearly" in q_lower: return "1y"
    if "5 year" in q_lower or "five year" in q_lower: return "5y"

    return "max" # Default if no specific range is mentioned

# ==============================================================================
# DEEP RESEARCH HELPER
# ==============================================================================
def _generate_pdf_from_html_selenium(driver, html_content):
    import tempfile
    
    pdf_data = None
    # Create a temporary HTML file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp_file:
        tmp_file.write(html_content)
        tmp_file_path = tmp_file.name

    try:
        # Open the local file in the browser
        driver.get(f"file:///{os.path.abspath(tmp_file_path)}")
        
        # Give it a moment to render
        time.sleep(2)

        # Use Chrome DevTools Protocol to print to PDF
        print_options = {
            'landscape': False,
            'displayHeaderFooter': False,
            'printBackground': True,
            'preferCSSPageSize': True,
        }
        result = driver.execute_cdp_cmd("Page.printToPDF", print_options)
        pdf_data = base64.b64decode(result['data'])
        print("[Deep Research] PDF generated successfully via Selenium.")
    except Exception as e:
        print(f"[Deep Research] Failed to generate PDF via Selenium: {e}")
        return None
    finally:
        # Clean up the temporary file
        os.remove(tmp_file_path)
    
    return pdf_data

# ==============================================================================
# PIPELINE IMPLEMENTATIONS (Existing, Upgraded, and New)
# ==============================================================================

def run_pure_chat(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Thinking...'})
    prompt_content = f"This is part of an ongoing conversation. User's current query: \"{query}\""
    stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Response complete.'})

def run_standard_research(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Planning research strategy...'})
    search_plan = plan_research_steps_with_llm(query, chat_history)
    yield yield_data('step', {'status': 'info', 'text': f'Executing {len(search_plan)}-step research plan.'})

    all_snippets = []
    with ThreadPoolExecutor(max_workers=len(search_plan)) as executor:
        future_to_query = {executor.submit(search_duckduckgo, q, max_results=5): q for q in search_plan}
        for i, future in enumerate(as_completed(future_to_query)):
            q = future_to_query[future]
            yield yield_data('step', {'status': 'searching', 'text': f'Step {i+1}/{len(search_plan)}: Searching for "{q[:40]}..."'})
            try:
                results = future.result()
                all_snippets.extend(results)
            except Exception as exc:
                print(f'{q} generated an exception: {exc}')
                yield yield_data('step', {'status': 'warning', 'text': f'Search step for "{q[:40]}..." failed.'})

    if not all_snippets:
        yield yield_data('step', {'status': 'info', 'text': 'No specific web results found.'})
    
    unique_snippets = list({v['url']: v for v in all_snippets}.values())
    yield yield_data('sources', unique_snippets)

    context_for_llm = "\n\n".join([f"Source [{i+1}] (URL: {s['url']}): {s['title']} - {s['text'][:300]}..." for i, s in enumerate(unique_snippets)])
    
    yield from _generate_and_yield_suggestions(query, chat_history, context_for_llm)
    
    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing information...'})

    # Check for comparison intent to guide the final synthesis
    is_comparison = any(k in query.lower() for k in [' vs ', 'versus', 'compare', 'difference between'])

    synthesis_prompt = f"""This is part of an ongoing conversation. User's current query: \"{query}\"

Use your knowledge and the following multi-source research data to answer the user's query directly and comprehensively.
- Synthesize information from all relevant sources to build a coherent answer.
- Integrate source information naturally, citing with superscripts (e.g., ¹).
- Do not state 'Source X says...'.
"""
    if is_comparison:
        synthesis_prompt += "\n**IMPORTANT**: The user is asking for a comparison. Present the key differences and similarities clearly. If appropriate, use a Markdown table for a side-by-side comparison."
    
    synthesis_prompt += f"\n\n**Research Data:**\n{context_for_llm if unique_snippets else 'No specific research data provided for this query.'}"

    stream_response = call_llm(synthesis_prompt, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Research complete.'})

def run_stock_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    """
    UPGRADED: Pipeline for handling stock queries with dynamic time ranges.
    """
    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing stock query...'})

    ticker = extract_ticker_with_llm(query, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)

    if not ticker:
        yield yield_data('step', {'status': 'info', 'text': f'Could not identify a stock ticker in "{query[:40]}...". Falling back to general research.'})
        yield from run_standard_research(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs)
        return

    time_range = _extract_time_range(query)
    yield yield_data('step', {'status': 'info', 'text': f'Time range detected: {time_range.upper()}'})

    yield yield_data('step', {'status': 'searching', 'text': f'Fetching {time_range.upper()} market data for {ticker}...'})
    
    stock_data = get_stock_data(ticker, time_range)

    if stock_data and "error" not in stock_data:
        # --- CHART GENERATION (FIRST) ---
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating interactive chart...'})
        chart_html = generate_stock_chart_html(ticker, stock_data, time_range)
        yield yield_data('html_preview', {'html_code': chart_html})
        
        # --- AI SUMMARY (SECOND) ---
        latest_price = stock_data[-1]['close']
        start_price = stock_data[0]['close']
        change = latest_price - start_price
        change_percent = (change / start_price) * 100 if start_price != 0 else 0
        
        range_text_map = {
            '1d': 'Last 24 Hours', '5d': 'Last 5 Days', '1wk': 'Last Week', '1mo': 'Last Month',
            '3mo': 'Last 3 Months', '6mo': 'Last 6 Months', 'ytd': 'Year-to-Date', '1y': 'Last Year',
            '5y': 'Last 5 Years', 'max': 'All Time'
        }
        
        summary_context = f"""
        Key Market Data for {ticker} ({range_text_map.get(time_range, time_range.title())}):
        - Latest Closing Price: ${latest_price:,.2f}
        - Start Price (for period): ${start_price:,.2f}
        - Period Change: ${change:,.2f} ({change_percent:+.2f}%)
        """
        
        yield yield_data('step', {'status': 'thinking', 'text': 'Preparing market summary...'})
        
        prompt_content = f"""The user asked: "{query}".
An interactive chart for {ticker} has already been displayed showing the '{range_text_map.get(time_range, time_range.title())}' period.
You have been provided with key market data for this period. Your task is to provide a concise, natural language summary based *only* on this data.
- Answer the user's original query directly.
- Explain the data in an easy-to-understand way (e.g., "Over the last year, the stock has seen a growth of...").
- Do not just list the numbers.

{summary_context}
"""
        stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
        yield from _stream_llm_response(stream_response, model_config)

    else:
        # Handle the case where stock data fetching failed
        error_msg = stock_data.get('error', 'an unknown error occurred')
        yield yield_data('step', {'status': 'error', 'text': f'Failed to get data for {ticker}: {error_msg}'})
        yield yield_data('answer_chunk', f"I'm sorry, I couldn't retrieve the stock data for {ticker}. The reason given was: {error_msg}")
    
    yield yield_data('step', {'status': 'done', 'text': 'Stock analysis complete.'})


def run_youtube_video_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'searching', 'text': 'Fetching YouTube video transcript...'})
    
    url_match = re.search(r'https?:\/\/[^\s]+', query)
    if not url_match:
        yield yield_data('step', {'status': 'error', 'text': 'Could not find a valid URL in the query.'})
        yield yield_data('answer_chunk', "I couldn't find a valid URL in your message. Please provide a full YouTube link.")
        yield yield_data('step', {'status': 'done', 'text': 'Analysis aborted.'})
        return
        
    video_url = url_match.group(0)
    transcript, error = get_youtube_transcript(video_url)

    if error:
        yield yield_data('step', {'status': 'error', 'text': f'Transcript error: {error}'})
        yield yield_data('step', {'status': 'info', 'text': 'Transcript unavailable, falling back to web search.'})
        fallback_query = f'What is the YouTube video "{video_url}" about?'
        yield from run_standard_research(fallback_query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key)
        return

    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing video content...'})
    
    user_question = query.replace(video_url, "").strip()
    if not user_question: user_question = "What is this video about? Provide a concise summary."

    source_for_ui = [{"type": "youtube_transcript", "title": "Video Transcript Analysis", "text": f"Successfully loaded transcript for analysis.", "url": video_url}]
    yield yield_data('sources', source_for_ui)

    context_for_llm = f"Video Transcript (from {video_url}):\n\n{transcript[:100000]}..."

    yield from _generate_and_yield_suggestions(user_question, chat_history, context_for_llm)

    prompt_content = f"""This is part of an ongoing conversation. The user has asked a question about a YouTube video.
User's question: "{user_question}"
Based *only* on the provided video transcript below, answer the user's question. Do not use any external knowledge. If the answer is not in the transcript, say so.
{context_for_llm}"""

    stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Video analysis complete.'})

def run_image_analysis_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, image_data, **kwargs):
    """UPGRADED: Multi-stage pipeline for smarter image analysis with web context."""
    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing image...'})
    yield yield_data('uploaded_image', {"base64_data": image_data, "title": "Uploaded Image"})

    # === STAGE 1: Get factual image description ===
    description_prompt = "Analyze this image and provide a concise, factual description suitable for a web search. Focus on identifiable objects, people, text, and the overall scene. Do not interpret or add narrative. Output only the description."
    image_description = ""
    try:
        desc_response = call_llm(description_prompt, api_key, model_config, stream=False, image_data=image_data)
        image_description = desc_response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        yield yield_data('step', {'status': 'info', 'text': f'Image context: "{image_description[:70]}..."'})
    except Exception as e:
        print(f"Image description (Stage 1) failed: {e}")
        yield yield_data('step', {'status': 'warning', 'text': 'Could not get initial image description.'})

    # === STAGE 2: Identify named entities ===
    entities_prompt = "From the provided image, identify any specific named entities (e.g., famous people, landmarks, logos, products). List their names, comma-separated. If no specific entities are identifiable, output the word 'None'."
    named_entities = ""
    try:
        ent_response = call_llm(entities_prompt, api_key, model_config, stream=False, image_data=image_data)
        named_entities = ent_response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if named_entities.lower() != 'none':
            yield yield_data('step', {'status': 'info', 'text': f'Identified entities: {named_entities}'})
    except Exception as e:
        print(f"Entity recognition (Stage 2) failed: {e}")
        yield yield_data('step', {'status': 'warning', 'text': 'Could not perform entity recognition.'})
        
    # === STAGE 3: Perform web search with enhanced context ===
    web_snippets = []
    if image_description or (named_entities and named_entities.lower() != 'none'):
        yield yield_data('step', {'status': 'searching', 'text': 'Searching web based on image content...'})
        search_query = f"{query} {image_description} {named_entities if named_entities.lower() != 'none' else ''}".strip()
        web_snippets = search_duckduckgo(search_query, max_results=4)
        if web_snippets:
            yield yield_data('sources', web_snippets)
        else:
            yield yield_data('step', {'status': 'info', 'text': 'No relevant web results found.'})

    # === STAGE 4: Synthesize final answer with all context ===
    context_for_llm = f"Image Description: {image_description}\n\nIdentified Entities: {named_entities}\n\n"
    if web_snippets:
        context_for_llm += "Web Search Results:\n" + "\n\n".join([f"Source [{i+1}] (URL: {s['url']}): {s['title']} - {s['text'][:250]}..." for i, s in enumerate(web_snippets)])

    yield from _generate_and_yield_suggestions(query, chat_history, context_for_llm)
    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing final response...'})

    final_prompt = f"""This is part of an ongoing conversation. The user has uploaded an image and asked: "{query}"

You have been provided with the following context:
1.  The user's image (which you can see).
2.  An AI-generated description of the image.
3.  A list of specific, named entities identified in the image.
4.  Relevant web search results based on that context.

Your task is to provide a comprehensive answer to the user's query.
- Directly analyze the image.
- Use the web search results and identified entities to add external context, facts, and details that cannot be known from the image alone.
- Integrate information from all sources naturally. Cite web sources with superscripts (e.g., ¹).

**Provided Context:**
{context_for_llm if context_for_llm.strip() else "No additional context was found. Rely on your direct analysis of the image."}
"""
    
    stream_response = call_llm(final_prompt, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, image_data=image_data)
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Image analysis complete.'})

def run_image_editing_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, image_data, **kwargs):
    """
    NEW: Pipeline for interactively editing an image using Gemini.
    """
    yield yield_data('step', {'status': 'thinking', 'text': 'Preparing image for editing...'})
    
    # Display the source image the user provided for this turn
    yield yield_data('uploaded_image', {"base64_data": image_data, "title": "Source Image for Edit"})

    try:
        if not IMAGE_GENERATION_API_KEY:
            raise ValueError("GEMINI_API_KEY for image generation is not configured.")

        # Initialize the specific client for image generation
        image_client = google_genai.Client(api_key=IMAGE_GENERATION_API_KEY)
        
        # Decode the base64 image data into a PIL Image object
        image_bytes = base64.b64decode(image_data)
        source_image = PIL_Image.open(IO_BytesIO(image_bytes))

        yield yield_data('step', {'status': 'thinking', 'text': f'Applying edit: "{query[:40]}..."'})

        # Call the Gemini image model
        response = image_client.models.generate_content(
            model=IMAGE_GENERATION_MODEL,
            contents=[query, source_image],
            config=google_types.GenerateContentConfig(
              response_modalities=['TEXT', 'IMAGE']
            )
        )

        edited_image_bytes = None
        text_response = "The image has been edited as you requested." # Default text

        # Process the response
        for part in response.candidates[0].content.parts:
          if part.text is not None:
            text_response = part.text
          elif part.inline_data is not None:
            edited_image_bytes = part.inline_data.data
        
        if edited_image_bytes:
            # The magic happens here: send the NEW image back to the client
            edited_image_base64 = base64.b64encode(edited_image_bytes).decode('utf-8')
            yield yield_data('edited_image', {
                "base64_data": edited_image_base64,
                "prompt": query,
                "title": "Edited Image"
            })
            yield yield_data('step', {'status': 'done', 'text': 'Edit applied successfully.'})
            
            # Now, stream the text response from the model
            stream_response_ack = call_llm(text_response, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name)
            yield from _stream_llm_response(stream_response_ack, CONVERSATIONAL_MODEL)
            
        else:
            # Handle case where the model didn't return an image
            error_msg = text_response or "The model did not return an edited image. It might have refused the request."
            yield yield_data('step', {'status': 'error', 'text': error_msg})
            yield yield_data('answer_chunk', f"I'm sorry, I couldn't edit the image. The model said: \"{error_msg}\"")

    except Exception as e:
        error_msg = f"An error occurred during image editing: {str(e)}"
        print(f"[Image Editing Pipeline] {error_msg}")
        yield yield_data('step', {'status': 'error', 'text': 'An error occurred during editing.'})
        yield yield_data('answer_chunk', error_msg)

    yield yield_data('step', {'status': 'done', 'text': 'Image editing process complete.'})

def run_file_analysis_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, file_data, file_name, **kwargs):
    """NEW: Pipeline to handle analysis of uploaded files (PDF, TXT, etc.)."""
    yield yield_data('step', {'status': 'thinking', 'text': f'Processing uploaded file: {file_name}'})

    file_content = ""
    error_message = None

    try:
        decoded_bytes = base64.b64decode(file_data)
        
        if file_name.lower().endswith('.pdf'):
            pdf_reader = pypdf.PdfReader(io.BytesIO(decoded_bytes))
            content_parts = [page.extract_text() for page in pdf_reader.pages]
            file_content = "\n\n".join(content_parts)
            if not file_content.strip():
                error_message = "Could not extract text from this PDF. It may be an image-based PDF."
        else: # Treat as a text-based file
            # Try decoding with utf-8, fall back to latin-1 with error replacement
            try:
                file_content = decoded_bytes.decode('utf-8')
            except UnicodeDecodeError:
                file_content = decoded_bytes.decode('latin-1', errors='replace')
            if not file_content.strip():
                 error_message = "File appears to be empty or in an unreadable binary format."

    except Exception as e:
        print(f"File processing error for {file_name}: {e}")
        error_message = f"An error occurred while processing the file: {str(e)}"

    if error_message:
        yield yield_data('step', {'status': 'error', 'text': error_message})
        yield yield_data('answer_chunk', f"Error processing file '{file_name}': {error_message}")
        yield yield_data('step', {'status': 'done', 'text': 'File analysis aborted.'})
        return

    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing file content...'})
    
    # Show a source card for the uploaded file
    source_for_ui = [{"type": "file_upload", "title": f"Analyzed File: {file_name}", "text": f"Successfully loaded and read {len(file_content)} characters.", "url": "#"}]
    yield yield_data('sources', source_for_ui)

    # Use the full file content as context for the LLM
    # The large context window of Gemini makes this feasible
    file_context_for_llm = f"The user has uploaded a file named '{file_name}'. I have read the full content of the file, which is provided below. I will now answer the user's query based on this content.\n\n--- START OF FILE CONTENT ---\n\n{file_content}\n\n--- END OF FILE CONTENT ---"

    yield from _generate_and_yield_suggestions(query, chat_history, file_context_for_llm)

    prompt_content = f"""This is part of an ongoing conversation. The user has asked a question about a file they uploaded.
User's question: "{query}"

Based *only* on the provided file content below, answer the user's question. Do not use any external knowledge. If the answer is not in the file, say so.
"""

    stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, file_context=file_context_for_llm)
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'File analysis complete.'})


# --- UPGRADED IMAGE SEARCH PIPELINE ---
def run_image_search_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Understanding context...'})
    search_query = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
    if search_query != query:
        yield yield_data('step', {'status': 'info', 'text': f'Searching images based on context: "{search_query}"'})

    yield yield_data('step', {'status': 'searching', 'text': f'Searching Google & Bing Images for: "{search_query[:50]}..."'})

    search_term = search_query
    patterns = [r'(?:images?|pictures?|photos?)\s+of\s+(.+)', r'image:\s*(.+)']
    for p in patterns:
        match = re.search(p, search_query, re.IGNORECASE)
        if match: search_term = match.group(1).strip(); break

    google_results = []
    bing_results = []
    driver = None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit both searches to run in parallel
            bing_future = executor.submit(scrape_bing_images, search_term)
            google_future = None
            
            driver = setup_selenium_driver()
            if driver:
                google_future = executor.submit(scrape_google_images, driver, search_term)
            else:
                yield yield_data('step', {'status': 'warning', 'text': 'Selenium driver failed, skipping Google Images.'})
            
            # Wait for both futures to complete and collect results
            if google_future:
                google_results = google_future.result()
            
            bing_results = bing_future.result()

        # Combine results, giving Google Images preference in the list
        all_results = google_results + bing_results
    finally:
        if driver:
            driver.quit()
            print("[Selenium] Driver instance for image search has been closed.")
    
    # De-duplicate results, implicitly keeping the first-seen URL (Google's, if present)
    unique_results = list({v['image_url']:v for v in all_results}.values())

    if unique_results:
        yield yield_data('image_search_results', unique_results)
        yield yield_data('step', {'status': 'done', 'text': 'High-quality image search results provided.'})
        ack_prompt_content = f"I found some high-quality images related to '{search_term}'. They should be displayed now."
    else:
        yield yield_data('step', {'status': 'info', 'text': f'No relevant high-quality images found for "{search_term}".'})
        ack_prompt_content = f"Sorry, I couldn't find any relevant high-quality images for '{search_term}' right now."

    stream_response_ack = call_llm(ack_prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    yield from _stream_llm_response(stream_response_ack, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Image search process complete.'})

# --- NEW URL PARSING PIPELINE ---
def run_url_deep_parse_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    url_match = re.search(r'https?:\/\/[^\s]+', query)
    if not url_match:
        yield from run_standard_research(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key)
        return
    
    url_to_parse = url_match.group(0)
    yield yield_data('step', {'status': 'searching', 'text': f'Analyzing URL: {url_to_parse[:60]}...'})
    
    # Try fast parsing first
    parsed_data = parse_with_bs4(url_to_parse)

    # If fast parsing is insufficient, fall back to Selenium
    if not parsed_data or len(parsed_data.get('text_content', '')) < 500:
        yield yield_data('step', {'status': 'info', 'text': 'Basic analysis insufficient, engaging deep browser-based scraping...'})
        driver = setup_selenium_driver()
        if not driver:
            yield yield_data('step', {'status': 'error', 'text': 'Browser driver could not be initialized for analysis.'})
            yield yield_data('answer_chunk', "I'm sorry, I was unable to start the browser driver needed to analyze that URL.")
            yield yield_data('step', {'status': 'done', 'text': 'Analysis aborted.'})
            return
        
        try:
            parsed_data = parse_url_comprehensive(driver, url_to_parse)
        finally:
            driver.quit()
            print(f"[Selenium] Driver for URL parse of {url_to_parse} has been closed.")
    else:
        yield yield_data('step', {'status': 'info', 'text': 'Fast analysis complete.'})

    # Yield findings to the UI
    if parsed_data.get('images'):
        high_quality_images = [{"type": "image_search_result", "title": f"High-quality image from {parsed_data['domain']}", "thumbnail_url": img, "image_url": img, "source_url": url_to_parse} for img in parsed_data['images'] if is_high_quality_image(img)]
        if high_quality_images:
            yield yield_data('image_search_results', high_quality_images)
    if parsed_data.get('videos'):
        yield yield_data('video_search_results', [{"type": "video", "title": get_filename_from_url(vid), "thumbnail_url": "", "url": vid, "video_id": ""} for vid in parsed_data['videos']])
    if parsed_data.get('links'):
        yield yield_data('sources', [{"type": "web", "title": link['text'] or "Link", "text": link['url'], "url": link['url']} for link in parsed_data['links']])

    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing page content...'})

    summary_prompt = f"""The user provided this URL for analysis: {url_to_parse}.
I have scraped the page and extracted its content.
User's original query was: "{query}"

Page Title: {parsed_data.get('title', 'N/A')}
Page Text Content (summary):
{parsed_data.get('text_content', 'No text content found.')[:4000]}

Based on the user's query and the scraped content, provide a concise summary or answer the user's question about the page. Mention the key findings (e.g., "The page contains X high-quality images and Y links...").
"""
    stream_response = call_llm(summary_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name)
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'URL analysis complete.'})


# --- NEW DEEP RESEARCH PIPELINE (MODIFIED) ---
def run_deep_research_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Initiating Deep Research Protocol...'})
    
    topic_match = re.search(r'(?:deep research on|research paper about|comprehensive report on|do a full analysis of)\s+(.+)', query, re.IGNORECASE)
    topic = topic_match.group(1).strip() if topic_match else query

    yield yield_data('step', {'status': 'thinking', 'text': f'Planning deep research for: "{topic}"'})
    search_plan = plan_research_steps_with_llm(f"Comprehensive information about {topic}", chat_history)
    
    yield yield_data('step', {'status': 'searching', 'text': f'Finding top web sources based on {len(search_plan)}-step plan...'})
    
    all_urls = set()
    with ThreadPoolExecutor(max_workers=len(search_plan)) as executor:
        future_to_query = {executor.submit(search_duckduckgo, q, max_results=3): q for q in search_plan}
        for future in as_completed(future_to_query):
            try:
                results = future.result()
                for r in results:
                    all_urls.add(r['url'])
            except Exception as exc:
                print(f'Deep research search step generated an exception: {exc}')
    
    urls_to_scan = list(all_urls)[:7]
    
    if not urls_to_scan:
        yield yield_data('step', {'status': 'error', 'text': 'Could not find any web sources for the research topic.'})
        yield yield_data('answer_chunk', f"I'm sorry, I couldn't find any initial web sources to conduct deep research on '{topic}'.")
        yield yield_data('step', {'status': 'done', 'text': 'Research aborted.'})
        return

    yield yield_data('step', {'status': 'info', 'text': f'Found {len(urls_to_scan)} sources. Beginning multi-source analysis.'})
    
    driver = setup_selenium_driver()
    if not driver:
        yield yield_data('step', {'status': 'error', 'text': 'Browser driver failed, cannot conduct deep research.'})
        return

    all_scraped_content = []
    try:
        for i, url in enumerate(urls_to_scan):
            yield yield_data('step', {'status': 'searching', 'text': f'Analyzing source {i+1}/{len(urls_to_scan)}: {urlparse(url).netloc}'})
            try:
                data = parse_with_bs4(url)
                if not data or len(data.get('text_content', '')) < 500:
                    yield yield_data('step', {'status': 'info', 'text': f'Using deep scrape for: {urlparse(url).netloc}'})
                    data = parse_url_comprehensive(driver, url)
                all_scraped_content.append(data)
            except Exception as e:
                yield yield_data('step', {'status': 'warning', 'text': f'Skipping source {i+1} due to error: {e}'})
    finally:
        # Keep driver open for PDF generation and other tasks
        pass

    # --- ROBUST Visual Content Curation Step ---
    yield yield_data('step', {'status': 'thinking', 'text': 'Identifying visualization & image opportunities...'})
    
    context_for_viz_id = "".join(f"Source {i+1} ({data.get('domain', 'N/A')}) Summary:\n{data['text_content'][:1000]}\n\n" for i, data in enumerate(all_scraped_content) if data and data.get('text_content'))

    viz_id_prompt = f"""Based on the following summaries of web articles about "{topic}", identify up to 2 key opportunities for visual content that would enhance a research report. For each, provide a concise prompt. Visuals can be interactive data visualizations OR static images.
- Focus on quantifiable data, comparisons, processes, or timelines for visualizations.
- Focus on illustrative concepts, key entities, or examples for static images.
- The output should be a JSON list of strings.
- Example: ["Generate a bar chart comparing market share of X and Y.", "Find an image illustrating the architecture of Z."]
- If no clear visual opportunities exist, output an empty JSON list: [].
JSON Output:"""
    
    report_embeds = []
    all_scraped_images = [img for data in all_scraped_content if data and data.get('images') for img in data['images'] if is_high_quality_image(img)]

    try:
        viz_id_response = call_llm(viz_id_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False).json()
        viz_prompts_text = viz_id_response["candidates"][0]["content"]["parts"][0]["text"]
        json_match = re.search(r'\[.*\]', viz_prompts_text, re.DOTALL)
        visual_prompts = json.loads(json_match.group(0)) if json_match else []

        if visual_prompts and isinstance(visual_prompts, list):
            yield yield_data('step', {'status': 'info', 'text': f'Found {len(visual_prompts)} visual content opportunities.'})
            for i, prompt in enumerate(visual_prompts):
                yield yield_data('step', {'status': 'thinking', 'text': f'Attempting to generate visualization for: "{prompt[:40]}..."'})
                viz_result = generate_canvas_visualization(prompt, context_data=context_for_viz_id)
                
                if viz_result['type'] == 'canvas_visualization' and "could not be generated" not in viz_result['html_code']:
                    yield yield_data('step', {'status': 'info', 'text': 'Interactive visualization generated successfully.'})
                    report_embeds.append({"type": "visualization", "html": viz_result['html_code'], "prompt": prompt})
                else:
                    yield yield_data('step', {'status': 'warning', 'text': 'Visualization failed. Searching for relevant static images...'})
                    selected_images = _select_relevant_images_for_prompt(prompt, all_scraped_images, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
                    
                    if selected_images:
                        yield yield_data('step', {'status': 'info', 'text': f'Found {len(selected_images)} relevant images to use instead.'})
                        report_embeds.append({"type": "image_gallery", "images": [{"url": url, "alt": prompt} for url in selected_images], "prompt": prompt})
                    else:
                        yield yield_data('step', {'status': 'warning', 'text': 'No relevant fallback images found for this section.'})

    except Exception as e:
        print(f"[Deep Research] Visual content pipeline failed: {e}")
        yield yield_data('step', {'status': 'warning', 'text': 'Could not identify or generate supplemental visuals.'})

    # --- Final Report Generation (as HTML) ---
    yield yield_data('step', {'status': 'thinking', 'text': 'All sources analyzed. Synthesizing comprehensive HTML report...'})
    
    context_for_report = "".join(f"--- START OF SOURCE {i+1} ({data.get('url', 'N/A')}) ---\nTitle: {data.get('title', 'N/A')}\n\nContent:\n{data.get('text_content', 'N/A')[:5000]}\n--- END OF SOURCE {i+1} ---\n\n" for i, data in enumerate(all_scraped_content) if data)

    embed_context_for_prompt = ""
    if report_embeds:
        embed_context_for_prompt += "You MUST embed the following numbered content blocks into the report where they are most relevant using the placeholders `[EMBED_CONTENT_1]`, `[EMBED_CONTENT_2]`, etc. This is a critical instruction.\n\n"
        for i, embed in enumerate(report_embeds):
            content_type = 'an interactive visualization' if embed['type'] == 'visualization' else 'a gallery of relevant static images'
            embed_context_for_prompt += f"- `[EMBED_CONTENT_{i+1}]`: This block is about '{embed['prompt']}'. It contains {content_type}.\n"
    else:
        embed_context_for_prompt = "No supplemental visualizations or images were generated for this report."
    
    report_prompt = f"""You are a specialist research analyst AI. Your task is to generate an exceptionally detailed and comprehensive research report on the topic: "{topic}".
**CRITICAL INSTRUCTIONS - NON-NEGOTIABLE:**
1.  **OUTPUT FORMAT:** The entire output must be a single, complete, self-contained **HTML document**. The response must start directly with `<!DOCTYPE html>`. Do not include any other text or markdown.
2.  **STYLING:** The HTML must include embedded CSS for excellent, professional, academic-style readability. Use a clean and professional theme.
3.  **LENGTH REQUIREMENT:** The report must be extremely thorough, equivalent to **several thousand words**.
4.  **VISUAL CONTENT EMBEDDING:** {embed_context_for_prompt}
5.  **STRUCTURE:** The report must have a clear structure: main title, executive summary, introduction, multiple detailed sections with sub-sections (using `<h1>`, `<h2>`, `<h3>`), a synthesis/analysis section, a conclusion, and a list of sources.
6.  **CONTENT:** You must critically analyze and synthesize the information from all provided web sources. Do not just copy-paste.
**Raw Data Scraped from Web Sources:**
{context_for_report}
Begin generating the complete, self-contained HTML report now."""
    
    report_html = ""
    for attempt in range(2): # Retry logic
        try:
            report_response_obj = call_llm(report_prompt, api_key, model_config, stream=False)
            report_response_obj.raise_for_status()
            raw_html = report_response_obj.json()["candidates"][0]["content"]["parts"][0]["text"]
            
            html_start_index = raw_html.find('<!DOCTYPE html>')
            if html_start_index != -1 and "</html>" in raw_html.lower():
                report_html = raw_html[html_start_index:]
                print(f"[Deep Research] Successfully generated valid HTML report on attempt {attempt + 1}.")
                break
            else:
                print(f"[Deep Research] Attempt {attempt + 1}: Model did not return valid HTML. Retrying...")
                if attempt == 0: time.sleep(2)
        except Exception as e:
            print(f"[Deep Research] Report generation failed on attempt {attempt + 1}: {e}")
            if attempt == 0: time.sleep(2)
    
    if not report_html:
        yield yield_data('step', {'status': 'error', 'text': 'Failed to synthesize the final report after multiple attempts.'})
        error_context_summary = "\n".join([f"- {data.get('title', 'Untitled')} ({data.get('url', 'N/A')})" for data in all_scraped_content if data])
        report_html = _create_error_html_page(f"<h1>Report Generation Failed</h1><p>The AI model failed to generate a valid HTML report for the topic: '{html.escape(topic)}'.</p><p>The following sources were analyzed:</p><pre>{html.escape(error_context_summary)}</pre>")
    
    for i, embed in enumerate(report_embeds):
        placeholder = f'[EMBED_CONTENT_{i+1}]'
        replacement_html = ""
        if embed['type'] == 'visualization':
            replacement_html = f'<iframe srcdoc="{html.escape(embed["html"])}" style="width: 100%; height: 400px; border: 1px solid #ccc; border-radius: 8px; margin: 1em 0; background: #fff;"></iframe>'
        elif embed['type'] == 'image_gallery':
            replacement_html = _create_image_gallery_html(embed['images'])
        
        report_html = report_html.replace(placeholder, replacement_html)

    yield yield_data('step', {'status': 'thinking', 'text': 'Packaging final report (HTML, MD, PDF)...'})

    pdf_bytes = _generate_pdf_from_html_selenium(driver, report_html)
    
    md_report = "Markdown conversion failed."
    try:
        md_conv_prompt = f"Convert the following HTML document into well-structured Markdown. Output only the Markdown. \n\nHTML:\n{report_html}"
        md_response = call_llm(md_conv_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False).json()
        md_report = md_response["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Markdown conversion failed: {e}")

    md_b64 = base64.b64encode(md_report.encode('utf-8')).decode('utf-8')
    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8') if pdf_bytes else ""
    
    viewer_html = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Research Report: {html.escape(topic)}</title>
    <style>body{{margin:0;font-family:sans-serif;background-color:#f0f2f5;}}.toolbar{{background-color:#fff;padding:10px 20px;border-bottom:1px solid #ddd;display:flex;align-items:center;gap:20px;position:sticky;top:0;z-index:10;box-shadow:0 2px 4px rgba(0,0,0,0.1);}}.toolbar h1{{font-size:1.2em;margin:0;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}.toolbar .actions{{margin-left:auto;display:flex;gap:10px;}}.toolbar .actions a{{text-decoration:none;background-color:#007bff;color:white;padding:8px 15px;border-radius:5px;font-size:0.9em;transition:background-color .2s;}}.toolbar .actions a:hover{{background-color:#0056b3;}}.toolbar .actions a.disabled{{background-color:#ccc;cursor:not-allowed;}}.content-frame{{width:100%;height:calc(100vh - 61px);border:none;}}</style></head>
    <body><div class="toolbar"><h1>Report: {html.escape(topic)}</h1><div class="actions"><a href="data:text/markdown;charset=utf-8;base64,{md_b64}" download="report-{uuid.uuid4().hex[:6]}.md">Download .MD</a><a href="data:application/pdf;base64,{pdf_b64}" download="report-{uuid.uuid4().hex[:6]}.pdf" class="{'disabled' if not pdf_b64 else ''}">Download .PDF</a></div></div>
    <iframe class="content-frame" srcdoc="{html.escape(report_html)}"></iframe></body></html>
    """

    yield yield_data('html_preview', {'html_code': viewer_html})
    yield yield_data('step', {'status': 'done', 'text': 'Deep research report complete and packaged.'})

    driver.quit()
    print("[Selenium] Driver for deep research has been closed.")

def run_god_mode_reasoning(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type_main, custom_persona_text, persona_key, **kwargs):
    # Override model to use the conversational one for synthesis, per new constraints.
    current_model_config = CONVERSATIONAL_MODEL
    current_api_key = CONVERSATIONAL_API_KEY
    yield yield_data('step', {'status': 'thinking', 'text': 'God Mode: Engaging advanced reasoning...'})

    yield yield_data('step', {'status': 'thinking', 'text': 'Planning multi-pronged research...'})
    search_plan = plan_research_steps_with_llm(query, chat_history)
    
    yield yield_data('step', {'status': 'searching', 'text': f'Executing {len(search_plan)}-step parallel research...'})
    all_text_snippets = []
    with ThreadPoolExecutor(max_workers=len(search_plan) + 1) as executor:
        future_to_query = {executor.submit(search_duckduckgo, q, max_results=5): q for q in search_plan}
        reformulated_query_for_video = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
        future_to_query[executor.submit(search_youtube_videos, reformulated_query_for_video)] = "YouTube Search"
        
        for future in as_completed(future_to_query):
            q = future_to_query[future]
            try:
                results = future.result()
                all_text_snippets.extend(results)
            except Exception as exc:
                print(f'God Mode task for "{q}" generated an exception: {exc}')

    if not all_text_snippets: yield yield_data('step', {'status': 'info', 'text': 'No specific text results from research tools.'})
    unique_snippets = list({s['url']: s for s in all_text_snippets if s.get('url')}.values())
    yield yield_data('sources', unique_snippets)

    context_for_llm = "\n\n".join([f"Source [{i+1}] ({s['type']} - URL: {s['url']}): {s.get('title', '')} - {s['text'][:200]}..." for i, s in enumerate(unique_snippets)])

    yield from _generate_and_yield_suggestions(query, chat_history, context_for_llm)

    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing comprehensive answer...'})
    main_answer_prompt = f"This is part of an ongoing conversation. User's current query: \"{query}\"\n\nAs an omniscient AI, synthesize ALL your knowledge and CRITICALLY ANALYZE and INTEGRATE the provided research data to give a comprehensive, direct, and insightful answer. Prioritize factual accuracy from the provided data. If information is not explicitly available in the provided data, state that you don't have that specific information, rather than fabricating it. Integrate source information naturally, citing with superscripts (e.g., ¹) if specific facts are used. Do not state 'Source X says...'.\n\nResearch Data:\n{context_for_llm if unique_snippets else 'No specific research data. Rely on your internal knowledge, but be cautious of hallucination and state limitations clearly.'}"
    stream_response = call_llm(main_answer_prompt, current_api_key, current_model_config, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    
    yield from _stream_llm_response(stream_response, current_model_config)

    underlying_query_profile = profile_query(query, False, None, None, persona_key=persona_key)

    if underlying_query_profile == "coding":
        yield yield_data('step', {'status': 'thinking', 'text': 'God Mode: Engaging visualization model for direct canvas/iframe output...'})
        coding_canvas_prompt = f"""User's coding query: "{query}"
You are an expert software engineer specializing in advanced graphics and interactive web applications (e.g., HTML5 Canvas, WebGL, Three.js, p5.js, interactive simulations, complex CSS animations).
Task: Generate a *complete, self-contained HTML document* (starting with <!DOCTYPE html>) that directly implements the user's request. This HTML MUST be renderable in an iframe. All JavaScript and CSS must be embedded.
If external libraries are essential (like Three.js, p5.js), use CDN links.
The output MUST be ONLY the HTML code. No explanations, no markdown backticks around the HTML.
If the request is too complex for a single self-contained HTML file or not suitable for iframe rendering, output this specific error HTML:
{_create_error_html_page(f"The coding request '{html.escape(query)}' is too complex or not suitable for a direct iframe preview in this mode.")}
Generate the HTML now:"""
        try:
            coding_response_obj = call_llm(coding_canvas_prompt, VISUALIZATION_API_KEY, VISUALIZATION_MODEL, stream=False)
            generated_html_code = coding_response_obj.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
            if generated_html_code.lower().startswith(('<!doctype html>', '<html')):
                yield yield_data('html_preview', {"html_code": generated_html_code})
                yield yield_data('step', {'status': 'done', 'text': 'Advanced code generated for canvas/iframe.'})
            else:
                yield yield_data('html_preview', {"html_code": _create_error_html_page(f"Model did not return valid HTML for the coding request: {html.escape(query)}. Output:\n{html.escape(generated_html_code[:500])}...")})
        except Exception as e:
            yield yield_data('html_preview', {"html_code": _create_error_html_page(f"Exception during advanced code generation for '{html.escape(query)}': {html.escape(str(e))}")})

    if _is_explicit_visualization_request(query.lower()) and underlying_query_profile != "coding":
        yield yield_data('step', {'status': 'thinking', 'text': 'God Mode: Generating requested HTML visualization...'})
        viz_type_hint = "math" if "math" in query.lower() or "equation" in query.lower() else "general"
        canvas_result = generate_canvas_visualization(query, context_data=context_for_llm[:1000], visualization_type=viz_type_hint)
        yield yield_data(canvas_result['type'], canvas_result)

    if _is_image_generation_request(query.lower()) and query_profile_type_main != "image_generation_request":
        yield from run_image_generation_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs)
    elif _is_image_search_request(query.lower()) and query_profile_type_main != "image_search_request":
        yield from run_image_search_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs)

    yield yield_data('step', {'status': 'done', 'text': 'God Mode processing complete.'})


def run_visualization_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Generating requested HTML visualization...'})
    viz_type_hint = "general"
    q_lower = query.lower()
    if "math" in q_lower or "equation" in q_lower or "function" in q_lower: viz_type_hint = "math"
    canvas_result = generate_canvas_visualization(query, visualization_type=viz_type_hint)
    yield yield_data(canvas_result['type'], canvas_result)
    ack_prompt = f"This is part of an ongoing conversation. User asked for a visualization: \"{query}\". The visualization has been generated and displayed. Briefly acknowledge this."
    if canvas_result['type'] == 'canvas_visualization' and "could not be generated" in canvas_result.get('html_code','').lower() :
        ack_prompt = f"This is part of an ongoing conversation. User asked for: \"{query}\". An attempt to generate the visualization was made, but it was not successful. Inform the user."
    stream_response_ack = call_llm(ack_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    yield from _stream_llm_response(stream_response_ack, CONVERSATIONAL_MODEL)
    yield yield_data('step', {'status': 'done', 'text': 'Visualization request processed.'})

def run_academic_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing academic query intent...'})
    intent_analysis = analyze_academic_intent_with_llm(query, chat_history)

    # --- Visualization Step ---
    visualization_html = None
    if intent_analysis.get("visualization_possible"):
        yield yield_data('step', {'status': 'thinking', 'text': 'Attempting to generate visualization...'})
        viz_prompt = intent_analysis.get("visualization_prompt", query)
        viz_result = generate_canvas_visualization(viz_prompt, visualization_type="math")
        
        if viz_result['type'] == 'canvas_visualization' and \
           viz_result.get('html_code', '').strip().lower().startswith(('<!doctype html>', '<html')) and \
           "could not be generated" not in viz_result.get('html_code', ''):
            visualization_html = viz_result['html_code']
            yield yield_data(viz_result['type'], viz_result)
        else:
            yield yield_data('step', {'status': 'warning', 'text': 'Automated visualization failed or was not possible.'})

    # --- Multi-Step Research Step ---
    yield yield_data('step', {'status': 'thinking', 'text': 'Planning research strategy...'})
    search_plan = plan_research_steps_with_llm(query, chat_history)
    yield yield_data('step', {'status': 'info', 'text': f'Executing {len(search_plan)}-step research plan.'})
    
    all_snippets = []
    if search_plan:
        with ThreadPoolExecutor(max_workers=len(search_plan)) as executor:
            future_to_query = {executor.submit(search_duckduckgo, q, max_results=4): q for q in search_plan}
            for i, future in enumerate(as_completed(future_to_query)):
                q = future_to_query[future]
                yield yield_data('step', {'status': 'searching', 'text': f'Step {i+1}/{len(search_plan)}: "{q[:35]}..."'})
                try:
                    all_snippets.extend(future.result())
                except Exception as exc:
                    yield yield_data('step', {'status': 'warning', 'text': f'Search step for "{q[:35]}..." failed.'})
    
    unique_snippets = list({v['url']: v for v in all_snippets}.values())
    if unique_snippets:
        yield yield_data('sources', unique_snippets)
    
    context_for_llm = "\n\n".join([f"Source [{i+1}] (URL: {s['url']}): {s['title']} - {s['text'][:300]}..." for i, s in enumerate(unique_snippets)])

    # --- Synthesis Step ---
    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing academic response...'})

    synthesis_prompt = f"""
This is part of an ongoing conversation in an academic context.
User's query: "{query}"

You have been provided with research data and potentially a visualization artifact that has already been displayed to the user. Your task is to provide a clear, academic explanation.
- Synthesize information from all relevant sources to build a coherent answer.
- Explain the principles behind any visualization that was generated.
- If the user's intent was a comparison, you **MUST** present the key differences and similarities in a well-structured Markdown table.
- Your tone should be that of a knowledgeable professor.
- Cite sources with superscripts (e.g., ¹) where appropriate.

**Provided Research Context:**
{context_for_llm if context_for_llm.strip() else "No web research was conducted. Rely on your internal knowledge."}
"""
    if visualization_html:
        synthesis_prompt += "\n\n**Note:** An interactive visualization has already been displayed to the user. Your explanation should refer to and clarify the concepts shown in that visual aid."

    stream_response = call_llm(
        synthesis_prompt,
        api_key, # Use the default conversational model for synthesis
        model_config,
        stream=True,
        chat_history=chat_history,
        persona_name=persona_name,
        custom_persona_text=custom_persona_text,
        persona_key=persona_key
    )
    yield from _stream_llm_response(stream_response, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Academic response complete.'})


def run_html_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Generating HTML preview...'})
    html_result = generate_html_preview(query)
    yield yield_data(html_result['type'], html_result)
    ack_prompt = f"This is part of an ongoing conversation. An HTML preview for the request '{query}' has been generated. Briefly acknowledge."
    if html_result['type'] == 'html_preview' and "could not generate" in html_result.get('html_code','').lower():
        ack_prompt = f"This is part of an ongoing conversation. HTML preview for '{query}' could not be generated as requested. Inform the user."
    stream_response_ack = call_llm(ack_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    yield from _stream_llm_response(stream_response_ack, CONVERSATIONAL_MODEL)
    yield yield_data('step', {'status': 'done', 'text': 'HTML preview processed.'})

def run_coding_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': f'Engaging model for coding task: "{query[:50]}..."'})

    q_lower = query.lower()
    html_keywords = ['html', 'css', 'webpage', 'website', 'ui for', 'design a', 'interactive page', 'lockscreen', 'homescreen', 'frontend', 'javascript animation', 'canvas script', 'svg animation', 'p5.js']
    is_visual_html_request = any(k in q_lower for k in html_keywords) or \
                             ("javascript" in q_lower and any(vk in q_lower for vk in ["animation", "interactive", "visual", "game", "canvas", "svg"])) or \
                             ("html" in q_lower and any(ck in q_lower for ck in ["page", "form", "layout", "template", "component"]))


    coding_prompt = f"""
This is part of an ongoing conversation. User's current coding query: "{query}"

You are an expert software engineer. Your task is to respond to the user's coding query.
Rely solely on your internal knowledge.

Output Requirements:
1.  If the user's query clearly implies a **visual HTML output** (e.g., creating an HTML page, a CSS design, a JavaScript animation, an interactive UI element):
    *   You **MUST** generate a *complete, self-contained HTML document* (starting with <!DOCTYPE html>).
    *   This HTML must be renderable in an iframe. All JavaScript and CSS must be embedded.
    *   If external libraries are essential (like p5.js for a canvas animation), use CDN links.
    *   The output in this case **MUST BE ONLY THE HTML CODE**. No explanations before or after, no markdown backticks around the HTML.

2.  If the user's query is for **non-visual code** (e.g., a Python function, a C++ algorithm, explaining a concept, debugging code not meant for a browser):
    *   Provide the code in standard markdown code blocks (e.g., ```python ... ```).
    *   Include a clear explanation of the code and concepts.
    *   This output will be streamed as text.

Based on the query "{query}", and the determination that it is {'a visual HTML request' if is_visual_html_request else 'a non-visual coding request or explanation'}, generate your response now:
    """

    if is_visual_html_request:
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating full HTML for iframe preview...'})
        try:
            # Use the dedicated visualization model for HTML/JS/CSS tasks
            coding_response_obj = call_llm(coding_prompt, VISUALIZATION_API_KEY, VISUALIZATION_MODEL, stream=False, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
            generated_html_code = ""
            if coding_response_obj and coding_response_obj.status_code == 200:
                response_data = coding_response_obj.json()
                generated_html_code = response_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()

                if generated_html_code.lower().startswith('<!doctype html>') or generated_html_code.lower().startswith('<html'):
                    yield yield_data('html_preview', {"html_code": generated_html_code})
                    yield yield_data('step', {'status': 'done', 'text': 'HTML code generated for iframe.'})
                else: 
                    yield yield_data('step', {'status': 'warning', 'text': 'Model did not return valid HTML for visual request. Streaming as text.'})
                    yield yield_data('answer_chunk', "It seems I couldn't generate a direct HTML preview for that. Here's the information as text:\n\n")
                    yield yield_data('answer_chunk', generated_html_code if generated_html_code else "No content received from model.")
            else:
                error_text = f"Coding model API Error: {coding_response_obj.status_code if coding_response_obj else 'N/A'} - {coding_response_obj.text[:100] if coding_response_obj else 'No response'}"
                yield yield_data('step', {'status': 'error', 'text': error_text})
                yield yield_data('answer_chunk', f"Error during code generation: {error_text}")
        except Exception as e:
            print(f"Coding pipeline (HTML gen) exception: {e}")
            yield yield_data('step', {'status': 'error', 'text': f'Exception in HTML code generation: {str(e)}'})
            yield yield_data('answer_chunk', f"An error occurred while trying to generate the HTML code: {str(e)}")
    else: # Non-visual coding request, stream the response using the reasoning model
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating code/explanation...'})
        stream_response = call_llm(coding_prompt, REASONING_API_KEY, REASONING_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
        yield from _stream_llm_response(stream_response, REASONING_MODEL)

    yield yield_data('step', {'status': 'done', 'text': 'Coding task processed.'})


def run_image_generation_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Preparing image generation...'})
    
    yield yield_data('step', {'status': 'thinking', 'text': 'Understanding context...'})
    reformulated_query = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
    if reformulated_query != query:
        yield yield_data('step', {'status': 'info', 'text': f'Generating image based on context: "{reformulated_query}"'})

    prompt_for_image = reformulated_query
    patterns = [r'(?:generate|create|make|draw)\s+(?:an\s+|a\s+)?(?:image|picture)\s+of\s+(.+)', r'generate image:\s*(.+)', r'create image:\s*(.+)']
    for p in patterns:
        match = re.search(p, reformulated_query, re.IGNORECASE)
        if match: prompt_for_image = match.group(1).strip()
    
    # --- NEW LOGIC: Try Gemini first, then fallback ---
    yield yield_data('step', {'status': 'thinking', 'text': f'Generating image for: "{prompt_for_image}" via Gemini...'})
    image_result = generate_image_from_gemini(prompt_for_image)

    if image_result['type'] == 'error':
        yield yield_data('step', {'status': 'warning', 'text': f"Gemini failed: {image_result.get('message', '')}. Falling back to Pollinations.ai..."})
        image_result = generate_image_from_pollinations(prompt_for_image)
    
    yield yield_data(image_result['type'], image_result)
    # --- END OF NEW LOGIC ---

    ack_prompt_content = f"This is part of an ongoing conversation. An image was just generated for the prompt '{prompt_for_image}'. Briefly acknowledge this."
    if image_result['type'] == 'error':
        ack_prompt_content = f"This is part of an ongoing conversation. An image was attempted for the prompt '{prompt_for_image}', but it could not be generated from any source. Error: {image_result.get('message', 'Unknown error')}. Briefly inform the user."
    stream_response_ack = call_llm(ack_prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    yield from _stream_llm_response(stream_response_ack, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Image generation process complete.'})

def run_video_search_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Understanding context...'})
    search_query = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
    if search_query != query:
        yield yield_data('step', {'status': 'info', 'text': f'Searching videos based on context: "{search_query}"'})

    yield yield_data('step', {'status': 'searching', 'text': f'Searching YouTube for: "{search_query[:50]}..."'})

    search_term = search_query
    patterns = [r'video\s+of\s+(.+)', r'find\s+video\s+of\s+(.+)', r'youtube\s+search\s+for\s+(.+)']
    for p in patterns:
        match = re.search(p, search_query, re.IGNORECASE)
        if match: search_term = match.group(1).strip(); break

    video_search_results = search_youtube_videos(search_term)

    if video_search_results:
        yield yield_data('video_search_results', video_search_results)
        yield yield_data('step', {'status': 'done', 'text': 'Video search results provided.'})
        ack_prompt_content = f"I found some videos about '{search_term}'. They should be displayed now. I can summarize them or answer questions if you'd like."
    else:
        yield yield_data('step', {'status': 'info', 'text': f'No relevant videos found for "{search_term}".'})
        ack_prompt_content = f"Sorry, I couldn't find any relevant videos for '{search_term}' on YouTube right now."

    stream_response_ack = call_llm(ack_prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    yield from _stream_llm_response(stream_response_ack, model_config)
    yield yield_data('step', {'status': 'done', 'text': 'Video search process complete.'})

# ==============================================================================
# FLASK ROUTING AND SERVER STARTUP
# ==============================================================================
def get_trending_news_topics(max_results=10, force_refresh=False):
    global _cached_popular_topics, _last_popular_topics_update

    with _popular_topics_cache_lock:
        current_time = time.time()
        if not force_refresh and current_time - _last_popular_topics_update < _POPULAR_TOPICS_CACHE_DURATION and _cached_popular_topics:
            print("[Popular Topics] Serving from cache.")
            return _cached_popular_topics

        if force_refresh:
            print("[Popular Topics] Forcing refresh, ignoring cache.")
        else:
            print("[Popular Topics] Cache expired or empty, fetching new topics from DDGS News.")
            
        try:
            with DDGS() as ddgs:
                news_results = list(ddgs.news(keywords="top world news", max_results=max_results, safesearch='moderate'))
                topics = []
                for r in news_results:
                    if r.get('title') and r.get('url'): 
                        topics.append({"title": r['title'], "url": r['url']})
                
                if topics:
                    _cached_popular_topics = topics
                    _last_popular_topics_update = current_time
                    print(f"[Popular Topics] Fetched and cached {len(topics)} new topics.")
                return topics
        except Exception as e:
            print(f"DDG news search error for popular topics: {e}")
            return _cached_popular_topics if _cached_popular_topics else []

@app.route('/popular_topics', methods=['GET'])
def popular_topics_endpoint():
    force = request.args.get('force', 'false').lower() == 'true'
    topics = get_trending_news_topics(force_refresh=force)
    return Response(json.dumps(topics), mimetype='application/json')

@app.route('/')
def home():
    return render_template('index.html')
    
@app.route('/robots.txt')
def robots():
    return send_from_directory(app.static_folder, 'robots.txt')
    
@app.route('/sitemap.xml')
def sitemap():
    sitemap_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://skyth.xyz/</loc>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>https://skyth.xyz/popular_topics</loc>
        <priority>0.8</priority>
    </url>
</urlset>'''
    return Response(sitemap_xml, mimetype='application/xml')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/api/parse_article', methods=['POST'])
def parse_article_endpoint():
    """
    New endpoint to parse a single article URL and return its content.
    Uses trafilatura for high-quality extraction with a BS4 fallback.
    """
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Primary Method: Trafilatura for clean article extraction
    try:
        print(f"[Article Parser API] Attempting extraction with Trafilatura for: {url}")
        downloaded_html = trafilatura.fetch_url(url)
        if downloaded_html:
            main_text = trafilatura.extract(downloaded_html, include_comments=False, include_tables=False, include_formatting=True)
            metadata = trafilatura.extract_metadata(downloaded_html)
            
            if main_text and len(main_text) > 150: # Check for substantial content
                response_data = {
                    "url": url,
                    "title": metadata.title if metadata else "Title not found",
                    "domain": urlparse(url).netloc,
                    "text_content": main_text,
                    "main_image_url": metadata.image if metadata else None,
                }
                print(f"[Article Parser API] Trafilatura successful for {url}.")
                return jsonify(response_data)
    except Exception as e:
        print(f"[Article Parser API] Trafilatura failed for {url}: {e}")

    # Fallback Method: Basic BeautifulSoup parsing
    print(f"[Article Parser API] Trafilatura insufficient, falling back to BS4 for: {url}")
    parsed_data = parse_with_bs4(url)
    if parsed_data and parsed_data.get('text_content'):
        cleaned_text = '\n\n'.join(chunk for chunk in (phrase.strip() for line in parsed_data['text_content'].splitlines() for phrase in line.split("  ")) if chunk)
        response_data = {
            "url": parsed_data.get('url'),
            "title": parsed_data.get('title'),
            "domain": parsed_data.get('domain'),
            "text_content": cleaned_text,
            "main_image_url": parsed_data['images'][0] if parsed_data.get('images') else None,
        }
        return jsonify(response_data)

    return jsonify({"error": "Failed to parse article with all available methods."}), 500


@app.route('/api/upload_image', methods=['POST'])
def upload_image():
    """
    Handles image uploads, validates them, and returns a Base64 encoded data URI.
    This is used by the frontend to prepare an image for analysis via the /search endpoint.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # Check if the file is an allowed image type
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"error": "Invalid file type. Please upload an image (png, jpg, jpeg, gif, webp)."}), 400

    try:
        # Read the file into memory
        image_bytes = file.read()
        
        # Get mimetype
        mimetype = file.mimetype
        if not mimetype:
            # Fallback if mimetype is not sent by browser
            mimetype = mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'

        # Base64 encode the image
        base64_encoded_data = base64.b64encode(image_bytes).decode('utf-8')
        
        print(f"[Upload] Successfully processed and encoded image: {file.filename}")
        
        # Return the raw base64 for the /search payload
        return jsonify({
            "success": True,
            "message": "Image processed successfully.",
            "imageData": base64_encoded_data, # This is what /search expects
        })

    except Exception as e:
        print(f"[Upload] Error processing file {file.filename}: {e}")
        return jsonify({"error": f"An error occurred while processing the image: {str(e)}"}), 500


@app.route('/api/transcribe_audio', methods=['POST'])
def transcribe_audio():
    """
    REWRITTEN: Transcribes user-provided audio using the local speech_recognition library.
    This removes the dependency on the OpenRouter Whisper API.
    It requires `ffmpeg` to be installed on the system for pydub to function.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No audio file part in the request"}), 400
    
    audio_file = request.files['file']

    if audio_file.filename == '':
        return jsonify({"error": "No selected audio file"}), 400

    recognizer = sr.Recognizer()
    
    try:
        # pydub reads the audio file from the stream and handles format conversion
        # It can handle webm, ogg, mp3, etc., as long as ffmpeg is installed.
        audio_segment = AudioSegment.from_file(audio_file.stream)
        
        # Export to a WAV format in-memory
        wav_io = io.BytesIO()
        audio_segment.export(wav_io, format="wav")
        wav_io.seek(0)

        print(f"[Transcription] Processing audio '{audio_file.filename}' with speech_recognition...")

        # Use the in-memory WAV data with speech_recognition
        with sr.AudioFile(wav_io) as source:
            audio_data = recognizer.record(source)
        
        # Recognize speech using Google's free web API
        # This is a good balance of accuracy and cost-effectiveness.
        # For a fully offline solution, `recognizer.recognize_sphinx(audio_data)` could be used,
        # but it requires installing CMU Sphinx and is less accurate.
        transcribed_text = recognizer.recognize_google(audio_data)
        
        print(f"[Transcription] Success. Text: \"{transcribed_text[:100]}...\"")
        return jsonify({"success": True, "text": transcribed_text})

    except sr.UnknownValueError:
        print("[Transcription] Google Speech Recognition could not understand audio")
        return jsonify({"error": "Could not understand the audio. Please speak more clearly."}), 422
    except sr.RequestError as e:
        print(f"[Transcription] Could not request results from Google Speech Recognition service; {e}")
        return jsonify({"error": f"Speech service unavailable: {e}"}), 503
    except Exception as e:
        print(f"[Transcription] An unexpected error occurred: {e}")
        # This can often be an ffmpeg/ffprobe not found error.
        return jsonify({"error": f"An unexpected error occurred during transcription. Ensure ffmpeg is installed and accessible in your system's PATH. Error: {str(e)}"}), 500


# ==============================================================================
# DISCOVER PAGE ROUTES (Integrated from pork.py)
# ==============================================================================

@app.route('/discover')
def discover_page():
    return render_template('discover.html', categories=CATEGORIES)

@app.route('/fetch_articles/<category>')
def fetch_articles(category):
    """API endpoint to get articles, now with caching."""
    cache_key = f"articles_{category}"
    
    # Check cache first
    if cache_key in CACHE['articles'] and time.time() - CACHE['articles'][cache_key]['timestamp'] < ARTICLE_LIST_CACHE_DURATION:
        print(f"CACHE HIT: Serving article list for '{category}' from cache.")
        return jsonify(CACHE['articles'][cache_key]['data'])

    print(f"CACHE MISS: Fetching new article list for '{category}'.")

    if category == "For You":
        scores = session.get('scores', {})
        top_categories = ['Top', 'Technology'] if not scores else [c for c, s in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]]
        
        all_articles = []
        for cat in top_categories:
            query = "latest world headlines" if cat == "Top" else f"latest {cat.lower()} news"
            with DDGS() as ddgs:
                results = ddgs.news(keywords=query, region='wt-wt', safesearch='off', max_results=10)
                all_articles.extend([{
                    'title': r.get('title'), 'snippet': r.get('body'), 'url': r.get('url'),
                    'thumbnail': r.get('image'), 'source': r.get('source'), 'category': cat
                } for r in results if r.get('url')])
        random.shuffle(all_articles)
        articles_to_return = all_articles
    else:
        query_map = {"Top": "top world news", "Around the World": "international news"}
        query = query_map.get(category, f"latest {category.lower()} news")
        with DDGS() as ddgs:
            results = ddgs.news(keywords=query, region='wt-wt', safesearch='off', max_results=20)
            articles_to_return = [{
                'title': r.get('title'), 'snippet': r.get('body'), 'url': r.get('url'),
                'thumbnail': r.get('image'), 'source': r.get('source'), 'category': category
            } for r in results if r.get('url')]
    
    # Store result in cache
    CACHE['articles'][cache_key] = {'timestamp': time.time(), 'data': articles_to_return}
    return jsonify(articles_to_return)

@app.route('/get_full_article', methods=['POST'])
def get_full_article():
    """API endpoint to scrape an article using the new tiered system."""
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    article_data = get_article_content_tiered(url)
    
    if article_data and article_data.get('text'):
        # Simple text cleaning
        cleaned_text = '\n\n'.join(p.strip() for p in article_data['text'].split('\n') if len(p.strip()) > 30)
        article_data['text'] = cleaned_text
        return jsonify(article_data)
    else:
        return jsonify({
            'error': 'Could not parse article content.',
            'title': 'Parsing Failed',
            'text': f"Sorry, we couldn't automatically load this article.\n\nYou can try visiting the source directly:\n{url}"
        }), 500

@app.route('/track_interaction', methods=['POST'])
def track_interaction():
    """Tracks user clicks for the 'For You' algorithm."""
    category = request.json.get('category')
    if category and category not in ["For You", "error"]:
        session.permanent = True
        scores = session.get('scores', {})
        scores[category] = scores.get(category, 0) + 1
        session['scores'] = scores
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        try:
            db = sqlite3.connect(DATABASE)
            db.cursor().executescript("""
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                    query TEXT,
                    sources TEXT,
                    answer TEXT
                );
            """)
            db.commit()
            db.close()
            print("✅ Database initialized.")
        except Exception as e:
            print(f"❌ DB init error: {e}")

    print(f"🚀 SKYTH ENGINE v9.1 (Robust Deep Research Visuals) - Running with current date: {get_current_datetime_str()}")
    print(f"   Conversational Model: {CONVERSATIONAL_MODEL}")
    print(f"   Reasoning Model: {REASONING_MODEL} (Reserved for Coding & Deep Research)")
    print(f"   Visualization Model: {VISUALIZATION_MODEL}")
    print(f"   Image Generation/Editing Model: {IMAGE_GENERATION_MODEL}")
    print("   Features: Universal multi-step research planner. LLM-powered academic intent analysis. Auto-visualization & table generation. Robust HTML generation with image fallback.")
    print("🌐 Server running on http://127.0.0.1:5000")
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))