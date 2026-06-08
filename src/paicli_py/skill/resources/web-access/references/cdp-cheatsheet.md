# CDP Cheatsheet

Use browser MCP tools when a page needs JavaScript rendering, interaction, login
state, or DOM snapshots.

- Prefer `take_snapshot` before screenshots.
- Use `navigate_page`, then wait for stable content before extracting.
- Use screenshots only for visual layout, colors, occlusion, or user-requested evidence.
- For authenticated pages, connect to shared browser state only when the user has asked for it or the content is unreachable otherwise.

