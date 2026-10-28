# The match tools (MCP)

Read-only tools over one session's synthetic match, served by our own MCP server (Python SDK `mcp`, FastMCP) inside the app process. Our half-time and full-time summaries use them (#12): the manager agent calls the tools through Agent Framework's MCP client, then writes the summary. In the pitch this is "our MCP server of match tools", not "Azure MCP", which is Microsoft's own product (rule 4.3).

## Tools

Every number is an evidence item (`contracts/schemas/common.schema.json#/$defs/evidence`) with an id. A model cites the ids of the numbers it uses.

| Tool | Input | Returns |
|---|---|---|
| `get_standings` | none | each manager's league points and rank |
| `get_squad_points` | `manager` (`mgr_1` to `mgr_5`) | IP per player and per rule this match, with the captain doubled |
| `get_match_stats` | `from_minute`, `to_minute` (minutes elapsed; default: the match so far) | shots, xG, possession, pass completion and control for both clubs |
| `get_key_moments` | `manager` | the key moments that mattered to that manager, with their stake in each |
| `get_rule_explanation` | `question` | why a rule exists, in words, from the league rulebook in Foundry IQ (#18); only when Foundry IQ is configured |
| `get_player_summary` | `player` (for example `p_home_9`) | events, metrics and IP |
| `get_scoring_rules` | none | impact points rules v1 |

## How it is scoped

- **In process:** the summaries reach the tools in process, over an in-memory transport, never over the network. Each request names the manager it writes for, so the rulebook entries one manager's agent was given are the only ones its summary may cite (#37).
- **Endpoint:** `/mcp/{session_id}/`, only when the app runs with `MCP_PUBLIC=1` (#35). It is off by default, so on the public demo nobody else can spend the match's model or Foundry IQ budget. It uses streamable HTTP, stateless, with JSON responses.
- **Authentication:** `Authorization: Bearer <phone_token>`, the session's phone token. The server keeps no MCP sessions of its own. Any other request gets a bare 404.
- **Snapshots:** `?at=half_time` or `?at=full_time` reads the view frozen at that whistle; without it, the tools read the live match. A summary is written from a frozen view, so its numbers cannot drift while the second half plays on.
- **Rulebook budget:** each summary's agent may make at most two Foundry IQ lookups, and a match at most 20 (`LOOKUPS_PER_SUMMARY` and `RULEBOOK_LOOKUPS` in `backend/watchparty/matchtools/view.py`). A question asked again is answered from the match's own cache (#34). A lookup that fails or takes more than 8 seconds returns no entries, and the summary goes on without them.
- **Checked numbers:** a frozen view records every evidence item it serves. A summary's numbers are accepted only if the tools served them to that manager's agent from that view (another manager's squad points and key moments never count; the standings do), otherwise it falls back to a plain summary from code. The same check runs again before any phone shows the text.

## Connecting an MCP client for debugging

1. Start the app with the endpoint on (`MCP_PUBLIC=1 make run`, or `MCP_PUBLIC=1 uv run uvicorn watchparty.app.main:app` from `backend/`).
2. Create a session:
   ```sh
   curl -s -X POST localhost:8000/api/sessions -H 'content-type: application/json' -d '{"seed":"demo","manager":"mgr_1"}'
   ```
   The response gives `session_id` and `phone_token`. The match starts when a screen connects (open the group screen). A session that never starts is removed after 30 seconds.
3. In VS Code, add `.vscode/mcp.json` (do not commit it; the token is a session secret):
   ```json
   {
     "servers": {
       "watchparty": {
         "type": "http",
         "url": "http://localhost:8000/mcp/<session_id>/",
         "headers": { "Authorization": "Bearer <phone_token>" }
       }
     }
   }
   ```
   Add `?at=half_time` to the URL to read the half-time snapshot once that whistle has gone.

Mention this in the pitch only if we use it in the video.
