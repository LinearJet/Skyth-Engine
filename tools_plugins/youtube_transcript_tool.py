import re
from basetool import BaseTool
from typing import List, Dict, Any
from youtube_transcript_api import YouTubeTranscriptApi

class YoutubeTranscriptTool(BaseTool):
    """
    A tool for fetching transcripts from YouTube videos.
    """

    @property
    def name(self) -> str:
        return "youtube_transcript_getter"

    @property
    def description(self) -> str:
        return "Fetches the full text transcript from a given YouTube video URL. Use when a user provides a YouTube link and asks a question about it."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "video_url", "type": "string", "description": "The full URL of the YouTube video."}
        ]

    @property
    def output_type(self) -> str:
        return "youtube_transcript"

    def execute(self, video_url: str) -> Dict[str, str]:
        """
        Fetches the transcript. Returns a dict with 'transcript' or 'error'.
        """
        try:
            video_id_match = re.search(r'(?:v=|\/|embed\/|youtu.be\/)([a-zA-Z0-9_-]{11})', video_url)
            if not video_id_match:
                return {"error": "Could not extract video ID from URL."}
            video_id = video_id_match.group(1)

            # Use the static API for listing transcripts per library docs
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            transcript = None
            try:
                transcript = transcript_list.find_manually_created_transcript(['en'])
            except Exception:
                try:
                    transcript = transcript_list.find_generated_transcript(['en'])
                except Exception:
                    try:
                        transcript = next(iter(transcript_list))
                    except Exception:
                        transcript = None

            if not transcript:
                return {"error": "No transcript available for this video (may be disabled or restricted)."}
            transcript_data = transcript.fetch()
            
            full_transcript = " ".join([item.get('text', '') for item in transcript_data])
            print(f"[YouTube Transcript Tool] Fetched transcript of length: {len(full_transcript)} characters.")
            return {"transcript": full_transcript}
        except Exception as e:
            print(f"YouTube Transcript API error: {e}")
            return {"error": str(e)}
