from basetool import BaseTool
from typing import Dict, Any, List, Optional
import json
import sqlite3
import requests
import base64
import io
from PIL import Image as PIL_Image
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from config import DATABASE, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL
from tools import call_llm
from tool_registry import ToolRegistry


class ContextTool(BaseTool):
    """
    Access and use context from prior artifacts and tool outputs in the current chat.
    - List prior artifacts and sources
    - Analyze a specific image URL (download + vision LLM)
    - Analyze a YouTube URL (fetch transcript when available + LLM)
    - Analyze a general URL (use url_parser to extract text, then LLM)
    - Answer questions based on a previously generated deep research report
    """

    @property
    def name(self) -> str:
        return "context_tool"

    @property
    def description(self) -> str:
        return (
            "Retrieves and analyzes prior artifacts and tool outputs from this chat. "
            "Use it to: (1) list or fetch previous artifacts/sources, (2) analyze an image URL (downloads and sends to Gemini), "
            "(3) analyze a YouTube URL using its transcript, (4) analyze a general URL using the url_parser, and (5) answer questions from a prior deep research report."
        )

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "chat_id", "type": "integer", "description": "The chat ID whose artifacts and sources should be used."},
            {"name": "action", "type": "string", "description": "One of: list, get_artifacts, get_sources, analyze_image_url, analyze_youtube, analyze_url, answer_from_report."},
            {"name": "url", "type": "string", "description": "Optional URL to analyze (image, YouTube, or general web)."},
            {"name": "index", "type": "integer", "description": "Optional index to pick from prior search results (imageResults/videoResults/sources)."},
            {"name": "prompt", "type": "string", "description": "Optional user question/instructions for the analysis."}
        ]

    @property
    def output_type(self) -> str:
        # We will return structured JSON; the generic pipeline will present it and craft a response.
        return "context_tool_result"

    def execute(self, chat_id: int, action: str, url: str = "", index: int = -1, prompt: str = "") -> Dict[str, Any]:
        try:
            ctx = self._load_latest_assistant_packet(chat_id)
            if action == "list":
                return self._list_context(ctx)
            if action == "get_artifacts":
                return {"artifacts": ctx.get("artifacts", [])}
            if action == "get_sources":
                return {"sources": ctx.get("sources", [])}
            if action == "analyze_image_url":
                target_url = self._resolve_url_from_results(ctx, url, index, key="imageResults")
                if not target_url:
                    return {"error": "No image URL provided or found in previous results."}
                return self._analyze_image_url(target_url, prompt)
            if action == "analyze_youtube":
                target_url = self._resolve_url_from_results(ctx, url, index, key="videoResults")
                if not target_url:
                    return {"error": "No YouTube/video URL provided or found in previous results."}
                return self._analyze_youtube_url(target_url, prompt)
            if action == "analyze_url":
                target_url = self._resolve_url_from_results(ctx, url, index, key="sources")
                if not target_url:
                    return {"error": "No URL provided or found in previous sources."}
                return self._analyze_general_url(target_url, prompt)
            if action == "answer_from_report":
                return self._answer_from_report(ctx, prompt)
            return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": f"Context tool failed: {str(e)}"}

    # --- Helpers ---
    def _db_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_latest_assistant_packet(self, chat_id: int) -> Dict[str, Any]:
        # Fetch the last assistant turn with final_data_json
        try:
            conn = self._db_conn()
            row = conn.execute(
                """
                SELECT final_data_json FROM episodic_memory
                WHERE chat_id = ? AND role = 'assistant' AND final_data_json IS NOT NULL
                ORDER BY timestamp DESC LIMIT 1
                """,
                (chat_id,)
            ).fetchone()
            conn.close()
            if not row or not row[0]:
                return {}
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return {}
        except Exception:
            return {}

    def _list_context(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        artifacts = ctx.get("artifacts", []) or []
        sources = ctx.get("sources", []) or []
        image_results = ctx.get("imageResults", []) or []
        video_results = ctx.get("videoResults", []) or []
        return {
            "summary": {
                "artifacts_count": len(artifacts),
                "sources_count": len(sources),
                "image_results_count": len(image_results),
                "video_results_count": len(video_results),
                "artifact_titles": [a.get("title", "") for a in artifacts][:5],
            }
        }

    def _resolve_url_from_results(self, ctx: Dict[str, Any], provided_url: str, index: int, key: str) -> Optional[str]:
        if provided_url:
            return provided_url
        items = ctx.get(key, []) or []
        if not items:
            return None
        if index is None or index < 0 or index >= len(items):
            index = 0
        item = items[index]
        # imageResults/videoResults: objects with 'url'
        # sources: objects with 'url'
        return item.get("url")

    def _analyze_image_url(self, url: str, prompt: str) -> Dict[str, Any]:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            return {"error": f"URL did not return an image. Content-Type: {content_type}"}
        # Optionally ensure it's a decodable image
        try:
            PIL_Image.open(io.BytesIO(resp.content)).verify()
        except Exception:
            pass
        b64 = base64.b64encode(resp.content).decode("utf-8")
        analysis_prompt = (
            (prompt or "Provide a concise, factual analysis of the image.")
        )
        llm_resp = call_llm(analysis_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False, image_data=b64)
        text = llm_resp.json()["candidates"][0]["content"]["parts"][0].get("text", "").strip()
        return {"action": "analyze_image_url", "url": url, "answer": text}

    def _analyze_youtube_url(self, url: str, prompt: str) -> Dict[str, Any]:
        try:
            transcript_text = self._fetch_youtube_transcript(url)
            if not transcript_text or len(transcript_text.strip()) < 10:
                # Fallback to page metadata summary if transcript is not available
                meta = self._fetch_youtube_metadata(url)
                if not meta:
                    return {"error": "Could not retrieve transcript or metadata for this YouTube video."}
                q = prompt or "Summarize the key points of this video based on its metadata and context. State that the transcript was unavailable."
                meta_json = json.dumps(meta, indent=2)
                full_prompt = f"Video URL: {url}\n\nNo transcript was available. Use the following metadata and context to infer content cautiously.\n\nMetadata:\n```json\n{meta_json}\n```\n\nTask: {q}"
                llm_resp = call_llm(full_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False)
                text = llm_resp.json()["candidates"][0]["content"]["parts"][0].get("text", "").strip()
                return {"action": "analyze_youtube", "url": url, "answer": text, "note": "Transcript unavailable; used metadata fallback."}

            q = prompt or "Summarize the key points and answer likely questions about this video."
            full_prompt = f"Video URL: {url}\n\nTranscript (truncated to 12k chars):\n{transcript_text[:12000]}\n\nTask: {q}"
            llm_resp = call_llm(full_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False)
            text = llm_resp.json()["candidates"][0]["content"]["parts"][0].get("text", "").strip()
            return {"action": "analyze_youtube", "url": url, "answer": text}
        except Exception as e:
            return {"error": f"YouTube analysis failed: {str(e)}"}

    def _fetch_youtube_transcript(self, url: str) -> Optional[str]:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            video_id = self._extract_youtube_id(url)
            if not video_id:
                return None
            # Try multiple strategies to improve success rate
            try:
                transcripts = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
                return "\n".join(item.get('text', '') for item in transcripts if item.get('text'))
            except Exception:
                pass

            # Try listing and picking best available
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = None
                try:
                    transcript = transcript_list.find_manually_created_transcript(['en'])
                except Exception:
                    try:
                        transcript = transcript_list.find_generated_transcript(['en'])
                    except Exception:
                        available = list(transcript_list)
                        transcript = available[0] if available else None
                if transcript:
                    transcript_data = transcript.fetch()
                    return "\n".join([item.get('text', '') for item in transcript_data if item.get('text')])
            except Exception:
                pass
            return None
        except Exception:
            return None

    def _extract_youtube_id(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            if parsed.hostname in ("youtu.be",):
                return parsed.path.lstrip("/")
            if parsed.hostname and "youtube.com" in parsed.hostname:
                # Support /watch?v=, /shorts/, /live/
                if parsed.path.startswith("/shorts/"):
                    return parsed.path.split("/shorts/")[-1].split("?")[0]
                if parsed.path.startswith("/live/"):
                    return parsed.path.split("/live/")[-1].split("?")[0]
                qs = parsed.query or ""
                for part in qs.split("&"):
                    if part.startswith("v="):
                        return part[2:]
            return None
        except Exception:
            return None

    def _fetch_youtube_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            # Simple metadata fetch from the watch page
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            html = resp.text
            data = {}
            try:
                # OpenGraph tags
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                og_title = soup.find("meta", property="og:title")
                og_desc = soup.find("meta", property="og:description")
                og_image = soup.find("meta", property="og:image")
                data["title"] = og_title.get("content") if og_title else None
                data["description"] = og_desc.get("content") if og_desc else None
                data["thumbnail"] = og_image.get("content") if og_image else None
            except Exception:
                pass
            # Try to parse initial data blob for additional hints
            import re
            m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
            if m:
                try:
                    player = json.loads(m.group(1))
                    microformat = player.get("microformat", {}).get("playerMicroformatRenderer", {})
                    data["lengthSeconds"] = microformat.get("lengthSeconds")
                    data["category"] = microformat.get("category")
                    data["isLive"] = microformat.get("isLiveBroadcast")
                except Exception:
                    pass
            return data or None
        except Exception:
            return None

    def _analyze_general_url(self, url: str, prompt: str) -> Dict[str, Any]:
        registry = ToolRegistry()
        url_parser = registry.get_tool("url_parser")
        if not url_parser:
            return {"error": "url_parser tool is not available."}
        parsed = url_parser.execute(url=url, deep_scrape=False)
        if not parsed or parsed.get("error"):
            return {"error": parsed.get("error", "Failed to parse URL.")}
        text_content = parsed.get("text_content", "")
        if not text_content:
            return {"error": "No text content could be extracted from the URL."}
        q = prompt or "Provide a concise, accurate answer based only on the provided page content."
        full_prompt = f"URL: {url}\n\nPage Content (truncated):\n{text_content[:12000]}\n\nTask: {q}"
        llm_resp = call_llm(full_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False)
        text = llm_resp.json()["candidates"][0]["content"]["parts"][0].get("text", "").strip()
        return {"action": "analyze_url", "url": url, "answer": text}

    def _answer_from_report(self, ctx: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        # Find the latest deep research report artifact and extract readable text
        artifacts = ctx.get("artifacts", []) or []
        report_html = None
        for art in reversed(artifacts):
            title = (art.get("title") or "").lower()
            if "deep research report" in title and art.get("type") == "html":
                report_html = art.get("content")
                break
        if not report_html:
            return {"error": "No deep research report artifact found in this chat."}
        text = self._html_to_text(report_html)
        q = prompt or "Answer questions based only on the report content."
        full_prompt = f"Report Content (truncated):\n{text[:12000]}\n\nTask: {q}"
        llm_resp = call_llm(full_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False)
        answer = llm_resp.json()["candidates"][0]["content"]["parts"][0].get("text", "").strip()
        return {"action": "answer_from_report", "answer": answer}

    def _html_to_text(self, html_str: str) -> str:
        try:
            soup = BeautifulSoup(html_str, "html.parser")
            # If it contains an iframe with srcdoc, extract that content
            iframe = soup.find("iframe")
            if iframe and iframe.get("srcdoc"):
                inner = BeautifulSoup(iframe.get("srcdoc"), "html.parser")
                return inner.get_text(separator="\n\n", strip=True)
            return soup.get_text(separator="\n\n", strip=True)
        except Exception:
            return html_str or ""