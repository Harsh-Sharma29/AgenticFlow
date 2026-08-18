"""Async LangGraph orchestrator – nodes, routing, and graph compilation.

Every node is ``async def`` and LLM calls use ``ainvoke``.
The compiled graph is exposed as ``app_graph`` for use by the API layer.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import Any, Dict

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from backend.app.agents.state import (
    Intent,
    OrchestratorState,
    ensure_errors,
    ensure_intent,
    ensure_metadata,
    normalize_state,
)
from backend.app.services.llm_router import AsyncLLMRouter
from backend.app.services.rag_service import RAGService
from backend.app.services import storage
from backend.app.utils.intent_parse import (
    escape_prompt_template_value,
    parse_intent_llm_response,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared singletons (created once at import time)
# ---------------------------------------------------------------------------
llm_router = AsyncLLMRouter()
rag_service = RAGService()

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
INTENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are an intent classification system for an Enterprise AI Orchestrator.

Classify the user query into one of these intents:
- "rag": Questions explicitly about the uploaded documents, extracting info from the text, or summarizing the files.
- "sql": Database queries, data analysis requests, SQL generation needs.
- "code": Code execution requests, data processing scripts, computational tasks.
- "research": Web research, current events, or looking up facts that require the internet.
- "chat": General knowledge questions, casual conversation, greetings, or questions that an LLM can answer from its own training data (e.g., "what is the capital of france?").

Context: {context_info}

CRITICAL RULES:
1. Just because documents are uploaded DOES NOT mean every question is a "rag" query. 
2. If the user asks a general knowledge question (e.g., "What is the capital of India?", "Write a poem", "Explain quantum physics"), classify it as "chat" or "research", NOT "rag".
3. Only classify as "rag" if the question is reasonably trying to extract information that would specifically be inside the uploaded documents, or if the user explicitly mentions the document (e.g., "in the pdf", "what does the file say").

Respond ONLY with valid JSON:
{{"intent": "<intent>", "confidence": <0-1>, "reasoning": "<short explanation>"}}"""),
    ("human", """Conversation history (for context only):
{history}

CURRENT Query to classify: {query}

Remember: Base your classification heavily on the CURRENT Query. Only use the history to resolve pronouns or follow-ups."""),
])

CHAT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a helpful AI assistant.

Answer questions directly and helpfully.

CRITICAL RULES:
- NEVER explain what RAG/document search is - it's automatic
- NEVER suggest using research tools - they're automatic
- NEVER say "I don't have access" if tools exist
- Focus on answering questions with your knowledge
- Be concise and accurate

If unsure, provide your best answer based on general knowledge."""),
    ("human", """Conversation history:
{history}

User: {query} """),
])

RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a highly capable AI assistant that answers questions based on the provided documents.

CRITICAL RULES FOR RESPONSE FORMATTING AND QUALITY:
1. Address the CURRENT 'Question' from the user directly. Use 'Conversation history' ONLY for context if needed (e.g., resolving pronouns).
2. Write a clear, well-structured, and comprehensive response using Markdown (headings, bold text, bullet points where appropriate).
3. If the context contains the answer, cite the filename naturally in your sentences (e.g., "According to *file.pdf*...") or at the end of the sentence (e.g., [Source: file.pdf]).
4. NEVER start your response with a JSON array or random filenames like `["file.pdf"]`. Start directly with your conversational response.
5. If the context contains *related* but not exact info, say: "The document contains related information, but not that exact detail." Then provide a helpful summary.
6. If the context is completely missing the requested info, say: "I found the document, but it doesn't seem to contain that specific detail." Then summarize what you DO see.
7. DO NOT hallucinate details not present in the text.
8. If the user asks for a learning schedule, timeline, or duration, and the document is a guide or textbook, DO NOT say the information is missing. Instead, use the breadth and complexity of the extracted topics to provide a logical, structured ESTIMATED timeline (e.g., 'Based on the topics covered, here is an estimated 30-day study plan').
9. DO NOT say "I cannot answer". Always provide at least a useful summary of the provided text."""),
    ("human", """Context from documents:
{context}

Conversation history (for context only):
{history}

Current Question: {question}

Answer:"""),
])

RESEARCH_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a research assistant. Answer questions using available information.

Rules:
- If search results are valid: Answer and cite sources.
- If search results are empty or irrelevant:
  1. Say: "Live data unavailable right now."
  2. Then provide your best answer based on general knowledge.
  3. Clearly label it as general knowledge, not current.
- NEVER refuse to answer.
- NEVER explain tool errors/limitations to the user."""),
    ("human", """Query: {query}

Search Results:
{search_results}

Conversation context:
{history}

Provide a comprehensive answer:"""),
])


# ===================================================================
# Graph Nodes (all async)
# ===================================================================

async def load_persistent_context_node(state: OrchestratorState) -> OrchestratorState:
    """Load persisted chat history + workspace docs from SQLite."""
    state = normalize_state(state)
    ws_id = state.get("workspace_id") or "default"
    state["workspace_id"] = ws_id

    if not state.get("session_id"):
        state["session_id"] = str(uuid.uuid4())

    session_id = state["session_id"]
    user_id = state["user_id"]

    # Load chat history
    history = await storage.load_chat_messages(user_id, ws_id, session_id, limit=50)
    state["chat_history"] = history

    if not state.get("messages"):
        state["messages"] = []
    if history:
        for m in history:
            state["messages"].append({"role": m["role"], "content": m["content"], "metadata": {"persisted": True}})
    state["memory_loaded_count"] = len(state["messages"])

    # Load existing workspace documents for this session
    docs, index_path = await storage.list_workspace_documents(user_id, ws_id, session_id)
    state["workspace_documents"] = docs
    state["uploaded_doc_ids"] = [d["doc_id"] for d in docs]
    state["vector_index_path"] = index_path

    # Register any incoming uploaded docs in SQLite
    incoming_paths = state.get("uploaded_docs", [])
    if incoming_paths:
        base_index_path = index_path or os.path.join(
            "workspaces", user_id, ws_id, session_id, "faiss_index"
        )
        for p in incoming_paths:
            abs_p = os.path.normpath(os.path.abspath(p))
            doc_id = hashlib.sha256(f"{user_id}|{ws_id}|{session_id}|{abs_p}".encode()).hexdigest()[:32]
            await storage.upsert_document(user_id, ws_id, session_id, doc_id, abs_p, base_index_path)

        # Refresh documents list
        docs, index_path = await storage.list_workspace_documents(user_id, ws_id, session_id)
        state["workspace_documents"] = docs
        state["uploaded_doc_ids"] = [d["doc_id"] for d in docs]
        state["vector_index_path"] = index_path

    # Keep uploaded_docs as-is so rag_node knows what to index
    return state


async def save_persistent_context_node(state: OrchestratorState) -> OrchestratorState:
    """Persist new chat messages written during this run."""
    state = normalize_state(state)
    start = state.get("memory_loaded_count", 0)
    new_msgs = state.get("messages", [])[start:]
    await storage.append_chat_messages(state["user_id"], state["workspace_id"], state["session_id"], new_msgs)
    state["memory_loaded_count"] = len(state.get("messages", []))
    return state


async def classify_intent_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """Classify user intent via LLM."""
    state = normalize_state(state)
    ensure_metadata(state)
    ensure_errors(state)
    ensure_intent(state)

    current_query = state["user_query"]
    messages = state.get("messages", [])

    should_append = True
    if messages:
        last_msg = messages[-1]
        if last_msg.get("role") == "user" and last_msg.get("content") == current_query:
            should_append = False

    if should_append:
        state["conversation_turn"] = state.get("conversation_turn", 0) + 1
        state["messages"].append({
            "role": "user",
            "content": current_query,
            "metadata": {"turn": state["conversation_turn"], "message_id": f"{state['session_id']}-{state['conversation_turn']}"},
        })

    # Build history context
    history_context = "No previous conversation"
    try:
        if messages:
            recent = messages[-5:]
            history_context = "\n".join(
                f"{msg.get('role', 'user')}: {msg.get('content', '')[:200]}" for msg in recent
            ) or "No previous conversation"
    except Exception:
        history_context = "No previous conversation"

    # Build context info
    uploaded_docs = state.get("uploaded_docs", [])
    workspace_docs = state.get("workspace_documents", [])
    has_docs = bool(uploaded_docs or workspace_docs)
    parts = []
    if has_docs:
        parts.append(f"Documents available: {len(uploaded_docs) + len(workspace_docs)} files uploaded")
    else:
        parts.append("No documents uploaded")
    parts.append("Research tool: Tavily search available")
    context_info = " | ".join(parts)

    query = state.get("user_query", "")
    if not query:
        state["intent"] = Intent.UNKNOWN.value
        state["intent_confidence"] = 0.0
        state["metadata"]["intent_reasoning"] = "Empty query"
        return state

    try:
        prompt_msgs = INTENT_PROMPT.format_messages(
            query=escape_prompt_template_value(query),
            history=escape_prompt_template_value(history_context),
            context_info=escape_prompt_template_value(context_info),
        )
    except Exception as e:
        state["errors"].append(f"Prompt formatting error: {e}")
        state["intent"] = Intent.UNKNOWN.value
        state["intent_confidence"] = 0.0
        return state

    try:
        response = await llm_router.ainvoke(prompt_msgs, state=state, temperature=0.1)
    except Exception as e:
        state["errors"].append(f"LLM invocation error: {e}")
        state["intent"] = Intent.UNKNOWN.value
        state["intent_confidence"] = 0.0
        return state

    text = getattr(response, "content", None)
    logger.info("Raw intent LLM response: %r", text)
    if not isinstance(text, str):
        state["intent"] = Intent.UNKNOWN.value
        state["intent_confidence"] = 0.0
        return state
        return state

    parsed = parse_intent_llm_response(text)

    intent = parsed.get("intent") or Intent.UNKNOWN.value
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (ValueError, TypeError):
        confidence = 0.5

    valid_intents = {i.value for i in Intent}
    if intent not in valid_intents:
        intent = Intent.UNKNOWN.value
        confidence = 0.0

    state["intent"] = intent
    state["intent_confidence"] = confidence
    state["metadata"]["intent_reasoning"] = str(parsed.get("reasoning", ""))
    return state


def route_after_classification(state: OrchestratorState) -> str:
    """Route to the appropriate agent node."""
    ensure_metadata(state)
    ensure_errors(state)
    ensure_intent(state)

    intent = state.get("intent", Intent.UNKNOWN.value)
    confidence = state.get("intent_confidence", 0.0)
    query = state.get("user_query", "")

    # Research keyword override
    query_lower = query.lower()
    research_kws = ["weather", "temperature", "current", "today", "now", "latest", "recent", "this week", "this month"]
    if any(kw in query_lower for kw in research_kws):
        state["intent"] = Intent.RESEARCH.value
        state["intent_confidence"] = max(confidence, 0.75)
        state.setdefault("metadata", {})["routing_override"] = "keyword_based_research"
        return "research_agent"

    # Low confidence / unknown → context-based routing
    if not intent or intent == Intent.UNKNOWN.value or confidence < 0.4:
        uploaded_docs = state.get("uploaded_docs", [])
        workspace_docs = state.get("workspace_documents", [])
        has_docs = bool(uploaded_docs or workspace_docs)

        if has_docs:
            rag_triggers = ["explain", "summarize", "what", "where", "how", "list", "describe", "analysis", "insight"]
            # Only trigger RAG on fallback if the query specifically looks like it's referring to the docs
            doc_keywords = ["document", "pdf", "file", "text", "resume", "attached"]
            is_asking_about_doc = any(kw in query_lower for kw in doc_keywords)
            
            if is_asking_about_doc or (any(t in query_lower for t in rag_triggers) and confidence > 0.2):
                state["intent"] = Intent.RAG.value
                state["intent_confidence"] = 0.5
                return "rag_agent"

        state["intent"] = Intent.CHAT.value
        state["intent_confidence"] = 0.6
        return "fallback_handler"
        
    # If they explicitly uploaded a new document in this exact turn, ALWAYS route to RAG
    if state.get("uploaded_docs"):
        state["intent"] = Intent.RAG.value
        state["intent_confidence"] = 1.0
        logger.info("route_intent: uploaded_docs present, routing to rag_agent")
        return "rag_agent"

    routing = {
        Intent.RAG.value: "rag_agent",
        Intent.SQL.value: "sql_agent",
        Intent.CODE.value: "code_agent",
        Intent.RESEARCH.value: "research_agent",
        Intent.CHAT.value: "chat_agent",
    }
    target = routing.get(intent, "fallback_handler")
    logger.info("route_intent: classified intent=%s, routing to %s", intent, target)
    return target


async def rag_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """RAG agent node — Hybrid GraphRAG (FAISS + Memgraph)."""
    state = normalize_state(state)
    try:
        user_id = state.get("user_id", "guest")
        session_id = state.get("session_id", "default")
        vector_index_path = state.get("vector_index_path")
        uploaded_docs = state.get("uploaded_docs", [])

        # Build list of document names for context
        doc_names = []
        for p in uploaded_docs:
            doc_names.append(os.path.basename(p))
        for d in state.get("workspace_documents", []):
            name = os.path.basename(d.get("file_path", ""))
            if name and name not in doc_names:
                doc_names.append(name)

        logger.info("RAG node: user=%s session=%s docs=%s", user_id, session_id, doc_names)

        # Load new documents if any (FAISS + Memgraph graph extraction)
        chunks_indexed = await rag_service.load_documents(
            doc_paths=uploaded_docs,
            user_id=user_id,
            session_id=session_id,
            vector_index_path=vector_index_path,
            errors=state["errors"],
            llm_router=llm_router,  # Enables Memgraph graph extraction
        )
        logger.info("RAG indexed %d new chunks", chunks_indexed)

        # Dynamic Context Scaling: 
        # If the user asks for a global summary, fetch a massive amount of chunks (k=150)
        # Otherwise, fetch a robust default context (k=20).
        user_query = state.get("user_query", "").lower()
        global_keywords = ["explain", "summarize", "all", "whole", "complete", "everything", "about", "days", "learn"]
        k_val = 150 if any(w in user_query for w in global_keywords) else 20

        # Hybrid Retrieve: FAISS semantic search + Memgraph graph context
        context = await rag_service.search(
            query=state.get("user_query", ""),
            user_id=user_id,
            session_id=session_id,
            vector_index_path=vector_index_path,
            errors=state["errors"],
            k=k_val,
            llm_router=llm_router,  # Enables Memgraph graph retrieval
        )
        state["retrieved_context"] = context

        logger.info("Hybrid RAG context length: %d", len(context))
        if not context or len(context.strip()) < 10:
            logger.info("RAG context too short, returning early.")
            if doc_names:
                state["final_answer"] = f"I have the document(s) ({', '.join(doc_names)}) but couldn't find relevant content for your question. Please try rephrasing."
            else:
                state["final_answer"] = "No documents have been uploaded yet. Please upload a document first."
            state["execution_status"] = "completed"
            return state

        history = ""
        if state.get("messages"):
            recent = (state.get("messages") or [])[-3:]
            history = "\n".join(f"{m.get('role','unknown')}: {str(m.get('content',''))[:150]}" for m in recent)

        msgs = RAG_PROMPT.format_messages(
            context=context, question=state.get("user_query", ""), history=history or "No previous conversation",
        )
        logger.info("RAG calling LLM...")
        resp = await llm_router.ainvoke(msgs, state=state, config=config, temperature=0.0)
        logger.info("RAG LLM returned.")
        answer = getattr(resp, "content", str(resp))

        if state.get("fallback_reason") and not state.get("metadata", {}).get("fallback_notified"):
            state["metadata"]["fallback_notified"] = True
            answer = f"(Note: Gemini quota was reached; using a fallback model.)\n\n{answer}"

        state["final_answer"] = answer
        state["execution_status"] = "completed"
        state["confidence_score"] = state.get("intent_confidence", 0.8)
        state["messages"].append({"role": "assistant", "content": answer, "metadata": {"agent": "rag", "sources": doc_names}})
    except Exception as e:
        logger.exception("RAG execution error")
        state = normalize_state(state)
        state["errors"].append(f"RAG execution error: {e}")
        state["execution_status"] = "failed"
        state["final_answer"] = "I encountered an error while processing your document query. Please try again."
    return state


async def chat_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """Chat agent node."""
    state = normalize_state(state)
    try:
        history = ""
        if state.get("messages"):
            recent = (state.get("messages") or [])[-5:]
            history = "\n".join(f"{m.get('role','unknown')}: {str(m.get('content',''))[:200]}" for m in recent)

        msgs = CHAT_PROMPT.format_messages(history=history or "No previous conversation", query=state.get("user_query", ""))
        resp = await llm_router.ainvoke(msgs, state=state, config=config, temperature=0.7)
        answer = getattr(resp, "content", str(resp))

        state["final_answer"] = answer
        state["execution_status"] = "completed"
        state["confidence_score"] = 0.8
        state["messages"].append({"role": "assistant", "content": answer, "metadata": {"agent": "chat"}})
    except Exception as e:
        state = normalize_state(state)
        state["errors"].append(f"Chat execution error: {e}")
        state["execution_status"] = "failed"
        state["final_answer"] = "I encountered an error. Please try again."
    return state


async def research_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """Research agent node – web search + LLM synthesis."""
    import asyncio
    state = normalize_state(state)
    try:
        query = state.get("user_query", "")

        # Best-effort web search
        search_results = []
        try:
            from langchain_community.tools.tavily_search import TavilySearchResults
            from backend.app.config import get_settings
            
            tavily_key = get_settings().TAVILY_API_KEY
            if not tavily_key:
                logger.warning("TAVILY_API_KEY is not set. Skipping web search.")
            else:
                os.environ["TAVILY_API_KEY"] = tavily_key
                tool = TavilySearchResults(max_results=5)
                # Tavily returns list of dicts directly
                results = await asyncio.to_thread(tool.invoke, {"query": query})
                if isinstance(results, list):
                    for i, r in enumerate(results):
                        search_results.append({
                            "title": r.get("title", f"Result {i+1}"), 
                            "snippet": r.get("content", str(r))[:500], 
                            "url": r.get("url", "")
                        })
        except Exception as search_err:
            logger.warning("Web search failed: %s", search_err)

        state["research_results"] = search_results

        history = ""
        if state.get("messages"):
            recent = (state.get("messages") or [])[-3:]
            history = "\n".join(f"{m.get('role','unknown')}: {str(m.get('content',''))[:150]}" for m in recent)

        search_text = "\n\n".join(f"[{i+1}] {r.get('title','')}\n{r.get('snippet','')}" for i, r in enumerate(search_results))
        msgs = RESEARCH_PROMPT.format_messages(query=query, search_results=search_text or "No results found.", history=history or "No previous conversation")
        resp = await llm_router.ainvoke(msgs, state=state, config=config, temperature=0.3)
        answer = getattr(resp, "content", str(resp))

        if state.get("fallback_reason") and not state.get("metadata", {}).get("fallback_notified"):
            state["metadata"]["fallback_notified"] = True
            answer = f"(Note: Gemini quota was reached; using a fallback model.)\n\n{answer}"

        state["final_answer"] = answer
        state["execution_status"] = "completed"
        state["confidence_score"] = 0.75
        state["messages"].append({"role": "assistant", "content": answer, "metadata": {"agent": "research", "sources_count": len(search_results)}})
    except Exception as e:
        logger.exception("Research execution error")
        state = normalize_state(state)
        state["errors"].append(f"Research execution error: {e}")
        state["execution_status"] = "failed"
        state["final_answer"] = "I encountered an error while performing research. Please try again."
    return state


async def sql_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """SQL agent node (generates SQL via LLM)."""
    state = normalize_state(state)
    query = state.get("user_query", "")
    try:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a SQL expert. Write a valid SQL query to solve the user's task based on standard assumptions. Return ONLY the SQL query, without markdown blocks or explanations."),
            ("human", "{query}")
        ])
        sql_resp = await llm_router.ainvoke(prompt.format_messages(query=query), state=state, config=config, temperature=0.1)
        sql = getattr(sql_resp, "content", str(sql_resp)).replace("```sql", "").replace("```", "").strip()
        
        answer = f"I generated the SQL query for your task (No active DB configured to run it):\n\n```sql\n{sql}\n```"
        state["final_answer"] = answer
        state["execution_status"] = "completed"
        state["messages"].append({"role": "assistant", "content": answer, "metadata": {"agent": "sql"}})
    except Exception as e:
        state["errors"].append(f"SQL error: {e}")
        state["execution_status"] = "failed"
        state["final_answer"] = "SQL agent error."
    return state


async def code_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """Code agent node (generates & executes code via LLM)."""
    state = normalize_state(state)
    query = state.get("user_query", "")
    try:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a Python code generator. Write ONLY valid Python code to solve the user's task. Do not include markdown formatting or explanations. If you need to print output, use print()."),
            ("human", "{query}")
        ])
        code_resp = await llm_router.ainvoke(prompt.format_messages(query=query), state=state, config=config, temperature=0.1)
        code = getattr(code_resp, "content", str(code_resp)).replace("```python", "").replace("```", "").strip()
        
        # Execute it
        import sys, io
        old_stdout = sys.stdout
        redirected_output = sys.stdout = io.StringIO()
        try:
            exec(code, {})
        except Exception as exec_err:
            print(f"Error executing code: {exec_err}")
        finally:
            sys.stdout = old_stdout
            
        output = redirected_output.getvalue()
        
        answer = f"**Generated Code:**\n```python\n{code}\n```\n\n**Output:**\n```text\n{output or 'Code executed successfully with no output.'}\n```"
        state["final_answer"] = answer
        state["execution_status"] = "completed"
        state["messages"].append({"role": "assistant", "content": answer, "metadata": {"agent": "code"}})
    except Exception as e:
        state["errors"].append(f"Code error: {e}")
        state["execution_status"] = "failed"
        state["final_answer"] = "Code agent error."
    return state


async def approval_gate_node(state: OrchestratorState) -> OrchestratorState:
    state = normalize_state(state)
    if state.get("approved", False):
        state["execution_status"] = "approved"
        state["should_continue"] = True
    else:
        state["execution_status"] = "requires_approval"
        state["requires_human_input"] = True
        state["should_continue"] = False
    return state


async def retry_handler_node(state: OrchestratorState) -> OrchestratorState:
    state = normalize_state(state)
    if state.get("retry_count", 0) < state.get("max_retries", 3):
        state["should_continue"] = True
    else:
        state["should_continue"] = False
        state["execution_status"] = "failed"
    return state


async def graceful_fallback_node(state: OrchestratorState) -> OrchestratorState:
    state = normalize_state(state)
    query = state.get("user_query", "")
    blocked_reason = state.get("metadata", {}).get("blocked_reason", "Agent unavailable")
    response = f"I understand you're asking about: '{query}'\n\nHowever, execution was blocked: {blocked_reason}.\nI can help with general questions or document queries instead."
    state["final_answer"] = response
    state["execution_status"] = "completed"
    state["messages"].append({"role": "assistant", "content": response, "metadata": {"agent": "graceful_fallback"}})
    return state


async def fallback_node(state: OrchestratorState, config: RunnableConfig) -> OrchestratorState:
    """Fallback – route to chat for a direct answer."""
    state = normalize_state(state)
    try:
        return await chat_node(state, config)
    except Exception as e:
        state["errors"].append(f"Fallback error: {e}")
        state["final_answer"] = "I apologize, but I encountered an error. Please try again."
        state["execution_status"] = "failed"
        return state


# ── Routing helpers ──

def route_after_agent(state: OrchestratorState) -> str:
    if state.get("approval_required", False) and not state.get("approved", False):
        return "approval_gate"
    if state.get("should_continue", False) and state.get("retry_count", 0) < state.get("max_retries", 3):
        return "retry_handler"
    return "end"


def route_after_approval(state: OrchestratorState) -> str:
    if state.get("approved", False):
        intent = state.get("intent")
        if intent == "sql":
            return "sql_agent"
        elif intent == "code":
            return "code_agent"
    return "end"


def route_after_retry(state: OrchestratorState) -> str:
    if state.get("should_continue", False):
        intent = state.get("intent")
        if intent == "sql":
            return "sql_agent"
        elif intent == "code":
            return "code_agent"
    return "end"


# ===================================================================
# Build & compile the graph
# ===================================================================

def build_graph() -> Any:
    """Construct and compile the LangGraph workflow."""
    workflow = StateGraph(OrchestratorState)

    workflow.add_node("load_persistent_context", load_persistent_context_node)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("rag_agent", rag_node)
    workflow.add_node("sql_agent", sql_node)
    workflow.add_node("code_agent", code_node)
    workflow.add_node("research_agent", research_node)
    workflow.add_node("chat_agent", chat_node)
    workflow.add_node("save_persistent_context", save_persistent_context_node)
    workflow.add_node("approval_gate", approval_gate_node)
    workflow.add_node("retry_handler", retry_handler_node)
    workflow.add_node("graceful_fallback", graceful_fallback_node)
    workflow.add_node("fallback_handler", fallback_node)

    workflow.set_entry_point("load_persistent_context")
    workflow.add_edge("load_persistent_context", "classify_intent")

    workflow.add_conditional_edges(
        "classify_intent",
        route_after_classification,
        {
            "rag_agent": "rag_agent",
            "sql_agent": "sql_agent",
            "code_agent": "code_agent",
            "research_agent": "research_agent",
            "chat_agent": "chat_agent",
            "graceful_fallback": "graceful_fallback",
            "fallback_handler": "fallback_handler",
        },
    )

    for agent in ["rag_agent", "sql_agent", "code_agent", "research_agent", "chat_agent"]:
        workflow.add_conditional_edges(
            agent,
            route_after_agent,
            {"approval_gate": "approval_gate", "retry_handler": "retry_handler", "end": "save_persistent_context"},
        )

    workflow.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {"sql_agent": "sql_agent", "code_agent": "code_agent", "end": "save_persistent_context"},
    )
    workflow.add_conditional_edges(
        "retry_handler",
        route_after_retry,
        {"sql_agent": "sql_agent", "code_agent": "code_agent", "end": "save_persistent_context"},
    )

    workflow.add_edge("graceful_fallback", "save_persistent_context")
    workflow.add_edge("fallback_handler", "save_persistent_context")
    workflow.add_edge("save_persistent_context", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# Module-level compiled graph – import this from the API layer
app_graph = build_graph()
