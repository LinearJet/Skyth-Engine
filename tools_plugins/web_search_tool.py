from basetool import BaseTool
from ddgs import DDGS
from typing import List, Dict, Any

class WebSearchTool(BaseTool):
    """
    A tool for searching the web using DuckDuckGo.
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Searches the web for up-to-date information using DuckDuckGo. Can be used for regular search or news search."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "query", "type": "string", "description": "The search query."},
            {"name": "max_results", "type": "integer", "description": "The maximum number of results to return."},
            {"name": "type", "type": "string", "description": "The type of search, either 'text' or 'news'."}
        ]

    def execute(self, query: str, max_results: int = 7, type: str = 'text') -> List[Dict[str, Any]]:
        try:
            with DDGS(timeout=20) as ddgs:
                if type == 'news':
                    results = list(ddgs.news(query, max_results=max_results, safesearch='off'))
                    return [{"type": "web", "title": r['title'], "text": r['body'], "url": r['url'], "image": r.get("image"), "source": r.get("source")}
                            for r in results]
                else:
                    results = list(ddgs.text(query, max_results=max_results, safesearch='off'))
                    return [{"type": "web", "title": r['title'], "text": r['body'], "url": r['href']}
                            for r in results]
        except Exception as e:
            print(f"DDG text search error: {e}")
            return []