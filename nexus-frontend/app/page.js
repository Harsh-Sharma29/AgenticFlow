'use client';

import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function Home() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState('Agent is working...');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [workspaceId] = useState('default');
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);

  const fetchSessions = async (ws) => {
    try {
      const res = await fetch(`http://127.0.0.1:8005/api/sessions?workspace_id=${ws}`);
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
      }
    } catch (e) {
      console.error('Failed to fetch sessions', e);
    }
  };

  useEffect(() => {
    fetchSessions(workspaceId);
  }, [workspaceId]);

  const loadSession = async (sessionId) => {
    try {
      setIsLoading(true);
      setLoadingStatus('Loading session...');
      const res = await fetch(`http://127.0.0.1:8005/api/sessions/${sessionId}?workspace_id=${workspaceId}`);
      if (res.ok) {
        const data = await res.json();
        setMessages(data.messages || []);
        setCurrentSessionId(sessionId);
      }
    } catch (e) {
      console.error('Failed to load session', e);
    } finally {
      setIsLoading(false);
      setSidebarOpen(false);
    }
  };

  const startNewChat = () => {
    setMessages([]);
    setCurrentSessionId(null);
    setUploadedFiles([]);
    setSidebarOpen(false);
  };

  const handleRenameSession = async (e, sessionId, currentName) => {
    e.stopPropagation();
    const newName = prompt('Enter new session name:', currentName);
    if (newName && newName.trim() !== '' && newName !== currentName) {
      try {
        await fetch(`http://127.0.0.1:8005/api/sessions/${sessionId}?workspace_id=${workspaceId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName.trim() })
        });
        fetchSessions(workspaceId);
      } catch (err) {
        console.error('Failed to rename session', err);
      }
    }
  };

  const handleDeleteSession = async (e, sessionId) => {
    e.stopPropagation();
    if (confirm('Are you sure you want to delete this chat session?')) {
      try {
        await fetch(`http://127.0.0.1:8005/api/sessions/${sessionId}?workspace_id=${workspaceId}`, {
          method: 'DELETE'
        });
        if (currentSessionId === sessionId) {
          startNewChat();
        }
        fetchSessions(workspaceId);
      } catch (err) {
        console.error('Failed to delete session', err);
      }
    }
  };
  
  // Auto-scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };
  
  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('http://127.0.0.1:8005/api/upload', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error('Upload failed');
      
      const data = await res.json();
      setUploadedFiles(prev => [...prev, { name: file.name, path: data.file_path }]);
    } catch (error) {
      console.error('Upload error:', error);
      alert('Failed to upload file.');
    } finally {
      setIsUploading(false);
      // Reset input
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const removeFile = (index) => {
    setUploadedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async (e) => {
    e?.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = { 
      role: 'user', 
      content: input,
      attachedDocs: uploadedFiles.map(f => f.name),
      timestamp: new Date().toISOString()
    };
    
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);
    setLoadingStatus('Connecting to agent...');

    const sessionIdToUse = currentSessionId || crypto.randomUUID();
    if (!currentSessionId) setCurrentSessionId(sessionIdToUse);

    try {
      // Direct call to FastAPI backend streaming endpoint
      const res = await fetch('http://127.0.0.1:8005/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          message: userMessage.content,
          user_id: 'guest',
          workspace_id: workspaceId,
          session_id: sessionIdToUse,
          tenant_id: 'default',
          uploaded_docs: uploadedFiles.map(f => f.path)
        }),
      });

      // Clear files after sending the message
      setUploadedFiles([]);

      if (!res.ok) throw new Error('API request failed');
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      
      let assistantContent = '';
      let assistantSources = [];
      let hasStartedStreaming = false;
      let done = false;
      
      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        
        if (value) {
          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split('\n');
          
          for (const line of lines) {
            if (line.startsWith('data: ') && line !== 'data: [DONE]') {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.type === 'metadata') {
                  setMessages(prev => {
                    const newMessages = [...prev];
                    const lastMsg = newMessages[newMessages.length - 1];
                    if (lastMsg && lastMsg.role === 'assistant') {
                      lastMsg.intent = data.intent;
                      lastMsg.confidence = data.confidence;
                      lastMsg.model = data.model;
                    }
                    return newMessages;
                  });
                } else if (data.type === 'status') {
                  setLoadingStatus(data.message);
                } else if (data.type === 'sources') {
                  assistantSources = data.documents || [];
                } else if (data.content) {
                  if (!hasStartedStreaming) {
                    hasStartedStreaming = true;
                    // Add the assistant message and hide the loading indicator
                    setMessages(prev => [...prev, {
                      role: 'assistant',
                      content: '',
                      sources: assistantSources,
                      timestamp: new Date().toISOString()
                    }]);
                    setIsLoading(false);
                  }
                  assistantContent += data.content;
                  setMessages(prev => {
                    const newMessages = [...prev];
                    const lastMsg = newMessages[newMessages.length - 1];
                    if (lastMsg && lastMsg.role === 'assistant') {
                      lastMsg.content = assistantContent;
                      lastMsg.sources = assistantSources;
                    }
                    return newMessages;
                  });
                }
              } catch (e) {
                // Ignore parse errors from partial chunks if any
              }
            }
          }
        }
      }

      // If we never streamed anything (e.g. fallback answer was sent as content),
      // make sure the loading indicator is off
      if (!hasStartedStreaming && assistantContent) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: assistantContent,
          sources: assistantSources,
          timestamp: new Date().toISOString()
        }]);
      }
      
    } catch (error) {
      console.error(error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'I encountered an error connecting to the orchestrator.',
        intent: 'error',
        timestamp: new Date().toISOString()
      }]);
    } finally {
      setIsLoading(false);
      fetchSessions(workspaceId); // Refresh sidebar sessions
    }
  };

  const toggleSidebar = () => setSidebarOpen(!sidebarOpen);

  return (
    <>
      {/* Sidebar */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
          <div className="sidebar-logo">Nx</div>
          <div>
            <div className="sidebar-title">Nexus AI</div>
            <div className="sidebar-subtitle">Orchestrator</div>
          </div>
        </div>
        
        <button className="new-chat-btn" onClick={startNewChat}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          New Chat
        </button>

        <div className="session-list">
          <div className="session-section-label">Recent Sessions</div>
          {sessions.map(s => (
            <div 
              key={s.session_id} 
              className={`session-item ${currentSessionId === s.session_id ? 'active' : ''}`}
              onClick={() => loadSession(s.session_id)}
            >
              <span className="session-item-icon">💬</span>
              <span className="session-item-text">{s.name}</span>
              <div className="session-item-actions">
                <button onClick={(e) => handleRenameSession(e, s.session_id, s.name)} title="Rename">✏️</button>
                <button onClick={(e) => handleDeleteSession(e, s.session_id)} title="Delete">🗑️</button>
              </div>
            </div>
          ))}
          {sessions.length === 0 && (
            <div className="session-item" style={{opacity: 0.5}}>
              <span className="session-item-text">No recent chats</span>
            </div>
          )}
        </div>
        
        <div className="sidebar-footer">
          <div className="sidebar-footer-item">
            <span>⚙️</span> Settings
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {/* Header */}
        <header className="chat-header">
          <div className="chat-header-left">
            <button className="header-icon-btn d-md-none" onClick={toggleSidebar} style={{ display: 'none' }}>
              ≡
            </button>
            <div className="chat-header-title">Nexus Orchestrator</div>
            <div className="chat-header-badge">Enterprise Edition</div>
          </div>
          <div className="chat-header-right">
            <button className="header-icon-btn" title="Model Info">ℹ️</button>
          </div>
        </header>

        {/* Messages */}
        <div className="chat-messages">
          {messages.length === 0 ? (
            <div className="welcome-screen">
              <div className="welcome-icon">🤖</div>
              <h1 className="welcome-title">How can I help you today?</h1>
              <p className="welcome-subtitle">
                Nexus is an enterprise-grade AI orchestrator. I can analyze documents (RAG), 
                run SQL queries, execute code, and perform web research.
              </p>
              
              <div className="suggestions-grid">
                <div className="suggestion-card" onClick={() => setInput('Summarize the latest trends in AI')}>
                  <div className="suggestion-icon">🌐</div>
                  <div className="suggestion-text">Research latest AI trends</div>
                </div>
                <div className="suggestion-card" onClick={() => setInput('Write a python script to calculate fibonacci numbers')}>
                  <div className="suggestion-icon">💻</div>
                  <div className="suggestion-text">Generate Python code</div>
                </div>
                <div className="suggestion-card" onClick={() => setInput('What is the total revenue for Q3? (Requires DB)')}>
                  <div className="suggestion-icon">📊</div>
                  <div className="suggestion-text">Query database</div>
                </div>
                <div className="suggestion-card" onClick={() => setInput('Explain the main concepts in the uploaded document')}>
                  <div className="suggestion-icon">📄</div>
                  <div className="suggestion-text">Analyze documents</div>
                </div>
              </div>
            </div>
          ) : (
            messages.map((msg, idx) => (
              <div key={idx} className="message-group">
                <div className={`message-avatar ${msg.role}`}>
                  {msg.role === 'user' ? 'U' : 'Nx'}
                </div>
                <div className="message-body">
                  <div className="message-sender">
                    <span className="name">{msg.role === 'user' ? 'You' : 'Nexus AI'}</span>
                    <span className="timestamp">
                      {new Date(msg.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                    </span>
                  </div>

                  {/* Show attached documents for user messages */}
                  {msg.role === 'user' && msg.attachedDocs && msg.attachedDocs.length > 0 && (
                    <div className="message-attached-docs">
                      {msg.attachedDocs.map((docName, i) => (
                        <span key={i} className="attached-doc-badge">📄 {docName}</span>
                      ))}
                    </div>
                  )}
                  
                  <div className="message-content">
                    {msg.role === 'assistant' ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content}
                      </ReactMarkdown>
                    ) : (
                      <p>{msg.content}</p>
                    )}
                  </div>

                  {/* Show source documents for assistant RAG responses */}
                  {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                    <div className="message-sources">
                      <span className="sources-label">📚 Sources:</span>
                      {msg.sources.map((src, i) => (
                        <span key={i} className="source-badge">{src}</span>
                      ))}
                    </div>
                  )}

                  {msg.role === 'assistant' && msg.intent && (
                    <div className="message-meta">
                      <span className={`meta-badge intent-${msg.intent}`}>
                        {msg.intent} Agent
                      </span>
                      {msg.model && (
                        <span className="meta-model">
                          Model: {msg.model} {msg.fallback ? '(Fallback used)' : ''}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          
          {isLoading && (
            <div className="message-group">
              <div className="message-avatar assistant">Nx</div>
              <div className="message-body">
                <div className="message-sender">
                  <span className="name">Nexus AI</span>
                </div>
                <div className="typing-indicator">
                  <div className="typing-dots">
                    <div className="typing-dot"></div>
                    <div className="typing-dot"></div>
                    <div className="typing-dot"></div>
                  </div>
                  <span className="typing-status">{loadingStatus}</span>
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="input-area">
          <div className="input-container">
            {uploadedFiles.length > 0 && (
              <div className="uploaded-files">
                {uploadedFiles.map((file, idx) => (
                  <div key={idx} className="uploaded-file-chip">
                    📄 {file.name}
                    <button type="button" onClick={() => removeFile(idx)}>✕</button>
                  </div>
                ))}
              </div>
            )}
            <form className="input-wrapper" onSubmit={handleSubmit}>
              <input 
                type="file" 
                ref={fileInputRef} 
                onChange={handleFileUpload} 
                style={{ display: 'none' }} 
                accept=".pdf,.txt,.md"
              />
              <button 
                type="button" 
                className="upload-btn" 
                title="Upload Document (PDF, TXT, MD)"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
              >
                {isUploading ? '⌛' : '📎'}
              </button>
              
              <textarea 
                className="chat-input"
                placeholder="Ask Nexus anything (RAG, SQL, Code, Research)..."
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit(e);
                  }
                }}
                rows={1}
                disabled={isLoading}
              />
              
              <button 
                type="submit" 
                className="send-btn" 
                disabled={!input.trim() || isLoading}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"></line>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                </svg>
              </button>
            </form>
            <div className="input-footer">
              <div className="input-footer-text">
                Press Enter to send, Shift+Enter for new line. AI can make mistakes. Check important info.
              </div>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
