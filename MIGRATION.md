# Java to Python Migration Plan

## Goal

Build a Python implementation of PaiCLI while preserving the Java project as
the reference implementation until feature parity is proven.

## Porting strategy

1. Port the stable runtime contracts first: LLM client, messages, tools,
   memory, policy, plan model, and ReAct loop.
2. Keep behavior testable with standard-library unit tests before adding rich
   terminal UI or network-heavy integrations.
3. Move feature families in slices instead of class-by-class translation.
4. Preserve Java naming where it clarifies lineage, but use idiomatic Python
   packages, dataclasses, and exceptions.

## Completed first slice

- `paicli_py.llm`: OpenAI-compatible message and tool schema.
- `paicli_py.agent`: ReAct loop with tool call feedback and a core
  Plan-and-Execute runtime with CLI review/supplement/cancel decisions, early
  failure replanning, and a core Multi-Agent orchestrator with bounded team
  history, progress events, CLI live progress lines, and cancellation checks.
- `paicli_py.tool`: built-in filesystem and command tools.
- `paicli_py.policy`: path and command guards.
- `paicli_py.memory`: short-term entries and explicit facts.
- `paicli_py.plan`: DAG ordering, cycle detection, and execution batches used
  by `/plan`.
- `paicli_py.cli`: minimal interactive shell.
- `paicli_py.rag`: text/brace-aware Java chunking, deterministic local
  embeddings, OpenAI-compatible embedding provider configuration, SQLite vector
  store, Java relation extraction/storage, relation-aware result context, and
  code retrieval tools.
- `paicli_py.mcp`: JSON-RPC client, MCP tool descriptors, schema sanitizer,
  config loader, server manager lifecycle, structured events/logs, in-memory,
  stdio, and Streamable HTTP transports with configurable headers/OAuth bearer
  token injection, stdio and HTTP background notification listening,
  transport/listener failure recovery marking, optional auto-restart, refresh
  hooks, prompt listing/getting, prompt CLI inspection/invocation,
  dynamic ToolRegistry registration, resource cache, sampling request callback
  scaffolding with in-memory/stdio/HTTP request routing, and `@server:uri` mention
  expansion.
- `paicli_py.skill`: `SKILL.md` discovery, frontmatter parsing,
  enabled/disabled state persistence, `SkillContextBuffer`, `load_skill`,
  bundled `web-access`, `code-review`, `rag-indexing`, and `mcp-ops` skills,
  reference file loading, and next-turn Agent context injection.
- `paicli_py.prompt`: package prompt resources for agent, plan, planner, team,
  and approval profiles, user/project overrides, and ordered prompt assembly.
- `paicli_py.runtime`: SQLite durable background tasks, `/task` CLI command formatting,
  runtime thread/event store, and local authenticated Runtime API with turn cancellation and incremental message deltas.
- `paicli_py.snapshot`: conservative project-local file snapshots, `/snapshot`
  CLI command formatting, and Agent snapshot/revert tools.
- `paicli_py.image`: local `@image:path` parsing, project-root guarded image
  loading, and OpenAI-compatible image content parts for ReAct user turns.
- `paicli_py.browser`: isolated/shared browser session state, local
  remote-debugging probes, tab listing, sensitive page policy, CLI `/browser`,
  and Agent browser session tools.
- `paicli_py.web`: public URL policy, request rate limiting, bounded response
  reads, lightweight readability-to-Markdown extraction, `web_fetch`, and
  configurable GLM/Zhipu, SerpAPI, or SearXNG-backed `web_search`.

## Next slices

### Slice 4: Productized plan and Multi-Agent UX

- Add richer terminal controls on top of the simple CLI plan review fallback.
- Expand Multi-Agent terminal UX with richer controls and better history
  inspection.

### Slice 5: Product shell

- Replace basic `input()` CLI with a richer terminal UX.
- Candidate libraries: `prompt_toolkit`, `rich`, and `textual`.

### Slice 6: Runtime hardening

### Slice 7: Snapshot parity hardening

- Replace file-copy snapshots with full side-git parity if exact Java
  `SideGitManager` behavior is required.

### Slice 8: Image input hardening

- Add richer terminal affordances and carry image inputs into Plan/Multi-Agent
  paths if those modes need multimodal turns.

### Slice 9: Browser/CDP hardening

- Replace the lightweight browser session scaffold with full CDP command
  execution and chrome-devtools MCP shared-mode parity.

### Slice 10: MCP hardening

- Add the full MCP OAuth authorization flow and full Streamable HTTP sampling
  response delivery when a server expects client responses over a separate HTTP
  channel.


