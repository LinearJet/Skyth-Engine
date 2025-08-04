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

    if isinstance(tool_output, str) and len(tool_output) > 500:
        return f"Tool '{tool_name}' returned a text of {len(tool_output)} characters. The full content is now available for other tools like 'artifact_creator'."

    if output_type == 'downloadable_file':
        filename = tool_output.get('filename', 'file')
        return f"Tool '{tool_name}' successfully created the file '{filename}'. The user can now download it."

    if isinstance(tool_output, (dict, list)):
        return f"Tool '{tool_name}' returned a JSON object with keys: {list(tool_output.keys()) if isinstance(tool_output, dict) else 'N/A'}. The content is now available."
    
    return str(tool_output)


class Agent:
    """
    An autonomous agent that uses the Gemini 2.5 thinking process to plan,
    execute tasks with multiple tools, and synthesize results in a continuous loop.
    """
    def __init__(self, api_key: str, tools: List[BaseTool]):
        try:
            self.client = genai.Client(api_key=api_key)
            self.model_id = REASONING_MODEL.split('/', 1)[1]
        except Exception as e:
            raise ValueError(f"Failed to initialize Gemini client: {e}")

        self.tools = [_convert_basetool_to_gemini_tool(t) for t in tools]
        self.tool_registry = ToolRegistry()
        self.original_tools = {t.name: t for t in tools}
        print(f"[Agent] Initialized with {len(self.tools)} tools for model '{self.model_id}'.")

    def run(self, initial_prompt: str, chat_history: List[dict]):
        yield yield_data('step', {'status': 'thinking', 'text': 'Activating Agentic Mode. Analyzing request...'})

        formatted_history = []
        if chat_history:
            for entry in chat_history:
                role = "model" if entry["role"] == "assistant" else entry["role"]
                formatted_history.append({"role": role, "parts": [{"text": entry["content"]}]})

        # --- THE CORE FIX IS IN THIS INSTRUCTION ---
        instruction = (
            "You are an advanced AI agent. Your primary goal is to use the provided tools to fulfill the user's request. "
            "Think step-by-step. First, form a plan. Then, execute the necessary tools. You can call multiple tools in parallel. "
            "After you get the results, review them and decide if you need to use more tools to complete the user's full request. "
            "When a tool returns a list of items with URLs, you MUST use those exact URLs in subsequent tool calls. "
            "**CRITICAL ARTIFACT CREATION WORKFLOW:** If the user asks to create a file from research or synthesized content (like a summary or table), you MUST follow this two-step process: "
            "1. First, gather all necessary information using your tools. Then, respond with ONLY the formatted text content (e.g., the markdown table, the summary) intended for the file. DO NOT call the artifact_creator tool yet. "
            "2. After you have outputted the text, in the *next* turn, call the `artifact_creator` tool to save that content. The system will automatically use the text you just generated."
        )
        # --- END OF FIX ---
        
        full_prompt = f"{instruction}\n\nUser Query: {initial_prompt}"
        
        conversation = formatted_history + [{"role": "user", "parts": [{"text": full_prompt}]}]
        
        results_from_previous_turn = []

        try:
            for i in range(10): 
                yield yield_data('step', {'status': 'thinking', 'text': f'Planning step {i+1}...'})
                
                response_stream = self.client.models.generate_content_stream(
                    model=self.model_id, contents=conversation,
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
                        
                        if tool_name == 'artifact_creator' and results_from_previous_turn:
                            yield yield_data('step', {'status': 'info', 'text': 'Assembling content from previous step(s) for file creation.'})
                            content_to_save = "\n\n---\n\n".join(str(res) for res in results_from_previous_turn if isinstance(res, str))
                            if not content_to_save:
                                content_to_save = json.dumps(results_from_previous_turn, indent=2)
                            args['content'] = content_to_save
                        
                        yield yield_data('step', {'status': 'acting', 'text': f'Calling: {tool_name}({json.dumps(args)})'})
                        
                        try:
                            result = self.tool_registry.execute_tool(tool_name, **args)
                            current_turn_raw_results.append(result)
                            original_tool = self.original_tools.get(tool_name)

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
                    results_from_previous_turn = current_turn_raw_results

                else:
                    # The model has returned a text response. This could be the final answer OR the synthesized content for a file.
                    # We store this text in our working memory for the next potential step.
                    results_from_previous_turn = [final_text_response]
                    conversation.append({"role": "model", "parts": [{"text": final_text_response}]})

                    # Now, we need to check if the model intends to do something else, like create a file with this text.
                    # So we loop again, but first, we yield the text chunk to the UI so the user sees the content.
                    yield yield_data('step', {'status': 'thinking', 'text': 'Synthesizing content...'})
                    for char in final_text_response:
                        yield yield_data('answer_chunk', char)
                        time.sleep(0.005) 
                    
                    # Add a "user" turn to prompt the model for its next action
                    conversation.append({"role": "user", "parts": [{"text": "OK, the content has been generated. What is the next step in the plan? If the plan is complete, provide the final summary to the user. If the next step is to create a file, call the artifact_creator tool now."}]})
                    
                    # If the model's response was just a simple final answer (e.g., it thinks it's done), we need a way to break the loop.
                    # A simple heuristic: if the response is short and doesn't look like data to be saved, we can assume it's the end.
                    # A more robust agent would have the model output a special "finish" token.
                    if len(function_calls) == 0 and len(final_text_response) < 200 and "file" not in initial_prompt.lower():
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