# Next Steps

The Python migration now has the core ReAct, Plan-and-Execute with CLI review
decisions and early-failure replanning, Multi-Agent orchestration with bounded
team history, progress events, CLI live progress lines, and cancellation checks,
tools, policy, memory, plan DAGs, conservative file snapshots, local image
input, Browser/CDP session scaffolding, RAG, Web provider configuration, Web
rate limiting, bounded Web responses, readability-to-Markdown extraction, MCP
tools/resources, MCP sampling callback scaffolding with in-memory/stdio/HTTP
request routing, MCP HTTP header/OAuth bearer token injection, and Skill
scaffolds.
Continue with the items below.

## Highest-value next slice

Improve prompt and product parity:

- Add richer terminal controls on top of the simple CLI plan review fallback.
- Expand Multi-Agent terminal UX with richer controls and better history
  inspection.
- Start a richer terminal shell while preserving simple `input()` fallback.

## RAG parity

- Optionally add tree-sitter or another Java parser when dependency policy allows it.

## Web parity

- Optionally refine extraction heuristics with dependency-backed readability
  when dependency policy allows it.

## Product shell

- Evaluate `prompt_toolkit` and `rich` for a Python terminal experience.
- Preserve simple `input()` mode as a fallback.

## Snapshot parity

- Replace the conservative file-copy snapshot scaffold with a full side-git
  implementation when parity with Java `SideGitManager` is required.

## Image parity

- Extend local `@image:path` ReAct input into richer terminal affordances and
  Plan/Multi-Agent paths when those modes need multimodal turns.

## Browser/CDP parity

- Replace the lightweight browser session scaffold with full CDP command
  execution and chrome-devtools MCP shared-mode parity.

## MCP parity

- Add the full MCP OAuth authorization flow and full Streamable HTTP sampling
  response delivery when a server expects client responses over a separate HTTP
  channel.

