import os
from google import genai
from google.genai import types
from ddgs import DDGS

# --- 1. Setup ---
GOOGLE_API_KEY = "AIzaSyBG3m-XlU1fLkfXiTCgIiieAWAg27chovE"  # <--- PASTE YOUR API KEY HERE

try:
    client = genai.Client(api_key=GOOGLE_API_KEY)
except Exception as e:
    print(f"Error initializing the client. Please ensure your API key is valid. Error: {e}")
    exit()

# --- 2. Define the External Tool's Python Function ---
def search_duckduckgo(query: str):
    """
    Performs a DuckDuckGo search and returns the top results.
    Args:
        query: The search query string.
    Returns:
        A formatted string of the top 3 search results, or an error message.
    """
    print(f"--- ACTING: Performing DuckDuckGo search for: '{query}' ---")
    try:
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=3)]
        if not results:
            return "No results found."
        formatted_results = "\n\n".join(
            [f"Title: {r.get('title', 'N/A')}\nSnippet: {r.get('body', 'N/A')}\nURL: {r.get('href', 'N/A')}" for r in results]
        )
        return formatted_results
    except Exception as e:
        return f"An error occurred during search: {e}"

# --- 3. Define the Tool for the Model ---
duckduckgo_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name='duckduckgo_search',
            description='Provides up-to-date information from the internet on any topic. Use this for recent events, future topics, or information about new models.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'query': types.Schema(type=types.Type.STRING, description='The search query to use.')
                },
                required=['query']
            )
        )
    ]
)

# --- 4. Model and Prompt ---
MODEL_ID = "gemini-2.5-flash-preview-05-20"

# --- THIS BLOCK IS THE FIX ---
# The user's original query that previously failed to trigger the tool
user_query = "What are The best coding Models of 2025"

# The new instruction that guides the model's thinking process
instruction = "You must use the duckduckgo_search tool to answer the user's query. Do not rely on your internal knowledge, especially for future or speculative topics. Search for the most relevant and current information available."

# Combine the instruction and the query into the final prompt
prompt = f"{instruction}\n\nUser Query: {user_query}"
# --- END OF FIX ---

print(f"User Prompt: {user_query}\n") # We still show the original query for clarity

# --- 5. The Agentic Loop ---
print(f"Calling model '{MODEL_ID}' with streaming and thinking enabled...")
print("--- REASONING: Waiting for the model to decide on an action ---")

try:
    response_stream = client.models.generate_content_stream(
        model=MODEL_ID,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[duckduckgo_tool],
            thinking_config=types.ThinkingConfig(
                thinking_budget=4096,
                include_thoughts=True
            )
        ),
    )

    function_call = None
    for chunk in response_stream:
        for part in chunk.candidates[0].content.parts:
            if hasattr(part, 'thought') and part.thought:
                print("\n--- Model is Thinking ---")
                print(part.text)
                print("-------------------------\n")
            elif hasattr(part, 'function_call') and part.function_call:
                function_call = part.function_call
                break
        if function_call:
            break

    if function_call:
        print(f"--- ACTION REQUIRED: Model wants to call '{function_call.name}' with args: {dict(function_call.args)} ---")
        
        query = dict(function_call.args).get('query', '')
        tool_result = search_duckduckgo(query)
        print(f"\n--- TOOL RESULT ---\n{tool_result}\n-------------------\n")

        print("--- SYNTHESIZING: Sending tool result back to the model for a final answer ---")

        tool_response_part = types.Part(
            function_response=types.FunctionResponse(
                name=function_call.name,
                response={'content': tool_result}
            )
        )

        final_response_stream = client.models.generate_content_stream(
            model=MODEL_ID,
            contents=[
                prompt,
                types.Part(function_call=function_call),
                tool_response_part
            ],
            config=types.GenerateContentConfig(
                tools=[duckduckgo_tool],
                thinking_config=types.ThinkingConfig(
                    thinking_budget=4096,
                )
            ),
        )

        print("\n--- FINAL ANSWER ---")
        for chunk in final_response_stream:
            for part in chunk.candidates[0].content.parts:
                if part.text:
                    print(part.text, end='', flush=True)

    else:
        print("The model did not request a tool call and provided a direct answer.")

except Exception as e:
    print(f"\nAn error occurred during the process: {e}")

print("\n\n--- Process Finished ---")