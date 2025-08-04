import os
import re
import time
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup
from basetool import BaseTool
from typing import List, Dict, Any, Optional

from tools import setup_selenium_driver, get_filename_from_url
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def _is_high_quality_image(url):
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

def _parse_with_bs4(url: str) -> Optional[Dict[str, Any]]:
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

def _parse_url_comprehensive(driver, url: str) -> Dict[str, Any]:
    """
    Comprehensive URL parsing - extracts text, images, videos, and links using Selenium.
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
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3)

        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

        parsed_data['title'] = driver.title
        body = driver.find_element(By.TAG_NAME, "body")
        parsed_data['text_content'] = body.text

        page_source = driver.page_source

        image_urls = set()
        for img in driver.find_elements(By.TAG_NAME, "img"):
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src and not src.startswith('data:image'):
                if _is_high_quality_image(src):
                    image_urls.add(urljoin(url, src))
        
        regex_patterns = [r'background-image:\s*url\(["\']?([^"\']*)["\']?\)']
        for pattern in regex_patterns:
            for match in re.findall(pattern, page_source, re.IGNORECASE):
                 if not match.startswith('data:image') and _is_high_quality_image(match):
                    image_urls.add(urljoin(url, match))
        parsed_data['images'] = list(image_urls)

        video_urls = set()
        for video in driver.find_elements(By.TAG_NAME, "video"):
            src = video.get_attribute("src")
            if src: video_urls.add(urljoin(url, src))
            for source in video.find_elements(By.TAG_NAME, "source"):
                src = source.get_attribute("src")
                if src: video_urls.add(urljoin(url, src))
        parsed_data['videos'] = list(video_urls)

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
        return parsed_data

class UrlParserTool(BaseTool):
    """
    A tool for parsing web pages to extract content.
    """

    @property
    def name(self) -> str:
        return "url_parser"

    @property
    def description(self) -> str:
        return "Comprehensively parses a web URL to extract text, images, videos, and links. Use when the user provides a URL and asks to analyze, summarize, or 'read' it."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "url", "type": "string", "description": "The URL of the web page to parse."},
            {"name": "deep_scrape", "type": "boolean", "description": "Force using the deep (Selenium) scraper instead of trying the fast scraper first."}
        ]

    @property
    def output_type(self) -> str:
        return "parsed_url_content"

    def execute(self, url: str, deep_scrape: bool = False, driver=None) -> Optional[Dict[str, Any]]:
        """
        Parses the URL. Tries a fast method first, then falls back to a comprehensive one.
        Can accept an existing Selenium driver to avoid creating a new one.
        """
        parsed_data = None
        if not deep_scrape:
            parsed_data = _parse_with_bs4(url)

        # Fallback to deep scrape if fast scrape fails, is insufficient, or is forced
        if not parsed_data or len(parsed_data.get('text_content', '')) < 500 or deep_scrape:
            print(f"URL Parser: Fast analysis insufficient or skipped, engaging deep browser-based scraping for {url}")
            
            should_quit_driver = False
            if driver is None:
                driver = setup_selenium_driver()
                should_quit_driver = True

            if not driver:
                return {"error": "Browser driver could not be initialized for deep analysis."}
            
            try:
                parsed_data = _parse_url_comprehensive(driver, url)
            finally:
                if should_quit_driver and driver:
                    driver.quit()
                    print(f"[Selenium] Driver for URL parse of {url} has been closed.")
        
        return parsed_data
