import re
import json
import requests
from urllib.parse import quote
from basetool import BaseTool
from typing import List, Dict, Any

class YoutubeSearchTool(BaseTool):
    """
    A tool for searching YouTube videos.
    """

    @property
    def name(self) -> str:
        return "youtube_search"

    @property
    def description(self) -> str:
        return "Searches YouTube for videos based on a query."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "query", "type": "string", "description": "The search query."},
            {"name": "max_results", "type": "integer", "description": "The maximum number of results to return."}
        ]

    def execute(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        try:
            print(f"[YouTube Search] Searching for: {query}, Max Results: {max_results}")
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
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