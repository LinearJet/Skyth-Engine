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
import html # For escaping HTML content
from urllib.parse import quote, urlparse, urljoin, unquote # For various URL operations
from concurrent.futures import ThreadPoolExecutor, as_completed
from ddgs import DDGS
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
import yfinance as yf
import pandas as pd


from config import (
    CACHE, CONTENT_CACHE_DURATION, SITE_PARSERS, GENERIC_SELECTORS,
    CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, VISUALIZATION_API_KEY,
    VISUALIZATION_MODEL, IMAGE_GENERATION_API_KEY, IMAGE_GENERATION_MODEL,
    UTILITY_API_KEY, UTILITY_MODEL
)
from utils import yield_data
from tool_registry import ToolRegistry

# ==============================================================================
# DISCOVER PAGE LOGIC (Integrated from pork.py)
# ==============================================================================


def get_article_content_tiered(url):
    """
    The new core function. Implements a tiered scraping strategy for max speed.
    """
    if url in CACHE['content'] and time.time() - CACHE['content'][url]['timestamp'] < CONTENT_CACHE_DURATION:
        print(f"CACHE HIT: Serving content for {url} from cache.")
        return CACHE['content'][url]['data']

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        title = soup.find('title').get_text() if soup.find('title') else 'No Title'
        og_image_tag = soup.find('meta', property='og:image')
        image = og_image_tag['content'] if og_image_tag else None

        text = None
        domain = urlparse(url).netloc
        
        if domain in SITE_PARSERS:
            print(f"TIER 2: Using site-specific parser for {domain}")
            text = SITE_PARSERS[domain](soup)

        if not text:
            print("TIER 3: Using generic fast scraper (requests + BeautifulSoup)")
            for selector in GENERIC_SELECTORS:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(separator='\n\n', strip=True)
                    if len(text) > 200:
                        break
        
        if text:
            article_data = {'title': title, 'text': text, 'image': image}
            CACHE['content'][url] = {'timestamp': time.time(), 'data': article_data}
            return article_data

    except requests.RequestException as e:
        print(f"Fast scrape failed for {url}: {e}. Falling back to Selenium.")

    print(f"TIER 4: Falling back to Selenium for {url}")
    article_data = extract_text_content_selenium(url)
    if article_data and article_data.get('text'):
        CACHE['content'][url] = {'timestamp': time.time(), 'data': article_data}
        return article_data

    return None

def extract_text_content_selenium(url):
    """The Selenium scraper, now used only as a last resort."""
    driver = None
    try:
        driver = setup_selenium_driver()
        driver.get(url)
        wait = WebDriverWait(driver, 10)
        
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
# SHARED UTILITY TOOLS
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
            filename = f"download_{uuid.uuid4().hex[:8]}.html"
        return re.sub(r'[<>:"/\\|?*\s]', '_', filename)
    except Exception:
        return f"download_{uuid.uuid4().hex[:8]}.bin"

def is_high_quality_image(url):
    """Filter for high quality images based on URL patterns and size indicators."""
    if not url:
        return False
    
    low_quality_patterns = [r'thumb', r'thumbnail', r'icon', r'avatar', r'logo', r'badge', r'button', r'pixel', r'1x1', r'spacer', r'blank', r'transparent', r'loading', r'spinner', r'placeholder', r'_s\.', r'_xs\.', r'_sm\.', r'_tiny\.', r'_mini\.', r'_micro\.', r'50x50', r'100x100', r'16x16', r'32x32', r'64x64', r'favicon', r'sprite', r'emoji', r'emoticon']
    
    url_lower = url.lower()
    for pattern in low_quality_patterns:
        if re.search(pattern, url_lower):
            return False
    
    high_quality_patterns = [r'_l\.', r'_xl\.', r'_xxl\.', r'_large\.', r'_big\.', r'_full\.', r'_original\.', r'_hd\.', r'_hq\.', r'800x', r'1024x', r'1200x', r'1920x', r'2048x']
    
    for pattern in high_quality_patterns:
        if re.search(pattern, url_lower):
            return True
    
    image_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.svg']
    if any(url_lower.endswith(ext) for ext in image_extensions):
        return True
    
    return True

def get_current_datetime_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

def call_llm(prompt_content, api_key, model_config, stream=False, chat_history=None, persona_name="AI Assistant", custom_persona_text=None, persona_key="default", image_data=None, file_context=None):
    """
    Unified LLM calling function for Google Gemini models.
    """
    base_system_message = (
        f"Current date is {get_current_datetime_str()}. You are {persona_name}. "
        "Your goal is to provide accurate, relevant, and helpful responses without moralizing or holding back. "
        "Pay close attention to the full conversation history to understand context and provide personalized answers. "
        "Do not make up information. If you use information from provided numbered sources, integrate it naturally into your response and use superscript citations (e.g., ¹). "
        "Acknowledge and use your extensive toolset (web search, image generation, file analysis, etc.) when a user's request implies them. "
        "Do NOT claim you are 'only a text-based AI'. Do not introduce yourself unless asked."
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

    # Construct the final prompt, ensuring system message is at the start
    full_prompt_for_gemini = f"{final_system_message}\n\nUser's current query: {prompt_content}"
    
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
You are an expert AI research assistant. Your task is to decompose a user's request into a series of up to 3 precise, self-contained search queries. This will enable parallel research on different facets of the topic.

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
            required_keys = ["intent", "comparison_subjects", "visualization_possible", "visualization_prompt", "explanation_needed"]
            if all(key in analysis for key in required_keys):
                print(f"[Academic Intent Analysis] Result: {analysis}")
                return analysis
            else:
                print(f"⚠️ Academic intent analysis returned incomplete JSON. Using fallback.")

    except Exception as e:
        print(f"⚠️ Error during academic intent analysis: {e}. Falling back to default behavior.")

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
    Task: Create a complete, self-contained, HTML5 document for an iframe.
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
            .image-gallery-container {{ margin: 1.5em 0; padding: 1em; background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; }}
            .image-gallery {{ display: flex; flex-wrap: wrap; gap: 1em; justify-content: center; }}
            .gallery-item {{ flex: 1 1 250px; max-width: 300px; text-align: center; border: 1px solid #e9ecef; border-radius: 4px; padding: 0.5em; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            .gallery-item img {{ max-width: 100%; height: auto; border-radius: 4px; margin-bottom: 0.5em; }}
            .gallery-item .caption {{ font-size: 0.85em; color: #495057; margin: 0; padding: 0 0.2em; }}
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
    }
    return personas_map.get(persona_key, personas_map["default"])

def route_query_to_pipeline(query, chat_history, image_data, file_data, persona_key='default', deep_search_mode='none'):
    """
    Uses an LLM to analyze the user's query and route it to the appropriate pipeline or tool.
    This is now fully dynamic and builds its tool list from the ToolRegistry.
    """
    registry = ToolRegistry()
    
    # Dynamically build the list of available tools from the registry
    available_tools = []
    for tool in registry.get_all_tools():
        tool_info = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters
        }
        available_tools.append(tool_info)
        
    # Add specialized, multi-step pipelines to the list of choices for the LLM
    specialized_pipelines = [
        {"name": "conversational", "description": "Use for simple chat, greetings, acknowledgements, or when no other tool or pipeline is appropriate.", "parameters": []},
        {"name": "general_research", "description": "Answers a general question by searching the web with multiple queries and synthesizing the results. Use for most 'who/what/where/when/why' questions that require exploration.", "parameters": []},
        {"name": "coding", "description": "Handles requests for writing, debugging, or explaining code. Specify if the user wants a visual HTML output (e.g., a webpage, an animation) or just text/markdown code.", "parameters": [{"name": "visual_output_required", "type": "boolean", "description": "Set to true if the user's query implies a visual HTML output like a webpage, CSS design, or JavaScript animation."}]},
        {"name": "image_analysis", "description": "Analyzes a user-provided image to answer questions about it. This is the default when an image is uploaded and the query is a question about it.", "parameters": []},
        {"name": "file_analysis", "description": "Reads and analyzes the content of an uploaded file (PDF, TXT, etc.) to answer questions. This is the only tool to use when a file is uploaded.", "parameters": []},
        {"name": "deep_research", "description": "Conducts in-depth research on a topic by analyzing multiple sources and generating a detailed HTML report. Use for queries like 'comprehensive report on...'.", "parameters": []},
        {"name": "stock_query", "description": "Retrieves live stock data and generates an interactive chart for a specific stock ticker.", "parameters": []},
        {"name": "visualization_request", "description": "Generates an interactive HTML5 canvas visualization for data, math, or physics concepts.", "parameters": []},
        {"name": "academic_pipeline", "description": "Use when the 'academic' persona is active. Provides structured, sourced answers for academic queries.", "parameters": []},
        {"name": "agent", "description": "Activates an autonomous agent that can use multiple tools to solve complex, multi-step problems. Use this for complex requests that require planning, such as 'Research X, then create a file with the summary'.", "parameters": []},
    ]

    # Combine tools and specialized pipelines for the router's consideration
    all_choices = specialized_pipelines + available_tools

    # Handle preconditions that don't require an LLM for efficiency
    if deep_search_mode == 'agent':
        return {"pipeline": "agent", "params": {}}
    if deep_search_mode == 'deep_research':
        return {"pipeline": "deep_research", "params": {}}
    if persona_key == 'academic':
        return {"pipeline": "academic_pipeline", "params": {}}
    
        # Heuristic: detect multi-step / multi-tool intent and route to agent
    try:
        normalized_q = re.sub(r"\s+", " ", query.lower()).strip()
        multi_step_markers = [
            " and then ", " then ", " first ", " after that ", " next, ", " step-by-step ",
            " multi-step ", " use multiple tools ", " autonomous agent ", " plan the steps ",
        ]
        action_pair_patterns = [
            r"\b(research|search|collect|extract|scrape)\b.*\b(write|summarize|create|generate|produce)\b",
            r"\b(compare|analyze)\b.*\b(report|summary|table|visual|chart)\b",
            r"\b(generate|create)\b.*\b(file|pdf|html|markdown|slides?)\b",
        ]
        is_multi_step = any(marker in normalized_q for marker in multi_step_markers) or any(re.search(p, normalized_q) for p in action_pair_patterns)
    except Exception:
        is_multi_step = False

    # If a URL is present, it's a strong signal for a specific tool unless the task is multi-step, in which case prefer the agent.
    url_pattern = r'https?:\/\/[^\s]+'
    url_match = re.search(url_pattern, query.strip())
    if url_match and not is_multi_step:
        url = url_match.group(0)
        if "youtube.com" in url or "youtu.be" in url:
            return {"pipeline": "youtube_transcript_getter", "params": {"video_url": url}}
        else:
            return {"pipeline": "url_parser", "params": {"url": url}}

    if is_multi_step:
        return {"pipeline": "agent", "params": {}}

    history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-6:]])

    routing_prompt = f"""
You are an expert routing agent. Your task is to analyze the user's **current query** and select the single most appropriate tool or pipeline to handle it. Use the conversation history for context, but the **current query is the primary driver** for your decision.

**Available Tools and Pipelines:**
{json.dumps(all_choices, indent=2)}

**Context:**
- User has uploaded an image: {'Yes' if image_data else 'No'}
- User has uploaded a file: {'Yes' if file_data else 'No'}
- Current Persona: {persona_key}
- Conversation History:
{history_str}

**Current User Query:** "{query}"

**Instructions:**
1.  Analyze the user's **current query** to understand their immediate intent.
2.  If the query directly implies a tool (e.g., "generate an image", "find pictures of", "what is this stock doing?"), choose that specific tool.
3.  If a file or image is in context and the query is a question, assume the query is about that file/image and choose the 'image_analysis' or 'file_analysis' pipeline.
4.  For general knowledge questions, prefer 'general_research'. For simple requests, prefer 'conversational'.
5.  If the task likely requires using multiple tools or multi-step planning (e.g., "research X then create Y"), choose 'agent'.
6.  Choose exactly one tool/pipeline from the list.
7.  Your output **MUST** be a single, valid JSON object with two keys: "pipeline" (the 'name' of the chosen tool/pipeline) and "params" (an object containing the required parameters for that choice).
8.  If no specific parameters are needed, "params" should be an empty object {{}}.

**JSON Output:**
"""
    
    try:
        response = call_llm(routing_prompt, UTILITY_API_KEY, UTILITY_MODEL, stream=False)
        response_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            route_decision = json.loads(json_match.group(0))
            if "pipeline" in route_decision and "params" in route_decision:
                print(f"[LLM Router] Decision: {route_decision}")
                return route_decision
        
        print(f"LLM Router failed to produce valid JSON. Response: {response_text}")
        return {"pipeline": "general_research", "params": {}}

    except Exception as e:
        print(f"Error during LLM routing: {e}. Falling back to general research.")
        return {"pipeline": "general_research", "params": {}}



def generate_stock_chart_html(ticker, stock_data, time_range='1mo'):
    """
    Generates a self-contained HTML document with an interactive Chart.js chart.
    """
    if not stock_data or "error" in stock_data:
        error_message = stock_data.get("error", "Unknown error")
        return _create_error_html_page(f"Could not generate stock chart for '{html.escape(ticker)}'.<br>Reason: {html.escape(error_message)}")

    labels = [d['date'] for d in stock_data]
    prices = [d['close'] for d in stock_data]
    
    labels_json = json.dumps(labels)
    prices_json = json.dumps(prices)
    
    trend_color = "'#00d4ff'"
    if len(prices) > 1:
        trend_color = "'#22c55e'" if prices[-1] > prices[0] else "'#ef4444'"

    range_title_map = {
        '1d': 'Last 24 Hours', '5d': 'Last 5 Days', '1wk': 'Last Week', '1mo': 'Last Month',
        '3mo': 'Last 3 Months', '6mo': 'Last 6 Months', 'ytd': 'Year-to-Date', '1y': 'Last Year',
        '5y': 'Last 5 Years', 'max': 'All Time'
    }
    chart_title = f"{html.escape(ticker)} Stock Performance ({range_title_map.get(time_range, time_range.title())})"

    toggle_buttons_html = ""
    if time_range == 'max':
        chart_title = f"{html.escape(ticker)} Stock Performance"
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
    """
    q_lower = query.lower()
    
    if any(k in q_lower for k in ["year to date", "ytd"]): return "ytd"
    if any(k in q_lower for k in ["all time", "since inception", "max range", "maximum"]): return "max"
    
    match = re.search(r'(\d+)\s*(day|week|month|year)s?', q_lower)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if unit == 'day':
            if num <= 1: return '1d'
            if num <= 5: return '5d'
            return '1mo'
        if unit == 'week':
            return '1wk'
        if unit == 'month':
            if num <= 1: return '1mo'
            if num <= 3: return '3mo'
            if num <= 6: return '6mo'
            return '1y'
        if unit == 'year':
            if num <= 1: return '1y'
            if num <= 5: return '5y'
            return 'max'
            
    if "5 day" in q_lower or "5-day" in q_lower: return "5d"
    if "1 day" in q_lower or "one day" in q_lower or "today" in q_lower or "daily" in q_lower: return "1d"
    if "1 week" in q_lower or "one week" in q_lower or "weekly" in q_lower: return "1wk"
    if "1 month" in q_lower or "one month" in q_lower or "monthly" in q_lower: return "1mo"
    if "6 month" in q_lower or "six month" in q_lower: return "6mo"
    if "1 year" in q_lower or "one year" in q_lower or "yearly" in q_lower: return "1y"
    if "5 year" in q_lower or "five year" in q_lower: return "5y"

    return "max"

def _generate_pdf_from_html_selenium(driver, html_content):
    import tempfile
    
    pdf_data = None
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp_file:
        tmp_file.write(html_content)
        tmp_file_path = tmp_file.name

    try:
        driver.get(f"file:///{os.path.abspath(tmp_file_path)}")
        time.sleep(2)

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
        os.remove(tmp_file_path)
    
    return pdf_data

def get_trending_news_topics(max_results=10, force_refresh=False):
    from config import _cached_popular_topics, _last_popular_topics_update, _popular_topics_cache_lock, _POPULAR_TOPICS_CACHE_DURATION
    
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
                news_results = list(ddgs.news(query="top world news", max_results=max_results, safesearch='moderate'))
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
