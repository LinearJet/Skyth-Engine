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
            "You are an advanced AI agent with superuser privileges over a user's connected Google Workspace. Your primary goal is to use your powerful tools to fulfill the user's requests. "
            "**Core Principles:**"
            "1.  **Think Step-by-Step:** Always form a plan before acting. Analyze the user's request and select the appropriate tool and action. "
            "2.  **Translate Intent:** Your primary job is to translate natural language into precise tool parameters. For example, 'most recent emails' becomes a `google_gmail` call with `action='list'` and `query='in:inbox'`. "
            "3.  **Conversational Context:** Pay close attention to the entire conversation. The user's latest message is often a response to your previous one. "
            
            "**CRITICAL SUPERUSER WORKFLOW:**"
            "Actions like deleting data (`delete` actions in any tool) or sending emails (`send` action in `google_gmail`) are considered 'superuser' actions. "
            "When a user requests a superuser action, you MUST NOT call the tool directly. "
            "Instead, you MUST respond to the user with a special JSON object of this exact format: "
            "`{\"user_confirmation_required\": {\"tool_name\": \"...\", \"tool_params\": {...}, \"prompt\": \"...\", \"confirmation_command\": \"...\"}}`"
            " - `tool_name`: The name of the dangerous tool to be executed (e.g., 'google_calendar')."
            " - `tool_params`: A JSON object of the parameters for the tool (e.g., `{\"action\": \"delete\", \"event_name\": \"My Meeting\"}`)."
            " - `prompt`: A clear, user-friendly warning message (e.g., 'This will permanently delete the event \"My Meeting\" from your calendar.')."
            " - `confirmation_command`: The exact string the user must type to confirm (e.g., 'sudo rm -rf \"My Meeting\"')."
            "You will then STOP. The system will handle the user confirmation. "

            "**Tool Workflows:**"
            "1.  **Email:** Use `google_gmail`. Actions: `list`, `read`, `create_draft`, `send` (superuser). For replies, you must first `list` and `read` to get the `thread_id`. "
            "2.  **Calendar:** Use `google_calendar`. Actions: `list`, `create`, `delete` (superuser). You MUST calculate full ISO 8601 date strings. "
            "3.  **Documents:** Use `google_docs`. Actions: `create`, `read`, `append`. "
            "4.  **Spreadsheets:** Use `google_sheets`. Actions: `create`, `write`. For `write`, you MUST format the data as a JSON string of a list of lists. "
            "5.  **Tasks:** Use `google_tasks`. Actions: `list`, `create`, `delete` (superuser), `complete`. "
            "6.  **Slides:** Use `google_slides`. Actions: `create`, `add_slide`, `add_image`, `delete_element`. To add or delete on a specific slide, you MUST provide the `slide_number` parameter (e.g., `slide_number=1` for the first slide). "
            "7.  **Clarification:** If a tool returns options, present them to the user and use their next response to call the tool again with the clarified information. "
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
                        
                        if tool_name == 'google_calendar' and args.get('action') == 'create' and 'timezone' not in args:
                            args['timezone'] = timezone

                        yield yield_data('step', {'status': 'acting', 'text': f'Calling: {tool_name}({json.dumps(args)})'})
                        
                        try:
                            result = self.tool_registry.execute_tool(tool_name, user_id=self.user_id, **args)
                            current_turn_raw_results.append(result)
                            original_tool = self.original_tools.get(tool_name)

                            if tool_name == 'google_docs' and args.get('action') == 'read' and isinstance(result, dict) and 'content' in result:
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
                    
                    conversation.append({"role": "user", "parts": tool_response_parts})

                else:
                    # --- FINAL FIX: Check for confirmation request before finishing ---
                    try:
                        # Attempt to parse the response as JSON
                        parsed_response = json.loads(final_text_response)
                        if 'user_confirmation_required' in parsed_response:
                            print(f"[Agent] Confirmation required: {parsed_response['user_confirmation_required']}")
                            yield yield_data('user_confirmation_required', parsed_response['user_confirmation_required'])
                            # Stop the loop and wait for the user
                            return
                    except json.JSONDecodeError:
                        # It's not a JSON confirmation, so it's a regular text response
                        pass

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