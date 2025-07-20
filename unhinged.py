from pipelines import run_standard_research

def run_unhinged_pipeline(query, persona_name, api_key, model_config, chat_history, is_god_mode, query_profile_type, custom_persona_text, persona_key, **kwargs):
    """
    The unhinged pipeline, which simply wraps the standard research pipeline.
    This can be expanded with more complex logic in the future.
    """
    return run_standard_research(
        query, 
        persona_name, 
        api_key, 
        model_config, 
        chat_history, 
        is_god_mode, 
        query_profile_type, 
        custom_persona_text, 
        persona_key, 
        **kwargs
    )