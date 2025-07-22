import html
import json
from config import VISUALIZATION_API_KEY, VISUALIZATION_MODEL, REASONING_API_KEY, REASONING_MODEL
from tools import call_llm, _create_error_html_page
from utils import yield_data, _stream_llm_response

def run_coding_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, visual_output_required=False, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': f'Engaging model for coding task: "{query[:50]}..."'})

    # The decision is now made by the router and passed as a parameter.
    is_visual_html_request = visual_output_required

    coding_prompt = f"""
This is part of an ongoing conversation. User's current coding query: "{query}"
You are an expert software engineer. Your task is to respond to the user's coding query.
Rely solely on your internal knowledge.
Output Requirements:
1.  If the request is for a **visual HTML output** (as determined by the routing agent):
    *   You **MUST** generate a *complete, self-contained HTML document* (starting with <!DOCTYPE html>).
    *   This HTML must be renderable in an iframe. All JavaScript and CSS must be embedded.
    *   If external libraries are essential (like p5.js for a canvas animation), use CDN links.
    *   The output in this case **MUST BE ONLY THE HTML CODE**. No explanations before or after, no markdown backticks around the HTML.
2.  If the request is for **non-visual code** (as determined by the routing agent):
    *   Provide the code in standard markdown code blocks (e.g., ```python ... ```).
    *   Include a clear explanation of the code and concepts.
    *   This output will be streamed as text.
Based on the query "{query}", and the determination that it is {'a visual HTML request' if is_visual_html_request else 'a non-visual coding request or explanation'}, generate your response now:
    """

    if is_visual_html_request:
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating full HTML for iframe preview...'})
        try:
            coding_response_obj = call_llm(coding_prompt, VISUALIZATION_API_KEY, VISUALIZATION_MODEL, stream=False, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
            generated_html_code = ""
            if coding_response_obj and coding_response_obj.status_code == 200:
                response_data = coding_response_obj.json()
                generated_html_code = response_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()

                if generated_html_code.lower().startswith('<!doctype html>') or generated_html_code.lower().startswith('<html'):
                    artifact = {"type": "html", "content": generated_html_code, "title": "HTML Preview"}
                    final_data['artifacts'].append(artifact)
                    yield yield_data('html_preview', {"html_code": generated_html_code})
                    final_data['content'] = "An interactive HTML preview was generated."
                    yield yield_data('step', {'status': 'done', 'text': 'HTML code generated for iframe.'})
                else:
                    yield yield_data('step', {'status': 'warning', 'text': 'Model did not return valid HTML for visual request. Streaming as text.'})
                    final_data['content'] = "It seems I couldn't generate a direct HTML preview for that. Here's the information as text:\n\n" + (generated_html_code if generated_html_code else "No content received from model.")
                    yield yield_data('answer_chunk', final_data['content'])
            else:
                error_text = f"Coding model API Error: {coding_response_obj.status_code if coding_response_obj else 'N/A'} - {coding_response_obj.text[:100] if coding_response_obj else 'No response'}"
                final_data['content'] = f"Error during code generation: {error_text}"
                yield yield_data('step', {'status': 'error', 'text': error_text})
                yield yield_data('answer_chunk', final_data['content'])
        except Exception as e:
            print(f"Coding pipeline (HTML gen) exception: {e}")
            error_text = f"An error occurred while trying to generate the HTML code: {str(e)}"
            final_data['content'] = error_text
            yield yield_data('step', {'status': 'error', 'text': f'Exception in HTML code generation: {str(e)}'})
            yield yield_data('answer_chunk', error_text)
    else:
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating code/explanation...'})
        stream_response = call_llm(coding_prompt, REASONING_API_KEY, REASONING_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
        
        full_response_content = ""
        for chunk in _stream_llm_response(stream_response, REASONING_MODEL):
            full_response_content += json.loads(chunk[6:])['data']
            yield chunk
        final_data['content'] = full_response_content

    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Coding task processed.'})
