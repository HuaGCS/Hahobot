"""Companion scene shortcut commands for AgentLoop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hahobot.agent.i18n import text
from hahobot.agent.personas import (
    DEFAULT_PERSONA,
    load_persona_reference_images,
    load_persona_scene_settings,
    resolve_persona_reference_image,
)
from hahobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


@dataclass(frozen=True)
class _SceneTemplate:
    prompt: str
    caption_key: str
    scene_key: str | None = None


@dataclass(frozen=True)
class SceneGenerationSpec:
    """Resolved scene generation request shared by chat commands and admin preview."""

    subcommand: str
    prompt: str
    caption_key: str
    caption_override: str | None = None
    reference_selector: str | None = None


_BUILTIN_SCENES = {
    "daily": _SceneTemplate(
        prompt=(
            "Create a warm shared-life snapshot of the user and their AI companion in an "
            "ordinary daily moment. Keep it candid and believable: natural home or cafe "
            "details, soft daylight or practical indoor lighting, relaxed body language, "
            "comfortable everyday clothing, realistic anatomy, and a quiet sense of being "
            "together instead of a posed studio image."
        ),
        caption_key="scene_caption_daily",
        scene_key="daily",
    ),
    "comfort": _SceneTemplate(
        prompt=(
            "Create a gentle comfort scene of the user with their AI companion staying close "
            "and reassuring. Use a calm indoor setting, soft warm lighting, subtle caring "
            "gestures, natural proximity, and a low-pressure emotional tone. Keep the image "
            "grounded, tender, and realistic rather than theatrical."
        ),
        caption_key="scene_caption_comfort",
        scene_key="comfort",
    ),
    "date": _SceneTemplate(
        prompt=(
            "Create a natural date photo of the user and their AI companion together. Pick a "
            "specific believable setting such as an evening walk, small restaurant, riverside, "
            "or bookstore. Keep wardrobe, pose, lighting, and expressions coherent with the "
            "environment so it feels like a real shared memory."
        ),
        caption_key="scene_caption_date",
        scene_key="date",
    ),
}


def _render_caption(template: str, persona: str) -> str:
    try:
        return template.format(persona=persona)
    except Exception:
        return template


def extract_generated_path(result: str) -> str | None:
    """Extract the saved image path from ImageGenTool's success message."""
    if not isinstance(result, str):
        return None
    marker = "File path: "
    if marker not in result:
        return None
    raw_path = result.split(marker, 1)[1].splitlines()[0].strip()
    if not raw_path:
        return None
    return str(Path(raw_path))


def available_scene_names(workspace: Path, persona: str | None) -> list[str]:
    """Return built-in plus persona-defined custom scene names."""
    settings = load_persona_scene_settings(workspace, persona)
    images = load_persona_reference_images(workspace, persona)
    names = set(_BUILTIN_SCENES)
    names.update(settings.prompts)
    names.update(settings.captions)
    names.update(images.scenes)
    builtins = [name for name in _BUILTIN_SCENES if name in names]
    custom = sorted(name for name in names if name not in _BUILTIN_SCENES)
    return [*builtins, *custom]


def scene_reference_selector(
    workspace: Path,
    *,
    persona: str | None,
    scene_key: str | None,
    image_gen_default_reference: str = "",
) -> str | None:
    """Resolve the best image_gen reference selector for a scene request."""
    if scene_key and resolve_persona_reference_image(workspace, persona, scene_key):
        return f"__default__:{scene_key}"
    if resolve_persona_reference_image(workspace, persona, None):
        return "__default__"
    if image_gen_default_reference:
        return "__default__"
    return None


def build_scene_generation_spec(
    workspace: Path,
    *,
    persona: str | None,
    subcommand: str,
    brief: str | None = None,
    image_gen_default_reference: str = "",
) -> SceneGenerationSpec | None:
    """Resolve prompt, caption, and reference selector for one scene request."""
    scene_settings = load_persona_scene_settings(workspace, persona)
    template = _BUILTIN_SCENES.get(subcommand)
    if template is None:
        if subcommand == "generate":
            if not brief:
                return None
            template = _SceneTemplate(
                prompt=(
                    "Create a polished companion-style image of the user with their AI companion. "
                    f"Scene brief: {brief.strip()}\n"
                    "Keep the composition specific and believable, preserve the companion's appearance "
                    "from the reference image when available, and avoid generic AI-art poses."
                ),
                caption_key="scene_caption_generate",
            )
        elif subcommand in available_scene_names(workspace, persona):
            template = _SceneTemplate(
                prompt=(
                    "Create a polished companion-style image of the user with their AI companion. "
                    f"Scene theme: {subcommand}\n"
                    "Keep the composition specific, believable, and grounded in one clear setting. "
                    "Preserve the companion's appearance from the reference image when available, "
                    "and avoid generic AI-art poses."
                ),
                caption_key="scene_caption_custom",
                scene_key=subcommand,
            )
        else:
            return None

    prompt = template.prompt
    prompt_override = scene_settings.prompts.get(subcommand)
    if prompt_override:
        prompt = f"{prompt}\nPersona scene guidance: {prompt_override}"

    return SceneGenerationSpec(
        subcommand=subcommand,
        prompt=prompt,
        caption_key=template.caption_key,
        caption_override=scene_settings.captions.get(subcommand),
        reference_selector=scene_reference_selector(
            workspace,
            persona=persona,
            scene_key=template.scene_key,
            image_gen_default_reference=image_gen_default_reference,
        ),
    )


def render_scene_caption(
    language: str,
    *,
    persona_label: str,
    spec: SceneGenerationSpec,
) -> str:
    """Render the final user-facing caption for a generated scene."""
    if spec.caption_override:
        return _render_caption(spec.caption_override, persona_label)
    kwargs = {"persona": persona_label}
    if spec.caption_key == "scene_caption_custom":
        kwargs["scene"] = spec.subcommand.replace("_", " ").replace("-", " ")
    return text(language, spec.caption_key, **kwargs)


class SceneCommandHandler:
    """Render companion-oriented image scenes without going through a full LLM turn."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

    def usage(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return self._response(msg, text(language, "scene_usage"))

    def missing_brief(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return self._response(msg, text(language, "scene_missing_brief"))

    def list(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        language = self.loop._get_session_language(session)
        persona = self.loop._get_session_persona(session)
        items = "\n".join(f"- {name}" for name in available_scene_names(self.loop.workspace, persona))
        return self._response(
            msg,
            text(
                language,
                "scene_available",
                persona=self._persona_label(language, persona),
                items=items,
            ),
        )

    def _persona_label(self, language: str, persona: str | None) -> str:
        if persona and persona != DEFAULT_PERSONA:
            return persona
        return text(language, "scene_persona_fallback")

    def unknown_scene(self, msg: InboundMessage, language: str, persona: str | None, name: str) -> OutboundMessage:
        items = "\n".join(f"- {scene}" for scene in available_scene_names(self.loop.workspace, persona))
        return self._response(
            msg,
            text(language, "scene_unknown", name=name, items=items),
        )

    async def generate(
        self,
        msg: InboundMessage,
        session: Session,
        *,
        subcommand: str,
        brief: str | None = None,
    ) -> OutboundMessage:
        language = self.loop._get_session_language(session)
        persona = self.loop._get_session_persona(session)
        spec = build_scene_generation_spec(
            self.loop.workspace,
            persona=persona,
            subcommand=subcommand,
            brief=brief,
            image_gen_default_reference=getattr(self.loop.image_gen_config, "reference_image", ""),
        )
        if spec is None:
            return self.unknown_scene(msg, language, persona, subcommand)

        params = {"prompt": spec.prompt}
        if spec.reference_selector:
            params["reference_image"] = spec.reference_selector

        if not self.loop.tools.has("image_gen"):
            return self._response(msg, text(language, "scene_tool_disabled"))

        self.loop._set_tool_context(
            msg.channel,
            msg.chat_id,
            msg.metadata.get("message_id"),
            persona=persona,
        )

        tool, cast_params, error = self.loop.tools.prepare_call("image_gen", params)
        if error:
            return self._response(msg, text(language, "scene_generation_failed", error=error))

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**cast_params)
        except Exception as exc:
            result = f"Error generating scene: {exc}"

        output_path = extract_generated_path(result) if isinstance(result, str) else None
        if output_path is None or not Path(output_path).exists():
            error_message = result if isinstance(result, str) else text(language, "generic_error")
            return self._response(
                msg,
                text(language, "scene_generation_failed", error=error_message),
            )

        persona_label = self._persona_label(language, persona)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=render_scene_caption(language, persona_label=persona_label, spec=spec),
            media=[output_path],
        )
