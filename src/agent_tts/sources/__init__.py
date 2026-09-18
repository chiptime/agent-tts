"""Connector adapters for structured agent transcripts (Vision B).

agent-tts is the universal voice layer for terminal coding agents. This
package answers "what did the agent just say?" by reading the last assistant
message from each agent tool's local structured transcript (SQLite, JSONL).
Hosts only hand over an agent identity plus a session id; the knowledge about
where and how each tool persists its transcripts lives here, in the engine.
"""

from agent_tts.sources.base import SourceResult, read_last_agent_message

__all__ = ["SourceResult", "read_last_agent_message"]
