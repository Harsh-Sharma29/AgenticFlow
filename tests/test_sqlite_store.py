"""Tests for SQLite storage layer — chat, workspaces, documents, sessions."""

import pytest
from storage.sqlite_store import (
    init_db,
    load_chat_messages,
    append_chat_messages,
    upsert_document,
    list_workspace_documents,
    create_workspace,
    list_workspaces,
    rename_workspace,
    create_chat_session,
    list_chat_sessions,
    update_chat_session_name,
    delete_chat_session,
)


class TestChatMessages:
    """Test chat message persistence."""

    def test_append_and_load_messages(self, temp_db):
        init_db(temp_db)
        
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        append_chat_messages("user1", "ws1", "session1", messages, db_path=temp_db)
        
        loaded = load_chat_messages("user1", "ws1", "session1", db_path=temp_db)
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "Hello!"
        assert loaded[1]["role"] == "assistant"

    def test_empty_messages_noop(self, temp_db):
        init_db(temp_db)
        append_chat_messages("user1", "ws1", "session1", [], db_path=temp_db)
        loaded = load_chat_messages("user1", "ws1", "session1", db_path=temp_db)
        assert len(loaded) == 0

    def test_messages_scoped_by_workspace(self, temp_db):
        init_db(temp_db)
        
        append_chat_messages("user1", "ws1", "s1", [{"role": "user", "content": "WS1 msg"}], db_path=temp_db)
        append_chat_messages("user1", "ws2", "s2", [{"role": "user", "content": "WS2 msg"}], db_path=temp_db)
        
        ws1_msgs = load_chat_messages("user1", "ws1", "s1", db_path=temp_db)
        ws2_msgs = load_chat_messages("user1", "ws2", "s2", db_path=temp_db)
        
        assert len(ws1_msgs) == 1
        assert ws1_msgs[0]["content"] == "WS1 msg"
        assert len(ws2_msgs) == 1
        assert ws2_msgs[0]["content"] == "WS2 msg"

    def test_messages_scoped_by_user(self, temp_db):
        init_db(temp_db)
        
        append_chat_messages("alice", "ws1", "s1", [{"role": "user", "content": "Alice msg"}], db_path=temp_db)
        append_chat_messages("bob", "ws1", "s2", [{"role": "user", "content": "Bob msg"}], db_path=temp_db)
        
        alice_msgs = load_chat_messages("alice", "ws1", "s1", db_path=temp_db)
        bob_msgs = load_chat_messages("bob", "ws1", "s2", db_path=temp_db)
        
        assert len(alice_msgs) == 1
        assert alice_msgs[0]["content"] == "Alice msg"

    def test_message_limit(self, temp_db):
        init_db(temp_db)
        
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(20)]
        append_chat_messages("user1", "ws1", "s1", messages, db_path=temp_db)
        
        limited = load_chat_messages("user1", "ws1", "s1", limit=5, db_path=temp_db)
        assert len(limited) == 5

    def test_skips_empty_content_messages(self, temp_db):
        init_db(temp_db)
        messages = [
            {"role": "user", "content": "Valid"},
            {"role": "user", "content": ""},
            {"role": "user"},  # No content key
        ]
        append_chat_messages("user1", "ws1", "s1", messages, db_path=temp_db)
        loaded = load_chat_messages("user1", "ws1", "s1", db_path=temp_db)
        assert len(loaded) == 1  # Only "Valid" saved


class TestDocuments:
    """Test document workspace persistence."""

    def test_upsert_and_list(self, temp_db):
        init_db(temp_db)
        
        upsert_document("user1", "ws1", "doc1", "/path/to/file.pdf", "/idx/path", db_path=temp_db)
        docs, index_path = list_workspace_documents("user1", "ws1", db_path=temp_db)
        
        assert len(docs) == 1
        assert docs[0]["doc_id"] == "doc1"
        assert docs[0]["file_path"] == "/path/to/file.pdf"
        assert index_path == "/idx/path"

    def test_upsert_updates_existing(self, temp_db):
        init_db(temp_db)
        
        upsert_document("user1", "ws1", "doc1", "/old/path.pdf", "/idx1", db_path=temp_db)
        upsert_document("user1", "ws1", "doc1", "/new/path.pdf", "/idx2", db_path=temp_db)
        
        docs, index_path = list_workspace_documents("user1", "ws1", db_path=temp_db)
        assert len(docs) == 1
        assert docs[0]["file_path"] == "/new/path.pdf"

    def test_documents_scoped_by_workspace(self, temp_db):
        init_db(temp_db)
        
        upsert_document("user1", "ws1", "doc1", "/a.pdf", "/idx1", db_path=temp_db)
        upsert_document("user1", "ws2", "doc2", "/b.pdf", "/idx2", db_path=temp_db)
        
        docs1, _ = list_workspace_documents("user1", "ws1", db_path=temp_db)
        docs2, _ = list_workspace_documents("user1", "ws2", db_path=temp_db)
        
        assert len(docs1) == 1
        assert len(docs2) == 1

    def test_empty_workspace_returns_none_path(self, temp_db):
        init_db(temp_db)
        docs, index_path = list_workspace_documents("user1", "empty_ws", db_path=temp_db)
        assert len(docs) == 0
        assert index_path is None


class TestWorkspaces:
    """Test workspace CRUD."""

    def test_create_and_list_workspaces(self, temp_db):
        init_db(temp_db)
        
        ws_id = create_workspace("user1", "My Project", db_path=temp_db)
        assert ws_id is not None
        
        workspaces = list_workspaces("user1", db_path=temp_db)
        assert any(w["name"] == "My Project" for w in workspaces)

    def test_default_workspace_created_if_none(self, temp_db):
        init_db(temp_db)
        workspaces = list_workspaces("new_user", db_path=temp_db)
        assert len(workspaces) >= 1
        assert any("default" in w["name"].lower() for w in workspaces)

    def test_rename_workspace(self, temp_db):
        init_db(temp_db)
        ws_id = create_workspace("user1", "Old Name", db_path=temp_db)
        rename_workspace(ws_id, "New Name", db_path=temp_db)
        
        workspaces = list_workspaces("user1", db_path=temp_db)
        assert any(w["name"] == "New Name" for w in workspaces)


class TestChatSessions:
    """Test chat session management."""

    def test_create_and_list_sessions(self, temp_db):
        init_db(temp_db)
        
        session_id = create_chat_session("user1", "ws1", name="Test Chat", db_path=temp_db)
        sessions = list_chat_sessions("user1", "ws1", db_path=temp_db)
        
        assert len(sessions) >= 1
        assert any(s["session_id"] == session_id for s in sessions)

    def test_update_session_name(self, temp_db):
        init_db(temp_db)
        session_id = create_chat_session("user1", "ws1", name="Old", db_path=temp_db)
        update_chat_session_name(session_id, "Renamed Session", db_path=temp_db)
        
        sessions = list_chat_sessions("user1", "ws1", db_path=temp_db)
        matching = [s for s in sessions if s["session_id"] == session_id]
        assert matching[0]["name"] == "Renamed Session"

    def test_delete_session(self, temp_db):
        init_db(temp_db)
        session_id = create_chat_session("user1", "ws1", name="To Delete", db_path=temp_db)
        
        # Add some messages
        append_chat_messages("user1", "ws1", session_id, [
            {"role": "user", "content": "Hello"}
        ], db_path=temp_db)
        
        delete_chat_session(session_id, db_path=temp_db)
        
        # Session and messages should be gone
        sessions = list_chat_sessions("user1", "ws1", db_path=temp_db)
        assert not any(s["session_id"] == session_id for s in sessions)
        
        msgs = load_chat_messages("user1", "ws1", session_id, db_path=temp_db)
        assert len(msgs) == 0

    def test_auto_name_from_first_message(self, temp_db):
        init_db(temp_db)
        
        # Append messages to a new session (auto-creates session)
        append_chat_messages("user1", "ws1", "auto-session", [
            {"role": "user", "content": "What is machine learning?"}
        ], db_path=temp_db)
        
        sessions = list_chat_sessions("user1", "ws1", db_path=temp_db)
        matching = [s for s in sessions if s["session_id"] == "auto-session"]
        assert len(matching) == 1
        # Name should be truncated first message
        assert "machine learning" in matching[0]["name"].lower()
