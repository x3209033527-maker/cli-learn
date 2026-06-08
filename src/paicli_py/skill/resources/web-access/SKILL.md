---
name: web-access
description: Choose between web_fetch, web_search, MCP browser tools, and resource mentions for web tasks.
---

# Web Access

Use `web_fetch` for known public URLs. Use `web_search` when the user asks for discovery or current information. Use MCP browser tools when pages require JavaScript rendering, interaction, authentication, or snapshots.

Prefer text extraction before screenshots. For public pages, avoid switching to a shared browser session unless login state is clearly required.

When MCP resources are mentioned with `@server:uri`, rely on the expanded `<resource>` blocks as first-class context.
