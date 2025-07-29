import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL
from tools import (
    analyze_academic_intent_with_llm,
    plan_research_steps_with_llm,
    search_duckduckgo,
    generate_canvas_visualization,
    call_llm,
)
from utils import yield_data, _stream_llm_response

def run_academic_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing academic query intent...'})
    intent_analysis = analyze_academic_intent_with_llm(query, chat_history)

    if intent_analysis.get("visualization_possible"):
        yield yield_data('step', {'status': 'thinking', 'text': 'Attempting to generate visualization...'})
        viz_prompt = intent_analysis.get("visualization_prompt", query)
        viz_result = generate_canvas_visualization(viz_prompt, visualization_type="math")
        
        if viz_result['type'] == 'canvas_visualization' and \
           viz_result.get('html_code', '').strip().lower().startswith(('<!doctype html>', '<html')) and \
           "could not be generated" not in viz_result.get('html_code', ''):
            artifact = {"type": "html", "content": viz_result['html_code'], "title": "Interactive Visualization"}
            final_data['artifacts'].append(artifact)
            yield yield_data(viz_result['type'], viz_result)
        else:
            yield yield_data('step', {'status': 'warning', 'text': 'Automated visualization failed or was not possible.'})

    yield yield_data('step', {'status': 'thinking', 'text': 'Planning research strategy...'})
    search_plan = plan_research_steps_with_llm(query, chat_history)
    yield yield_data('step', {'status': 'info', 'text': f'Executing {len(search_plan)}-step research plan.'})
    
    all_snippets = []
    if search_plan:
        with ThreadPoolExecutor(max_workers=len(search_plan)) as executor:
            future_to_query = {executor.submit(search_duckduckgo, q, max_results=4): q for q in search_plan}
            for i, future in enumerate(as_completed(future_to_query)):
                q = future_to_query[future]
                yield yield_data('step', {'status': 'searching', 'text': f'Step {i+1}/{len(search_plan)}: "{q[:35]}..."'})
                try:
                    all_snippets.extend(future.result())
                except Exception as exc:
                    yield yield_data('step', {'status': 'warning', 'text': f'Search step for "{q[:35]}..." failed.'})
    
    unique_snippets = list({v['url']: v for v in all_snippets}.values())
    if unique_snippets:
        final_data['sources'] = unique_snippets
        yield yield_data('sources', unique_snippets)
    
    context_for_llm = "\n\n".join([f"Source [{i+1}] (URL: {s['url']}): {s['title']} - {s['text'][:300]}..." for i, s in enumerate(unique_snippets)])

    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing academic response...'})

    synthesis_prompt = f"""
User's Query: "{query}"

As an academic, your task is to provide a clear, academic explanation for the user's query.
- Synthesize information from all relevant sources to build a coherent answer.
- Explain the principles behind any visualization that was generated.
- If the user's intent was a comparison, you **MUST** present the key differences and similarities in a well-structured Markdown table.
- Your tone should be that of a knowledgeable professor.
- Cite sources with superscripts (e.g., ¹) where appropriate.
**Provided Research Context:**
{context_for_llm if context_for_llm.strip() else "No web research was conducted. Rely on your internal knowledge."}
"""
    if final_data['artifacts']:
        synthesis_prompt += "\n\n**Note:** An interactive visualization has already been displayed to the user. Your explanation should refer to and clarify the concepts shown in that visual aid."

    stream_response = call_llm(
        synthesis_prompt, api_key, model_config, stream=True,
        chat_history=chat_history, persona_name=persona_name,
        custom_persona_text=custom_persona_text, persona_key=persona_key
    )
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk

    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Academic response complete.'})
