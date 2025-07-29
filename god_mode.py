import json
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, VISUALIZATION_API_KEY, VISUALIZATION_MODEL
from tools import (
    plan_research_steps_with_llm,
    search_duckduckgo,
    reformulate_query_with_context,
    search_youtube_videos,
    route_query_to_pipeline,
    generate_canvas_visualization,
    _create_error_html_page,
    call_llm,
    _generate_and_yield_suggestions,
)
from pipelines import run_image_generation_pipeline, run_image_search_pipeline
from utils import yield_data, _stream_llm_response

def run_god_mode_reasoning(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type_main, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    current_model_config = CONVERSATIONAL_MODEL
    current_api_key = CONVERSATIONAL_API_KEY
    yield yield_data('step', {'status': 'thinking', 'text': 'God Mode: Engaging advanced reasoning...'})

    yield yield_data('step', {'status': 'thinking', 'text': 'Planning multi-pronged research...'})
    search_plan = plan_research_steps_with_llm(query, chat_history)
    
    yield yield_data('step', {'status': 'searching', 'text': f'Executing {len(search_plan)}-step parallel research...'})
    all_text_snippets = []
    with ThreadPoolExecutor(max_workers=len(search_plan) + 1) as executor:
        future_to_query = {executor.submit(search_duckduckgo, q, max_results=5): q for q in search_plan}
        reformulated_query_for_video = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
        future_to_query[executor.submit(search_youtube_videos, reformulated_query_for_video)] = "YouTube Search"
        
        for future in as_completed(future_to_query):
            q = future_to_query[future]
            try:
                results = future.result()
                all_text_snippets.extend(results)
            except Exception as exc:
                print(f'God Mode task for "{q}" generated an exception: {exc}')

    if not all_text_snippets: yield yield_data('step', {'status': 'info', 'text': 'No specific text results from research tools.'})
    unique_snippets = list({s['url']: s for s in all_text_snippets if s.get('url')}.values())
    final_data['sources'] = unique_snippets
    yield yield_data('sources', unique_snippets)

    context_for_llm = "\n\n".join([f"Source [{i+1}] ({s['type']} - URL: {s['url']}): {s.get('title', '')} - {s['text'][:200]}..." for i, s in enumerate(unique_snippets)])

    for suggestion_chunk in _generate_and_yield_suggestions(query, chat_history, context_for_llm):
        yield suggestion_chunk
        # This logic is flawed, but keeping as per original. Suggestions are now yielded directly.
        if 'final_suggestions' in json.loads(suggestion_chunk[6:])['data']:
            final_data['suggestions'] = json.loads(suggestion_chunk[6:])['data']['final_suggestions']

    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing comprehensive answer...'})
    main_answer_prompt = f"""The user's current query is: "{query}"

As an omniscient AI, synthesize ALL your knowledge and CRITICALLY ANALYZE and INTEGRATE the provided research data to give a comprehensive, direct, and insightful answer. Prioritize factual accuracy from the provided data. If information is not explicitly available in the provided data, state that you don't have that specific information, rather than fabricating it. Integrate source information naturally, citing with superscripts (e.g., ¹) if specific facts are used. Do not state 'Source X says...'.

Research Data:
{context_for_llm if unique_snippets else 'No specific research data. Rely on your internal knowledge, but be cautious of hallucination and state limitations clearly.'}"""
    stream_response = call_llm(main_answer_prompt, current_api_key, current_model_config, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, current_model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
    final_data['content'] = full_response_content

    # Determine underlying intent without God Mode to see if we should add artifacts
    underlying_route = route_query_to_pipeline(query, chat_history, None, None, persona_key)
    underlying_query_profile = underlying_route.get("pipeline")

    if underlying_query_profile == "coding":
        yield yield_data('step', {'status': 'thinking', 'text': 'God Mode: Engaging visualization model for direct canvas/iframe output...'})
        coding_canvas_prompt = f"""User's coding query: "{query}"
You are an expert software engineer specializing in advanced graphics and interactive web applications (e.g., HTML5 Canvas, WebGL, Three.js, p5.js, interactive simulations, complex CSS animations).
Task: Generate a *complete, self-contained HTML document* (starting with <!DOCTYPE html>) that directly implements the user's request. This HTML MUST be renderable in an iframe. All JavaScript and CSS must be embedded.
If external libraries are essential (like Three.js, p5.js), use CDN links.
The output MUST be ONLY the HTML code. No explanations, no markdown backticks around the HTML.
If the request is too complex for a single self-contained HTML file or not suitable for iframe rendering, output this specific error HTML:
{_create_error_html_page(f"The coding request '{html.escape(query)}' is too complex or not suitable for a direct iframe preview in this mode.")}
Generate the HTML now:"""
        try:
            coding_response_obj = call_llm(coding_canvas_prompt, VISUALIZATION_API_KEY, VISUALIZATION_MODEL, stream=False)
            generated_html_code = coding_response_obj.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
            if generated_html_code.lower().startswith(('<!doctype html>', '<html')):
                artifact = {"type": "html", "content": generated_html_code, "title": "Interactive Code Preview"}
                final_data['artifacts'].append(artifact)
                yield yield_data('html_preview', {"html_code": generated_html_code})
                yield yield_data('step', {'status': 'done', 'text': 'Advanced code generated for canvas/iframe.'})
            else:
                error_html = _create_error_html_page(f"Model did not return valid HTML for the coding request: {html.escape(query)}. Output:\n{html.escape(generated_html_code[:500])}...")
                artifact = {"type": "html", "content": error_html, "title": "HTML Generation Error"}
                final_data['artifacts'].append(artifact)
                yield yield_data('html_preview', {"html_code": error_html})
        except Exception as e:
            error_html = _create_error_html_page(f"Exception during advanced code generation for '{html.escape(query)}': {html.escape(str(e))}")
            artifact = {"type": "html", "content": error_html, "title": "HTML Generation Error"}
            final_data['artifacts'].append(artifact)
            yield yield_data('html_preview', {"html_code": error_html})

    if underlying_query_profile == 'visualization_request':
        yield yield_data('step', {'status': 'thinking', 'text': 'God Mode: Generating requested HTML visualization...'})
        viz_type_hint = "math" if "math" in query.lower() or "equation" in query.lower() else "general"
        canvas_result = generate_canvas_visualization(query, context_data=context_for_llm[:1000], visualization_type=viz_type_hint)
        if canvas_result['type'] == 'canvas_visualization':
            artifact = {"type": "html", "content": canvas_result['html_code'], "title": "Interactive Visualization"}
            final_data['artifacts'].append(artifact)
        yield yield_data(canvas_result['type'], canvas_result)

    if underlying_query_profile == 'image_generation_request':
        yield from run_image_generation_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type_main, custom_persona_text, persona_key, **kwargs)
    elif underlying_query_profile == 'image_search_request':
        yield from run_image_search_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type_main, custom_persona_text, persona_key, **kwargs)
    else:
        yield yield_data('final_response', final_data)

    yield yield_data('step', {'status': 'done', 'text': 'God Mode processing complete.'})
