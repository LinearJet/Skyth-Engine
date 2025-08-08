import os
import json
import base64
import io
import mimetypes
import time
import random
import traceback
import requests # Import requests for exception handling
import shutil # For backing up the database

from flask import request, Response, stream_with_context, send_from_directory, render_template, jsonify, session, url_for, redirect
from flask_cors import CORS
from tinydb import Query

from config import app, DATABASE, CONVERSATIONAL_MODEL, REASONING_MODEL, VISUALIZATION_MODEL, CONVERSATIONAL_API_KEY, REASONING_API_KEY, VISUALIZATION_API_KEY, UTILITY_API_KEY, UTILITY_MODEL, EDGE_TTS_VOICE_MAPPING, CATEGORIES, ARTICLE_LIST_CACHE_DURATION, CACHE, USER_DB
from tools import (
    get_persona_prompt_name, route_query_to_pipeline, get_trending_news_topics,
    get_article_content_tiered,
    setup_selenium_driver, call_llm
)
from pipelines import (
    run_pure_chat, run_visualization_pipeline,
    run_html_pipeline, run_standard_research,
    run_deep_research_pipeline, run_image_analysis_pipeline,
    run_file_analysis_pipeline,
    run_stock_pipeline, yield_data, run_generic_tool_pipeline,
    run_agent_pipeline  # <-- IMPORT THE NEW AGENT PIPELINE
)
from academic import run_academic_pipeline
from coding import run_coding_pipeline
from default import run_default_pipeline
from unhinged import run_unhinged_pipeline
from custom import run_custom_pipeline
from tools_plugins.web_search_tool import WebSearchTool
from tool_registry import ToolRegistry

# Apply CORS to the app object from config
CORS(app, resources={r"/*": {"origins": "*"}})

# Instantiate the tool registry for use in API endpoints and the main search function
registry = ToolRegistry()

@app.before_request
def ensure_guest_user():
    if session.get('user') is None:
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', ('guest@local',)).fetchone()
        if user is None:
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('guest@local', 'guest'))
            conn.commit()
            user = conn.execute('SELECT * FROM users WHERE username = ?', ('guest@local',)).fetchone()
        conn.close()
        session['user'] = {
            'id': user['id'],
            'email': user['username'],
            'name': 'Guest User',
            'picture': 'https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y'
        }

# ==============================================================================
# AI UTILITY FUNCTIONS (REFACTORED)
# ==============================================================================
def generate_chat_title(query, final_answer_content):
    prompt = f"""
    Based on the user's first query and the AI's answer, create a very short, concise title for this conversation (max 5 words).
    The title should capture the main topic or essence of the conversation.
    
    User Query: "{query}"
    AI Answer: "{final_answer_content[:300]}..."
    
    Title:
    """
    try:
        response = call_llm(prompt, UTILITY_API_KEY, UTILITY_MODEL, stream=False)
        title = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"')
        return title if title else "New Chat"
    except Exception as e:
        print(f"Error generating chat title: {e}")
        return "New Chat"

# ==============================================================================
# STREAMING EDGE-TTS API ENDPOINT
# ==============================================================================
@app.route('/api/tts', methods=['POST'])
def text_to_speech():
    import asyncio
    import edge_tts

    data = request.json
    text = data.get('text')
    persona = data.get('persona', 'default')

    if not text:
        return Response(json.dumps({'error': 'No text provided.'}), status=400, mimetype='application/json')

    voice = EDGE_TTS_VOICE_MAPPING.get(persona, EDGE_TTS_VOICE_MAPPING['default'])

    def generate_audio_stream():
        async def async_generator():
            try:
                communicate = edge_tts.Communicate(text, voice)
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        yield chunk["data"]
            except Exception as e:
                print(f"edge-tts streaming error: {e}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async_gen = async_generator().__aiter__()

        try:
            while True:
                chunk = loop.run_until_complete(async_gen.__anext__())
                yield chunk
        except StopAsyncIteration:
            pass
        finally:
            loop.close()

    return Response(stream_with_context(generate_audio_stream()), mimetype="audio/mpeg")

# ==============================================================================
# CORE APPLICATION LOGIC (HEAVILY REFACTORED)
# ==============================================================================
@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    response.headers['Content-Security-Policy'] = "frame-ancestors *"
    return response

@app.route('/search', methods=['POST'])
def search(): # This is now a REGULAR function, not a generator
    # --- All setup code runs immediately within the request context ---
    data = request.json
    user_query = data.get('query')
    persona_key = data.get('persona', 'default')
    model = data.get('model')
    deep_search_mode = data.get('deep_search_mode', 'none')
    custom_persona_text = data.get('custom_persona_prompt', '')
    image_data = data.get('image_data')
    file_data = data.get('file_data')
    file_name = data.get('file_name')
    chat_id = data.get('chat_id')

    if not user_query and not image_data and not file_data:
        return Response(json.dumps({'error': 'No query, image, or file provided.'}), status=400, mimetype='application/json')
    if not user_query:
        if image_data: user_query = "Describe this image."
        elif file_data: user_query = f"Summarize this file: {file_name}"

    user = session.get('user')
    user_id = user['id'] if user else 0

    # --- NEW CONTEXT PERSISTENCE LOGIC ---
    # This logic ensures that an uploaded file or image persists for the entire chat.
    if chat_id and user:
        conn = get_db_connection()
        try:
            # If new data is uploaded, it replaces any old resource for this chat.
            if image_data or file_data:
                conn.execute('DELETE FROM resource_memory WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
                if image_data:
                    conn.execute(
                        'INSERT INTO resource_memory (user_id, chat_id, resource_type, content) VALUES (?, ?, ?, ?)',
                        (user_id, chat_id, 'uploaded_image', image_data)
                    )
                    print(f"[Context] Saved image to resource memory for chat {chat_id}.")
                elif file_data:
                    file_content_json = json.dumps({'filename': file_name, 'b64data': file_data})
                    conn.execute(
                        'INSERT INTO resource_memory (user_id, chat_id, resource_type, content) VALUES (?, ?, ?, ?)',
                        (user_id, chat_id, 'uploaded_file', file_content_json)
                    )
                    print(f"[Context] Saved file '{file_name}' to resource memory for chat {chat_id}.")
                conn.commit()
            # If no new data is uploaded, try to load an existing resource for the chat.
            else:
                resource = conn.execute(
                    'SELECT resource_type, content FROM resource_memory WHERE chat_id = ? AND user_id = ? LIMIT 1',
                    (chat_id, user_id)
                ).fetchone()

                if resource:
                    if resource['resource_type'] == 'uploaded_image':
                        image_data = resource['content']
                        print(f"[Context] Loaded image from resource memory for chat {chat_id}.")
                    elif resource['resource_type'] == 'uploaded_file':
                        try:
                            file_content = json.loads(resource['content'])
                            file_data = file_content.get('b64data')
                            file_name = file_content.get('filename')
                            print(f"[Context] Loaded file '{file_name}' from resource memory for chat {chat_id}.")
                        except (json.JSONDecodeError, TypeError):
                            print(f"[Context] Error decoding file resource for chat {chat_id}.")
        finally:
            conn.close()
    # --- END NEW CONTEXT PERSISTENCE LOGIC ---

    # --- NEW HISTORY FETCHING (No MemoryManager) ---
    chat_history = []
    if chat_id and user:
        conn = get_db_connection()
        history_records = conn.execute(
            "SELECT role, content FROM episodic_memory WHERE user_id = ? AND chat_id = ? ORDER BY timestamp ASC",
            (user_id, chat_id)
        ).fetchall()
        conn.close()
        chat_history = [{"role": row['role'], "content": row['content']} for row in history_records]
    # --- END NEW HISTORY FETCHING ---

    active_persona_name = get_persona_prompt_name(persona_key, custom_persona_text)

    if not CONVERSATIONAL_API_KEY:
        error_msg = "GEMINI_API_KEY not configured. This is required for all operations."
        def error_stream():
            yield yield_data('step', {'status': 'error', 'text': error_msg})
            yield yield_data('error', {'message': error_msg})
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    # --- Define the generator that will be streamed ---
    def streaming_logic():
        # This first yield is now safely inside the generator passed to the Response
        yield yield_data('step', {'status': 'routing', 'text': 'Analyzing query intent...'})

        # The router gets the current query and the past history for context
        route_decision = route_query_to_pipeline(user_query, chat_history, image_data, file_data, persona_key, deep_search_mode)
        query_profile_type = route_decision.get("pipeline", "general_research")
        pipeline_params = route_decision.get("params", {})

        yield yield_data('query_profile', {'type': query_profile_type})

        print(f"Query: '{user_query}', Profiled as: {query_profile_type}, Params: {pipeline_params}")

        current_model_config = model or CONVERSATIONAL_MODEL
        current_api_key = CONVERSATIONAL_API_KEY

        if query_profile_type in ["deep_research", "coding", "agent"]:
            current_model_config = model or REASONING_MODEL
            current_api_key = REASONING_API_KEY
        elif query_profile_type in ["visualization_request", "html_preview", "stock_query"]:
            current_model_config = model or VISUALIZATION_MODEL
            current_api_key = VISUALIZATION_API_KEY

        print(f"Using model: {current_model_config} for initial routing. Specific models may be used within pipelines.")

        # Specialized pipelines that have complex logic beyond a single tool call
        specialized_pipelines = {
            "conversational": run_pure_chat,
            "visualization_request": run_visualization_pipeline,
            "academic_pipeline": run_academic_pipeline,
            "html_preview": run_html_pipeline,
            "coding": run_coding_pipeline,
            "general_research": run_standard_research,
            "deep_research": run_deep_research_pipeline,
            "image_analysis": run_image_analysis_pipeline,
            "file_analysis": run_file_analysis_pipeline,
            "stock_query": run_stock_pipeline,
            "default": run_default_pipeline,
            "unhinged": run_unhinged_pipeline,
            "custom": run_custom_pipeline,
            "agent": run_agent_pipeline, # <-- THE CHANGE: Point 'agent' to the new pipeline
        }

        # The "Plug-and-Play" Logic
        # If the router selected a tool from the registry, use the generic pipeline.
        # Otherwise, fall back to the specialized pipelines dictionary.
        if query_profile_type in registry.tools:
            pipeline_func = run_generic_tool_pipeline
        else:
            pipeline_func = specialized_pipelines.get(query_profile_type, run_default_pipeline)

        pipeline_kwargs = {
            "image_data": image_data, "file_data": file_data, "file_name": file_name,
            "params": pipeline_params,  # Pass the router's params to the pipeline
            "chat_id": chat_id
        }

        final_query = user_query # The generic pipeline gets the original query for its acknowledgment prompt

        main_generator = pipeline_func(
            final_query, active_persona_name, current_api_key, current_model_config,
            chat_history, query_profile_type, custom_persona_text,
            persona_key, **pipeline_kwargs
        )

        # Robust error handling for the entire stream
        try:
            for chunk in main_generator:
                if chunk.startswith('data: '):
                    try:
                        chunk_data = json.loads(chunk[6:])
                        if chunk_data.get('type') == 'final_response':
                            final_data_packet = chunk_data.get('data')

                            if final_data_packet and chat_id and user:
                                conn = get_db_connection()
                                try:
                                    conn.execute(
                                        'INSERT INTO episodic_memory (user_id, chat_id, role, content) VALUES (?, ?, ?, ?)',
                                        (user_id, chat_id, 'user', user_query)
                                    )
                                    conn.execute(
                                        'INSERT INTO episodic_memory (user_id, chat_id, role, content, final_data_json) VALUES (?, ?, ?, ?, ?)',
                                        (user_id, chat_id, 'assistant', final_data_packet.get('content', ''), json.dumps(final_data_packet))
                                    )
                                    conn.commit()
                                finally:
                                    conn.close()

                                conn = get_db_connection()
                                message_count = conn.execute('SELECT COUNT(id) FROM episodic_memory WHERE chat_id = ? AND user_id = ?', (chat_id, user_id)).fetchone()[0]
                                conn.close()

                                if message_count == 2:
                                    title = generate_chat_title(user_query, final_data_packet.get('content', ''))
                                    if title:
                                        conn = get_db_connection()
                                        conn.execute('UPDATE chats SET title = ? WHERE id = ? AND user_id = ?', (title, chat_id, user_id))
                                        conn.commit()
                                        conn.close()
                                        yield yield_data('chat_title_generated', {'chat_id': chat_id, 'title': title})
                    except (json.JSONDecodeError, AttributeError):
                        pass
                yield chunk
        except requests.exceptions.HTTPError as e:
            print(f"Caught HTTPError during stream: {e}")
            error_message = f"The AI model is currently unavailable or overloaded (Error {e.response.status_code}). Please try again in a few moments."
            try:
                error_details = e.response.json().get("error", {}).get("message", "No details provided.")
                error_message = f"The AI model reported an error (Code: {e.response.status_code}): {error_details}. Please try again later."
            except (json.JSONDecodeError, AttributeError):
                pass
            yield yield_data('step', {'status': 'error', 'text': error_message})
            yield yield_data('error', {'message': error_message})
        except Exception as e:
            print(f"Caught generic exception during stream: {e}")
            traceback.print_exc()
            error_message = f"An unexpected error occurred while generating the response. Please check the server logs."
            yield yield_data('step', {'status': 'error', 'text': 'An unexpected error occurred.'})
            yield yield_data('error', {'message': error_message})


        yield 'data: [DONE]\n\n'

    # The search function returns the Response object with the generator
    return Response(stream_with_context(streaming_logic()), mimetype='text/event-stream')


# ==============================================================================
# FLASK ROUTING AND SERVER STARTUP
# ==============================================================================
@app.route('/popular_topics', methods=['GET'])
def popular_topics_endpoint():
    force = request.args.get('force', 'false').lower() == 'true'
    topics = get_trending_news_topics(force_refresh=force)
    return Response(json.dumps(topics), mimetype='application/json')

@app.route('/')
def home():
    user = session.get('user')
    if not user:
        return render_template('index.html', user=None, chats=None)
    
    conn = get_db_connection()
    chats = conn.execute('SELECT * FROM chats WHERE user_id = ? ORDER BY timestamp DESC', (user['id'],)).fetchall()
    conn.close()
    
    return render_template('index.html', user=user, chats=chats)
    
@app.route('/robots.txt')
def robots():
    return send_from_directory(app.static_folder, 'robots.txt')
    
@app.route('/sitemap.xml')
def sitemap():
    sitemap_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://skyth.xyz/</loc>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>https://skyth.xyz/popular_topics</loc>
        <priority>0.8</priority>
    </url>
</urlset>'''
    return Response(sitemap_xml, mimetype='application/xml')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/api/parse_article', methods=['POST'])
def parse_article_endpoint():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        import trafilatura
        from urllib.parse import urlparse
        print(f"[Article Parser API] Attempting extraction with Trafilatura for: {url}")
        downloaded_html = trafilatura.fetch_url(url)
        if downloaded_html:
            main_text = trafilatura.extract(downloaded_html, include_comments=False, include_tables=False, include_formatting=True)
            metadata = trafilatura.extract_metadata(downloaded_html)
            
            if main_text and len(main_text) > 150:
                response_data = {
                    "url": url,
                    "title": metadata.title if metadata else "Title not found",
                    "domain": urlparse(url).netloc,
                    "text_content": main_text,
                    "main_image_url": metadata.image if metadata else None,
                }
                print(f"[Article Parser API] Trafilatura successful for {url}.")
                return jsonify(response_data)
    except Exception as e:
        print(f"[Article Parser API] Trafilatura failed for {url}: {e}")

    print(f"[Article Parser API] Trafilatura insufficient, falling back to url_parser tool for: {url}")
    parsed_data = registry.execute_tool("url_parser", url=url)
    if parsed_data and parsed_data.get('text_content'):
        cleaned_text = '\n\n'.join(chunk for chunk in (phrase.strip() for line in parsed_data['text_content'].splitlines() for phrase in line.split("  ")) if chunk)
        response_data = {
            "url": parsed_data.get('url'),
            "title": parsed_data.get('title'),
            "domain": parsed_data.get('domain'),
            "text_content": cleaned_text,
            "main_image_url": parsed_data['images'][0] if parsed_data.get('images') else None,
        }
        return jsonify(response_data)

    return jsonify({"error": "Failed to parse article with all available methods."}), 500

@app.route('/api/upload_image', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"error": "Invalid file type. Please upload an image (png, jpg, jpeg, gif, webp)."}), 400

    try:
        image_bytes = file.read()
        
        mimetype = file.mimetype
        if not mimetype:
            mimetype = mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'

        base64_encoded_data = base64.b64encode(image_bytes).decode('utf-8')
        
        print(f"[Upload] Successfully processed and encoded image: {file.filename}")
        
        return jsonify({
            "success": True,
            "message": "Image processed successfully.",
            "imageData": base64_encoded_data,
        })

    except Exception as e:
        print(f"[Upload] Error processing file {file.filename}: {e}")
        return jsonify({"error": f"An error occurred while processing the image: {str(e)}"}), 500

@app.route('/api/transcribe_audio', methods=['POST'])
def transcribe_audio():
    import speech_recognition as sr
    from pydub import AudioSegment

    if 'file' not in request.files:
        return jsonify({"error": "No audio file part in the request"}), 400
    
    audio_file = request.files['file']

    if audio_file.filename == '':
        return jsonify({"error": "No selected audio file"}), 400

    recognizer = sr.Recognizer()
    
    try:
        audio_segment = AudioSegment.from_file(audio_file.stream)
        
        wav_io = io.BytesIO()
        audio_segment.export(wav_io, format="wav")
        wav_io.seek(0)

        print(f"[Transcription] Processing audio '{audio_file.filename}' with speech_recognition...")

        with sr.AudioFile(wav_io) as source:
            audio_data = recognizer.record(source)
        
        transcribed_text = recognizer.recognize_google(audio_data)
        
        print(f"[Transcription] Success. Text: \"{transcribed_text[:100]}...\"")
        return jsonify({"success": True, "text": transcribed_text})

    except sr.UnknownValueError:
        print("[Transcription] Google Speech Recognition could not understand audio")
        return jsonify({"error": "Could not understand the audio. Please speak more clearly."}), 422
    except sr.RequestError as e:
        print(f"[Transcription] Could not request results from Google Speech Recognition service; {e}")
        return jsonify({"error": f"Speech service unavailable: {e}"}), 503
    except Exception as e:
        print(f"[Transcription] An unexpected error occurred: {e}")
        return jsonify({"error": f"An unexpected error occurred during transcription. Ensure ffmpeg is installed and accessible in your system's PATH. Error: {str(e)}"}), 500

# ==============================================================================
# DISCOVER PAGE ROUTES
# ==============================================================================
@app.route('/discover')
def discover_page_route():
    return render_template('discover.html', categories=CATEGORIES)

@app.route('/fetch_articles/<category>')
def fetch_articles(category):
    web_search_tool = WebSearchTool()
    cache_key = f"articles_{category}"
    
    if cache_key in CACHE['articles'] and time.time() - CACHE['articles'][cache_key]['timestamp'] < ARTICLE_LIST_CACHE_DURATION:
        print(f"CACHE HIT: Serving article list for '{category}' from cache.")
        return jsonify(CACHE['articles'][cache_key]['data'])

    print(f"CACHE MISS: Fetching new article list for '{category}'.")

    if category == "For You":
        scores = session.get('scores', {})
        top_categories = ['Top', 'Technology'] if not scores else [c for c, s in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]]
        
        all_articles = []
        for cat in top_categories:
            query = "latest world headlines" if cat == "Top" else f"latest {cat.lower()} news"
            results = web_search_tool.execute(query, max_results=10, type='news')
            all_articles.extend([{
                'title': r.get('title'), 'snippet': r.get('text'), 'url': r.get('url'),
                'thumbnail': r.get('image'), 'source': r.get('source'), 'category': cat
            } for r in results if r.get('url')])
        random.shuffle(all_articles)
        articles_to_return = all_articles
    else:
        query_map = {"Top": "top world news", "Around the World": "international news"}
        query = query_map.get(category, f"latest {category.lower()} news")
        results = web_search_tool.execute(query, max_results=20, type='news')
        articles_to_return = [{
            'title': r.get('title'), 'snippet': r.get('text'), 'url': r.get('url'),
            'thumbnail': r.get('image'), 'source': r.get('source'), 'category': category
        } for r in results if r.get('url')]
    
    CACHE['articles'][cache_key] = {'timestamp': time.time(), 'data': articles_to_return}
    return jsonify(articles_to_return)

@app.route('/get_full_article', methods=['POST'])
def get_full_article():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    article_data = get_article_content_tiered(url)
    
    if article_data and article_data.get('text'):
        cleaned_text = '\n\n'.join(p.strip() for p in article_data['text'].split('\n') if len(p.strip()) > 30)
        article_data['text'] = cleaned_text
        return jsonify(article_data)
    else:
        return jsonify({
            'error': 'Could not parse article content.',
            'title': 'Parsing Failed',
            'text': f"Sorry, we couldn't automatically load this article.\n\nYou can try visiting the source directly:\n{url}"
        }), 500

@app.route('/track_interaction', methods=['POST'])
def track_interaction():
    category = request.json.get('category')
    if category and category not in ["For You", "error"]:
        session.permanent = True
        scores = session.get('scores', {})
        scores[category] = scores.get(category, 0) + 1
        session['scores'] = scores
    return jsonify({'status': 'ok'})

# ==============================================================================
# USER AUTHENTICATION & CHAT MANAGEMENT ROUTES
# ==============================================================================
@app.route('/login')
def login():
    # Create a guest user session (no OAuth)
    conn = get_db_connection()
    # Ensure a single shared guest user exists
    user = conn.execute('SELECT * FROM users WHERE username = ?', ('guest@local',)).fetchone()
    if user is None:
        conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('guest@local', 'guest'))
        conn.commit()
        user = conn.execute('SELECT * FROM users WHERE username = ?', ('guest@local',)).fetchone()
    conn.close()

    session['user'] = {
        'id': user['id'],
        'email': user['username'],
        'name': 'Guest User',
        'picture': 'https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y'
    }

    return redirect('/')
    
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

@app.route('/profile')
def profile():
    user = session.get('user')
    if not user:
        # Auto-create guest on profile access
        return redirect('/login')
    return render_template('profile.html', user=user)


def get_db_connection():
    import sqlite3
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/api/chats', methods=['GET'])
def get_chats():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    conn = get_db_connection()
    chats = conn.execute('SELECT * FROM chats WHERE user_id = ? ORDER BY timestamp DESC', (user['id'],)).fetchall()
    conn.close()
    
    return jsonify([dict(chat) for chat in chats])

@app.route('/api/chats', methods=['POST'])
def create_chat():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    title = request.json.get('title', 'New Chat')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO chats (user_id, title) VALUES (?, ?)', (user['id'], title))
    new_chat_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return jsonify({'id': new_chat_id, 'title': title})

@app.route('/api/chats/<int:chat_id>', methods=['PUT'])
def rename_chat(chat_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    new_title = request.json.get('title')
    if not new_title:
        return jsonify({"error": "New title is required"}), 400

    conn = get_db_connection()
    conn.execute('UPDATE chats SET title = ? WHERE id = ? AND user_id = ?', (new_title, chat_id, user['id']))
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": "Chat renamed."})

@app.route('/api/chats/<int:chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    try:
        # Cascade delete from all memory tables
        conn.execute('DELETE FROM episodic_memory WHERE chat_id = ? AND user_id = ?', (chat_id, user['id']))
        conn.execute('DELETE FROM resource_memory WHERE chat_id = ? AND user_id = ?', (chat_id, user['id']))
        # Finally, delete the chat itself
        conn.execute('DELETE FROM chats WHERE id = ? AND user_id = ?', (chat_id, user['id']))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error deleting chat {chat_id}: {e}")
        return jsonify({"error": "Database error during deletion."}), 500
    finally:
        conn.close()
    
    return jsonify({"success": True, "message": "Chat deleted."})


@app.route('/api/chats/<int:chat_id>/history', methods=['GET'])
def get_chat_history(chat_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    chat = conn.execute('SELECT * FROM chats WHERE id = ? AND user_id = ?', (chat_id, user['id'])).fetchone()
    if not chat:
        conn.close()
        return jsonify({"error": "Chat not found or access denied"}), 404

    # Directly query episodic memory
    history_records = conn.execute(
        "SELECT role, content, final_data_json FROM episodic_memory WHERE chat_id = ? AND user_id = ? ORDER BY timestamp ASC",
        (chat_id, user['id'])
    ).fetchall()
    conn.close()
    
    formatted_history = []
    for record in history_records:
        if record['role'] == 'user':
            formatted_history.append({
                'role': 'user',
                'content': record['content']
            })
        elif record['role'] == 'assistant' and record['final_data_json']:
             try:
                answer_json = json.loads(record['final_data_json'])
                formatted_history.append({
                    'role': 'assistant',
                    'content': answer_json.get('content', ''),
                    'artifacts': answer_json.get('artifacts', []),
                    'sources': answer_json.get('sources', []),
                    'suggestions': answer_json.get('suggestions', []),
                    'imageResults': answer_json.get('imageResults', []),
                    'videoResults': answer_json.get('videoResults', [])
                })
             except (json.JSONDecodeError, TypeError):
                formatted_history.append({
                    'role': 'assistant',
                    'content': 'Error: Could not decode message content.'
                })
            
    return jsonify(formatted_history)

def init_db():
    """
    Robust database initializer. Checks for schema validity and automatically
    recreates the DB from schema.sql if it's outdated or missing.
    """
    import sqlite3
    
    db_path = DATABASE
    schema_path = 'schema.sql'

    if not os.path.exists(schema_path):
        print(f"❌ CRITICAL: {schema_path} not found. Cannot initialize or verify the database.")
        return

    schema_is_valid = False
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # Check for the existence and structure of a critical table from the new schema
            cursor.execute("PRAGMA table_info(resource_memory);")
            columns = [row[1] for row in cursor.fetchall()]
            # Check for specific columns that were added in the new schema
            if 'content' in columns and 'resource_type' in columns:
                schema_is_valid = True
                print("✅ Database schema appears up-to-date.")
            else:
                 print("⚠️ Database schema is outdated (missing columns in 'resource_memory').")
            conn.close()
        except sqlite3.OperationalError:
            # This happens if the table 'resource_memory' doesn't exist at all
            print("⚠️ Database schema is outdated ('resource_memory' table is missing).")
        except Exception as e:
            print(f"❌ Error checking database schema: {e}")
    else:
        print("Database not found. A new one will be created.")

    if not schema_is_valid:
        if os.path.exists(db_path):
            backup_path = f"{db_path}.backup.{int(time.time())}"
            print(f"Backing up existing database to {backup_path}...")
            try:
                shutil.move(db_path, backup_path)
            except Exception as e:
                print(f"❌ Could not back up database: {e}. Please check file permissions.")
                return
        
        print(f"Initializing new database from {schema_path}...")
        try:
            conn = sqlite3.connect(db_path)
            with open(schema_path, 'r') as f:
                conn.executescript(f.read())
            conn.commit()
            conn.close()
            print("✅ New database initialized successfully.")
        except Exception as e:
            print(f"❌ DB initialization from schema failed: {e}")


if __name__ == '__main__':
    import sqlite3
    from tools import get_current_datetime_str
    init_db()

    print(f"🚀 SKYTH ENGINE v11.0 (Generalized Plugin System) - Running with current date: {get_current_datetime_str()}")
    print(f"   Conversational Model: {CONVERSATIONAL_MODEL}")
    print(f"   Reasoning Model: {REASONING_MODEL} (Reserved for Coding & Deep Research)")
    print(f"   Visualization Model: {VISUALIZATION_MODEL}")
    print(f"   Utility/Routing Model: {UTILITY_MODEL}")
    print(f"   Image Generation/Editing Model: {os.getenv('IMAGE_GENERATION_MODEL', 'gemini-2.0-flash-preview-image-generation')}")
    print("   Features: Generalized 'plug-and-play' tool system, dynamic router, robust DB, persistent context.")
    print("🌐 Server running on http://127.0.0.1:5000")
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
