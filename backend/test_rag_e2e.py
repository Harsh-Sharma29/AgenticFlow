import asyncio
import os
import json

async def test_rag():
    # Step 1: Create test file inside the container
    os.makedirs('/app/data/uploads', exist_ok=True)
    test_content = 'The company mentioned in this document is NovaTech Global Industries. NovaTech was founded in 2015 and specializes in quantum computing solutions. The CEO is Dr. Elena Vasquez.'
    with open('/app/data/uploads/test_company.txt', 'w') as f:
        f.write(test_content)
    print('STEP 1: Test file created')
    
    # Step 2: Test RAG via the graph directly
    from backend.app.agents.graph import app_graph
    from backend.app.agents.state import normalize_state
    
    input_state = normalize_state({
        'tenant_id': 'default',
        'user_id': 'guest',
        'session_id': 'test-session-e2e-002',
        'user_query': 'What is the name of the company mentioned in the document?',
        'workspace_id': 'default',
        'messages': [],
        'uploaded_docs': ['/app/data/uploads/test_company.txt'],
        'metadata': {},
    })
    config = {'configurable': {'thread_id': 'test-session-e2e-002'}}
    
    print('STEP 2: Running LangGraph...')
    final_state = await app_graph.ainvoke(input_state, config=config)
    
    intent = final_state.get('intent', 'N/A')
    confidence = final_state.get('intent_confidence', 0)
    answer = final_state.get('final_answer', 'N/A')
    status = final_state.get('execution_status', 'N/A')
    errors = final_state.get('errors', [])
    ctx_len = len(final_state.get('retrieved_context', '') or '')
    
    print('STEP 3: Results:')
    print('  Intent:', intent)
    print('  Confidence:', confidence)
    print('  Status:', status)
    print('  Context Length:', ctx_len)
    print('  Errors:', errors)
    print('  Answer:', answer)

asyncio.run(test_rag())
