from __future__ import annotations

import json
import sys
from pathlib import Path

from paicli_py.agent import Agent, AgentOrchestrator, PlanExecuteAgent, PlanReviewDecision
from paicli_py.browser import BrowserService, handle_browser_command
from paicli_py.config import PaiCliConfig
from paicli_py.image import ImageInputExpander
from paicli_py.llm import OpenAICompatibleClient
from paicli_py.mcp.manager import McpServerManager
from paicli_py.mcp.mention import AtMentionExpander
from paicli_py.plan import ExecutionPlan, Task
from paicli_py.runtime import DurableTaskManager, handle_task_command
from paicli_py.snapshot import SnapshotService, handle_snapshot_command
from paicli_py.skill import SkillContextBuffer, SkillRegistry
from paicli_py.tool import ToolRegistry


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    project_path = Path.cwd()
    config = PaiCliConfig.load(project_path)
    if config is None:
        print("No API key found. Set GLM_API_KEY, DEEPSEEK_API_KEY, STEP_API_KEY, or KIMI_API_KEY.")
        return 2
    llm = OpenAICompatibleClient(config.api_url, config.api_key, config.model)
    skill_registry = SkillRegistry(project_path)
    skill_registry.reload()
    skill_buffer = SkillContextBuffer()
    registry = ToolRegistry(project_path, skill_registry=skill_registry, skill_buffer=skill_buffer)
    browser_service = BrowserService()
    registry.browser_service = browser_service
    mcp_manager = McpServerManager(registry, project_path)
    mcp_manager.load_configured_servers()
    mcp_manager.start_all()
    agent = Agent(
        llm,
        registry,
        mention_expander=AtMentionExpander(mcp_manager),
        image_expander=ImageInputExpander(project_path),
        skill_registry=skill_registry,
        skill_buffer=skill_buffer,
    )
    plan_agent = PlanExecuteAgent(
        llm,
        registry,
        skill_registry=skill_registry,
        skill_buffer=skill_buffer,
        review_handler=_review_plan_for_cli,
    )
    team_agent = AgentOrchestrator(
        llm,
        registry,
        skill_registry=skill_registry,
        skill_buffer=skill_buffer,
        progress_handler=_make_team_progress_handler(),
    )
    task_manager = DurableTaskManager.open_default(agent.run)
    task_manager.start()
    snapshot_service = SnapshotService(project_path)
    print(f"PaiCLI Python 0.1.0 - model={config.model} provider={config.provider}")
    print("Type /exit to quit, /memory for memory status, /mcp for MCP status, /skill list for skills.")
    while True:
        try:
            text = input("* ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            task_manager.close()
            return 0
        if not text:
            continue
        if text in {"/exit", "exit", "quit"}:
            task_manager.close()
            return 0
        if text == "/clear":
            agent.memory.clear_short_term()
            print("cleared short-term memory")
            continue
        if text == "/memory":
            print(agent.memory.status())
            continue
        if text == "/mcp" or text.startswith("/mcp "):
            print(_handle_mcp_command(text, mcp_manager))
            continue
        if text == "/skill" or text.startswith("/skill "):
            print(_handle_skill_command(text, skill_registry))
            continue
        if text == "/task" or text.startswith("/task "):
            print(_handle_task_command(text, task_manager))
            continue
        if text == "/snapshot" or text.startswith("/snapshot "):
            print(_handle_snapshot_command(text, snapshot_service))
            continue
        if text == "/browser" or text.startswith("/browser "):
            print(_handle_browser_command(text, browser_service))
            continue
        if text == "/index":
            print(registry.execute_tool_for_cli("index_code", {}))
            continue
        if text.startswith("/search "):
            print(registry.execute_tool_for_cli("search_code", {"query": text[len("/search "):]}))
            continue
        if text.startswith("/save "):
            agent.memory.store_fact(text[len("/save "):])
            print("saved")
            continue
        if text.startswith("/plan "):
            print(plan_agent.run(text[len("/plan "):]))
            continue
        if text.startswith("/team "):
            print(team_agent.run(text[len("/team "):]))
            continue
        print(agent.run(text))


def _describe_plan(goal: str) -> str:
    plan = ExecutionPlan(goal)
    try:
        data = json.loads(goal)
        tasks = data.get("tasks", [])
        plan = ExecutionPlan(data.get("goal", "json plan"))
        for item in tasks:
            plan.add_task(Task(
                str(item["id"]),
                str(item.get("description", "")),
                str(item.get("type", "command")),
                [str(dep) for dep in item.get("dependencies", [])],
            ))
    except Exception:
        plan.add_task(Task("task_1", goal))
    batches = plan.execution_batches()
    lines = [f"Plan: {plan.goal}", f"Tasks: {len(plan.tasks)}", f"Batches: {len(batches)}"]
    for index, batch in enumerate(batches, start=1):
        lines.append(f"  batch {index}: " + ", ".join(task.id for task in batch))
    return "\n".join(lines)


def _format_plan_for_review(goal: str, plan: ExecutionPlan) -> str:
    batches = plan.execution_batches()
    lines = [
        f"Plan review: {goal}",
        f"Planner summary: {plan.goal}",
        f"Tasks: {len(plan.tasks)}",
        f"Batches: {len(batches)}",
    ]
    for index, batch in enumerate(batches, start=1):
        lines.append(f"Batch {index}:")
        for task in batch:
            deps = ", ".join(task.dependencies) if task.dependencies else "-"
            lines.append(f"  - {task.id} [{task.type}] {task.description} deps={deps}")
    lines.append("Enter=execute | c=cancel | i <feedback>=revise")
    return "\n".join(lines)


def _review_plan_for_cli(
    goal: str,
    plan: ExecutionPlan,
    read_line=input,
    write_line=print,
) -> PlanReviewDecision:
    write_line(_format_plan_for_review(goal, plan))
    response = read_line("plan> ").strip()
    normalized = response.lower()
    if not response or normalized in {"y", "yes", "run", "execute"}:
        return PlanReviewDecision.execute()
    if normalized in {"c", "cancel", "esc"}:
        return PlanReviewDecision.cancel()
    for prefix in ("i ", "s ", "revise ", "feedback "):
        if normalized.startswith(prefix):
            return PlanReviewDecision.supplement(response[len(prefix):].strip())
    return PlanReviewDecision.supplement(response)


def _make_team_progress_handler(write_line=print):
    def handle(event: str, payload: dict) -> None:
        line = _format_team_progress_event(event, payload)
        if line:
            write_line(line)

    return handle


def _format_team_progress_event(event: str, payload: dict) -> str:
    if event == "team.planning":
        return "team: planning"
    if event == "team.planned":
        return f"team: planned {payload.get('steps', 0)} steps"
    if event == "team.batch.started":
        return f"team: batch started {_join_ids(payload.get('steps', []))}"
    if event == "team.batch.completed":
        return f"team: batch completed {_join_ids(payload.get('steps', []))}"
    if event == "team.step.started":
        return f"team: step {payload.get('step_id', '?')} started - {payload.get('description', '')}".rstrip()
    if event == "team.step.attempt":
        return f"team: step {payload.get('step_id', '?')} attempt {payload.get('attempt', 1)}"
    if event == "team.step.reviewed":
        status = "approved" if payload.get("approved") else "rejected"
        summary = str(payload.get("summary", "")).strip()
        return f"team: step {payload.get('step_id', '?')} review {status}" + (f" - {summary}" if summary else "")
    if event == "team.step.completed":
        return f"team: step {payload.get('step_id', '?')} completed"
    if event == "team.step.failed":
        return f"team: step {payload.get('step_id', '?')} failed"
    if event == "team.canceled":
        return "team: canceled"
    return ""


def _join_ids(values) -> str:
    items = [str(value) for value in values if value is not None]
    return ", ".join(items) if items else "-"


def _handle_mcp_command(text: str, mcp_manager: McpServerManager) -> str:
    parts = text.split()
    if len(parts) == 1:
        return mcp_manager.format_status()
    command = parts[1]
    if command == "resources":
        server = parts[2] if len(parts) > 2 else None
        resources = mcp_manager.list_resources(server)
        if not resources:
            return "No MCP resources."
        return "\n".join(
            f"@{resource.server_name}:{resource.uri} {resource.name}".strip()
            for resource in resources
        )
    if command == "prompts":
        server = parts[2] if len(parts) > 2 else None
        prompts = mcp_manager.list_prompts(server)
        if not prompts:
            return "No MCP prompts."
        return "\n".join(_format_prompt_descriptor(prompt) for prompt in prompts)
    if command == "prompt":
        parts = text.split(maxsplit=4)
        if len(parts) < 4:
            return "Usage: /mcp prompt <server> <name> [json-args]"
        server_name = parts[2]
        prompt_name = parts[3]
        try:
            arguments = json.loads(parts[4]) if len(parts) > 4 else {}
        except json.JSONDecodeError as exc:
            return f"Invalid JSON args: {exc}"
        if not isinstance(arguments, dict):
            return "Invalid JSON args: expected object"
        messages = mcp_manager.get_prompt(server_name, prompt_name, arguments)
        if not messages:
            return "MCP prompt returned no messages."
        return "\n".join(
            f"{message.role}: {_format_prompt_message_content(message)}"
            for message in messages
        )
    if command in {"restart", "enable", "disable", "logs"}:
        if len(parts) < 3:
            return f"Usage: /mcp {command} <name>"
        name = parts[2]
        if command == "restart":
            return mcp_manager.restart(name)
        if command == "enable":
            return mcp_manager.enable(name)
        if command == "disable":
            return mcp_manager.disable(name)
        return mcp_manager.logs(name)
    return "Usage: /mcp [resources [server] | prompts [server] | prompt <server> <name> [json-args] | restart <name> | enable <name> | disable <name> | logs <name>]"


def _format_prompt_descriptor(prompt) -> str:
    arguments = getattr(prompt, "arguments", [])
    if not arguments:
        suffix = ""
    else:
        names = []
        for argument in arguments:
            marker = "*" if getattr(argument, "required", False) else ""
            names.append(f"{argument.name}{marker}")
        suffix = " args=" + ",".join(names)
    description = f" - {prompt.description}" if getattr(prompt, "description", "") else ""
    return f"@{prompt.server_name}:{prompt.name}{suffix}{description}"


def _format_prompt_message_content(message) -> str:
    if getattr(message, "content_type", "text") == "text":
        return getattr(message, "text", "")
    mime_type = getattr(message, "mime_type", "")
    data = getattr(message, "data", "")
    label = f"{message.content_type} {mime_type}".strip()
    return f"[{label} {len(data)} chars]"


def _handle_task_command(text: str, task_manager: DurableTaskManager) -> str:
    payload = text[len("/task"):].strip() if text.startswith("/task") else text
    return handle_task_command(task_manager, payload)


def _handle_snapshot_command(text: str, snapshot_service: SnapshotService) -> str:
    payload = text[len("/snapshot"):].strip() if text.startswith("/snapshot") else text
    return handle_snapshot_command(snapshot_service, payload)


def _handle_browser_command(text: str, browser_service: BrowserService) -> str:
    payload = text[len("/browser"):].strip() if text.startswith("/browser") else text
    return handle_browser_command(browser_service, payload)


def _handle_skill_command(text: str, skill_registry: SkillRegistry) -> str:
    parts = text.split(maxsplit=2)
    if len(parts) == 1 or parts[1] == "list":
        skills = skill_registry.all_skills()
        if not skills:
            return "No skills found."
        return "\n".join(
            f"- {'on' if skill.enabled else 'off'} {skill.name}: {skill.description}"
            for skill in skills
        )
    command = parts[1]
    if command == "reload":
        skill_registry.reload()
        return "skills reloaded"
    if command in {"show", "on", "off"}:
        if len(parts) < 3 or not parts[2].strip():
            return f"Usage: /skill {command} <name>"
        name = parts[2].strip()
        if command == "show":
            skill = skill_registry.get(name)
            return skill.body if skill else f"Skill not found: {name}"
        return skill_registry.set_enabled(name, command == "on")
    return "Usage: /skill [list | show <name> | on <name> | off <name> | reload]"


if __name__ == "__main__":
    raise SystemExit(main())
