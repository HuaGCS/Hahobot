from markupsafe import Markup

from hahobot.utils.html_templates import render_html_template


def test_gateway_html_templates_render_from_package() -> None:
    admin_html = render_html_template(
        "gateway/admin/login.html",
        title="Admin Login",
        brand="hahobot",
        heading_text="Admin Login",
        lang="en",
        config_path="/tmp/config.json",
        workspace="/tmp/workspace",
        admin_meta_config_label="Config",
        admin_meta_workspace_label="Workspace",
        language_switch_html=Markup(""),
        nav_html=Markup(""),
        notices_html=Markup(""),
        missing_key=False,
        form_only=False,
        next_path="/admin",
        auth_key_label="Auth key",
        submit_label="Sign in",
        login_feature_items_html=Markup("<li>Config</li><li>Commands</li><li>Personas</li>"),
    )
    status_html = render_html_template(
        "gateway/status.html",
        runtime_health_text="正常运行",
        current_state_badge_class="badge ok",
        current_state_text="idle",
        active_runs=0,
        current_detail="暂无详细状态",
        current_model="openrouter/sonnet",
        uptime_text="10m",
        started_at="2026-04-13 10:00:00",
        uptime_s=600,
        task_html=Markup("<div class='muted'>no task</div>"),
        heartbeat_badge_class="badge ok",
        heartbeat_status_text="最近一次成功",
        heartbeat_model="openrouter/sonnet",
        heartbeat_enabled_text="开启",
        heartbeat_running_text="运行中",
        heartbeat_interval="600s",
        heartbeat_checked_at="2026-04-13 10:10:00",
        heartbeat_detail="最近一次 heartbeat 检测成功",
    )

    assert "<!doctype html>" in admin_html.lower()
    assert "Admin Login" in admin_html
    assert "hahobot gateway status" in status_html
    assert "运行状态页" in status_html
