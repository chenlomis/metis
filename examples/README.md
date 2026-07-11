# Examples

Small, copyable examples for safer local testing. They avoid real writes unless
the command itself is explicitly about your live Metis state.

| File | Use it for |
|---|---|
| `isolated_sources_smoke.sh` | Try source-list/add/on/off flows against a temp profile. |
| `track_dry_run_capped.sh` | Scan recent application emails without tracker writes, capped for speed and cost. |
| `mcp_status_smoke.py` | Call the local MCP server over stdio and list the exposed tools. |

Persona profiles live in `examples/personas/`. They are synthetic and safe to
commit.
