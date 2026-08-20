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
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [guestMessageCount, setGuestMessageCount] = useState(0);
  const [showPaywall, setShowPaywall] = useState(false);
  const [shareStatus, setShareStatus] = useState('');
  const [theme, setTheme] = useState('dark');
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    const savedTheme = localStorage.getItem('agenticflow_theme');
    if (savedTheme) {
      setTheme(savedTheme);
      document.documentElement.className = savedTheme;
    } else {
      document.documentElement.className = 'dark';
    }
  }, []);

  const toggleTheme = () => {
    const newTheme = theme === 'dark' ? 'light' : 'dark';
    setTheme(newTheme);
    localStorage.setItem('agenticflow_theme', newTheme);
    document.documentElement.className = newTheme;
  };

  useEffect(() => {
    const savedToken = localStorage.getItem('agenticflow_token');
    const savedUser = localStorage.getItem('agenticflow_user');
    const savedGuestCount = localStorage.getItem('agenticflow_guest_count');
    
    if (savedToken && savedUser) {
      setToken(savedToken);
      setUser(JSON.parse(savedUser));
    }
    if (savedGuestCount) {
      setGuestMessageCount(parseInt(savedGuestCount, 10));
    }
  }, []);

  const getGuestId = () => {
    let gid = localStorage.getItem('agenticflow_guest_id');
    if (!gid) {
      gid = 'guest_' + crypto.randomUUID();
      localStorage.setItem('agenticflow_guest_id', gid);
    }
    return gid;
  };

  const authHeaders = () => {
    return token ? { 'Authorization': `Bearer ${token}` } : { 'X-Guest-ID': getGuestId() };
  };

  const handleLogout = () => {
    localStorage.removeItem('agenticflow_token');
    localStorage.removeItem('agenticflow_user');
    setToken(null);
    setUser(null);
    setSessions([]);
    startNewChat();
  };

  const fetchSessions = async (ws) => {
    try {
      const res = await fetch(`http://127.0.0.1:8005/api/sessions?workspace_id=${ws}`, {
        headers: authHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
      }
    } catch (e) {
      console.error('Failed to fetch sessions', e);
    }
  };

  useEffect(() => {
    if (token) {
      fetchSessions(workspaceId);
    } else {
      setSessions([]);
    }
  }, [workspaceId, token]);

  const loadSession = async (sessionId) => {
    try {
      setIsLoading(true);
      setLoadingStatus('Loading session...');
      const res = await fetch(`http://127.0.0.1:8005/api/sessions/${sessionId}?workspace_id=${workspaceId}`, {
        headers: authHeaders()
      });
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
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
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
          method: 'DELETE',
          headers: authHeaders()
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
        headers: authHeaders(),
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

  const handleShareChat = async () => {
    if (messages.length === 0) return;
    
    // Format messages for sharing
    const chatText = messages.map(m => {
      const role = m.role === 'user' ? 'User' : 'AgenticFlow';
      return `**${role}**:\n${m.content}\n`;
    }).join('\n---\n\n');
    
    try {
      await navigator.clipboard.writeText(chatText);
      setShareStatus('Copied to clipboard!');
      setTimeout(() => setShareStatus(''), 2000);
    } catch (err) {
      console.error('Failed to copy', err);
      setShareStatus('Failed to copy');
      setTimeout(() => setShareStatus(''), 2000);
    }
  };

  const handleExportPDF = async () => {
    if (messages.length === 0) return;
    setShareStatus('Generating PDF...');
    
    try {
      const html2pdf = (await import('html2pdf.js')).default;
      const element = document.querySelector('.chat-messages');
      const opt = {
        margin:       10,
        filename:     'agenticflow-chat.pdf',
        image:        { type: 'jpeg', quality: 0.98 },
        html2canvas:  { scale: 2, useCORS: true },
        jsPDF:        { unit: 'mm', format: 'a4', orientation: 'portrait' }
      };
      await html2pdf().set(opt).from(element).save();
      setShareStatus('Downloaded!');
    } catch (err) {
      console.error('Failed to export PDF', err);
      setShareStatus('Export failed');
    }
    setTimeout(() => setShareStatus(''), 3000);
  };

  // Group sessions by date
  const groupedSessions = (() => {
    const groups = { 'Today': [], 'Yesterday': [], 'Previous 7 Days': [], 'Older': [] };
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const sevenDaysAgo = new Date(today);
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

    sessions.forEach(s => {
      const dStr = s.created_at || s.updated_at;
      const date = dStr ? new Date(dStr) : new Date(); 
      date.setHours(0,0,0,0);
      
      if (date.getTime() === today.getTime()) {
        groups['Today'].push(s);
      } else if (date.getTime() === yesterday.getTime()) {
        groups['Yesterday'].push(s);
      } else if (date > sevenDaysAgo) {
        groups['Previous 7 Days'].push(s);
      } else {
        groups['Older'].push(s);
      }
    });
    return groups;
  })();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() && uploadedFiles.length === 0) return;

    // Soft Paywall for Guests
    if (!user && guestMessageCount >= 5) {
      setShowPaywall(true);
      return;
    }

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

    // Increment guest count
    if (!user) {
      const newCount = guestMessageCount + 1;
      setGuestMessageCount(newCount);
      localStorage.setItem('agenticflow_guest_count', newCount);
    }

    const sessionIdToUse = currentSessionId || crypto.randomUUID();
    if (!currentSessionId) setCurrentSessionId(sessionIdToUse);

    try {
      // Direct call to FastAPI backend streaming endpoint
      const res = await fetch('http://127.0.0.1:8005/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ 
          message: userMessage.content,
          user_id: user ? user.id : getGuestId(),
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
            <div className="sidebar-title">AgenticFlow</div>
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
          {Object.entries(groupedSessions).map(([label, items]) => {
            if (items.length === 0) return null;
            return (
              <div key={label} className="session-group">
                <div className="session-section-label">{label}</div>
                {items.map(s => (
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
              </div>
            );
          })}
          
          {sessions.length === 0 && (
            <div className="session-item" style={{opacity: 0.5}}>
              <span className="session-item-text">No recent chats</span>
            </div>
          )}
        </div>
        
        <div className="sidebar-footer">
          {user ? (
            <>
              <div className="sidebar-footer-item" style={{cursor: 'default'}}>
                <span>👤</span> {user.name || 'User'}
              </div>
              <div className="sidebar-footer-item" onClick={handleLogout}>
                <span>🚪</span> Logout
              </div>
            </>
          ) : (
            <div className="sidebar-footer-item" onClick={() => window.location.href = '/login'}>
              <span>🔑</span> Sign In / Register
            </div>
          )}
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
            <div className="chat-header-title">AgenticFlow Orchestrator</div>
            <div className="chat-header-badge">Enterprise Edition</div>
          </div>
          <div className="chat-header-right" style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {shareStatus && <span style={{ fontSize: '12px', color: '#10b981', fontWeight: 500 }}>{shareStatus}</span>}
            
            <button className="header-icon-btn" title="Toggle Theme" onClick={toggleTheme}>
              {theme === 'dark' ? (
                <svg stroke="currentColor" fill="none" strokeWidth="2" viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" height="18" width="18" xmlns="http://www.w3.org/2000/svg">
                  <circle cx="12" cy="12" r="5"></circle>
                  <line x1="12" y1="1" x2="12" y2="3"></line>
                  <line x1="12" y1="21" x2="12" y2="23"></line>
                  <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                  <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                  <line x1="1" y1="12" x2="3" y2="12"></line>
                  <line x1="21" y1="12" x2="23" y2="12"></line>
                  <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                  <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
                </svg>
              ) : (
                <svg stroke="currentColor" fill="none" strokeWidth="2" viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" height="18" width="18" xmlns="http://www.w3.org/2000/svg">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
                </svg>
              )}
            </button>
            
            <button className="header-icon-btn" title="Export to PDF" onClick={handleExportPDF}>
              <svg stroke="currentColor" fill="none" strokeWidth="2" viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" height="18" width="18" xmlns="http://www.w3.org/2000/svg">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
            </button>
            
            <button className="header-icon-btn" title="Share Chat" onClick={handleShareChat}>
              <svg stroke="currentColor" fill="none" strokeWidth="2" viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" height="18" width="18" xmlns="http://www.w3.org/2000/svg">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"></path>
                <polyline points="16 6 12 2 8 6"></polyline>
                <line x1="12" y1="2" x2="12" y2="15"></line>
              </svg>
            </button>
          </div>
        </header>

        {/* Messages */}
        <div className="chat-messages">
          {messages.length === 0 ? (
            <div className="welcome-screen">
              <div className="welcome-icon logo-glow" style={{ marginBottom: '20px' }}>
                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 2L2 7L12 12L22 7L12 2Z" fill="url(#paint0_linear)" />
                  <path d="M2 17L12 22L22 17" stroke="url(#paint1_linear)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  <path d="M2 12L12 17L22 12" stroke="url(#paint2_linear)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  <defs>
                    <linearGradient id="paint0_linear" x1="2" y1="7" x2="22" y2="7" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#F97316"/>
                      <stop offset="1" stopColor="#EA580C"/>
                    </linearGradient>
                    <linearGradient id="paint1_linear" x1="2" y1="19.5" x2="22" y2="19.5" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#FDBA74"/>
                      <stop offset="1" stopColor="#F97316"/>
                    </linearGradient>
                    <linearGradient id="paint2_linear" x1="2" y1="14.5" x2="22" y2="14.5" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#FDBA74"/>
                      <stop offset="1" stopColor="#F97316"/>
                    </linearGradient>
                  </defs>
                </svg>
              </div>
              <h1 className="welcome-title text-transparent bg-clip-text" style={{ backgroundImage: 'var(--accent-gradient)' }}>
                How can I help you today?
              </h1>
              <p className="welcome-subtitle">
                I am your intelligent orchestration engine. I can synthesize documents, execute data queries, and conduct live web research.
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
                  {msg.role === 'user' ? 'U' : 'AF'}
                </div>
                <div className="message-body">
                  <div className="message-sender">
                    <span className="name">{msg.role === 'user' ? 'You' : 'AgenticFlow'}</span>
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
                      {msg.intent === 'research' && (
                        <span className="meta-badge badge-research" style={{background: 'linear-gradient(to right, #3b82f6, #14b8a6)', color: 'white', border: 'none'}}>
                          🌐 Browsing Web
                        </span>
                      )}
                      {msg.intent !== 'research' && (
                        <span className={`meta-badge intent-${msg.intent}`}>
                          {msg.intent} Agent
                        </span>
                      )}
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
                  <span className="name">AgenticFlow</span>
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
                placeholder="Ask AgenticFlow anything (RAG, SQL, Code, Research)..."
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

      {/* Soft Paywall Modal */}
      {showPaywall && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(10px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999
        }}>
          <div style={{
            background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
            padding: '40px', borderRadius: '24px', maxWidth: '400px', textAlign: 'center',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
          }}>
            <h2 style={{ fontSize: '24px', fontWeight: 'bold', marginBottom: '16px', background: 'linear-gradient(to right, #FDBA74, #F97316)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              Guest Limit Reached
            </h2>
            <p style={{ color: '#9ca3af', marginBottom: '32px', lineHeight: '1.5' }}>
              You&apos;ve sent 5 messages as a guest. To continue chatting and save your documents permanently, please create a free account.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <button 
                onClick={() => window.location.href = '/register'}
                style={{ background: 'linear-gradient(to right, #EA580C, #C2410C)', color: 'white', padding: '12px', borderRadius: '12px', border: 'none', cursor: 'pointer', fontWeight: '500' }}
              >
                Create Free Account
              </button>
              <button 
                onClick={() => window.location.href = '/login'}
                style={{ background: 'transparent', color: '#9ca3af', padding: '12px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.1)', cursor: 'pointer', fontWeight: '500' }}
              >
                Sign In
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
