import sqlite3
import json
import time
from config import DATABASE, UTILITY_API_KEY, UTILITY_MODEL
from tools import call_llm
import re 
class BaseMemoryManager:
    def __init__(self, db_path, user_id):
        self.db_path = db_path
        self.user_id = user_id

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

class CoreMemoryManager(BaseMemoryManager):
    def save(self, segment, key, value):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO core_memory (user_id, segment, key, value) VALUES (?, ?, ?, ?)",
                (self.user_id, segment, key, value)
            )
            conn.commit()
        print(f"[Memory] Saved to Core Memory: {{user: {self.user_id}, segment: {segment}, key: {key}}}")

    def retrieve(self, segment, key):
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT value FROM core_memory WHERE user_id = ? AND segment = ? AND key = ?",
                (self.user_id, segment, key)
            )
            row = cursor.fetchone()
            return row['value'] if row else None

    def retrieve_all_for_segment(self, segment):
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT key, value FROM core_memory WHERE user_id = ? AND segment = ?",
                (self.user_id, segment)
            )
            return {row['key']: row['value'] for row in cursor.fetchall()}

class EpisodicMemoryManager(BaseMemoryManager):
    def save_turn(self, chat_id, role, content, final_data_json=None):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO episodic_memory (user_id, chat_id, role, content, final_data_json) VALUES (?, ?, ?, ?, ?)",
                (self.user_id, chat_id, role, content, final_data_json)
            )
            conn.commit()

    def get_chat_history(self, chat_id, limit=20):
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT role, content FROM episodic_memory WHERE user_id = ? AND chat_id = ? ORDER BY timestamp DESC LIMIT ?",
                (self.user_id, chat_id, limit)
            )
            history = cursor.fetchall()
            # Reverse to get chronological order
            return [{"role": row['role'], "content": row['content']} for row in reversed(history)]

    def get_raw_history(self, chat_id):
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT role, content, final_data_json FROM episodic_memory WHERE chat_id = ? AND user_id = ? ORDER BY timestamp ASC",
                (chat_id, self.user_id)
            )
            return cursor.fetchall()

class SemanticMemoryManager(BaseMemoryManager):
    def save(self, entity_type, summary, details, source):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO semantic_memory (user_id, entity_type, summary, details, source) VALUES (?, ?, ?, ?, ?)",
                (self.user_id, entity_type, summary, details, source)
            )
            conn.commit()
        print(f"[Memory] Saved to Semantic Memory: {{user: {self.user_id}, type: {entity_type}, summary: {summary}}}")

class ResourceMemoryManager(BaseMemoryManager):
    def save(self, chat_id, title, resource_type, link_or_content, summary=None):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO resource_memory (user_id, chat_id, title, summary, resource_type, link_or_content) VALUES (?, ?, ?, ?, ?, ?)",
                (self.user_id, chat_id, title, summary, resource_type, link_or_content)
            )
            conn.commit()
        print(f"[Memory] Saved to Resource Memory: {{user: {self.user_id}, title: {title}, type: {resource_type}}}")

class MetaMemoryManager:
    def __init__(self, user_id, db_path=DATABASE):
        self.user_id = user_id
        self.core = CoreMemoryManager(db_path, user_id)
        self.episodic = EpisodicMemoryManager(db_path, user_id)
        self.semantic = SemanticMemoryManager(db_path, user_id)
        self.resource = ResourceMemoryManager(db_path, user_id)

    def retrieve_context_for_llm(self, query, chat_history):
        human_prefs = self.core.retrieve_all_for_segment('human')
        
        context_parts = []
        if human_prefs:
            pref_str = "\n".join([f"- {key}: {value}" for key, value in human_prefs.items()])
            context_parts.append(f"--- User Preferences ---\n{pref_str}")

        # In the future, a semantic search on the query against episodic/semantic memory would go here.
        # For now, we just use the provided chat history.
        if chat_history:
             history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history])
             context_parts.append(f"--- Conversation History ---\n{history_str}")

        return "\n\n".join(context_parts)

    def analyze_and_save_turn(self, user_query, final_data, chat_id):
        # 1. Save the turn to episodic memory
        self.episodic.save_turn(chat_id, 'user', user_query)
        self.episodic.save_turn(chat_id, 'assistant', final_data.get('content', ''), json.dumps(final_data))
        
        saved_memories = []

        # 2. LLM call to analyze the turn for new memories
        analysis_prompt = f"""
Analyze the following conversation turn. Your goal is to extract key information to be stored in a structured memory system for future personalization.

**Memory Types:**
- **Core (Human):** Extract long-term facts or preferences about the user. Examples: "User is a vegetarian.", "User's favorite programming language is Python.", "User lives in London."
- **Semantic:** Extract general knowledge concepts, entities, or definitions that were discussed. Examples: "The definition of 'photosynthesis'.", "The fact that 'Eiffel Tower' is in 'Paris'."
- **Resource:** Identify any specific resources (files, images) that were central to the conversation.

**Conversation Turn:**
User: "{user_query}"
Assistant: "{final_data.get('content', '')[:500]}..."

**Instructions:**
- Analyze the turn and identify information for each memory type.
- If you find a new Core (Human) memory, formulate it as a key-value pair.
- If no new information for a memory type is found, leave its list empty.
- Your output **MUST** be a single, valid JSON object.

**JSON Output:**
{{
  "core_human": [
    {{"key": "example_preference", "value": "example_value"}}
  ],
  "semantic": [
    {{"type": "concept", "summary": "example_concept", "details": "example_details"}}
  ]
}}
"""
        try:
            response = call_llm(analysis_prompt, UTILITY_API_KEY, UTILITY_MODEL, stream=False)
            response_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            
            # === FIX: Add robust JSON parsing ===
            json_match = None
            try:
                # Attempt to find and parse a JSON object from the response
                json_str_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_str_match:
                    json_match = json.loads(json_str_match.group(0))
            except json.JSONDecodeError:
                print(f"Warning: Memory analysis LLM returned non-JSON response: {response_text}")
                return [] # Exit gracefully

            if not json_match:
                return [] # Exit if no valid JSON was found
            
            # 3. Save the extracted memories
            if 'core_human' in json_match:
                for item in json_match['core_human']:
                    if 'key' in item and 'value' in item:
                        self.core.save('human', item['key'], item['value'])
                        saved_memories.append(f"core:{item['key']}={item['value']}")
            
            if 'semantic' in json_match:
                for item in json_match['semantic']:
                    self.semantic.save(item.get('type'), item.get('summary'), item.get('details'), f"chat_id:{chat_id}")

            # Resource memory is saved directly when a file/image is uploaded/analyzed
            if final_data.get('artifacts'):
                for artifact in final_data['artifacts']:
                    if artifact.get('type') in ['image', 'file', 'html']:
                        self.resource.save(chat_id, artifact.get('title', 'Untitled Resource'), artifact.get('type'), 'embedded_content')

            return saved_memories
        except Exception as e:
            print(f"Error during memory analysis and saving: {e}")
            return []
