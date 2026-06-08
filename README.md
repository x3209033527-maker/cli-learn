# PaiCLI Python Migration

This directory is a new Python project that ports the core PaiCLI runtime shape
without modifying the existing Java implementation.

## Current scope

Implemented in this first migration slice:

- OpenAI-compatible LLM client abstraction
- ReAct agent loop
- Tool registry with parallel tool execution
- Safe path guard and command guard
- Built-in tools: `read_file`, `write_file`, `list_dir`, `execute_command`, `index_code`, `search_code`
- Web tools: `web_fetch` with public-URL policy, request rate limiting, bounded response reads, lightweight readability-to-Markdown extraction, and `web_search` via configurable GLM/Zhipu, SerpAPI, or SearXNG providers
- RAG scaffold: SQLite vector store, deterministic local hashed embeddings, OpenAI-compatible embedding provider configuration, brace-aware Java chunking, Java relation extraction/storage, and relation-aware result context
- MCP scaffold: config loader, server manager lifecycle, structured events/logs, transport/listener failure recovery marking, optional auto-restart, JSON-RPC client, stdio and Streamable HTTP transports with configurable headers/OAuth bearer token injection, schema sanitizer, stdio and HTTP background notification listening, refresh hooks, prompt listing/getting, prompt CLI inspection/invocation, dynamic tool registration, resource listing/reading, sampling request callback scaffold with in-memory/stdio/HTTP request routing, and `@server:uri` expansion
- Skill scaffold: bundled skills, `SKILL.md` discovery, reference file loading, frontmatter parsing, `load_skill`, and next-turn context injection
- Prompt layering: package prompt resources for agent, plan, planner, team, and approval profiles, project/user overrides, and ordered assembly
- Minimal memory manager
- Plan-and-Execute core runtime: planner JSON parsing, CLI plan review with execute/cancel/revise decisions, early-failure replanning, DAG execution batches, parallel task execution, per-task tool loops, and CLI `/plan`
- Multi-Agent core runtime: team planner JSON parsing, dependency-aware worker batches, reviewer approval/retry, bounded team history, progress events, cancellation token checks, and CLI `/team` live progress lines
- Runtime scaffold: SQLite durable background tasks, CLI `/task`, plus a local authenticated thread/turn/events API with turn status, cancellation, and incremental message deltas
- Snapshot scaffold: project-local `.paicli-py/snapshots` file snapshots, CLI `/snapshot`, and Agent tools `create_snapshot`, `list_snapshots`, `revert_snapshot`
- Image input scaffold: local `@image:path` references, project-root path policy, base64 data URL conversion, and OpenAI-compatible image content parts for ReAct user turns
- Browser/CDP scaffold: isolated/shared browser session state, local remote-debugging port probe, tab listing via `/json`, sensitive page policy, CLI `/browser`, and Agent tools `browser_status`, `browser_connect`, `browser_disconnect`, `browser_tabs`
- Interactive CLI with `/exit`, `/clear`, `/memory`, `/plan`, `/team`, `/index`, `/search`, `/mcp`, `/mcp resources`, `/mcp prompts`, `/mcp prompt`, `/mcp restart`, `/mcp enable`, `/mcp disable`, `/mcp logs`, `/task`, `/task add`, `/task log`, `/task cancel`, `/snapshot`, `/snapshot create`, `/snapshot show`, `/snapshot revert`, `/browser`, `/browser connect`, `/browser tabs`, `/browser disconnect`, `/skill list`, `/skill show`, `/skill on`, `/skill off`, `/skill reload`
- Unit tests for policy, tools, plan DAG, memory, agent loop, RAG, Skill, Web, and MCP

Not yet ported:

- Full MCP OAuth authorization flow and full Streamable HTTP sampling response delivery
- JLine/Lanterna TUI equivalent
- Productized Multi-Agent terminal UX and richer terminal review controls
- Full Browser/CDP tool execution and shared Chrome MCP parity beyond the scaffold
- Full side-git implementation beyond conservative file snapshots
- Productized image input UX beyond local `@image:path` ReAct turns

## Run

```bash
cd paicli-python
python -m paicli_py.cli
```

Inside the CLI, use `/index` to index the current directory and `/search <query>`
to search indexed code.
Use `/mcp` to inspect configured MCP servers, `/mcp resources [server]` to list
known resources, `/mcp prompts [server]` to list prompts, and
`/mcp prompt <server> <name> [json-args]` to invoke a prompt. Plain user input
can include `@server:uri`; matching MCP resources are expanded into
`<resource>` blocks before the Agent receives the message.
Local images can be attached to a ReAct turn with `@image:path/to/file.png`.
Supported MIME types are PNG, JPEG, WebP, and GIF. Paths are constrained to the
project root and are sent as OpenAI-compatible image content parts.
Skills are loaded from `~/.paicli-py/skills/<name>/SKILL.md` and
`<project>/.paicli-py/skills/<name>/SKILL.md`, with project skills overriding
user skills of the same name.
Disabled skill state is persisted to `~/.paicli-py/skills.json`.
The Python port includes bundled `web-access`, `code-review`, `rag-indexing`,
and `mcp-ops` skills as package data. Skill `references/**/*.md` files are
included in `load_skill` context as reference blocks.
Prompts are loaded from package defaults, with user overrides at
`~/.paicli-py/prompts/...` and project overrides at
`<project>/.paicli-py/prompts/...`.

HTTP MCP servers can provide static headers and bearer tokens in
`.paicli-py/mcp.json`:

```json
{
  "mcpServers": {
    "remote": {
      "url": "https://example.com/mcp",
      "headers": {"X-Client": "paicli-py"},
      "oauth": {"tokenEnv": "REMOTE_MCP_TOKEN"}
    }
  }
}
```

Set one provider key before using a real model:

```bash
set GLM_API_KEY=...
set DEEPSEEK_API_KEY=...
set STEP_API_KEY=...
set KIMI_API_KEY=...
```

For `web_search`, provider selection follows the Java implementation: explicit
`PAICLI_SEARCH_PROVIDER` wins, then `GLM_API_KEY`, `SERPAPI_API_KEY`, and
`SEARXNG_URL` are checked in order. Zhipu is the default placeholder when no
search provider is configured. Search JSON responses are capped by
`PAICLI_SEARCH_MAX_BYTES` and default to 1MB.

```bash
set PAICLI_SEARCH_PROVIDER=zhipu
set GLM_API_KEY=...
set ZHIPU_SEARCH_ENGINE=search_std

set PAICLI_SEARCH_PROVIDER=serpapi
set SERPAPI_API_KEY=...

set PAICLI_SEARCH_PROVIDER=searxng
set SEARXNG_URL=https://your-searxng.example

set PAICLI_SEARCH_MAX_BYTES=1048576
```

RAG embeddings default to deterministic local hashing for offline tests. To use
an OpenAI-compatible embedding API, set:

```bash
set PAICLI_EMBEDDING_PROVIDER=openai
set PAICLI_EMBEDDING_API_KEY=...
set PAICLI_EMBEDDING_API_URL=https://api.openai.com/v1/embeddings
set PAICLI_EMBEDDING_MODEL=text-embedding-3-small
```

## Test

```bash
cd paicli-python
python -m pytest
```

If `pytest` is not installed, the standard-library tests can still be run with:

```bash
python -m unittest discover -s tests
```

## Migration map

| Java module | Python module |
| --- | --- |
| `cli/Main.java` | `paicli_py.cli` |
| `agent/Agent.java` | `paicli_py.agent.react` |
| `agent/PlanExecuteAgent.java` | `paicli_py.agent.plan_execute` |
| `agent/AgentOrchestrator.java` | `paicli_py.agent.orchestrator` |
| `tool/ToolRegistry.java` | `paicli_py.tool.registry` |
| `policy/PathGuard.java` | `paicli_py.policy.path_guard` |
| `policy/CommandGuard.java` | `paicli_py.policy.command_guard` |
| `memory/MemoryManager.java` | `paicli_py.memory.manager` |
| `plan/ExecutionPlan.java` | `paicli_py.plan.execution_plan` |
| `rag/*` | `paicli_py.rag` |
| `mcp/*` | `paicli_py.mcp` |
| `browser/*` | `paicli_py.browser` |
| `runtime/*` | `paicli_py.runtime` |
| `snapshot/*` | `paicli_py.snapshot` |
| `image/*` | `paicli_py.image` |
| `skill/*` | `paicli_py.skill` |
| `prompt/*` | `paicli_py.prompt` |
| `llm/*Client.java` | `paicli_py.llm.openai_compatible` |

