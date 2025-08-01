import json
import time
import re
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup
from basetool import BaseTool
from typing import List, Dict, Any
from tools import setup_selenium_driver
from selenium.webdriver.common.by import By

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

def _scrape_google_images(driver, query, max_results=10):
    """
    Extracts high-quality image URLs from Google Images.
    """
    print(f"[Google Images] Searching for: {query}")
    try:
        encoded_query = quote(query)
        url = f"https://www.google.com/search?tbm=isch&q={encoded_query}&safe=off&tbs=isz:m"
        driver.get(url)
        time.sleep(2)

        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

        img_elements = driver.find_elements(By.TAG_NAME, "img")
        
        image_urls = set()
        for img in img_elements:
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src and src.startswith('http'):
                if not src.startswith('data:image') and _is_high_quality_image(src):
                    image_urls.add(src)
            if len(image_urls) >= max_results:
                break
        
        image_urls_list = list(image_urls)
        print(f"[Google Images] Found {len(image_urls_list)} high-quality images.")
        
        source_page_url = f"https://www.google.com/search?tbm=isch&q={encoded_query}"
        return [{"type": "image_search_result", "title": query, "thumbnail_url": url, "image_url": url, "source_url": source_page_url} for url in image_urls_list]
    except Exception as e:
        print(f"[Google Images] Error scraping Google Images: {e}")
        return []


def _scrape_bing_images(query, max_results=8):
    try:
        print(f"[Bing Images] Searching for: {query}")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        url = f"https://www.bing.com/images/search?q={quote(query)}&form=HDRSC2&qft=+filterui:imagesize-large"
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
                    if image_url and _is_high_quality_image(image_url):
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

class ImageSearchTool(BaseTool):
    """
    A tool for searching the web for high-quality images.
    """

    @property
    def name(self) -> str:
        return "image_searcher"

    @property
    def description(self) -> str:
        return "Searches both Google and Bing for high-quality images based on a query."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "query", "type": "string", "description": "The search query for images."},
            {"name": "max_results_per_source", "type": "integer", "description": "The max number of results from each source (Google, Bing)."}
        ]

    def execute(self, query: str, max_results_per_source: int = 8) -> List[Dict[str, Any]]:
        google_results = []
        bing_results = []
        driver = None
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                bing_future = executor.submit(_scrape_bing_images, query, max_results_per_source)
                google_future = None
                
                driver = setup_selenium_driver()
                if driver:
                    google_future = executor.submit(_scrape_google_images, driver, query, max_results_per_source)
                else:
                    print('[ImageSearchTool] Selenium driver failed, skipping Google Images.')
                
                if google_future:
                    google_results = google_future.result()
                
                bing_results = bing_future.result()

            all_results = google_results + bing_results
        finally:
            if driver:
                driver.quit()
                print("[Selenium] Driver instance for image search has been closed.")
        
        unique_results = list({v['image_url']:v for v in all_results}.values())
        return unique_results
