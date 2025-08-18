import json
import time
from google import genai
from google.genai import types
from typing import List, Any, Dict

from basetool import BaseTool
from tool_registry import ToolRegistry
from utils import yield_data
from config import REASONING_MODEL, REASONING_API_KEY

def _convert_basetool_to_gemini_tool(tool: BaseTool) -> types.Tool:
    """Converts a tool from our BaseTool format to the google.genai.types.Tool format."""
    type_map = {
        "string": types.Type.STRING, "integer": types.Type.INTEGER,
        "number": types.Type.NUMBER, "boolean": types.Type.BOOLEAN,
    }
    properties = {}
    required = []
    for param in tool.parameters:
        param_name = param["name"]
        properties[param_name] = types.Schema(
            type=type_map.get(param["type"], types.Type.STRING),
            description=param["description"]
        )
        required.append(param_name)
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=tool.name, description=tool.description,
            parameters=types.Schema(type=types.Type.OBJECT, properties=properties, required=required)
        )
    ])

def _create_model_response_summary(tool_name: str, tool_output: Any, original_tool: BaseTool) -> str:
    """Creates a concise, structured summary of a tool's output for the next model call."""
    if isinstance(tool_output, dict) and 'error' in tool_output:
        return f"Tool '{tool_name}' failed with error: {tool_output['error']}"

    if isinstance(tool_output, dict) and 'clarification_needed' in tool_output:
        options = ", ".join([f"'{opt}'" for opt in tool_output.get('options', [])])
        return f"Tool '{tool_name}' found multiple possible documents: {options}. The agent must now ask the user to clarify which one they want."

    output_type = original_tool.output_type if original_tool else 'unknown'

    if output_type in ['web_search_results', 'video_search_results', 'image_search_results']:
        if isinstance(tool_output, list) and tool_output:
            summary = f"Tool '{tool_name}' found {len(tool_output)} results. The key information (titles, URLs) is now available for subsequent tool calls.\n"
            for i, item in enumerate(tool_output[:3]):
                title = item.get('title', 'No Title')
                url = item.get('url', item.get('href', 'No URL'))
                summary += f"[{i+1}] {title} ({url})\n"
            return summary.strip()
        else:
            return f"Tool '{tool_name}' returned no results."

    if output_type == 'text_content' and isinstance(tool_output, dict) and 'content' in tool_output:
        doc_name = tool_output.get('document_name', 'the document')
        return f"Successfully read the content from '{doc_name}'. The full text is now available for analysis."

    if isinstance(tool_output, str) and len(tool_output) > 500:
        return f"Tool '{tool_name}' returned a text of {len(tool_output)} characters. The full content is now available for other tools like 'artifact_creator'."

    if output_type == 'downloadable_file':
        filename = tool_output.get('filename', 'file')
        return f"Tool '{tool_name}' successfully created the file '{filename}'. The user can now download it."

    if isinstance(tool_output, (dict, list)):
        return json.dumps(tool_output, indent=2)
    
    return str(tool_output)


class Agent:
    """
    An autonomous agent that uses the Gemini 2.5 thinking process to plan,
    execute tasks with multiple tools, and synthesize results in a continuous loop.
    """
    def __init__(self, api_key: str, tools: List[BaseTool], user_id: int = None):
        try:
            self.client = genai.Client(api_key=api_key)
            self.model_id = REASONING_MODEL.split('/', 1)[1]
        except Exception as e:
            raise ValueError(f"Failed to initialize Gemini client: {e}")

        self.user_id = user_id
        self.tools = [_convert_basetool_to_gemini_tool(t) for t in tools]
        self.tool_registry = ToolRegistry()
        self.original_tools = {t.name: t for t in tools}
        print(f"[Agent] Initialized with {len(self.tools)} tools for model '{self.model_id}' for user_id: {self.user_id}.")

    def run(self, initial_prompt: str, chat_history: List[dict], timezone: str = 'UTC'):
        yield yield_data('step', {'status': 'thinking', 'text': 'Activating Agentic Mode. Analyzing request...'})

        system_instruction = (
            "You are an advanced AI agent. Your primary goal is to use the provided tools to fulfill the user's request. "
            "**Core Principles:**"
            "1.  **Prioritize Tools Over Knowledge:** You MUST prioritize using a tool to fetch real-world, real-time, or user-specific data. DO NOT use your internal knowledge or make up information for things that a tool can answer. This is your most important rule. "
            "2.  **Think Step-by-Step:** Always form a plan before acting. "
            "3.  **Conversational Context:** Pay close attention to the entire conversation. The user's latest message is often a response to your previous one. "
            
            "**Critical Workflows:**"
            "1.  **Email Handling (NON-NEGOTIABLE WORKFLOW):**"
            "    - **Scenario 1: General Request (e.g., 'summarize my inbox').** Your plan MUST be to call `gmail_list_threads` and then present the results to the user to await clarification. "
            "    - **Scenario 2: Specific Request (e.g., 'summarize the email from ACLU', 'elaborate on the second one').** Your plan MUST be a two-step tool call in the SAME turn: "
            "        - **Step 1: ALWAYS call `gmail_list_threads` first.** Use the user's keywords (e.g., 'from:ACLU') in the `query` parameter to find the relevant email. This step is MANDATORY to get the current, correct `thread_id`. "
            "        - **Step 2: Immediately call `gmail_read_email` in the same plan.** You MUST take the `thread_id` from the result of the first tool call and use it as the input for this second tool call. "
            "    - **You are FORBIDDEN from ever calling `gmail_read_email` by itself. It MUST always be preceded by a `gmail_list_threads` call in the same plan.** "
            "2.  **Date & Time Handling:** You are aware of the current date and time. When a user provides a relative time (e.g., 'tomorrow at 4pm', 'a week later on Sunday'), you MUST calculate the full, absolute date and time and convert it to a precise ISO 8601 string (e.g., '2025-08-25T15:00:00') before calling any tool. You must also pass the user's timezone. "
            "3.  **Document Retrieval:** When a user asks to retrieve a document (e.g., 'find my story', 'pull up the notes'), use the `google_docs_find_and_read` tool. Extract the key nouns and topics from their request to use as `search_keywords`. "
            "4.  **Document Creation:** When a user asks to 'create a new doc' or 'make a new document', use the `google_docs_create` tool. When they ask to 'create a new sheet' or 'make a spreadsheet', use the `google_sheets_create` tool. Extract the title from the user's request. "
            "5.  **Document Appending:** When a user asks to 'add to', 'append', or 'update' a document, use the `google_docs_append_text` tool. You must determine the document name and the exact text to append. Always start the appended text with a newline character '\\n' for proper formatting. "
            "6.  **Spreadsheet Writing:** When a user asks to add data to a sheet, use the `google_sheets_write` tool. You MUST convert the user's data into a JSON string representing a list of lists. For example, 'add Name and Score as headers, then Alice with 100' becomes the JSON string `'[[\"Name\", \"Score\"], [\"Alice\", 100]]'`. "
            "7.  **Task Management:** When a user asks to be reminded, add a to-do, or create a task, use the `google_tasks_create` tool. When they ask what's on their list, use `google_tasks_list`. "
            "8.  **Handling Clarification:** If a tool returns a list of options for the user to clarify (e.g., multiple documents found), you MUST present these options to the user. Then, you MUST use their next response to select an option and call the appropriate tool again with the clarified information. "
            "9.  **Document Analysis:** After successfully reading a document with a tool, your task is to act as an expert editor. Provide a comprehensive, insightful, and constructive analysis. Do not just summarize. Identify themes, suggest improvements, and use markdown for clarity. "
            "10. **Artifact Creation:** If the user asks to create a file from content, first generate the content as a text response. In the next turn, call the `artifact_creator` tool to save that content."
        )
        
        conversation = [
            {"role": "user", "parts": [{"text": system_instruction}]},
            {"role": "model", "parts": [{"text": "Understood. I am ready to assist."}]}
        ]
        
        for entry in chat_history:
            role = "model" if entry["role"] == "assistant" else entry["role"]
            conversation.append({"role": role, "parts": [{"text": entry["content"]}]})
        
        conversation.append({"role": "user", "parts": [{"text": initial_prompt}]})
        
        try:
            for i in range(10): 
                yield yield_data('step', {'status': 'thinking', 'text': f'Planning step {i+1}...'})
                
                response_stream = self.client.models.generate_content_stream(
                    model=self.model_id,
                    contents=conversation,
                    config=types.GenerateContentConfig(
                        tools=self.tools,
                        thinking_config=types.ThinkingConfig(thinking_budget=8192, include_thoughts=True)
                    ),
                )

                function_calls = []
                final_text_response = ""

                for chunk in response_stream:
                    if not chunk.candidates or not chunk.candidates[0].content:
                        continue
                    
                    if chunk.candidates[0].content.parts:
                        for part in chunk.candidates[0].content.parts:
                            if hasattr(part, 'thought') and part.thought:
                                yield yield_data('step', {'status': 'thinking', 'text': part.text})
                            elif hasattr(part, 'function_call') and part.function_call:
                                function_calls.append(part.function_call)
                            elif part.text:
                                final_text_response += part.text
                
                if function_calls:
                    yield yield_data('step', {'status': 'acting', 'text': f'Executing {len(function_calls)} tool(s)...'})
                    conversation.append({"role": "model", "parts": [types.Part(function_call=fc) for fc in function_calls]})
                    
                    tool_response_parts = []
                    current_turn_raw_results = []

                    for call in function_calls:
                        tool_name = call.name
                        args = dict(call.args)
                        
                        if tool_name == 'google_calendar_create_event' and 'timezone' not in args:
                            args['timezone'] = timezone

                        yield yield_data('step', {'status': 'acting', 'text': f'Calling: {tool_name}({json.dumps(args)})'})
                        
                        try:
                            result = self.tool_registry.execute_tool(tool_name, user_id=self.user_id, **args)
                            current_turn_raw_results.append(result)
                            original_tool = self.original_tools.get(tool_name)

                            if tool_name == 'google_docs_find_and_read' and isinstance(result, dict) and 'content' in result:
                                doc_source = {
                                    "type": "google_doc",
                                    "title": f"Opened Doc: {result.get('document_name', 'Google Doc')}",
                                    "text": f"Successfully read {len(result['content'])} characters.",
                                    "url": result.get('url', '#')
                                }
                                yield yield_data('sources', [doc_source])

                            if original_tool and original_tool.output_type == 'downloadable_file':
                                yield yield_data('downloadable_file', result)

                            model_response_content = _create_model_response_summary(tool_name, result, original_tool)

                            tool_response_parts.append(types.Part(
                                function_response=types.FunctionResponse(name=tool_name, response={'content': model_response_content})
                            ))
                        except Exception as e:
                            error_message = f"Error executing tool '{tool_name}': {str(e)}"
                            yield yield_data('step', {'status': 'error', 'text': error_message})
                            tool_response_parts.append(types.Part(
                                function_response=types.FunctionResponse(name=tool_name, response={'content': error_message})
                            ))
                    
                    if any(call.name == 'google_docs_find_and_read' for call in function_calls):
                        doc_result = next((res for res in current_turn_raw_results if isinstance(res, dict) and 'content' in res), None)
                        if doc_result:
                            doc_content = doc_result['content']
                            analysis_prompt = f"""
                            The content of the Google Doc has been successfully read. Now, your task is to act as an expert editor and analyst.
                            Based on the user's original query and the full text of the document provided below, provide a comprehensive, insightful, and constructive analysis.
                            - Do not just summarize.
                            - Identify key themes, strengths, and weaknesses.
                            - Suggest specific, actionable changes to improve the document (e.g., varying sentence structure, enhancing character introductions, elaborating on lore, strengthening motivations).
                            - Use markdown formatting (like bullet points) to structure your feedback clearly.

                            **User's Original Query:** "{initial_prompt}"

                            **Full Document Content:**
                            ---
                            {doc_content}
                            ---
                            """
                            tool_response_parts = [types.Part(text=analysis_prompt)]

                    conversation.append({"role": "user", "parts": tool_response_parts})

                else:
                    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing final response...'})
                    for char in final_text_response:
                        yield yield_data('answer_chunk', char)
                        time.sleep(0.005) 
                    
                    final_data = { "content": final_text_response, "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
                    yield yield_data('final_response', final_data)
                    yield yield_data('step', {'status': 'done', 'text': 'Agent finished.'})
                    return


            yield yield_data('step', {'status': 'error', 'text': 'Agent reached maximum steps without finishing.'})

        except Exception as e:
            error_msg = f"An error occurred in the agent loop: {e}"
            print(error_msg)
            yield yield_data('step', {'status': 'error', 'text': error_msg})
            final_data = { "content": error_msg, "artifacts": [], "sources": [], "suggestions": [], "imageResults": [], "videoResults": [] }
            yield yield_data('final_response', final_data)