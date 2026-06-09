"""Visual configuration editor pages and form parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from html import escape
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from hahobot.agent.memory import migrate_legacy_memory_workspace
from hahobot.config.loader import _migrate_config, load_config
from hahobot.config.schema import Config
from hahobot.gateway.admin.base import (
    _current_config_path,
    _load_current_config,
    _load_raw_config_data,
    _markup,
    _page,
    _pretty_json,
    _redirect,
    _require_admin_auth,
    _runtime_workspace,
    _save_raw_config_data,
    _t,
    _th,
)
from hahobot.gateway.admin.constants import (
    _ADMIN_RELOAD_RUNTIME_KEY,
    _MEMORIX_MCP_DEFAULT_ARGS,
    _MEMORIX_MCP_DEFAULT_COMMAND,
    _MEMORIX_MCP_DEFAULT_TIMEOUT,
    _MEMORIX_MCP_SERVER_NAME,
)
from hahobot.gateway.admin.field_specs import (
    _BLANK_AS_NONE_FIELDS,
    _CHANNEL_CONFIG_FIELD_NAMES,
    _CHANNEL_CONFIG_FIELD_TO_GROUP,
    _CHANNEL_CONFIG_GROUPS,
    _CHANNEL_GROUP_SUMMARY_URL_FIELDS,
    _CONFIG_FIELD_MAP,
    _CONFIG_FIELDS,
    _CONFIG_SECTIONS,
    _MEMORIX_CONFIG_FIELD_NAMES,
    _PROVIDER_CONFIG_GROUPS,
    _PROVIDER_POOL_CONFIG_FIELD_NAMES,
    ConfigFieldSpec,
)

# Field kinds whose render value can be derived generically from the config
# model by walking ``ConfigFieldSpec.path``. Everything else (provider pool
# rows, the custom-headers JSON editor, the dynamic memorix MCP entry, and the
# multi-instance channel cards) is handled explicitly in _config_form_values.
_MISSING = object()
_AUTO_VALUE_KINDS = frozenset({"text", "select", "textarea", "int", "float", "bool", "csv"})
_AUTO_VALUE_EXCLUDED_FIELDS = (
    _CHANNEL_CONFIG_FIELD_NAMES | _PROVIDER_POOL_CONFIG_FIELD_NAMES | _MEMORIX_CONFIG_FIELD_NAMES
)


def _auto_field_values(config: Config) -> dict[str, Any]:
    """Derive form values for simple scalar fields straight from the model.

    Walks each ``ConfigFieldSpec.path`` against the by-alias config dump so new
    scalar fields no longer need a hand-written entry in _config_form_values.
    Fields with bespoke handling (channels, provider pool, memorix, JSON) are
    skipped and remain explicit.
    """
    dump = config.model_dump(mode="json", by_alias=True)
    values: dict[str, Any] = {}
    for field in _CONFIG_FIELDS:
        if field.kind not in _AUTO_VALUE_KINDS or field.name in _AUTO_VALUE_EXCLUDED_FIELDS:
            continue
        node: Any = dump
        for key in field.path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = _MISSING
                break
        if node is _MISSING:
            continue
        if field.kind == "bool":
            values[field.name] = bool(node)
        elif field.kind == "csv":
            values[field.name] = ", ".join(node or [])
        else:  # text, select, textarea, int, float
            values[field.name] = "" if node is None else str(node)
    return values


def _snake_case_key(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _resolve_nested_key(data: dict[str, Any], segment: str) -> str:
    if segment in data:
        return segment
    snake = _snake_case_key(segment)
    if snake in data:
        return snake
    return segment


def _set_nested_value(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = data
    for segment in path[:-1]:
        key = _resolve_nested_key(node, segment)
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[_resolve_nested_key(node, path[-1])] = value


def _config_form_values(config: Config) -> dict[str, Any]:
    memorix = config.tools.mcp_servers.get(_MEMORIX_MCP_SERVER_NAME)
    provider_pool = config.agents.defaults.provider_pool
    memorix_args = (
        list(memorix.args) if memorix and memorix.args else list(_MEMORIX_MCP_DEFAULT_ARGS)
    )
    channel_group_modes: dict[str, str] = {}
    channel_group_enabled: dict[str, bool] = {}
    channel_group_instances: dict[str, int] = {}
    channel_values: dict[str, Any] = {}
    for _, _, _, field_names in _CHANNEL_CONFIG_GROUPS:
        for field_name in field_names:
            field = _CONFIG_FIELD_MAP[field_name]
            channel_values[field_name] = False if field.kind == "bool" else ""

    for group_key, _, _, _ in _CHANNEL_CONFIG_GROUPS:
        channel_config = getattr(config.channels, group_key)
        is_multi = hasattr(channel_config, "instances")
        channel_group_modes[group_key] = "multi" if is_multi else "single"
        channel_group_enabled[group_key] = bool(getattr(channel_config, "enabled", False))
        channel_group_instances[group_key] = (
            len(getattr(channel_config, "instances", [])) if is_multi else 0
        )
        if is_multi:
            continue
        if group_key == "whatsapp":
            channel_values.update(
                {
                    "channels_whatsapp_enabled": channel_config.enabled,
                    "channels_whatsapp_bridge_url": channel_config.bridge_url,
                    "channels_whatsapp_bridge_token": channel_config.bridge_token,
                }
            )
        elif group_key == "telegram":
            channel_values.update(
                {
                    "channels_telegram_enabled": channel_config.enabled,
                    "channels_telegram_token": channel_config.token,
                    "channels_telegram_proxy": channel_config.proxy or "",
                    "channels_telegram_stream_edit_interval": str(
                        channel_config.stream_edit_interval
                    ),
                }
            )
        elif group_key == "discord":
            channel_values.update(
                {
                    "channels_discord_enabled": channel_config.enabled,
                    "channels_discord_token": channel_config.token,
                    "channels_discord_gateway_url": channel_config.gateway_url,
                    "channels_discord_intents": str(channel_config.intents),
                    "channels_discord_proxy": channel_config.proxy or "",
                    "channels_discord_proxy_username": channel_config.proxy_username or "",
                    "channels_discord_proxy_password": channel_config.proxy_password or "",
                    "channels_discord_streaming": channel_config.streaming,
                    "channels_discord_read_receipt_emoji": channel_config.read_receipt_emoji,
                    "channels_discord_working_emoji": channel_config.working_emoji,
                    "channels_discord_working_emoji_delay": str(channel_config.working_emoji_delay),
                }
            )
        elif group_key == "feishu":
            channel_values.update(
                {
                    "channels_feishu_enabled": channel_config.enabled,
                    "channels_feishu_app_id": channel_config.app_id,
                    "channels_feishu_app_secret": channel_config.app_secret,
                    "channels_feishu_encrypt_key": channel_config.encrypt_key,
                    "channels_feishu_verification_token": channel_config.verification_token,
                }
            )
        elif group_key == "dingtalk":
            channel_values.update(
                {
                    "channels_dingtalk_enabled": channel_config.enabled,
                    "channels_dingtalk_client_id": channel_config.client_id,
                    "channels_dingtalk_client_secret": channel_config.client_secret,
                }
            )
        elif group_key == "slack":
            channel_values.update(
                {
                    "channels_slack_enabled": channel_config.enabled,
                    "channels_slack_bot_token": channel_config.bot_token,
                    "channels_slack_app_token": channel_config.app_token,
                }
            )
        elif group_key == "qq":
            channel_values.update(
                {
                    "channels_qq_enabled": channel_config.enabled,
                    "channels_qq_app_id": channel_config.app_id,
                    "channels_qq_secret": channel_config.secret,
                }
            )
        elif group_key == "matrix":
            channel_values.update(
                {
                    "channels_matrix_enabled": channel_config.enabled,
                    "channels_matrix_homeserver": channel_config.homeserver,
                    "channels_matrix_access_token": channel_config.access_token,
                    "channels_matrix_user_id": channel_config.user_id,
                    "channels_matrix_device_id": channel_config.device_id,
                }
            )
        elif group_key == "weixin":
            channel_values.update(
                {
                    "channels_weixin_enabled": channel_config.enabled,
                    "channels_weixin_allow_from": ", ".join(channel_config.allow_from),
                    "channels_weixin_token": channel_config.token,
                    "channels_weixin_route_tag": (
                        "" if channel_config.route_tag is None else str(channel_config.route_tag)
                    ),
                    "channels_weixin_state_dir": channel_config.state_dir or "",
                    "channels_weixin_poll_timeout": str(channel_config.poll_timeout),
                }
            )
        elif group_key == "wecom":
            channel_values.update(
                {
                    "channels_wecom_enabled": channel_config.enabled,
                    "channels_wecom_bot_id": channel_config.bot_id,
                    "channels_wecom_secret": channel_config.secret,
                }
            )

    return {
        # Simple scalar fields are derived generically from the config model.
        **_auto_field_values(config),
        # --- Fields with bespoke handling below ---
        # Provider pool: ordered list-of-rows widget.
        "agents_defaults_provider_pool_strategy": (
            provider_pool.strategy if provider_pool and provider_pool.targets else "failover"
        ),
        "agents_defaults_provider_pool_targets": [
            target.model_dump(mode="json", by_alias=True)
            for target in (provider_pool.targets if provider_pool else [])
        ],
        # Custom provider headers: JSON editor.
        "providers_custom_extra_headers": _pretty_json(config.providers.custom.extra_headers or {}),
        # Memorix MCP server: dynamic entry that may be absent from the config.
        "tools_mcp_memorix_enabled": memorix is not None,
        "tools_mcp_memorix_type": memorix.type if memorix and memorix.type else "",
        "tools_mcp_memorix_command": (
            memorix.command if memorix and memorix.command else _MEMORIX_MCP_DEFAULT_COMMAND
        ),
        "tools_mcp_memorix_args": ", ".join(memorix_args),
        "tools_mcp_memorix_url": memorix.url if memorix else "",
        "tools_mcp_memorix_tool_timeout": str(
            memorix.tool_timeout if memorix else _MEMORIX_MCP_DEFAULT_TIMEOUT
        ),
        # Multi-instance channel cards and their group metadata.
        **channel_values,
        "__channel_group_modes": channel_group_modes,
        "__channel_group_enabled": channel_group_enabled,
        "__channel_group_instances": channel_group_instances,
    }


def validate_admin_config_specs() -> None:
    """Fail-fast integrity check for the admin visual-config wiring.

    Verifies every field has a render value source and that all i18n keys it
    references (label, derived tooltip, optional hint, section title/desc) exist
    in every supported locale and are ``str.format``-safe. Raises ``RuntimeError``
    listing all problems, so a misconfiguration surfaces at gateway startup
    rather than the first time a user opens the config page.
    """
    from hahobot.agent.i18n import SUPPORTED_LANGUAGES, _load_locale

    problems: list[str] = []

    values = _config_form_values(Config())
    for field in _CONFIG_FIELDS:
        if field.name not in values:
            problems.append(f"field '{field.name}' has no value in _config_form_values")

    required_keys: set[str] = set()
    for field in _CONFIG_FIELDS:
        required_keys.add(field.label_key)
        required_keys.add(field.label_key.removesuffix("_label") + "_tooltip")
        if field.hint_key:
            required_keys.add(field.hint_key)
    for title_key, desc_key, _names in _CONFIG_SECTIONS:
        required_keys.add(title_key)
        required_keys.add(desc_key)

    for lang in SUPPORTED_LANGUAGES:
        texts = _load_locale(lang).get("texts", {})
        for key in sorted(required_keys):
            value = texts.get(key)
            if value is None:
                problems.append(f"locale '{lang}' missing i18n key '{key}'")
                continue
            try:
                value.format()
            except (KeyError, IndexError, ValueError):
                problems.append(
                    f"locale '{lang}' key '{key}' is not str.format-safe "
                    "(escape literal braces as {{ }})"
                )

    if problems:
        raise RuntimeError("admin config spec validation failed:\n  - " + "\n  - ".join(problems))


def _extract_visual_values(
    form: Any,
    *,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    values = dict(baseline)
    bool_fields = {str(value) for value in form.getall("__bool_fields", [])}
    for field in _CONFIG_FIELDS:
        if field.kind == "bool":
            if field.name in bool_fields:
                values[field.name] = str(form.get(field.name, "")).lower() in {
                    "1",
                    "true",
                    "on",
                    "yes",
                }
            continue
        if field.kind == "provider_pool_targets":
            providers = [str(value) for value in form.getall(f"{field.name}_provider", [])]
            models = [str(value) for value in form.getall(f"{field.name}_model", [])]
            row_count = max(len(providers), len(models))
            values[field.name] = [
                {
                    "provider": providers[index] if index < len(providers) else "",
                    "model": models[index] if index < len(models) else "",
                }
                for index in range(row_count)
            ]
            continue
        if field.name in form:
            values[field.name] = str(form.get(field.name, ""))
    return values


def _parse_visual_value(request: web.Request, field: ConfigFieldSpec, raw_value: Any) -> Any:
    if field.kind == "bool":
        return bool(raw_value)

    if field.kind == "csv":
        return [part.strip() for part in re.split(r"[\n,]", str(raw_value)) if part.strip()]

    text_value = str(raw_value)
    stripped = text_value.strip()

    if field.kind == "json":
        if not stripped:
            return {}
        try:
            data = json.loads(text_value)
        except ValueError as exc:
            raise ValueError(_t(request, "admin_error_invalid_json", error=exc)) from exc
        if not isinstance(data, dict):
            raise ValueError(_t(request, "admin_json_object_required"))
        return data

    if field.kind == "provider_pool_targets":
        rows = raw_value if isinstance(raw_value, list) else []
        normalized_rows: list[dict[str, str]] = []
        for item in rows:
            provider = ""
            model = ""
            if isinstance(item, dict):
                provider = str(item.get("provider", "")).strip()
                model = str(item.get("model", "")).strip()
            if not provider and not model:
                continue
            if not provider:
                raise ValueError(_t(request, "admin_error_provider_pool_target_provider_required"))
            row = {"provider": provider}
            if model:
                row["model"] = model
            normalized_rows.append(row)
        return normalized_rows

    if field.kind == "int":
        if not stripped:
            raise ValueError(
                _t(request, "admin_error_invalid_integer", field=_t(request, field.label_key))
            )
        try:
            return int(stripped)
        except ValueError as exc:
            raise ValueError(
                _t(request, "admin_error_invalid_integer", field=_t(request, field.label_key))
            ) from exc

    if field.kind == "float":
        if not stripped and field.name in _BLANK_AS_NONE_FIELDS:
            return None
        if not stripped:
            raise ValueError(
                _t(request, "admin_error_invalid_number", field=_t(request, field.label_key))
            )
        try:
            return float(stripped)
        except ValueError as exc:
            raise ValueError(
                _t(request, "admin_error_invalid_number", field=_t(request, field.label_key))
            ) from exc

    if field.name in _BLANK_AS_NONE_FIELDS and not stripped:
        return None

    if field.kind == "textarea":
        return text_value.replace("\r\n", "\n")
    return stripped


def _apply_visual_config_values(
    request: web.Request,
    *,
    raw_data: dict[str, Any],
    visual_values: dict[str, Any],
) -> dict[str, Any]:
    updated = json.loads(json.dumps(raw_data))
    memorix_enabled = bool(visual_values["tools_mcp_memorix_enabled"])
    tools_node = updated.setdefault("tools", {})
    if not isinstance(tools_node, dict):
        tools_node = {}
        updated["tools"] = tools_node
    servers_node = tools_node.get("mcpServers")
    if not isinstance(servers_node, dict):
        servers_node = {}
        tools_node["mcpServers"] = servers_node

    if memorix_enabled:
        memorix_values = {
            "type": _parse_visual_value(
                request,
                _CONFIG_FIELD_MAP["tools_mcp_memorix_type"],
                visual_values["tools_mcp_memorix_type"],
            ),
            "command": _parse_visual_value(
                request,
                _CONFIG_FIELD_MAP["tools_mcp_memorix_command"],
                visual_values["tools_mcp_memorix_command"],
            ),
            "args": _parse_visual_value(
                request,
                _CONFIG_FIELD_MAP["tools_mcp_memorix_args"],
                visual_values["tools_mcp_memorix_args"],
            ),
            "url": _parse_visual_value(
                request,
                _CONFIG_FIELD_MAP["tools_mcp_memorix_url"],
                visual_values["tools_mcp_memorix_url"],
            ),
            "toolTimeout": _parse_visual_value(
                request,
                _CONFIG_FIELD_MAP["tools_mcp_memorix_tool_timeout"],
                visual_values["tools_mcp_memorix_tool_timeout"],
            ),
        }
        servers_node[_MEMORIX_MCP_SERVER_NAME] = memorix_values
    else:
        servers_node.pop(_MEMORIX_MCP_SERVER_NAME, None)
        if not servers_node:
            tools_node.pop("mcpServers", None)

    provider_pool_targets = _parse_visual_value(
        request,
        _CONFIG_FIELD_MAP["agents_defaults_provider_pool_targets"],
        visual_values["agents_defaults_provider_pool_targets"],
    )
    agents_node = updated.setdefault("agents", {})
    if not isinstance(agents_node, dict):
        agents_node = {}
        updated["agents"] = agents_node
    defaults_node = agents_node.get("defaults")
    if not isinstance(defaults_node, dict):
        defaults_node = {}
        agents_node["defaults"] = defaults_node

    if provider_pool_targets:
        provider_pool_strategy = _parse_visual_value(
            request,
            _CONFIG_FIELD_MAP["agents_defaults_provider_pool_strategy"],
            visual_values["agents_defaults_provider_pool_strategy"],
        )
        _set_nested_value(
            updated, ("agents", "defaults", "providerPool", "strategy"), provider_pool_strategy
        )
        _set_nested_value(
            updated, ("agents", "defaults", "providerPool", "targets"), provider_pool_targets
        )
    else:
        defaults_node.pop(_resolve_nested_key(defaults_node, "providerPool"), None)

    for field in _CONFIG_FIELDS:
        if (
            field.name in _MEMORIX_CONFIG_FIELD_NAMES
            or field.name in _PROVIDER_POOL_CONFIG_FIELD_NAMES
        ):
            continue
        channel_group = _CHANNEL_CONFIG_FIELD_TO_GROUP.get(field.name)
        if channel_group and _channel_group_mode(visual_values, channel_group) == "multi":
            continue
        value = _parse_visual_value(request, field, visual_values[field.name])
        _set_nested_value(updated, field.path, value)
    return updated


def _validate_config_data(request: web.Request, data: dict[str, Any]) -> Config:
    try:
        config = Config.model_validate(data).bind_config_path(_current_config_path(request))
    except Exception as exc:
        raise ValueError(_t(request, "admin_error_config_validation", error=exc)) from exc
    if config.gateway.admin.enabled and not config.gateway.admin.auth_key.strip():
        raise ValueError(_t(request, "admin_error_admin_auth_required"))
    return config


def _reload_runtime_callback(request: web.Request) -> Callable[[], Awaitable[None]] | None:
    try:
        callback = request.app[_ADMIN_RELOAD_RUNTIME_KEY]
    except KeyError:
        return None
    return callback if callable(callback) else None


def _config_section_id(title_key: str) -> str:
    slug = title_key.removeprefix("admin_config_section_").removesuffix("_title")
    return f"section-{slug}"


def _render_field_chrome(request: web.Request, field: ConfigFieldSpec) -> tuple[str, str]:
    label = escape(_t(request, field.label_key))
    tooltip_key = field.label_key.removesuffix("_label") + "_tooltip"
    badge_class = "restart" if field.restart_required else "hot"
    badge_key = (
        "admin_badge_restart_required" if field.restart_required else "admin_badge_hot_reload"
    )
    runtime_badge = f'<span class="pill {badge_class}">{escape(_t(request, badge_key))}</span>'
    label_row = (
        '<span class="label-row tooltip-anchor" tabindex="0">'
        f'<span class="label">{label}</span>'
        f"{runtime_badge}"
        '<span class="tooltip-trigger" aria-hidden="true">?</span>'
        f'<span class="tooltip-card">{_th(request, tooltip_key)}</span>'
        "</span>"
    )
    hint = ""
    if field.hint_key:
        hint = f'<div class="hint">{_th(request, field.hint_key)}</div>'
    return label_row, hint


def _provider_pool_rows(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "provider": str(item.get("provider", "") or ""),
                    "model": str(item.get("model", "") or ""),
                }
            )
    return rows or [{"provider": "", "model": ""}]


def _provider_pool_options_html(request: web.Request, selected: str) -> str:
    from hahobot.providers.registry import PROVIDERS

    options = [f'<option value="">{escape(_t(request, "admin_option_select_provider"))}</option>']
    for spec in PROVIDERS:
        is_selected = " selected" if spec.name == selected else ""
        options.append(
            f'<option value="{escape(spec.name)}"{is_selected}>{escape(spec.name)}</option>'
        )
    return "".join(options)


def _render_provider_pool_row(
    request: web.Request,
    *,
    field_name: str,
    row: dict[str, str],
) -> str:
    provider_options = _provider_pool_options_html(request, row.get("provider", ""))
    model_value = escape(row.get("model", ""))
    return (
        '<div class="provider-pool-row" data-provider-pool-row>'
        f'<select name="{escape(field_name)}_provider">{provider_options}</select>'
        f'<input type="text" name="{escape(field_name)}_model" value="{model_value}">'
        '<div class="provider-pool-row-actions">'
        f'<button type="button" class="ghost provider-pool-move" data-provider-pool-move-up>'
        f"{escape(_t(request, 'admin_provider_pool_move_up'))}</button>"
        f'<button type="button" class="ghost provider-pool-move" data-provider-pool-move-down>'
        f"{escape(_t(request, 'admin_provider_pool_move_down'))}</button>"
        f'<button type="button" class="ghost provider-pool-remove" data-provider-pool-remove>'
        f"{escape(_t(request, 'admin_provider_pool_remove'))}</button>"
        "</div>"
        "</div>"
    )


def _render_provider_pool_targets_field(
    request: web.Request,
    field: ConfigFieldSpec,
    value: Any,
) -> str:
    label_row, hint = _render_field_chrome(request, field)
    rows_html = "".join(
        _render_provider_pool_row(request, field_name=field.name, row=row)
        for row in _provider_pool_rows(value)
    )
    template_row = _render_provider_pool_row(
        request,
        field_name=field.name,
        row={"provider": "", "model": ""},
    )
    return f"""
        <div class="field full">
          {label_row}
          <div class="provider-pool-editor" data-provider-pool-editor>
            <div class="provider-pool-head">
              <span>{escape(_t(request, "admin_provider_pool_column_provider"))}</span>
              <span>{escape(_t(request, "admin_provider_pool_column_model"))}</span>
              <span>{escape(_t(request, "admin_provider_pool_column_actions"))}</span>
            </div>
            <div class="provider-pool-rows" data-provider-pool-rows>
              {rows_html}
            </div>
            <template data-provider-pool-template>{template_row}</template>
            <div class="actions provider-pool-actions">
              <button type="button" class="ghost" data-provider-pool-add>
                {escape(_t(request, "admin_provider_pool_add"))}
              </button>
            </div>
          </div>
          {hint}
        </div>
    """


def _visual_value_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _provider_group_is_configured(values: dict[str, Any], field_names: tuple[str, ...]) -> bool:
    return any(_visual_value_present(values.get(field_name)) for field_name in field_names)


def _provider_group_configured_count(values: dict[str, Any], field_names: tuple[str, ...]) -> int:
    return sum(1 for field_name in field_names if _visual_value_present(values.get(field_name)))


def _channel_group_mode(values: dict[str, Any], group_key: str) -> str:
    raw = values.get("__channel_group_modes")
    if isinstance(raw, dict):
        mode = raw.get(group_key)
        if mode in {"single", "multi"}:
            return mode
    return "single"


def _channel_group_enabled(values: dict[str, Any], group_key: str) -> bool:
    raw = values.get("__channel_group_enabled")
    if isinstance(raw, dict):
        return bool(raw.get(group_key))
    return False


def _channel_group_instance_count(values: dict[str, Any], group_key: str) -> int:
    raw = values.get("__channel_group_instances")
    if isinstance(raw, dict):
        try:
            return max(int(raw.get(group_key, 0)), 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _channel_group_is_configured(
    values: dict[str, Any],
    *,
    group_key: str,
    field_names: tuple[str, ...],
) -> bool:
    if _channel_group_enabled(values, group_key):
        return True
    if _channel_group_mode(values, group_key) == "multi":
        return _channel_group_instance_count(values, group_key) > 0
    return any(
        _visual_value_present(values.get(field_name))
        for field_name in field_names
        if field_name not in _CHANNEL_GROUP_SUMMARY_URL_FIELDS
        and not field_name.endswith("_enabled")
    ) or any(
        _visual_value_present(values.get(field_name))
        for field_name in field_names
        if field_name in _CHANNEL_GROUP_SUMMARY_URL_FIELDS
    )


def _compact_provider_url(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    parsed = urlsplit(trimmed)
    if parsed.scheme and parsed.netloc:
        compact = f"{parsed.netloc}{parsed.path}".rstrip("/")
        if parsed.query:
            compact = f"{compact}?{parsed.query}" if compact else parsed.query
        return compact or parsed.netloc
    return trimmed


def _provider_header_count(raw_value: Any) -> int | None:
    text = str(raw_value).strip()
    if not text or text == "{}":
        return 0
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        return len(parsed)
    return None


def _render_provider_group_chip(
    text: str,
    *,
    code: bool = False,
    title: str | None = None,
) -> str:
    css_class = "provider-group-chip code" if code else "provider-group-chip"
    title_attr = f' title="{escape(title)}"' if title else ""
    content = f"<code>{escape(text)}</code>" if code else escape(text)
    return f'<span class="{css_class}"{title_attr}>{content}</span>'


def _render_provider_group_summary(
    request: web.Request,
    *,
    group_key: str,
    field_names: tuple[str, ...],
    values: dict[str, Any],
) -> str:
    items: list[str] = []
    api_key_name = f"providers_{group_key}_api_key"
    api_base_name = f"providers_{group_key}_api_base"

    if api_key_name in field_names and _visual_value_present(values.get(api_key_name)):
        items.append(_render_provider_group_chip(_t(request, "admin_provider_group_meta_api_key")))

    api_base_value = str(values.get(api_base_name, "")).strip()
    if api_base_name in field_names and api_base_value:
        items.append(
            _render_provider_group_chip(
                _compact_provider_url(api_base_value),
                code=True,
                title=api_base_value,
            )
        )

    if "providers_custom_extra_headers" in field_names:
        header_count = _provider_header_count(values.get("providers_custom_extra_headers", ""))
        if header_count:
            items.append(
                _render_provider_group_chip(
                    _t(request, "admin_provider_group_meta_headers", count=header_count)
                )
            )
        elif header_count is None and _visual_value_present(
            values.get("providers_custom_extra_headers")
        ):
            items.append(
                _render_provider_group_chip(
                    _t(request, "admin_provider_group_meta_headers_present")
                )
            )

    if not items:
        configured_count = _provider_group_configured_count(values, field_names)
        if configured_count:
            items.append(
                _render_provider_group_chip(
                    _t(request, "admin_provider_group_meta_fields", count=configured_count)
                )
            )

    if not items:
        return ""
    return (
        f'<div class="provider-group-meta" data-provider-group-meta="{escape(group_key)}">'
        f"{''.join(items)}"
        "</div>"
    )


def _render_provider_groups_section(
    request: web.Request,
    *,
    values: dict[str, Any],
) -> str:
    groups = []
    for group_key, title_key, desc_key, field_names in _PROVIDER_CONFIG_GROUPS:
        configured = _provider_group_is_configured(values, field_names)
        open_attr = " open" if configured else ""
        status_key = (
            "admin_provider_group_configured" if configured else "admin_provider_group_empty"
        )
        status_class = "pill hot" if configured else "pill"
        fields = "".join(
            _render_config_field(request, _CONFIG_FIELD_MAP[field_name], values[field_name])
            for field_name in field_names
        )
        summary = _render_provider_group_summary(
            request,
            group_key=group_key,
            field_names=field_names,
            values=values,
        )
        groups.append(
            f'<details class="provider-group" data-provider-group="{escape(group_key)}"{open_attr}>'
            "<summary>"
            '<div class="provider-group-top">'
            f'<h3 class="provider-group-title">{escape(_t(request, title_key))}</h3>'
            f'<span class="{status_class}">{escape(_t(request, status_key))}</span>'
            "</div>"
            f'<div class="provider-group-desc">{_th(request, desc_key)}</div>'
            f"{summary}"
            "</summary>"
            '<div class="provider-group-body">'
            f'<div class="provider-group-fields">{fields}</div>'
            "</div>"
            "</details>"
        )
    return f'<div class="provider-groups">{"".join(groups)}</div>'


def _render_channel_group_summary(
    request: web.Request,
    *,
    group_key: str,
    field_names: tuple[str, ...],
    values: dict[str, Any],
) -> str:
    items = [
        _render_provider_group_chip(
            _t(
                request,
                "admin_channel_group_meta_enabled"
                if _channel_group_enabled(values, group_key)
                else "admin_channel_group_meta_disabled",
            )
        )
    ]
    if _channel_group_mode(values, group_key) == "multi":
        items.append(
            _render_provider_group_chip(
                _t(
                    request,
                    "admin_channel_group_meta_instances",
                    count=_channel_group_instance_count(values, group_key),
                )
            )
        )
    else:
        for field_name in field_names:
            if field_name not in _CHANNEL_GROUP_SUMMARY_URL_FIELDS:
                continue
            raw_value = str(values.get(field_name, "")).strip()
            if not raw_value:
                continue
            items.append(
                _render_provider_group_chip(
                    _compact_provider_url(raw_value),
                    code=True,
                    title=raw_value,
                )
            )
        configured_count = sum(
            1
            for field_name in field_names
            if field_name not in _CHANNEL_GROUP_SUMMARY_URL_FIELDS
            and not field_name.endswith("_enabled")
            and _visual_value_present(values.get(field_name))
        )
        if configured_count:
            items.append(
                _render_provider_group_chip(
                    _t(request, "admin_channel_group_meta_fields", count=configured_count)
                )
            )
    return (
        f'<div class="provider-group-meta" data-channel-group-meta="{escape(group_key)}">'
        f"{''.join(items)}"
        "</div>"
    )


def _render_channel_groups_section(
    request: web.Request,
    *,
    values: dict[str, Any],
) -> str:
    groups = []
    for group_key, title_key, desc_key, field_names in _CHANNEL_CONFIG_GROUPS:
        is_multi = _channel_group_mode(values, group_key) == "multi"
        configured = _channel_group_is_configured(
            values,
            group_key=group_key,
            field_names=field_names,
        )
        open_attr = " open" if configured or is_multi else ""
        status_key = (
            "admin_channel_group_multi_instance"
            if is_multi
            else "admin_channel_group_single_instance"
        )
        status_class = "pill restart" if is_multi else "pill"
        if is_multi:
            fields = f'<div class="notice">{_th(request, "admin_channel_group_multi_instance_notice", path=f"channels.{group_key}.instances")}</div>'
        else:
            fields = (
                '<div class="provider-group-fields">'
                + "".join(
                    _render_config_field(request, _CONFIG_FIELD_MAP[field_name], values[field_name])
                    for field_name in field_names
                )
                + "</div>"
            )
            if group_key == "weixin":
                fields += (
                    '<div class="actions">'
                    f'<a class="nav-link active" href="/admin/weixin">{escape(_t(request, "admin_weixin_open_from_config"))}</a>'
                    "</div>"
                )
        summary = _render_channel_group_summary(
            request,
            group_key=group_key,
            field_names=field_names,
            values=values,
        )
        groups.append(
            f'<details class="provider-group" data-channel-group="{escape(group_key)}"{open_attr}>'
            "<summary>"
            '<div class="provider-group-top">'
            f'<h3 class="provider-group-title">{escape(_t(request, title_key))}</h3>'
            f'<span class="{status_class}">{escape(_t(request, status_key))}</span>'
            "</div>"
            f'<div class="provider-group-desc">{_th(request, desc_key)}</div>'
            f"{summary}"
            "</summary>"
            '<div class="provider-group-body">'
            f"{fields}"
            "</div>"
            "</details>"
        )
    return f'<div class="provider-groups">{"".join(groups)}</div>'


def _render_config_field(request: web.Request, field: ConfigFieldSpec, value: Any) -> str:
    label_row, hint = _render_field_chrome(request, field)

    if field.kind == "bool":
        checked = " checked" if bool(value) else ""
        return (
            '<div class="field">'
            f'<input type="hidden" name="__bool_fields" value="{escape(field.name)}">'
            f'<label class="toggle"><input type="checkbox" name="{escape(field.name)}" value="1"{checked}>'
            f"{label_row}</label>{hint}</div>"
        )

    if field.kind == "provider_pool_targets":
        return _render_provider_pool_targets_field(request, field, value)

    if field.kind in {"textarea", "json"}:
        rows = max(field.rows, 3)
        css_class = "field full"
        return (
            f'<label class="{css_class}">{label_row}'
            f'<textarea name="{escape(field.name)}" rows="{rows}" spellcheck="false">'
            f"{escape(str(value))}</textarea>{hint}</label>"
        )

    if field.kind == "select":
        options = []
        for option in field.options:
            selected = " selected" if str(value) == option else ""
            text = _t(request, "admin_option_default") if option == "" else option
            options.append(f'<option value="{escape(option)}"{selected}>{escape(text)}</option>')
        control = f'<select name="{escape(field.name)}">{"".join(options)}</select>'
    else:
        input_type = "number" if field.kind in {"int", "float"} else "text"
        step = ' step="any"' if field.kind == "float" else ""
        placeholder = f' placeholder="{escape(field.placeholder)}"' if field.placeholder else ""
        control = (
            f'<input type="{input_type}" name="{escape(field.name)}" value="{escape(str(value))}"'
            f"{step}{placeholder}>"
        )

    return f'<label class="field">{label_row}{control}{hint}</label>'


def _render_config_section(
    request: web.Request,
    *,
    index: int,
    title_key: str,
    desc_key: str,
    field_names: tuple[str, ...],
    values: dict[str, Any],
) -> str:
    section_id = _config_section_id(title_key)
    if title_key == "admin_config_section_providers_title":
        fields = _render_provider_groups_section(request, values=values)
    elif title_key == "admin_config_section_channels_title":
        fields = _render_channel_groups_section(request, values=values)
    else:
        fields = "".join(
            _render_config_field(request, _CONFIG_FIELD_MAP[field_name], values[field_name])
            for field_name in field_names
        )
    return (
        f'<section id="{section_id}" class="card stack section-card">'
        '<div class="section-topline">'
        '<div class="section-head">'
        f"<h2>{escape(_t(request, title_key))}</h2>"
        f'<div class="muted">{_th(request, desc_key)}</div>'
        "</div>"
        f'<span class="section-index">{index:02d}</span>'
        "</div>"
        f'<div class="field-grid">{fields}</div>'
        "</section>"
    )


def _render_config_page(
    request: web.Request,
    *,
    visual_values: dict[str, Any],
    raw_text: str,
    flash: str | None = None,
    error: str | None = None,
    active_mode: str = "visual",
) -> web.Response:
    sections_parts: list[str] = []
    jump_links: list[str] = []
    for index, (title_key, desc_key, field_names) in enumerate(_CONFIG_SECTIONS, start=1):
        section_id = _config_section_id(title_key)
        sections_parts.append(
            _render_config_section(
                request,
                index=index,
                title_key=title_key,
                desc_key=desc_key,
                field_names=field_names,
                values=visual_values,
            )
        )
        jump_links.append(
            f'<a class="jump-link" href="#{section_id}">'
            '<div class="jump-link-top">'
            f'<span class="jump-link-index">{index:02d}</span>'
            f"<strong>{escape(_t(request, title_key))}</strong>"
            "</div>"
            f'<div class="jump-link-meta">{len(field_names)} {escape(_t(request, "admin_label_fields"))}</div>'
            "</a>"
        )
    sections = "".join(sections_parts)
    return _page(
        template_name="gateway/admin/config.html",
        title=_t(request, "admin_config_title"),
        heading=_t(request, "admin_config_heading"),
        request=request,
        flash=flash,
        error=error,
        config_nav_label=_t(request, "admin_nav_config"),
        config_intro_html=_markup(
            _th(request, "admin_config_intro", config_path=_current_config_path(request))
        ),
        config_reload_notice_html=_markup(_th(request, "admin_config_reload_notice")),
        hot_reload_label=_t(request, "admin_badge_hot_reload"),
        restart_required_label=_t(request, "admin_badge_restart_required"),
        sections_label=_t(request, "admin_label_sections"),
        sections_count=len(_CONFIG_SECTIONS),
        fields_label=_t(request, "admin_label_fields"),
        fields_count=len(_CONFIG_FIELDS),
        jump_title=_t(request, "admin_config_jump_title"),
        jump_desc=_t(request, "admin_config_jump_desc"),
        jump_links_html=_markup("".join(jump_links)),
        sections_html=_markup(sections),
        save_visual_label=_t(request, "admin_config_save_visual"),
        memory_migrate_card_html=_markup(_render_memory_migrate_card(request)),
        advanced_title=_t(request, "admin_config_advanced_title"),
        advanced_desc_html=_markup(_th(request, "admin_config_advanced_desc")),
        raw_label=_t(request, "admin_config_raw_label"),
        raw_text=raw_text,
        save_raw_label=_t(request, "admin_config_save_raw"),
        raw_open=active_mode == "raw",
    )


def _render_memory_migrate_card(request: web.Request) -> str:
    """Render the standalone form that triggers the MEMORY.md legacy migration."""
    return (
        '<div class="card stack">'
        f"<h2>{escape(_t(request, 'admin_memory_migration_title'))}</h2>"
        f'<p class="muted">{_th(request, "admin_memory_migration_desc")}</p>'
        '<form method="post" action="/admin/memory/migrate-legacy" class="actions">'
        f'<button type="submit" class="ghost">{escape(_t(request, "admin_memory_migration_button"))}</button>'
        "</form>"
        "</div>"
    )


async def _admin_config_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    config = _load_current_config(request)
    try:
        raw_data = _load_raw_config_data(request)
    except Exception:
        raw_data = config.model_dump(mode="json", by_alias=True)
    flash = None
    if request.query.get("saved") == "1":
        flash = (
            _t(request, "admin_config_saved_reloaded")
            if request.query.get("reloaded") == "1"
            else _t(request, "admin_config_saved")
        )
    migrated_param = request.query.get("memory_migrated")
    if migrated_param is not None:
        try:
            migrated_count = int(migrated_param)
            files_count = int(request.query.get("memory_files", "0"))
        except ValueError:
            migrated_count = 0
            files_count = 0
        if migrated_count > 0:
            flash = _t(
                request,
                "admin_memory_migration_success",
                migrated=migrated_count,
                files=files_count,
            )
        else:
            flash = _t(request, "admin_memory_migration_none")
    error = None
    if request.query.get("memory_migrate_error") == "1":
        error = _t(request, "admin_memory_migration_failed")
    return _render_config_page(
        request,
        visual_values=_config_form_values(config),
        raw_text=_pretty_json(raw_data),
        flash=flash,
        error=error,
    )


async def _admin_memory_migrate_legacy(request: web.Request) -> web.Response:
    """Re-emit every persona's MEMORY.md so legacy fragments get structured headers."""
    _require_admin_auth(request)
    workspace = _runtime_workspace(request)
    try:
        summary = migrate_legacy_memory_workspace(workspace)
    except Exception:
        raise _redirect(request, "/admin/config?memory_migrate_error=1") from None
    raise _redirect(
        request,
        f"/admin/config?memory_migrated={summary['total_migrated']}"
        f"&memory_files={summary['files_changed']}",
    )


async def _admin_config_submit(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    form = await request.post()
    mode = str(form.get("mode", "visual"))
    current_config = _load_current_config(request)
    baseline_values = _config_form_values(current_config)
    try:
        current_raw = _load_raw_config_data(request)
    except Exception:
        current_raw = current_config.model_dump(mode="json", by_alias=True)

    if mode == "raw":
        raw_text = str(form.get("config_json", ""))
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return _render_config_page(
                request,
                visual_values=baseline_values,
                raw_text=raw_text,
                error=_t(request, "admin_error_invalid_json", error=exc),
                active_mode="raw",
            )
        if not isinstance(data, dict):
            return _render_config_page(
                request,
                visual_values=baseline_values,
                raw_text=raw_text,
                error=_t(
                    request,
                    "admin_error_config_validation",
                    error=_t(request, "admin_json_object_required"),
                ),
                active_mode="raw",
            )
        data = _migrate_config(data)
        try:
            _validate_config_data(request, data)
        except ValueError as exc:
            return _render_config_page(
                request,
                visual_values=_config_form_values(current_config),
                raw_text=raw_text,
                error=str(exc),
                active_mode="raw",
            )
        _save_raw_config_data(request, data)
        reload_runtime = _reload_runtime_callback(request)
        if reload_runtime is not None:
            try:
                await reload_runtime()
            except Exception as exc:
                return _render_config_page(
                    request,
                    visual_values=_config_form_values(load_config(_current_config_path(request))),
                    raw_text=_pretty_json(_load_raw_config_data(request)),
                    error=_t(request, "admin_error_runtime_reload_failed", error=exc),
                    active_mode="raw",
                )
            raise _redirect(request, "/admin/config?saved=1&reloaded=1")
        raise _redirect(request, "/admin/config?saved=1")

    visual_values = _extract_visual_values(form, baseline=baseline_values)
    try:
        updated = _apply_visual_config_values(
            request,
            raw_data=current_raw,
            visual_values=visual_values,
        )
        _validate_config_data(request, updated)
    except ValueError as exc:
        return _render_config_page(
            request,
            visual_values=visual_values,
            raw_text=_pretty_json(current_raw),
            error=str(exc),
        )

    _save_raw_config_data(request, updated)
    reload_runtime = _reload_runtime_callback(request)
    if reload_runtime is not None:
        try:
            await reload_runtime()
        except Exception as exc:
            return _render_config_page(
                request,
                visual_values=_config_form_values(load_config(_current_config_path(request))),
                raw_text=_pretty_json(_load_raw_config_data(request)),
                error=_t(request, "admin_error_runtime_reload_failed", error=exc),
            )
        raise _redirect(request, "/admin/config?saved=1&reloaded=1")
    raise _redirect(request, "/admin/config?saved=1")
