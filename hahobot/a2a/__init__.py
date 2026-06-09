"""Standard A2A (Agent2Agent) protocol adapter for hahobot.

Exposes a JSON-RPC endpoint at ``/a2a`` and an Agent Card at
``/.well-known/agent-card.json`` so that other A2A-compliant agents
can discover and communicate with this bot.
"""

from hahobot.a2a.models import build_agent_card
from hahobot.a2a.server import register_a2a_routes

__all__ = ["build_agent_card", "register_a2a_routes"]
