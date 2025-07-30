import base64
from basetool import BaseTool
from typing import Dict, Any, List
from google import genai as google_genai
from google.genai import types as google_types
from config import IMAGE_GENERATION_API_KEY, IMAGE_GENERATION_MODEL

class ImageGenerationTool(BaseTool):
    """
    A tool for generating images from a text prompt using Gemini.
    """

    @property
    def name(self) -> str:
        return "image_generator"

    @property
    def description(self) -> str:
        return "Generates a new image from a textual description using Google's Gemini model."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "prompt", "type": "string", "description": "The text prompt to generate the image from."}
        ]

    def execute(self, prompt: str) -> Dict[str, Any]:
        """
        Generates an image from a text prompt using Gemini.
        """
        try:
            if not IMAGE_GENERATION_API_KEY:
                raise ValueError("GEMINI_API_KEY for image generation is not configured.")
            
            print(f"[Gemini Image Gen] Calling model for prompt: '{prompt}'")
            image_client = google_genai.Client(api_key=IMAGE_GENERATION_API_KEY)
            
            response = image_client.models.generate_content(
                model=IMAGE_GENERATION_MODEL,
                contents=prompt,
                config=google_types.GenerateContentConfig(
                  response_modalities=['TEXT', 'IMAGE']
                )
            )
            
            image_bytes = None
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    image_bytes = part.inline_data.data
                    break
            
            if image_bytes:
                img_base64 = base64.b64encode(image_bytes).decode('utf-8')
                return {"type": "generated_image", "base64_data": img_base64, "prompt": prompt, "source_url": "#gemini"}
            else:
                text_response = response.candidates[0].content.parts[0].text if response.candidates[0].content.parts else "Model did not return an image."
                print(f"Gemini Image Gen Failed: {text_response}")
                return {"type": "error", "message": f"Gemini model refused to generate the image. Reason: {text_response}"}
                
        except Exception as e:
            print(f"Gemini Image Gen connection error: {e}")
            return {"type": "error", "message": f"Gemini API connection error: {str(e)}"}