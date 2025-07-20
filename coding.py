import html
from config import VISUALIZATION_API_KEY, VISUALIZATION_MODEL, REASONING_API_KEY, REASONING_MODEL
from tools import call_llm, _create_error_html_page
from utils import yield_data, _stream_llm_response

def run_coding_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': f'Engaging model for coding task: "{query[:50]}..."'})

    q_lower = query.lower()
    html_keywords = ['html', 'css', 'webpage', 'website', 'ui for', 'design a', 'interactive page', 'lockscreen', 'homescreen', 'frontend', 'javascript animation', 'canvas script', 'svg animation', 'p5.js']
    is_visual_html_request = any(k in q_lower for k in html_keywords) or \
                             ("javascript" in q_lower and any(vk in q_lower for vk in ["animation", "interactive", "visual", "game", "canvas", "svg"])) or \
                             ("html" in q_lower and any(ck in q_lower for ck in ["page", "form", "layout", "template", "component"]))

    coding_prompt = f"""
This is part of an ongoing conversation. User's current coding query: "{query}"

You are an expert software engineer. Your task is to respond to the user's coding query.
Rely solely on your internal knowledge.

Output Requirements:
1.  If the user's query clearly implies a **visual HTML output** (e.g., creating an HTML page, a CSS design, a JavaScript animation, an interactive UI element):
    *   You **MUST** generate a *complete, self-contained HTML document* (starting with <!DOCTYPE html>).
    *   This HTML must be renderable in an iframe. All JavaScript and CSS must be embedded.
    *   If external libraries are essential (like p5.js for a canvas animation), use CDN links.
    *   The output in this case **MUST BE ONLY THE HTML CODE**. No explanations before or after, no markdown backticks around the HTML.

2.  If the user's query is for **non-visual code** (e.g., a Python function, a C++ algorithm, explaining a concept, debugging code not meant for a browser):
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
                    yield yield_data('html_preview', {"html_code": generated_html_code})
                    yield yield_data('step', {'status': 'done', 'text': 'HTML code generated for iframe.'})
                else: 
                    yield yield_data('step', {'status': 'warning', 'text': 'Model did not return valid HTML for visual request. Streaming as text.'})
                    yield yield_data('answer_chunk', "It seems I couldn't generate a direct HTML preview for that. Here's the information as text:\n\n")
                    yield yield_data('answer_chunk', generated_html_code if generated_html_code else "No content received from model.")
            else:
                error_text = f"Coding model API Error: {coding_response_obj.status_code if coding_response_obj else 'N/A'} - {coding_response_obj.text[:100] if coding_response_obj else 'No response'}"
                yield yield_data('step', {'status': 'error', 'text': error_text})
                yield yield_data('answer_chunk', f"Error during code generation: {error_text}")
        except Exception as e:
            print(f"Coding pipeline (HTML gen) exception: {e}")
            yield yield_data('step', {'status': 'error', 'text': f'Exception in HTML code generation: {str(e)}'})
            yield yield_data('answer_chunk', f"An error occurred while trying to generate the HTML code: {str(e)}")
    else:
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating code/explanation...'})
        stream_response = call_llm(coding_prompt, REASONING_API_KEY, REASONING_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
        yield from _stream_llm_response(stream_response, REASONING_MODEL)

    yield yield_data('step', {'status': 'done', 'text': 'Coding task processed.'})