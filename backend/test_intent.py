import asyncio
import os

async def test_intent():
    from backend.app.agents.graph import app_graph
    from backend.app.agents.state import normalize_state
    
    input_state = normalize_state({
        'tenant_id': 'default',
        'user_id': 'guest',
        'session_id': 'test-session-intent-001',
        'user_query': 'last 5 capitals of UK ?',
        'workspace_id': 'default',
        'messages': [
            {"role": "user", "content": "explain the attached document"},
            {"role": "assistant", "content": "The attached document appears to be an internship offer letter."}
        ],
        'uploaded_docs': [],
        'workspace_documents': [{"doc_id": "123", "file_path": "shivani rawat offer letter.pdf"}],
        'metadata': {},
    })
    
    # Run just the load_persistent_context -> classify_intent nodes
    from backend.app.agents.graph import load_persistent_context_node, classify_intent_node
    
    class DummyConfig:
        configurable = {"thread_id": "123"}
    config = DummyConfig()
    
    print("Running load_persistent_context_node...")
    s1 = await load_persistent_context_node(input_state)
    
    print("Running classify_intent_node...")
    s2 = await classify_intent_node(s1, config)
    
    print(f"Intent classified as: {s2.get('intent')} with confidence {s2.get('intent_confidence')}")
    print(f"Metadata reasoning: {s2.get('metadata', {}).get('intent_reasoning', 'None')}")

asyncio.run(test_intent())
