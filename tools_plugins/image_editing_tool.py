import base64
from basetool import BaseTool
from typing import Dict, Any, List
from config import IMAGE_GENERATION_API_KEY, IMAGE_GENERATION_MODEL
from google import genai as google_genai
from google.genai import types as google_types
from PIL import Image as PIL_Image
from io import BytesIO as IO_BytesIO

class ImageEditingTool(BaseTool):
    """
    A tool for editing images based on a text prompt using Gemini.
    """

    @property
    def name(self) -> str:
        return "image_editor"

    @property
    def description(self) -> str:
        return "Edits a given image based on a textual description using Google's Gemini model."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "prompt", "type": "string", "description": "The text prompt describing the edit."},
            {"name": "image_data", "type": "string", "description": "The base64 encoded string of the image to edit."}
        ]

    def execute(self, prompt: str, image_data: str) -> Dict[str, Any]:
        """
        Edits an image based on a text prompt using Gemini.
        Returns a dictionary with 'base64_data' and 'text_response' on success, or 'error' on failure.
        """
        try:
            if not IMAGE_GENERATION_API_KEY:
                raise ValueError("GEMINI_API_KEY for image generation is not configured.")

            if not image_data:
                return {"error": "No image data provided for editing."}

            image_client = google_genai.Client(api_key=IMAGE_GENERATION_API_KEY)
            
            image_bytes = base64.b64decode(image_data)
            source_image = PIL_Image.open(IO_BytesIO(image_bytes))

            print(f"[Gemini Image Edit] Calling model for prompt: '{prompt}'")
            
            response = image_client.models.generate_content(
                model=IMAGE_GENERATION_MODEL,
                contents=[prompt, source_image],
                config=google_types.GenerateContentConfig(response_modalities=['TEXT', 'IMAGE'])
            )

            edited_image_bytes = None
            text_response_from_model = "The image has been edited as you requested."

            for part in response.candidates[0].content.parts:
              if part.text is not None:
                text_response_from_model = part.text
              elif part.inline_data is not None:
                edited_image_bytes = part.inline_data.data
            
            if edited_image_bytes:
                edited_image_base64 = base64.b64encode(edited_image_bytes).decode('utf-8')
                return {
                    "type": "edited_image", 
                    "base64_data": edited_image_base64, 
                    "text_response": text_response_from_model
                }
            else:
                error_msg = text_response_from_model or "The model did not return an edited image. It might have refused the request."
                return {"error": error_msg}

        except Exception as e:
            print(f"Gemini Image Edit connection error: {e}")
            return {"error": f"Gemini API connection error: {str(e)}"}
