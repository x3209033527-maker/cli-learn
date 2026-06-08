import unittest
from dataclasses import dataclass

from paicli_py.agent import PlanReviewAction
from paicli_py.cli import (
    _format_plan_for_review,
    _format_team_progress_event,
    _handle_browser_command,
    _handle_mcp_command,
    _handle_snapshot_command,
    _handle_skill_command,
    _handle_task_command,
    _make_team_progress_handler,
    _review_plan_for_cli,
)
from paicli_py.mcp import McpPromptArgument, McpPromptDescriptor, McpPromptMessage
from paicli_py.plan import ExecutionPlan, Task
from paicli_py.skill import Skill


@dataclass(frozen=True)
class FakeResource:
    server_name: str
    uri: str
    name: str


class FakeMcpManager:
    def __init__(self):
        self.calls = []

    def format_status(self):
        return "status"

    def list_resources(self, server=None):
        self.calls.append(("resources", server))
        return [FakeResource("demo", "file://a.md", "A")]

    def list_prompts(self, server=None):
        self.calls.append(("prompts", server))
        return [
            McpPromptDescriptor(
                "demo",
                "review",
                "Review code",
                [McpPromptArgument("file", required=True)],
            )
        ]

    def get_prompt(self, server, name, arguments=None):
        self.calls.append(("prompt", server, name, arguments or {}))
        return [McpPromptMessage("user", "text", text=f"{name}:{arguments.get('file')}")]

    def restart(self, name):
        self.calls.append(("restart", name))
        return f"restart {name}"

    def enable(self, name):
        self.calls.append(("enable", name))
        return f"enable {name}"

    def disable(self, name):
        self.calls.append(("disable", name))
        return f"disable {name}"

    def logs(self, name):
        self.calls.append(("logs", name))
        return f"logs {name}"


class FakeSkillRegistry:
    def __init__(self):
        self.skills = {
            "demo": Skill("demo", "demo skill", "body", str(__file__), enabled=True),
        }
        self.calls = []

    def all_skills(self):
        return list(self.skills.values())

    def reload(self):
        self.calls.append(("reload", None))

    def get(self, name):
        return self.skills.get(name)

    def set_enabled(self, name, enabled):
        self.calls.append(("set_enabled", name, enabled))
        skill = self.skills[name]
        self.skills[name] = Skill(skill.name, skill.description, skill.body, skill.path, enabled=enabled)
        return f"skill {'enabled' if enabled else 'disabled'}: {name}"


class FakeTask:
    def __init__(self, task_id="task_1", status="enqueued", prompt="demo", result=""):
        from paicli_py.runtime import TaskStatus
        self.id = task_id
        self.status = TaskStatus.from_value(status)
        self.prompt = prompt
        self.result = result
        self.error = ""
        self.created_at = "now"
        self.started_at = ""
        self.finished_at = ""
        self.duration_ms = 0

    @property
    def terminal(self):
        return self.status.value in {"completed", "failed", "canceled"}

    def short_prompt(self, max_chars=80):
        return self.prompt


class FakeTaskManager:
    def __init__(self):
        self.tasks = {"task_1": FakeTask("task_1", "completed", "demo", "ok")}
        self.calls = []

    def list(self, limit=20):
        self.calls.append(("list", limit))
        return list(self.tasks.values())[:limit]

    def enqueue(self, prompt):
        self.calls.append(("enqueue", prompt))
        task = FakeTask("task_2", "enqueued", prompt)
        self.tasks[task.id] = task
        return task

    def cancel(self, task_id):
        self.calls.append(("cancel", task_id))
        return task_id in self.tasks

    def find(self, task_id):
        self.calls.append(("find", task_id))
        return self.tasks.get(task_id)


class FakeSnapshotService:
    def __init__(self):
        self.calls = []

    def list(self, limit=20):
        self.calls.append(("list", limit))
        return []

    def create(self, label=""):
        self.calls.append(("create", label))
        from paicli_py.snapshot import Snapshot
        return Snapshot("snap_1", label, "now", 0, 0)


class FakeBrowserService:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append(("status", None))
        return "browser status"

    def connect(self, port=9222):
        self.calls.append(("connect", port))
        return f"connect {port}"

    def disconnect(self):
        self.calls.append(("disconnect", None))
        return "disconnect"

    def tabs(self):
        self.calls.append(("tabs", None))
        return "tabs"


class CliTest(unittest.TestCase):
    def test_plan_review_formatter_shows_batches_and_dependencies(self):
        plan = ExecutionPlan("ship feature")
        plan.add_task(Task("a", "inspect code", "ANALYSIS"))
        plan.add_task(Task("b", "write tests", "TEST", ["a"]))

        output = _format_plan_for_review("goal text", plan)

        self.assertIn("Plan review: goal text", output)
        self.assertIn("Planner summary: ship feature", output)
        self.assertIn("Batch 1:", output)
        self.assertIn("a [ANALYSIS] inspect code deps=-", output)
        self.assertIn("b [TEST] write tests deps=a", output)

    def test_plan_review_empty_input_executes(self):
        plan = ExecutionPlan("demo")
        plan.add_task(Task("a", "inspect"))
        writes = []

        decision = _review_plan_for_cli(
            "goal",
            plan,
            read_line=lambda prompt: "",
            write_line=writes.append,
        )

        self.assertEqual(PlanReviewAction.EXECUTE, decision.action)
        self.assertIn("Enter=execute", writes[0])

    def test_plan_review_cancel_input_cancels(self):
        plan = ExecutionPlan("demo")
        plan.add_task(Task("a", "inspect"))

        decision = _review_plan_for_cli(
            "goal",
            plan,
            read_line=lambda prompt: "c",
            write_line=lambda _line: None,
        )

        self.assertEqual(PlanReviewAction.CANCEL, decision.action)

    def test_plan_review_feedback_input_supplements(self):
        plan = ExecutionPlan("demo")
        plan.add_task(Task("a", "inspect"))

        decision = _review_plan_for_cli(
            "goal",
            plan,
            read_line=lambda prompt: "i check docs first",
            write_line=lambda _line: None,
        )

        self.assertEqual(PlanReviewAction.SUPPLEMENT, decision.action)
        self.assertEqual("check docs first", decision.feedback)

    def test_team_progress_event_formatter(self):
        self.assertEqual("team: planning", _format_team_progress_event("team.planning", {}))
        self.assertEqual("team: planned 2 steps", _format_team_progress_event("team.planned", {"steps": 2}))
        self.assertEqual(
            "team: batch started step_1, step_2",
            _format_team_progress_event("team.batch.started", {"steps": ["step_1", "step_2"]}),
        )
        self.assertEqual(
            "team: step step_1 review approved - ok",
            _format_team_progress_event("team.step.reviewed", {"step_id": "step_1", "approved": True, "summary": "ok"}),
        )
        self.assertEqual("", _format_team_progress_event("team.unknown", {}))

    def test_team_progress_handler_writes_known_events_only(self):
        writes = []
        handler = _make_team_progress_handler(writes.append)

        handler("team.step.completed", {"step_id": "step_1"})
        handler("team.unknown", {})

        self.assertEqual(["team: step step_1 completed"], writes)

    def test_mcp_status_and_resources(self):
        manager = FakeMcpManager()
        self.assertEqual("status", _handle_mcp_command("/mcp", manager))
        resources = _handle_mcp_command("/mcp resources demo", manager)
        self.assertIn("@demo:file://a.md A", resources)
        self.assertEqual(("resources", "demo"), manager.calls[-1])

    def test_mcp_lifecycle_commands(self):
        manager = FakeMcpManager()
        self.assertEqual("restart demo", _handle_mcp_command("/mcp restart demo", manager))
        self.assertEqual("enable demo", _handle_mcp_command("/mcp enable demo", manager))
        self.assertEqual("disable demo", _handle_mcp_command("/mcp disable demo", manager))
        self.assertEqual("logs demo", _handle_mcp_command("/mcp logs demo", manager))

    def test_mcp_usage_for_missing_name(self):
        self.assertIn("Usage", _handle_mcp_command("/mcp restart", FakeMcpManager()))

    def test_mcp_prompt_commands(self):
        manager = FakeMcpManager()
        prompts = _handle_mcp_command("/mcp prompts demo", manager)
        self.assertIn("@demo:review args=file* - Review code", prompts)
        self.assertEqual(("prompts", "demo"), manager.calls[-1])

        result = _handle_mcp_command('/mcp prompt demo review {"file":"a.py"}', manager)
        self.assertEqual("user: review:a.py", result)
        self.assertEqual(("prompt", "demo", "review", {"file": "a.py"}), manager.calls[-1])

    def test_mcp_prompt_usage_and_json_errors(self):
        self.assertIn("Usage", _handle_mcp_command("/mcp prompt demo", FakeMcpManager()))
        self.assertIn("Invalid JSON", _handle_mcp_command("/mcp prompt demo review nope", FakeMcpManager()))
        self.assertIn("expected object", _handle_mcp_command("/mcp prompt demo review []", FakeMcpManager()))

    def test_task_commands(self):
        manager = FakeTaskManager()
        self.assertIn("task_1", _handle_task_command("/task", manager))
        self.assertIn("background task submitted", _handle_task_command("/task add build docs", manager))
        self.assertEqual(("enqueue", "build docs"), manager.calls[-1])
        self.assertIn("Task task_1", _handle_task_command("/task log task_1", manager))
        self.assertIn("cancel requested", _handle_task_command("/task cancel task_1", manager))

    def test_snapshot_commands(self):
        service = FakeSnapshotService()
        self.assertEqual("No snapshots.", _handle_snapshot_command("/snapshot", service))
        self.assertIn("snapshot created: snap_1", _handle_snapshot_command("/snapshot create before change", service))
        self.assertEqual(("create", "before change"), service.calls[-1])

    def test_browser_commands(self):
        service = FakeBrowserService()
        self.assertEqual("browser status", _handle_browser_command("/browser", service))
        self.assertEqual("connect 9223", _handle_browser_command("/browser connect 9223", service))
        self.assertEqual(("connect", 9223), service.calls[-1])
        self.assertEqual("tabs", _handle_browser_command("/browser tabs", service))

    def test_skill_commands(self):
        registry = FakeSkillRegistry()
        self.assertIn("on demo", _handle_skill_command("/skill list", registry))
        self.assertEqual("body", _handle_skill_command("/skill show demo", registry))
        self.assertEqual("skill disabled: demo", _handle_skill_command("/skill off demo", registry))
        self.assertIn("off demo", _handle_skill_command("/skill list", registry))
        self.assertEqual("skill enabled: demo", _handle_skill_command("/skill on demo", registry))
        self.assertEqual("skills reloaded", _handle_skill_command("/skill reload", registry))


if __name__ == "__main__":
    unittest.main()
