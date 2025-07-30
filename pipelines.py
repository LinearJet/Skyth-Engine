import os
import json
import re
import requests
import sqlite3
import time
import base64
import io
import uuid
import html
from urllib.parse import quote, urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Response, stream_with_context, jsonify
from bs4 import BeautifulSoup

from config import (
    CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, REASONING_API_KEY, REASONING_MODEL,
    VISUALIZATION_API_KEY, VISUALIZATION_MODEL, IMAGE_GENERATION_API_KEY, IMAGE_GENERATION_MODEL
)
from tool_registry import ToolRegistry
from tools import (
    plan_research_steps_with_llm, reformulate_query_with_context,
    _generate_and_yield_suggestions, call_llm, get_persona_prompt_name,
    extract_ticker_with_llm, _extract_time_range, generate_stock_chart_html,
    get_youtube_transcript, parse_with_bs4, setup_selenium_driver, parse_url_comprehensive,
    is_high_quality_image, get_filename_from_url, _select_relevant_images_for_prompt,
    generate_canvas_visualization, _create_error_html_page, _generate_pdf_from_html_selenium,
    _create_image_gallery_html, scrape_bing_images, scrape_google_images,
    generate_image_from_pollinations,
    route_query_to_pipeline, analyze_academic_intent_with_llm, generate_html_preview
)

registry = ToolRegistry()

# ==============================================================================
# PIPELINE STREAMING FUNCTIONS
# ==============================================================================
from utils import yield_data, _stream_llm_response


# ==============================================================================
# PIPELINE IMPLEMENTATIONS
# ==============================================================================

def run_pure_chat(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Thinking...'})
    stream_response = call_llm(query, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk

    final_data = {
        "content": full_response_content, "artifacts": [], "sources": [],
        "suggestions": [], "imageResults": [], "videoResults": []
    }
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Response complete.'})

def run_standard_research(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    
    yield yield_data('step', {'status': 'thinking', 'text': 'Planning research strategy...'})
    search_plan = plan_research_steps_with_llm(query, chat_history)
    yield yield_data('step', {'status': 'info', 'text': f'Executing {len(search_plan)}-step research plan.'})

    all_snippets = []
    with ThreadPoolExecutor(max_workers=len(search_plan)) as executor:
        future_to_query = {executor.submit(registry.execute_tool, "web_search", query=q, max_results=5): q for q in search_plan}
        for i, future in enumerate(as_completed(future_to_query)):
            q = future_to_query[future]
            yield yield_data('step', {'status': 'searching', 'text': f'Step {i+1}/{len(search_plan)}: Searching for "{q[:40]}..."'})
            try:
                results = future.result()
                all_snippets.extend(results)
            except Exception as exc:
                print(f'{q} generated an exception: {exc}')
                yield yield_data('step', {'status': 'warning', 'text': f'Search step for "{q[:40]}..." failed.'})

    if not all_snippets:
        yield yield_data('step', {'status': 'info', 'text': 'No specific web results found.'})
    
    unique_snippets = list({v['url']: v for v in all_snippets}.values())
    final_data['sources'] = unique_snippets
    yield yield_data('sources', unique_snippets)

    context_for_llm = "\n\n".join([f"Source [{i+1}] (URL: {s['url']}): {s['title']} - {s['text'][:300]}..." for i, s in enumerate(unique_snippets)])
    
    for suggestion_chunk in _generate_and_yield_suggestions(query, chat_history, context_for_llm):
        yield suggestion_chunk
        if 'final_suggestions' in json.loads(suggestion_chunk[6:])['data']:
            final_data['suggestions'] = json.loads(suggestion_chunk[6:])['data']['final_suggestions']

    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing information...'})

    synthesis_prompt = f"""The user's current query is: "{query}"

Use your knowledge and the following multi-source research data to answer the user's query directly and comprehensively.
- Synthesize information from all relevant sources to build a coherent answer.
- Integrate source information naturally, citing with superscripts (e.g., ¹).
- Do not state 'Source X says...'.
- If the user is asking for a comparison, present the key differences and similarities clearly, using a Markdown table if appropriate.
\n\n**Research Data:**\n{context_for_llm if unique_snippets else 'No specific research data provided for this query.'}"""

    stream_response = call_llm(synthesis_prompt, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk

    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Research complete.'})

def run_stock_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing stock query...'})
    ticker = extract_ticker_with_llm(query, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)

    if not ticker:
        yield yield_data('step', {'status': 'info', 'text': f'Could not identify a stock ticker in "{query[:40]}...". Falling back to general research.'})
        yield from run_standard_research(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs)
        return

    time_range = _extract_time_range(query)
    yield yield_data('step', {'status': 'info', 'text': f'Time range detected: {time_range.upper()}'})

    yield yield_data('step', {'status': 'searching', 'text': f'Fetching {time_range.upper()} market data for {ticker}...'})
    
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    stock_data = registry.execute_tool("stock_data_fetcher", ticker=ticker, time_range=time_range)

    if stock_data and "error" not in stock_data:
        yield yield_data('step', {'status': 'thinking', 'text': 'Generating interactive chart...'})
        chart_html = generate_stock_chart_html(ticker, stock_data, time_range)
        artifact = {"type": "html", "content": chart_html, "title": f"Stock Chart for {ticker}"}
        final_data['artifacts'].append(artifact)
        yield yield_data('html_preview', {'html_code': chart_html})
        
        latest_price = stock_data[-1]['close']
        start_price = stock_data[0]['close']
        change = latest_price - start_price
        change_percent = (change / start_price) * 100 if start_price != 0 else 0
        
        range_text_map = {
            '1d': 'Last 24 Hours', '5d': 'Last 5 Days', '1wk': 'Last Week', '1mo': 'Last Month',
            '3mo': 'Last 3 Months', '6mo': 'Last 6 Months', 'ytd': 'Year-to-Date', '1y': 'Last Year',
            '5y': 'Last 5 Years', 'max': 'All Time'
        }
        
        summary_context = f"""
        Key Market Data for {ticker} ({range_text_map.get(time_range, time_range.title())}):
        - Latest Closing Price: ${latest_price:,.2f}
        - Start Price (for period): ${start_price:,.2f}
        - Period Change: ${change:,.2f} ({change_percent:+.2f}%)
        """
        
        yield yield_data('step', {'status': 'thinking', 'text': 'Preparing market summary...'})
        
        prompt_content = f"""The user asked: "{query}".
An interactive chart for {ticker} has already been displayed showing the '{range_text_map.get(time_range, time_range.title())}' period.
You have been provided with key market data for this period. Your task is to provide a concise, natural language summary based *only* on this data.
- Answer the user's original query directly.
- Explain the data in an easy-to-understand way (e.g., "Over the last year, the stock has seen a growth of...").
- Do not just list the numbers.

{summary_context}
"""
        stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
        
        full_response_content = ""
        for chunk in _stream_llm_response(stream_response, model_config):
            full_response_content += json.loads(chunk[6:])['data']
            yield chunk
        final_data['content'] = full_response_content

    else:
        error_msg = stock_data.get('error', 'an unknown error occurred')
        yield yield_data('step', {'status': 'error', 'text': f'Failed to get data for {ticker}: {error_msg}'})
        full_response_content = f"I'm sorry, I couldn't retrieve the stock data for {ticker}. The reason given was: {error_msg}"
        final_data['content'] = full_response_content
        yield yield_data('answer_chunk', full_response_content)
    
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Stock analysis complete.'})

def run_youtube_video_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'searching', 'text': 'Fetching YouTube video transcript...'})
    
    url_match = re.search(r'https?:\/\/[^\s]+', query)
    if not url_match:
        yield yield_data('step', {'status': 'error', 'text': 'Could not find a valid URL in the query.'})
        error_content = "I couldn't find a valid URL in your message. Please provide a full YouTube link."
        yield yield_data('answer_chunk', error_content)
        final_data['content'] = error_content
        yield yield_data('final_response', final_data)
        yield yield_data('step', {'status': 'done', 'text': 'Analysis aborted.'})
        return
        
    video_url = url_match.group(0)
    transcript, error = get_youtube_transcript(video_url)

    if error:
        yield yield_data('step', {'status': 'error', 'text': f'Transcript error: {error}'})
        yield yield_data('step', {'status': 'info', 'text': 'Transcript unavailable, falling back to web search.'})
        fallback_query = f'What is the YouTube video "{video_url}" about?'
        yield from run_standard_research(fallback_query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs)
        return

    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing video content...'})
    
    user_question = query.replace(video_url, "").strip()
    if not user_question: user_question = "What is this video about? Provide a concise summary."

    source_for_ui = [{"type": "youtube_transcript", "title": "Video Transcript Analysis", "text": f"Successfully loaded transcript for analysis.", "url": video_url}]
    final_data['sources'] = source_for_ui
    yield yield_data('sources', source_for_ui)

    context_for_llm = f"Video Transcript (from {video_url}):\n\n{transcript[:100000]}..."

    for suggestion_chunk in _generate_and_yield_suggestions(user_question, chat_history, context_for_llm):
        yield suggestion_chunk
        if 'final_suggestions' in json.loads(suggestion_chunk[6:])['data']:
            final_data['suggestions'] = json.loads(suggestion_chunk[6:])['data']['final_suggestions']

    prompt_content = f"""The user has asked a question about a YouTube video.
User's question: "{user_question}"
Based *only* on the provided video transcript below, answer the user's question. Do not use any external knowledge. If the answer is not in the transcript, say so.
{context_for_llm}"""

    stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
    
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Video analysis complete.'})

def run_image_analysis_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    image_data = kwargs.get('image_data')
    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing image...'})
    
    artifact = {"type": "image", "content": image_data, "title": "Uploaded Image"}
    final_data['artifacts'].append(artifact)
    yield yield_data('uploaded_image', {"base64_data": image_data, "title": "Uploaded Image"})

    description_prompt = "Analyze this image and provide a concise, factual description suitable for a web search. Focus on identifiable objects, people, text, and the overall scene. Do not interpret or add narrative. Output only the description."
    image_description = ""
    try:
        desc_response = call_llm(description_prompt, api_key, model_config, stream=False, image_data=image_data)
        image_description = desc_response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        yield yield_data('step', {'status': 'info', 'text': f'Image context: "{image_description[:70]}..."'})
    except Exception as e:
        print(f"Image description (Stage 1) failed: {e}")
        yield yield_data('step', {'status': 'warning', 'text': 'Could not get initial image description.'})

    entities_prompt = "From the provided image, identify any specific named entities (e.g., famous people, landmarks, logos, products). List their names, comma-separated. If no specific entities are identifiable, output the word 'None'."
    named_entities = ""
    try:
        ent_response = call_llm(entities_prompt, api_key, model_config, stream=False, image_data=image_data)
        named_entities = ent_response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if named_entities.lower() != 'none':
            yield yield_data('step', {'status': 'info', 'text': f'Identified entities: {named_entities}'})
    except Exception as e:
        print(f"Entity recognition (Stage 2) failed: {e}")
        yield yield_data('step', {'status': 'warning', 'text': 'Could not perform entity recognition.'})
        
    web_snippets = []
    if image_description or (named_entities and named_entities.lower() != 'none'):
        yield yield_data('step', {'status': 'searching', 'text': 'Searching web based on image content...'})
        search_query = f"{query} {image_description} {named_entities if named_entities.lower() != 'none' else ''}".strip()
        web_snippets = registry.execute_tool("web_search", query=search_query, max_results=4)
        if web_snippets:
            final_data['sources'] = web_snippets
            yield yield_data('sources', web_snippets)
        else:
            yield yield_data('step', {'status': 'info', 'text': 'No relevant web results found.'})

    context_for_llm = f"Image Description: {image_description}\n\nIdentified Entities: {named_entities}\n\n"
    if web_snippets:
        context_for_llm += "Web Search Results:\n" + "\n\n".join([f"Source [{i+1}] (URL: {s['url']}): {s['title']} - {s['text'][:250]}..." for i, s in enumerate(web_snippets)])

    for suggestion_chunk in _generate_and_yield_suggestions(query, chat_history, context_for_llm):
        yield suggestion_chunk
        if 'final_suggestions' in json.loads(suggestion_chunk[6:])['data']:
            final_data['suggestions'] = json.loads(suggestion_chunk[6:])['data']['final_suggestions']
            
    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing final response...'})

    final_prompt = f"""The user has uploaded an image and asked: "{query}"
You have been provided with the following context:
1.  The user's image (which you can see).
2.  An AI-generated description of the image.
3.  A list of specific, named entities identified in the image.
4.  Relevant web search results based on that context.
Your task is to provide a comprehensive answer to the user's query.
- Directly analyze the image.
- Use the web search results and identified entities to add external context, facts, and details that cannot be known from the image alone.
- Integrate information from all sources naturally. Cite web sources with superscripts (e.g., ¹).
**Provided Context:**
{context_for_llm if context_for_llm.strip() else "No additional context was found. Rely on your direct analysis of the image."}
"""
    
    stream_response = call_llm(final_prompt, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, image_data=image_data)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Image analysis complete.'})

def run_image_editing_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    image_data = kwargs.get('image_data')

    if not image_data:
        error_msg = "No image was provided to edit. Please upload an image first."
        yield yield_data('step', {'status': 'error', 'text': error_msg})
        final_data['content'] = error_msg
        yield yield_data('answer_chunk', error_msg)
        yield yield_data('final_response', final_data)
        yield yield_data('step', {'status': 'done', 'text': 'Image editing aborted.'})
        return

    yield yield_data('step', {'status': 'thinking', 'text': 'Preparing image for editing...'})
    
    source_artifact = {"type": "image", "content": image_data, "title": "Source Image"}
    final_data['artifacts'].append(source_artifact)
    yield yield_data('uploaded_image', {"base64_data": image_data, "title": "Source Image for Edit"})

    try:
        if not IMAGE_GENERATION_API_KEY:
            raise ValueError("GEMINI_API_KEY for image generation is not configured.")

        from google import genai as google_genai
        from google.genai import types as google_types
        from PIL import Image as PIL_Image
        from io import BytesIO as IO_BytesIO

        image_client = google_genai.Client(api_key=IMAGE_GENERATION_API_KEY)
        
        image_bytes = base64.b64decode(image_data)
        source_image = PIL_Image.open(IO_BytesIO(image_bytes))

        yield yield_data('step', {'status': 'thinking', 'text': f'Applying edit: "{query[:40]}..."'})

        response = image_client.models.generate_content(
            model=IMAGE_GENERATION_MODEL,
            contents=[query, source_image],
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
            edited_artifact = {"type": "image", "content": edited_image_base64, "title": "Edited Image"}
            final_data['artifacts'].append(edited_artifact)
            yield yield_data('edited_image', {"base64_data": edited_image_base64, "prompt": query, "title": "Edited Image"})
            yield yield_data('step', {'status': 'done', 'text': 'Edit applied successfully.'})
            
            # **FIX**: Create an explicit acknowledgment prompt
            ack_prompt = f"You have just successfully edited the user's image with the instruction: '{query}'. The edited image is now displayed. Briefly acknowledge this success. You can also use the model's response if it's relevant: '{text_response_from_model}'"
            stream_response_ack = call_llm(ack_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name)
            
            full_response_content = ""
            for chunk in _stream_llm_response(stream_response_ack, CONVERSATIONAL_MODEL):
                full_response_content += json.loads(chunk[6:])['data']
                yield chunk
            final_data['content'] = full_response_content
            
        else:
            error_msg = text_response_from_model or "The model did not return an edited image. It might have refused the request."
            yield yield_data('step', {'status': 'error', 'text': error_msg})
            final_data['content'] = f"I'm sorry, I couldn't edit the image. The model said: \"{error_msg}\""
            yield yield_data('answer_chunk', final_data['content'])

    except TypeError as e:
        error_msg = f"An error occurred during image editing: The image data was missing. This can happen if an image wasn't uploaded in the current session. Please upload the image again to edit it. (Error: {str(e)})"
        print(f"[Image Editing Pipeline] {error_msg}")
        yield yield_data('step', {'status': 'error', 'text': 'An error occurred during editing.'})
        final_data['content'] = error_msg
        yield yield_data('answer_chunk', error_msg)
    except Exception as e:
        error_msg = f"An error occurred during image editing: {str(e)}"
        print(f"[Image Editing Pipeline] {error_msg}")
        yield yield_data('step', {'status': 'error', 'text': 'An error occurred during editing.'})
        final_data['content'] = error_msg
        yield yield_data('answer_chunk', error_msg)

    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Image editing process complete.'})

def run_file_analysis_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    file_data = kwargs.get('file_data')
    if not file_data:
        yield yield_data('step', {'status': 'info', 'text': 'No file context found. To discuss a file, please upload it first.'})
        error_content = "It seems you're asking about a file, but I don't have one in our current conversation. Please upload the file you'd like to discuss."
        final_data = { "content": error_content, "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
        yield yield_data('answer_chunk', error_content)
        yield yield_data('final_response', final_data)
        yield yield_data('step', {'status': 'done', 'text': 'File analysis aborted.'})
        return

    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    file_name = kwargs.get('file_name')
    import pypdf
    yield yield_data('step', {'status': 'thinking', 'text': f'Processing file context: {file_name}'})

    file_content = ""
    error_message = None

    try:
        decoded_bytes = base64.b64decode(file_data)
        
        if file_name and file_name.lower().endswith('.pdf'):
            pdf_reader = pypdf.PdfReader(io.BytesIO(decoded_bytes))
            content_parts = [page.extract_text() for page in pdf_reader.pages]
            file_content = "\n\n".join(content_parts)
            if not file_content.strip():
                error_message = "Could not extract text from this PDF. It may be an image-based PDF."
        else:
            try:
                file_content = decoded_bytes.decode('utf-8')
            except UnicodeDecodeError:
                file_content = decoded_bytes.decode('latin-1', errors='replace')
            if not file_content.strip():
                 error_message = "File appears to be empty or in an unreadable binary format."

    except Exception as e:
        print(f"File processing error for {file_name}: {e}")
        error_message = f"An error occurred while processing the file: {str(e)}"

    if error_message:
        yield yield_data('step', {'status': 'error', 'text': error_message})
        final_data['content'] = f"Error processing file '{file_name}': {error_message}"
        yield yield_data('answer_chunk', final_data['content'])
        yield yield_data('final_response', final_data)
        yield yield_data('step', {'status': 'done', 'text': 'File analysis aborted.'})
        return

    yield yield_data('step', {'status': 'thinking', 'text': 'Analyzing file content...'})
    
    source_for_ui = [{"type": "file_upload", "title": f"Analyzed File: {file_name}", "text": f"Successfully loaded and read {len(file_content)} characters.", "url": "#"}]
    final_data['sources'] = source_for_ui
    yield yield_data('sources', source_for_ui)

    file_context_for_llm = f"The user has uploaded a file named '{file_name}'. I have read the full content of the file, which is provided below. I will now answer the user's query based on this content.\n\n--- START OF FILE CONTENT ---\n\n{file_content}\n\n--- END OF FILE CONTENT ---"

    for suggestion_chunk in _generate_and_yield_suggestions(query, chat_history, file_context_for_llm):
        yield suggestion_chunk
        if 'final_suggestions' in json.loads(suggestion_chunk[6:])['data']:
            final_data['suggestions'] = json.loads(suggestion_chunk[6:])['data']['final_suggestions']

    # **FIX**: Create a more explicit prompt to prevent context confusion from previous turns.
    prompt_content = f"""CRITICAL INSTRUCTION: Your primary task is to answer the user's query based *only* on the provided file content. Ignore any unrelated topics from the recent conversation history.

User's query about the file: "{query}"

Based *only* on the provided file content, answer the user's question. Do not use any external knowledge. If the answer is not in the file, state that clearly.
"""

    stream_response = call_llm(prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, file_context=file_context_for_llm)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'File analysis complete.'})

def run_image_search_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Understanding context...'})
    search_query = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
    if search_query != query:
        yield yield_data('step', {'status': 'info', 'text': f'Searching images based on context: "{search_query}"'})

    yield yield_data('step', {'status': 'searching', 'text': f'Searching Google & Bing Images for: "{search_query[:50]}..."'})

    search_term = search_query
    patterns = [r'(?:images?|pictures?|photos?)\s+of\s+(.+)', r'image:\s*(.+)']
    for p in patterns:
        match = re.search(p, search_query, re.IGNORECASE)
        if match: search_term = match.group(1).strip(); break

    google_results = []
    bing_results = []
    driver = None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            bing_future = executor.submit(scrape_bing_images, search_term)
            google_future = None
            
            driver = setup_selenium_driver()
            if driver:
                google_future = executor.submit(scrape_google_images, driver, search_term)
            else:
                yield yield_data('step', {'status': 'warning', 'text': 'Selenium driver failed, skipping Google Images.'})
            
            if google_future:
                google_results = google_future.result()
            
            bing_results = bing_future.result()

        all_results = google_results + bing_results
    finally:
        if driver:
            driver.quit()
            print("[Selenium] Driver instance for image search has been closed.")
    
    unique_results = list({v['image_url']:v for v in all_results}.values())

    if unique_results:
        final_data['imageResults'] = unique_results
        yield yield_data('image_search_results', unique_results)
        yield yield_data('step', {'status': 'done', 'text': 'High-quality image search results provided.'})
        # **FIX**: Create an explicit acknowledgment prompt
        ack_prompt_content = f"You have just successfully performed an image search for '{search_term}' and the results are now displayed to the user. Briefly acknowledge this accomplishment and ask if the user wants to do anything else with these images or ask another question."
    else:
        yield yield_data('step', {'status': 'info', 'text': f'No relevant high-quality images found for "{search_term}".'})
        ack_prompt_content = f"I'm sorry, I couldn't find any relevant high-quality images for '{search_term}' right now. Is there something else I can look for?"

    stream_response_ack = call_llm(ack_prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response_ack, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Image search process complete.'})

def run_url_deep_parse_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    url_match = re.search(r'https?:\/\/[^\s]+', query)
    if not url_match:
        yield from run_standard_research(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs)
        return
    
    url_to_parse = url_match.group(0)
    yield yield_data('step', {'status': 'searching', 'text': f'Analyzing URL: {url_to_parse[:60]}...'})
    
    parsed_data = parse_with_bs4(url_to_parse)

    if not parsed_data or len(parsed_data.get('text_content', '')) < 500:
        yield yield_data('step', {'status': 'info', 'text': 'Basic analysis insufficient, engaging deep browser-based scraping...'})
        driver = setup_selenium_driver()
        if not driver:
            yield yield_data('step', {'status': 'error', 'text': 'Browser driver could not be initialized for analysis.'})
            error_content = "I'm sorry, I was unable to start the browser driver needed to analyze that URL."
            yield yield_data('answer_chunk', error_content)
            final_data['content'] = error_content
            yield yield_data('final_response', final_data)
            yield yield_data('step', {'status': 'done', 'text': 'Analysis aborted.'})
            return
        
        try:
            parsed_data = parse_url_comprehensive(driver, url_to_parse)
        finally:
            driver.quit()
            print(f"[Selenium] Driver for URL parse of {url_to_parse} has been closed.")
    else:
        yield yield_data('step', {'status': 'info', 'text': 'Fast analysis complete.'})

    if parsed_data.get('images'):
        high_quality_images = [{"type": "image_search_result", "title": f"High-quality image from {parsed_data['domain']}", "thumbnail_url": img, "image_url": img, "source_url": url_to_parse} for img in parsed_data['images'] if is_high_quality_image(img)]
        if high_quality_images:
            final_data['imageResults'] = high_quality_images
            yield yield_data('image_search_results', high_quality_images)
    if parsed_data.get('videos'):
        video_results = [{"type": "video", "title": get_filename_from_url(vid), "thumbnail_url": "", "url": vid, "video_id": ""} for vid in parsed_data['videos']]
        final_data['videoResults'] = video_results
        yield yield_data('video_search_results', video_results)
    if parsed_data.get('links'):
        source_links = [{"type": "web", "title": link['text'] or "Link", "text": link['url'], "url": link['url']} for link in parsed_data['links']]
        final_data['sources'] = source_links
        yield yield_data('sources', source_links)

    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing page content...'})

    summary_prompt = f"""I have scraped the page at {url_to_parse} and extracted its content.
Page Title: {parsed_data.get('title', 'N/A')}
Page Text Content (summary):
{parsed_data.get('text_content', 'No text content found.')[:4000]}
Based on the scraped content, provide a concise summary or answer the user's question about the page. Mention the key findings (e.g., "The page contains X high-quality images and Y links...").
"""
    stream_response = call_llm(summary_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'URL analysis complete.'})

def run_deep_research_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Initiating Deep Research Protocol...'})
    
    topic_match = re.search(r'(?:deep research on|research paper about|comprehensive report on|do a full analysis of)\s+(.+)', query, re.IGNORECASE)
    topic = topic_match.group(1).strip() if topic_match else query

    yield yield_data('step', {'status': 'thinking', 'text': f'Planning deep research for: "{topic}"'})
    search_plan = plan_research_steps_with_llm(f"Comprehensive information about {topic}", chat_history)
    
    yield yield_data('step', {'status': 'searching', 'text': f'Finding top web sources based on {len(search_plan)}-step plan...'})
    
    all_urls = set()
    with ThreadPoolExecutor(max_workers=len(search_plan)) as executor:
        future_to_query = {executor.submit(registry.execute_tool, "web_search", query=q, max_results=3): q for q in search_plan}
        for future in as_completed(future_to_query):
            try:
                results = future.result()
                for r in results:
                    all_urls.add(r['url'])
            except Exception as exc:
                print(f'Deep research search step generated an exception: {exc}')
    
    urls_to_scan = list(all_urls)[:7]
    
    if not urls_to_scan:
        yield yield_data('step', {'status': 'error', 'text': 'Could not find any web sources for the research topic.'})
        error_content = f"I'm sorry, I couldn't find any initial web sources to conduct deep research on '{topic}'."
        yield yield_data('answer_chunk', error_content)
        final_data['content'] = error_content
        yield yield_data('final_response', final_data)
        yield yield_data('step', {'status': 'done', 'text': 'Research aborted.'})
        return

    yield yield_data('step', {'status': 'info', 'text': f'Found {len(urls_to_scan)} sources. Beginning multi-source analysis.'})
    
    driver = setup_selenium_driver()
    if not driver:
        yield yield_data('step', {'status': 'error', 'text': 'Browser driver failed, cannot conduct deep research.'})
        error_content = "I'm sorry, the browser driver failed, so I can't conduct deep research right now."
        yield yield_data('answer_chunk', error_content)
        final_data['content'] = error_content
        yield yield_data('final_response', final_data)
        return

    all_scraped_content = []
    try:
        for i, url in enumerate(urls_to_scan):
            yield yield_data('step', {'status': 'searching', 'text': f'Analyzing source {i+1}/{len(urls_to_scan)}: {urlparse(url).netloc}'})
            try:
                data = parse_with_bs4(url)
                if not data or len(data.get('text_content', '')) < 500:
                    yield yield_data('step', {'status': 'info', 'text': f'Using deep scrape for: {urlparse(url).netloc}'})
                    data = parse_url_comprehensive(driver, url)
                all_scraped_content.append(data)
            except Exception as e:
                yield yield_data('step', {'status': 'warning', 'text': f'Skipping source {i+1} due to error: {e}'})
    finally:
        pass

    yield yield_data('step', {'status': 'thinking', 'text': 'Identifying visualization & image opportunities...'})
    
    context_for_viz_id = "".join(f"Source {i+1} ({data.get('domain', 'N/A')}) Summary:\n{data['text_content'][:1000]}\n\n" for i, data in enumerate(all_scraped_content) if data and data.get('text_content'))

    viz_id_prompt = f"""Based on the following summaries of web articles about "{topic}", identify up to 2 key opportunities for visual content that would enhance a research report. For each, provide a concise prompt. Visuals can be interactive data visualizations OR static images.
- Focus on quantifiable data, comparisons, processes, or timelines for visualizations.
- Focus on illustrative concepts, key entities, or examples for static images.
- The output should be a JSON list of strings.
- Example: ["Generate a bar chart comparing market share of X and Y.", "Find an image illustrating the architecture of Z."]
- If no clear visual opportunities exist, output an empty JSON list: [].
JSON Output:"""
    
    report_embeds = []
    all_scraped_images = [img for data in all_scraped_content if data and data.get('images') for img in data['images'] if is_high_quality_image(img)]

    try:
        viz_id_response = call_llm(viz_id_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False).json()
        viz_prompts_text = viz_id_response["candidates"][0]["content"]["parts"][0]["text"]
        json_match = re.search(r'\[.*\]', viz_prompts_text, re.DOTALL)
        visual_prompts = json.loads(json_match.group(0)) if json_match else []

        if visual_prompts and isinstance(visual_prompts, list):
            yield yield_data('step', {'status': 'info', 'text': f'Found {len(visual_prompts)} visual content opportunities.'})
            for i, prompt in enumerate(visual_prompts):
                yield yield_data('step', {'status': 'thinking', 'text': f'Attempting to generate visualization for: "{prompt[:40]}..."'})
                viz_result = generate_canvas_visualization(prompt, context_data=context_for_viz_id)
                
                if viz_result['type'] == 'canvas_visualization' and "could not be generated" not in viz_result['html_code']:
                    yield yield_data('step', {'status': 'info', 'text': 'Interactive visualization generated successfully.'})
                    report_embeds.append({"type": "visualization", "html": viz_result['html_code'], "prompt": prompt})
                else:
                    yield yield_data('step', {'status': 'warning', 'text': 'Visualization failed. Searching for relevant static images...'})
                    selected_images = _select_relevant_images_for_prompt(prompt, all_scraped_images, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
                    
                    if selected_images:
                        yield yield_data('step', {'status': 'info', 'text': f'Found {len(selected_images)} relevant images to use instead.'})
                        report_embeds.append({"type": "image_gallery", "images": [{"url": url, "alt": prompt} for url in selected_images], "prompt": prompt})
                    else:
                        yield yield_data('step', {'status': 'warning', 'text': 'No relevant fallback images found for this section.'})

    except Exception as e:
        print(f"[Deep Research] Visual content pipeline failed: {e}")
        yield yield_data('step', {'status': 'warning', 'text': 'Could not identify or generate supplemental visuals.'})

    yield yield_data('step', {'status': 'thinking', 'text': 'All sources analyzed. Synthesizing comprehensive HTML report...'})
    
    context_for_report = "".join(f"--- START OF SOURCE {i+1} ({data.get('url', 'N/A')}) ---\nTitle: {data.get('title', 'N/A')}\n\nContent:\n{data.get('text_content', 'N/A')[:5000]}\n--- END OF SOURCE {i+1} ---\n\n" for i, data in enumerate(all_scraped_content) if data)

    embed_context_for_prompt = ""
    if report_embeds:
        embed_context_for_prompt += "You MUST embed the following numbered content blocks into the report where they are most relevant using the placeholders `[EMBED_CONTENT_1]`, `[EMBED_CONTENT_2]`, etc. This is a critical instruction.\n\n"
        for i, embed in enumerate(report_embeds):
            content_type = 'an interactive visualization' if embed['type'] == 'visualization' else 'a gallery of relevant static images'
            embed_context_for_prompt += f"- `[EMBED_CONTENT_{i+1}]`: This block is about '{embed['prompt']}'. It contains {content_type}.\n"
    else:
        embed_context_for_prompt = "No supplemental visualizations or images were generated for this report."
    
    report_prompt = f"""You are a specialist research analyst AI. Your task is to generate an exceptionally detailed and comprehensive research report on the topic: "{topic}".
**CRITICAL INSTRUCTIONS - NON-NEGOTIABLE:**
1.  **OUTPUT FORMAT:** The entire output must be a single, complete, self-contained **HTML document**. The response must start directly with `<!DOCTYPE html>`. Do not include any other text or markdown.
2.  **STYLING:** The HTML must include embedded CSS for excellent, professional, academic-style readability. Use a clean and professional theme.
3.  **LENGTH REQUIREMENT:** The report must be extremely thorough, equivalent to **several thousand words**.
4.  **VISUAL CONTENT EMBEDDING:** {embed_context_for_prompt}
5.  **STRUCTURE:** The report must have a clear structure: main title, executive summary, introduction, multiple detailed sections with sub-sections (using `<h1>`, `<h2>`, `<h3>`), a synthesis/analysis section, a conclusion, and a list of sources.
6.  **CONTENT:** You must critically analyze and synthesize the information from all provided web sources. Do not just copy-paste.
**Raw Data Scraped from Web Sources:**
{context_for_report}
Begin generating the complete, self-contained HTML report now."""
    
    report_html = ""
    for attempt in range(2):
        try:
            report_response_obj = call_llm(report_prompt, api_key, model_config, stream=False)
            report_response_obj.raise_for_status()
            raw_html = report_response_obj.json()["candidates"][0]["content"]["parts"][0]["text"]
            
            html_start_index = raw_html.find('<!DOCTYPE html>')
            if html_start_index != -1 and "</html>" in raw_html.lower():
                report_html = raw_html[html_start_index:]
                print(f"[Deep Research] Successfully generated valid HTML report on attempt {attempt + 1}.")
                break
            else:
                print(f"[Deep Research] Attempt {attempt + 1}: Model did not return valid HTML. Retrying...")
                if attempt == 0: time.sleep(2)
        except Exception as e:
            print(f"[Deep Research] Report generation failed on attempt {attempt + 1}: {e}")
            if attempt == 0: time.sleep(2)
    
    if not report_html:
        yield yield_data('step', {'status': 'error', 'text': 'Failed to synthesize the final report after multiple attempts.'})
        error_context_summary = "\n".join([f"- {data.get('title', 'Untitled')} ({data.get('url', 'N/A')})" for data in all_scraped_content if data])
        report_html = _create_error_html_page(f"<h1>Report Generation Failed</h1><p>The AI model failed to generate a valid HTML report for the topic: '{html.escape(topic)}'.</p><p>The following sources were analyzed:</p><pre>{html.escape(error_context_summary)}</pre>")
    
    for i, embed in enumerate(report_embeds):
        placeholder = f'[EMBED_CONTENT_{i+1}]'
        replacement_html = ""
        if embed['type'] == 'visualization':
            replacement_html = f'<iframe srcdoc="{html.escape(embed["html"])}" style="width: 100%; height: 400px; border: 1px solid #ccc; border-radius: 8px; margin: 1em 0; background: #fff;"></iframe>'
        elif embed['type'] == 'image_gallery':
            replacement_html = _create_image_gallery_html(embed['images'])
        
        report_html = report_html.replace(placeholder, replacement_html)

    yield yield_data('step', {'status': 'thinking', 'text': 'Packaging final report (HTML, MD, PDF)...'})

    pdf_bytes = _generate_pdf_from_html_selenium(driver, report_html)
    
    md_report = "Markdown conversion failed."
    try:
        md_conv_prompt = f"Convert the following HTML document into well-structured Markdown. Output only the Markdown. \n\nHTML:\n{report_html}"
        md_response = call_llm(md_conv_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=False).json()
        md_report = md_response["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Markdown conversion failed: {e}")

    md_b64 = base64.b64encode(md_report.encode('utf-8')).decode('utf-8')
    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8') if pdf_bytes else ""
    
    viewer_html = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Research Report: {html.escape(topic)}</title>
    <style>body{{margin:0;font-family:sans-serif;background-color:#f0f2f5;}}.toolbar{{background-color:#fff;padding:10px 20px;border-bottom:1px solid #ddd;display:flex;align-items:center;gap:20px;position:sticky;top:0;z-index:10;box-shadow:0 2px 4px rgba(0,0,0,0.1);}}.toolbar h1{{font-size:1.2em;margin:0;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}.toolbar .actions{{margin-left:auto;display:flex;gap:10px;}}.toolbar .actions a{{text-decoration:none;background-color:#007bff;color:white;padding:8px 15px;border-radius:5px;font-size:0.9em;transition:background-color .2s;}}.toolbar .actions a:hover{{background-color:#0056b3;}}.toolbar .actions a.disabled{{background-color:#ccc;cursor:not-allowed;}}.content-frame{{width:100%;height:calc(100vh - 61px);border:none;}}</style></head>
    <body><div class="toolbar"><h1>Report: {html.escape(topic)}</h1><div class="actions"><a href="data:text/markdown;charset=utf-8;base64,{md_b64}" download="report-{uuid.uuid4().hex[:6]}.md">Download .MD</a><a href="data:application/pdf;base64,{pdf_b64}" download="report-{uuid.uuid4().hex[:6]}.pdf" class="{'disabled' if not pdf_b64 else ''}">Download .PDF</a></div></div>
    <iframe class="content-frame" srcdoc="{html.escape(report_html)}"></iframe></body></html>
    """
    
    artifact = {"type": "html", "content": viewer_html, "title": f"Deep Research Report: {topic}"}
    final_data['artifacts'].append(artifact)
    yield yield_data('html_preview', {'html_code': viewer_html})
    
    final_data['content'] = f"I have completed the deep research report on '{topic}'. An interactive preview has been generated."
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Deep research report complete and packaged.'})

    driver.quit()
    print("[Selenium] Driver for deep research has been closed.")


def run_visualization_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Generating requested HTML visualization...'})
    viz_type_hint = "general"
    q_lower = query.lower()
    if "math" in q_lower or "equation" in q_lower or "function" in q_lower: viz_type_hint = "math"
    canvas_result = generate_canvas_visualization(query, visualization_type=viz_type_hint)
    
    if canvas_result['type'] == 'canvas_visualization' and "could not be generated" not in canvas_result.get('html_code','').lower():
        artifact = {"type": "html", "content": canvas_result['html_code'], "title": "Interactive Visualization"}
        final_data['artifacts'].append(artifact)
        yield yield_data(canvas_result['type'], canvas_result)
        # **FIX**: Create an explicit acknowledgment prompt
        ack_prompt = f"You have just successfully generated and displayed an interactive visualization based on the user's request: '{query}'. Briefly acknowledge this and explain what the visualization shows."
    else:
        ack_prompt = f"You attempted to generate a visualization for the user's request, but it was not successful. Inform the user of this and ask if you can help in another way."

    stream_response_ack = call_llm(ack_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response_ack, CONVERSATIONAL_MODEL):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Visualization request processed.'})


def run_html_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Generating HTML preview...'})
    html_result = generate_html_preview(query)
    
    if html_result['type'] == 'html_preview' and "could not generate" not in html_result.get('html_code','').lower():
        artifact = {"type": "html", "content": html_result['html_code'], "title": "HTML Preview"}
        final_data['artifacts'].append(artifact)
        yield yield_data(html_result['type'], html_result)
        # **FIX**: Create an explicit acknowledgment prompt
        ack_prompt = f"You have just successfully generated and displayed an HTML preview based on the user's request. Briefly acknowledge this."
    else:
        ack_prompt = f"You attempted to generate an HTML preview, but it was not successful. Inform the user of this."

    stream_response_ack = call_llm(ack_prompt, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response_ack, CONVERSATIONAL_MODEL):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'HTML preview processed.'})


def run_image_generation_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Preparing image generation...'})
    
    yield yield_data('step', {'status': 'thinking', 'text': 'Understanding context...'})
    reformulated_query = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
    if reformulated_query != query:
        yield yield_data('step', {'status': 'info', 'text': f'Generating image based on context: "{reformulated_query}"'})

    prompt_for_image = reformulated_query
    
    yield yield_data('step', {'status': 'thinking', 'text': f'Generating image for: "{prompt_for_image}" via Gemini...'})
    image_result = registry.execute_tool("image_generator", prompt=prompt_for_image)

    if image_result['type'] == 'error':
        yield yield_data('step', {'status': 'warning', 'text': f"Gemini failed: {image_result.get('message', '')}. Falling back to Pollinations.ai..."})
        image_result = generate_image_from_pollinations(prompt_for_image)
    
    if image_result['type'] != 'error':
        artifact = {"type": "image", "content": image_result['base64_data'], "title": image_result['prompt']}
        final_data['artifacts'].append(artifact)
        yield yield_data(image_result['type'], image_result)
        # **FIX**: Create an explicit acknowledgment prompt
        ack_prompt_content = f"You have just successfully generated an image for the prompt '{prompt_for_image}', and it is now displayed to the user. Briefly acknowledge this and ask what's next."
    else:
        ack_prompt_content = f"You attempted to generate an image for the prompt '{prompt_for_image}', but it could not be generated from any available source. The error was: {image_result.get('message', 'Unknown error')}. Apologize to the user and ask if you can try a different prompt or assist in another way."

    stream_response_ack = call_llm(ack_prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response_ack, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Image generation process complete.'})

def run_video_search_pipeline(query, persona_name, api_key, model_config, chat_history, query_profile_type, custom_persona_text, persona_key, **kwargs):
    final_data = { "content": "", "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
    yield yield_data('step', {'status': 'thinking', 'text': 'Understanding context...'})
    search_query = reformulate_query_with_context(query, chat_history, CONVERSATIONAL_API_KEY, CONVERSATIONAL_MODEL)
    if search_query != query:
        yield yield_data('step', {'status': 'info', 'text': f'Searching videos based on context: "{search_query}"'})

    yield yield_data('step', {'status': 'searching', 'text': f'Searching YouTube for: "{search_query[:50]}..."'})

    search_term = search_query

    video_search_results = registry.execute_tool("youtube_search", query=search_term)

    if video_search_results:
        final_data['videoResults'] = video_search_results
        yield yield_data('video_search_results', video_search_results)
        yield yield_data('step', {'status': 'done', 'text': 'Video search results provided.'})
        # **FIX**: Create an explicit acknowledgment prompt
        ack_prompt_content = f"You have just found several videos about '{search_term}' and displayed them. Let the user know you can summarize one if they provide the link, or they can ask something else."
    else:
        yield yield_data('step', {'status': 'info', 'text': f'No relevant videos found for "{search_term}".'})
        ack_prompt_content = f"I'm sorry, I couldn't find any relevant videos for '{search_term}' on YouTube right now. Can I help with something else?"

    stream_response_ack = call_llm(ack_prompt_content, api_key, model_config, stream=True, chat_history=chat_history, persona_name=persona_name, custom_persona_text=custom_persona_text, persona_key=persona_key)
    
    full_response_content = ""
    for chunk in _stream_llm_response(stream_response_ack, model_config):
        full_response_content += json.loads(chunk[6:])['data']
        yield chunk
        
    final_data['content'] = full_response_content
    yield yield_data('final_response', final_data)
    yield yield_data('step', {'status': 'done', 'text': 'Video search process complete.'})