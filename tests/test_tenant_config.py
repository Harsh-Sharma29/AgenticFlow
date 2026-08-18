"""Tests for Tenant Configuration — tiers, access control, validation."""

import pytest
from config.tenant_config import TenantConfig, TenantTier, TenantConfigManager


class TestTenantConfig:
    """Test tenant configuration defaults."""

    def test_free_tier_defaults(self):
        config = TenantConfig(tenant_id="free-user", tier=TenantTier.FREE)
        assert config.allowed_agents == ["chat", "rag"]
        assert config.enable_code_execution is False
        assert config.enable_sql_execution is False

    def test_basic_tier_defaults(self):
        config = TenantConfig(tenant_id="basic-user", tier=TenantTier.BASIC)
        assert "research" in config.allowed_agents
        assert "sql" not in config.allowed_agents

    def test_pro_tier_defaults(self):
        config = TenantConfig(tenant_id="pro-user", tier=TenantTier.PRO)
        assert "sql" in config.allowed_agents
        assert "code" in config.allowed_agents

    def test_enterprise_tier_defaults(self):
        config = TenantConfig(tenant_id="enterprise", tier=TenantTier.ENTERPRISE)
        assert len(config.allowed_agents) == 5  # All agents

    def test_custom_allowed_agents(self):
        config = TenantConfig(
            tenant_id="custom",
            tier=TenantTier.FREE,
            allowed_agents=["chat", "rag", "code"]
        )
        assert config.allowed_agents == ["chat", "rag", "code"]


class TestTenantConfigManager:
    """Test config manager operations."""

    def test_default_config_exists(self):
        manager = TenantConfigManager()
        config = manager.get_config("default")
        assert config.tier == TenantTier.PRO
        assert "code" in config.allowed_agents

    def test_new_tenant_gets_free_tier(self):
        manager = TenantConfigManager()
        config = manager.get_config("brand-new-tenant")
        assert config.tier == TenantTier.FREE

    def test_set_and_get_config(self):
        manager = TenantConfigManager()
        custom = TenantConfig(tenant_id="acme", tier=TenantTier.ENTERPRISE)
        manager.set_config("acme", custom)
        
        retrieved = manager.get_config("acme")
        assert retrieved.tier == TenantTier.ENTERPRISE

    def test_update_config(self):
        manager = TenantConfigManager()
        manager.update_config("default", max_documents=100, max_retries=5)
        
        config = manager.get_config("default")
        assert config.max_documents == 100
        assert config.max_retries == 5

    def test_is_agent_allowed(self):
        manager = TenantConfigManager()
        
        # Default (PRO) should allow all
        assert manager.is_agent_allowed("default", "rag") is True
        assert manager.is_agent_allowed("default", "code") is True
        
        # Free tier only allows chat + rag
        free_config = TenantConfig(tenant_id="free-user", tier=TenantTier.FREE)
        manager.set_config("free-user", free_config)
        assert manager.is_agent_allowed("free-user", "chat") is True
        assert manager.is_agent_allowed("free-user", "sql") is False

    def test_validate_request_success(self):
        manager = TenantConfigManager()
        is_valid, error = manager.validate_request("default", "What is AI?", "chat")
        assert is_valid is True
        assert error is None

    def test_validate_request_query_too_long(self):
        manager = TenantConfigManager()
        long_query = "x" * 2000
        is_valid, error = manager.validate_request("default", long_query, "chat")
        assert is_valid is False
        assert "length" in error.lower()

    def test_validate_request_agent_not_allowed(self):
        manager = TenantConfigManager()
        free_config = TenantConfig(tenant_id="limited", tier=TenantTier.FREE)
        manager.set_config("limited", free_config)
        
        is_valid, error = manager.validate_request("limited", "Run SQL", "sql")
        assert is_valid is False
        assert "not available" in error.lower()

    def test_validate_code_execution_disabled(self):
        manager = TenantConfigManager()
        config = TenantConfig(
            tenant_id="no-code",
            tier=TenantTier.PRO,
            enable_code_execution=False,
        )
        manager.set_config("no-code", config)
        
        is_valid, error = manager.validate_request("no-code", "Run code", "code")
        assert is_valid is False
        assert "not enabled" in error.lower()
