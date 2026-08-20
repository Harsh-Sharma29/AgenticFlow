"""FastAPI endpoint definitions for the Nexus AI Orchestrator.

Provides:
- POST /api/chat  – main conversational endpoint
- GET  /api/health – liveness probe
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.agents.graph import app_graph
from backend.app.agents.state import normalize_state
from backend.app.api.dependencies import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["orchestrator"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Incoming chat message from the client."""

    user_id: str = Field(default="guest", description="User identifier for session tracking")
    message: str = Field(..., min_length=1, description="The user's message / query")
    workspace_id: str = Field(default="default", description="Workspace context for RAG")
    session_id: Optional[str] = Field(default=None, description="Explicit session/thread ID. Auto-generated if omitted.")
    tenant_id: str = Field(default="default", description="Tenant identifier for multi-tenancy")
    uploaded_docs: List[str] = Field(default_factory=list, description="New document paths to index")


class ChatResponse(BaseModel):
    """Response returned to the client."""

    answer: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    model_used: Optional[str] = None
    fallback_reason: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    execution_status: str = "completed"
    session_id: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "nexus-ai-orchestrator"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness / readiness probe."""
    return HealthResponse()


@router.get("/sessions")
async def get_sessions(workspace_id: str = "default", user_id: str = Depends(get_current_user_id)):
    from backend.app.services.storage import list_chat_sessions
    sessions = await list_chat_sessions(user_id, workspace_id)
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str, workspace_id: str = "default", user_id: str = Depends(get_current_user_id)):
    from backend.app.services.storage import load_chat_messages
    history = await load_chat_messages(user_id, workspace_id, session_id, limit=50)
    return {"messages": history}


class SessionRenameRequest(BaseModel):
    name: str

@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, req: SessionRenameRequest, workspace_id: str = "default", user_id: str = Depends(get_current_user_id)):
    from backend.app.services.storage import rename_chat_session
    await rename_chat_session(user_id, workspace_id, session_id, req.name)
    return {"status": "renamed"}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, workspace_id: str = "default", user_id: str = Depends(get_current_user_id)):
    from backend.app.services.storage import delete_chat_session
    await delete_chat_session(user_id, workspace_id, session_id)
    return {"status": "deleted"}



@router.post("/upload")
async def upload_document(file: UploadFile = File(...), user_id: str = Depends(get_current_user_id)):
    import os
    import shutil
    upload_dir = "/app/data/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"file_path": file_path}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user_id: str = Depends(get_current_user_id)):
    """Main conversational endpoint.

    Accepts a user message, runs the full LangGraph orchestrator
    asynchronously, and returns the AI-generated response.
    """
    session_id = req.session_id or str(uuid.uuid4())

    # Build the input state delta
    input_state: Dict[str, Any] = {
        "tenant_id": req.tenant_id,
        "user_id": user_id,
        "is_guest": user_id == "guest",
        "session_id": session_id,
        "user_query": req.message,
        "workspace_id": req.workspace_id,
        "messages": [],
        "uploaded_docs": req.uploaded_docs,
        "metadata": {},
    }

    normalized = normalize_state(input_state)
    config = {"configurable": {"thread_id": session_id}}

    try:
        final_state = await app_graph.ainvoke(normalized, config=config)
    except Exception as exc:
        logger.exception("Graph invocation failed")
        raise HTTPException(status_code=500, detail=f"Orchestrator error: {exc}")

    final_state = normalize_state(final_state)

    return ChatResponse(
        answer=final_state.get("final_answer") or "No response generated.",
        intent=final_state.get("intent"),
        confidence=final_state.get("intent_confidence"),
        model_used=final_state.get("model_used"),
        fallback_reason=final_state.get("fallback_reason"),
        errors=final_state.get("errors", []),
        execution_status=final_state.get("execution_status", "completed"),
        session_id=session_id,
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(get_current_user_id)):
    """Streaming conversational endpoint using Server-Sent Events."""
    session_id = req.session_id or str(uuid.uuid4())

    input_state: Dict[str, Any] = {
        "tenant_id": req.tenant_id,
        "user_id": user_id,
        "is_guest": user_id == "guest",
        "session_id": session_id,
        "user_query": req.message,
        "workspace_id": req.workspace_id,
        "messages": [],
        "uploaded_docs": req.uploaded_docs,
        "metadata": {},
    }

    normalized = normalize_state(input_state)
    config = {"configurable": {"thread_id": session_id}}

    async def event_generator():
        streamed_something = False
        try:
            # Use version="v2" for latest langchain compatibility
            async for event in app_graph.astream_events(normalized, config=config, version="v2"):
                kind = event["event"]
                
                # Stream token-by-token output from the LLM
                if kind == "on_chat_model_stream":
                    # Filter out the intent node's LLM calls from leaking into the UI stream
                    node_name = event.get("metadata", {}).get("langgraph_node")
                    if node_name != "classify_intent":
                        chunk = event["data"]["chunk"]
                        content = getattr(chunk, "content", "")
                        if content:
                            streamed_something = True
                            yield f"data: {json.dumps({'content': content})}\n\n"
                        
                elif kind == "on_chain_start":
                    node_name = event["name"]
                    agent_status_map = {
                        "load_persistent_context": "Loading session context...",
                        "classify_intent": "Analyzing your query...",
                        "rag_agent": "📄 Processing documents (chunking → embedding → searching)...",
                        "sql_agent": "Generating SQL query...",
                        "code_agent": "Generating and executing code...",
                        "research_agent": "🌐 Searching the web...",
                        "chat_agent": "💬 Thinking...",
                        "save_persistent_context": "Saving conversation...",
                    }
                    if node_name in agent_status_map:
                        yield f"data: {json.dumps({'type': 'status', 'message': agent_status_map[node_name]})}\n\n"

                elif kind == "on_chain_end":
                    node_state = event["data"].get("output", {})
                    if isinstance(node_state, dict):
                        logger.info("Checking fallback for node %s. streamed_something=%s", event["name"], streamed_something)
                        
                        # Send document source metadata when RAG finishes
                        if event["name"] == "rag_agent":
                            sources = []
                            for doc in node_state.get("workspace_documents", []):
                                fp = doc.get("file_path", "")
                                if fp:
                                    sources.append(os.path.basename(fp))
                            if sources:
                                yield f"data: {json.dumps({'type': 'sources', 'documents': sources})}\n\n"
                        
                        if "final_answer" in node_state:
                            logger.info("Found final_answer in %s", event["name"])
                        # Capture the final answer if any node returns it
                        final_ans = node_state.get("final_answer")
                        if final_ans and not streamed_something:
                            logger.info("Yielding fallback from %s", event["name"])
                            yield f"data: {json.dumps({'content': final_ans})}\n\n"
                            streamed_something = True
                            
                        # If this is the main LangGraph completion, send metadata
                        if event["name"] == "LangGraph":
                            pass
                            
            yield "data: [DONE]\n\n"
        except Exception as exc:
            logger.exception("Stream failed")
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")
