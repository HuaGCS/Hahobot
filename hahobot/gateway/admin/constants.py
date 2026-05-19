"""Module-level constants for the admin UI."""

from __future__ import annotations

import re
from pathlib import Path

from aiohttp import web

_ADMIN_COOKIE = "hahobot_admin_session"
_LEGACY_ADMIN_COOKIE = "nanobot_admin_session"
_ADMIN_LANG_COOKIE = "hahobot_admin_lang"
_LEGACY_ADMIN_LANG_COOKIE = "nanobot_admin_lang"
_ADMIN_COOKIE_TTL_S = 12 * 60 * 60
_ADMIN_LANG_COOKIE_TTL_S = 365 * 24 * 60 * 60
_DEFAULT_ADMIN_LANG = "zh"
_ADMIN_CONFIG_PATH_KEY = web.AppKey("admin_config_path", Path)
_ADMIN_WORKSPACE_KEY = web.AppKey("admin_workspace_path", Path)
_ADMIN_RELOAD_RUNTIME_KEY = web.AppKey("admin_reload_runtime", object)
_ADMIN_WEIXIN_LOGIN_SESSIONS_KEY = web.AppKey("admin_weixin_login_sessions", object)
_MEMORIX_MCP_SERVER_NAME = "memorix"
_MEMORIX_MCP_DEFAULT_COMMAND = "memorix"
_MEMORIX_MCP_DEFAULT_ARGS = ("serve",)
_MEMORIX_MCP_DEFAULT_TIMEOUT = 60
_WEIXIN_ADMIN_SESSION_TTL_S = 15 * 60
_SCENE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LEGACY_USER_PROFILE_TITLE_RE = re.compile(
    r"^#\s*(user profile|用户画像|用户资料)\s*$", re.IGNORECASE
)
_LEGACY_USER_PROFILE_SECTION_TITLES = {
    "basic information",
    "preferences",
    "communication style",
    "response length",
    "technical level",
    "work context",
    "topics of interest",
    "基本信息",
    "偏好",
    "沟通风格",
    "回复长度",
    "技术水平",
    "工作背景",
    "工作上下文",
    "兴趣主题",
}
_LEGACY_USER_INSIGHTS_SECTION_TITLES = {
    "special instructions",
    "workflow",
    "collaboration",
    "working style",
    "decision rules",
    "heuristics",
    "pitfalls",
    "strategy",
    "特别说明",
    "协作方式",
    "工作方式",
    "决策规则",
    "启发",
    "坑点",
    "策略",
}
_LEGACY_USER_RELATIONSHIP_SECTION_TITLES = {
    "relationship",
    "关系",
}
