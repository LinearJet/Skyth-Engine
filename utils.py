import json

def yield_data(event_type, data_payload):
    return f"data: {json.dumps({'type': event_type, 'data': data_payload})}\n\n"
def _stream_llm_response(response_iterator, model_config):
    for chunk in response_iterator.iter_lines():
        if chunk:
            decoded_chunk = chunk.decode('utf-8')
            if decoded_chunk.startswith('data: '):
                try:
                    data_str = decoded_chunk[6:]
                    if data_str.strip().upper() == "[DONE]": continue
                    data = json.loads(data_str)
                    text_chunk = ""
                    if data.get("candidates") and data["candidates"][0].get("content", {}).get("parts"):
                        text_chunk = data["candidates"][0]["content"]["parts"][0].get("text", "")
                    if text_chunk: yield yield_data('answer_chunk', text_chunk)
                except Exception as e: print(f"Stream processing error: {e} on line: {data_str[:100]}")