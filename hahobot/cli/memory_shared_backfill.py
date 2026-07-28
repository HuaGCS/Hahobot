"""Rendering helpers for the shared-memory backfill CLI."""

from __future__ import annotations

from typing import Any


def render_shared_memory_backfill_text(payload: dict[str, Any]) -> str:
    """Render a concise report without exposing candidate memory bodies."""
    totals = payload["totals"]
    status = str(payload["status"])
    title = "Shared-memory backfill preview" if payload.get("dryRun") else "Shared-memory backfill"
    lines = [
        title,
        f"Status: {status}",
        f"Workspace: {payload['workspace']}",
        f"Personas: {', '.join(payload['selectedPersonas'])}",
        f"Files scanned: {totals['filesScanned']} (missing: {totals['filesMissing']})",
        (
            "Candidate writes: "
            f"{totals['candidateWrites']} "
            f"(public: {totals['publicWrites']}, "
            f"persona-private: {totals['personaPrivateWrites']})"
        ),
        f"Skipped sources: {totals['skipped']}",
    ]
    statuses = totals.get("statuses") or {}
    if not payload.get("dryRun") and statuses:
        lines.append("Delivery: " + ", ".join(f"{key}={value}" for key, value in statuses.items()))
    routes: dict[tuple[str, str, str, str], int] = {}
    for item in payload.get("items") or []:
        key = (
            str(item["persona"]),
            str(item["sourceFile"]),
            str(item["layer"]),
            str(item["userId"]),
        )
        routes[key] = routes.get(key, 0) + 1
    if routes:
        lines.append("Routes:")
        for (persona, source, layer, user_id), count in routes.items():
            lines.append(f"- {persona}:{source} -> {layer}:{user_id} ({count} write(s))")
    skipped = payload.get("skipped") or []
    if skipped:
        lines.append("Skipped:")
        lines.extend(
            f"- {item['persona']}:{item['sourceFile']} ({item['reason']})" for item in skipped
        )
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    if payload.get("dryRun"):
        lines.append("No network request or local shared-memory state write was performed.")
        lines.append("Run again without --dry-run to queue and submit this plan.")
    elif status in {"queued", "partial"}:
        lines.append(
            "Undelivered writes are durable in the local outbox and will retry when Mem0 is available."
        )
    return "\n".join(lines)
