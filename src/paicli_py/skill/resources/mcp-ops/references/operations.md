# MCP Operations Checklist

- Startup should register tools only after initialize and list calls succeed.
- Disable/restart should unregister dynamic tools and clear cached resources.
- Notification handlers should refresh tools, resources, or prompts without blocking reader threads.
- Transport failures should record structured events and avoid leaving stale tools registered.
- Auto-restart should be explicit, bounded, and observable in logs.

