# Check optional Feishu dependencies before running tests
try:
    from hahobot.channels import feishu

    FEISHU_AVAILABLE = getattr(feishu, "FEISHU_AVAILABLE", False)
except ImportError:
    FEISHU_AVAILABLE = False

if not FEISHU_AVAILABLE:
    import pytest

    pytest.skip("Feishu dependencies not installed (lark-oapi)", allow_module_level=True)

from hahobot.channels.feishu import FeishuChannel, _extract_element_content, _extract_post_content


def test_extract_post_content_supports_post_wrapper_shape() -> None:
    payload = {
        "post": {
            "zh_cn": {
                "title": "日报",
                "content": [
                    [
                        {"tag": "text", "text": "完成"},
                        {"tag": "img", "image_key": "img_1"},
                    ]
                ],
            }
        }
    }

    text, image_keys = _extract_post_content(payload)

    assert text == "日报 完成"
    assert image_keys == ["img_1"]


def test_extract_post_content_keeps_direct_shape_behavior() -> None:
    payload = {
        "title": "Daily",
        "content": [
            [
                {"tag": "text", "text": "report"},
                {"tag": "img", "image_key": "img_a"},
                {"tag": "img", "image_key": "img_b"},
            ]
        ],
    }

    text, image_keys = _extract_post_content(payload)

    assert text == "Daily report"
    assert image_keys == ["img_a", "img_b"]


def test_extract_post_content_tolerates_null_text_fields() -> None:
    text, image_keys = _extract_post_content(
        {
            "title": "T",
            "content": [
                [
                    {"tag": "text", "text": None},
                    {"tag": "a", "text": None},
                    {"tag": "at", "user_name": None},
                    {"tag": "text", "text": "ok"},
                    {"tag": "code_block", "language": None, "text": None},
                ]
            ],
        }
    )

    assert "@user" in text
    assert "ok" in text
    assert image_keys == []


def test_extract_element_content_tolerates_null_lists_and_multi_url() -> None:
    assert _extract_element_content({"tag": "div", "text": {"content": "hi"}, "fields": None}) == [
        "hi"
    ]
    assert _extract_element_content(
        {"tag": "button", "text": {"content": "Go"}, "multi_url": None}
    ) == ["Go"]
    assert _extract_element_content({"tag": "note", "elements": None}) == []
    assert _extract_element_content({"tag": "column_set", "columns": None}) == []
    assert (
        _extract_element_content({"tag": "column_set", "columns": [None, {"elements": None}]}) == []
    )


def test_register_optional_event_keeps_builder_when_method_missing() -> None:
    class Builder:
        pass

    builder = Builder()
    same = FeishuChannel._register_optional_event(builder, "missing", object())
    assert same is builder


def test_register_optional_event_calls_supported_method() -> None:
    called = []

    class Builder:
        def register_event(self, handler):
            called.append(handler)
            return self

    builder = Builder()
    handler = object()
    same = FeishuChannel._register_optional_event(builder, "register_event", handler)

    assert same is builder
    assert called == [handler]
