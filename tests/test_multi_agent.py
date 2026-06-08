import tempfile
import unittest

from paicli_py.agent import AgentOrchestrator, TeamHistory, format_review_summary, parse_review_response, parse_steps_response
from paicli_py.llm import ChatResponse, ToolCall
from paicli_py.tool import ToolRegistry


class MultiAgentLlm:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools):
        system = messages[0].content
        user = messages[-1].content
        self.calls.append((system, user, bool(tools)))
        if "Team Planner" in system:
            return ChatResponse('{"summary":"demo","steps":[{"id":"a","description":"write file","type":"FILE_WRITE","dependencies":[]},{"id":"b","description":"summarize","type":"ANALYSIS","dependencies":["a"]}]}')
        if "Team Reviewer" in system:
            return ChatResponse('{"approved":true,"summary":"ok","issues":[],"suggestions":[]}')
        if messages[-1].role == "tool":
            return ChatResponse("worker wrote file")
        if "Current step [step_1]" in user:
            return ChatResponse("", [ToolCall("call_1", "write_file", {"path": "team.txt", "content": "ok"})])
        self.final_worker_prompt = user
        return ChatResponse("final team summary")


class RejectOnceLlm(MultiAgentLlm):
    def __init__(self):
        super().__init__()
        self.review_count = 0

    def chat(self, messages, tools):
        system = messages[0].content
        if "Team Planner" in system:
            return ChatResponse('{"steps":[{"id":"a","description":"fix","dependencies":[]}]}')
        if "Team Reviewer" in system:
            self.review_count += 1
            if self.review_count == 1:
                return ChatResponse('{"approved":false,"summary":"retry","issues":["missing detail"],"suggestions":[]}')
            return ChatResponse('{"approved":true,"summary":"ok","issues":[],"suggestions":[]}')
        if "Reviewer feedback" in messages[-1].content:
            return ChatResponse("fixed result")
        return ChatResponse("first result")


class MultiAgentTest(unittest.TestCase):
    def test_parse_steps_accepts_tasks_and_normalizes_dependencies(self):
        steps = parse_steps_response('{"tasks":[{"id":"a","description":"one"},{"id":"b","description":"two","dependencies":["a"]}]}')
        self.assertEqual(["step_1", "step_2"], [step.id for step in steps])
        self.assertEqual(["step_1"], steps[1].dependencies)

    def test_parse_review_response_defaults_to_rejected_when_json_missing_approval(self):
        self.assertFalse(parse_review_response('{"summary":"unclear"}').approved)
        self.assertTrue(parse_review_response('{"approved":true}').approved)

    def test_orchestrator_plans_executes_reviews_and_passes_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = MultiAgentLlm()
            result = AgentOrchestrator(llm, ToolRegistry(tmp)).run("do team work")
            self.assertIn("Status: completed", result)
            self.assertIn("final team summary", result)
            self.assertIn("worker wrote file", llm.final_worker_prompt)
            with open(f"{tmp}/team.txt", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_orchestrator_passes_team_history_to_later_steps(self):
        llm = MultiAgentLlm()
        AgentOrchestrator(llm).run("do team work")

        self.assertIn("Team history:", llm.final_worker_prompt)
        self.assertIn("planner created plan", llm.final_worker_prompt)
        self.assertIn("worker step_1 attempt 1: worker wrote file", llm.final_worker_prompt)
        self.assertIn("reviewer step_1 reviewed: approved | ok", llm.final_worker_prompt)

    def test_team_history_compacts_and_bounds_entries(self):
        history = TeamHistory(max_entries=2)
        history.append("worker", "attempt 1", "first")
        history.append("worker", "attempt 2", "second")
        history.append("reviewer", "reviewed", "third\nwith   space")

        rendered = history.format_for_role("team-worker", limit=10)

        self.assertNotIn("first", rendered)
        self.assertIn("second", rendered)
        self.assertIn("third with space", rendered)

    def test_format_review_summary_includes_status_and_feedback(self):
        review = parse_review_response('{"approved":false,"summary":"retry","issues":["missing"],"suggestions":["add detail"]}')

        self.assertEqual("rejected | retry | issues=missing | suggestions=add detail", format_review_summary(review))

    def test_orchestrator_emits_progress_events(self):
        events = []

        def progress(event, payload):
            events.append((event, payload))

        result = AgentOrchestrator(MultiAgentLlm(), progress_handler=progress).run("do team work")

        self.assertIn("Status: completed", result)
        event_names = [event for event, _payload in events]
        self.assertIn("team.planning", event_names)
        self.assertIn("team.planned", event_names)
        self.assertIn("team.step.started", event_names)
        self.assertIn("team.step.reviewed", event_names)
        self.assertIn("team.step.completed", event_names)
        self.assertEqual({"steps": 2}, events[event_names.index("team.planned")][1])

    def test_orchestrator_honors_cancellation_token(self):
        class CancelToken:
            canceled = True

        events = []
        result = AgentOrchestrator(
            MultiAgentLlm(),
            progress_handler=lambda event, payload: events.append((event, payload)),
            cancellation_token=CancelToken(),
        ).run("do team work")

        self.assertEqual("Multi-Agent canceled.", result)
        self.assertEqual([("team.canceled", {})], events)

    def test_reviewer_rejection_retries_worker_with_feedback(self):
        llm = RejectOnceLlm()
        result = AgentOrchestrator(llm, max_retries_per_step=1).run("fix it")
        self.assertIn("Status: completed", result)
        self.assertIn("fixed result", result)
        self.assertEqual(2, llm.review_count)

    def test_failed_dependency_leaves_downstream_pending(self):
        class FailingLlm(MultiAgentLlm):
            def chat(self, messages, tools):
                system = messages[0].content
                if "Team Planner" in system:
                    return ChatResponse('{"steps":[{"id":"a","description":"bad","dependencies":[]},{"id":"b","description":"after","dependencies":["a"]}]}')
                if "Team Reviewer" in system:
                    return ChatResponse('{"approved":false,"issues":["bad"],"suggestions":[]}')
                return ChatResponse("bad result")

        result = AgentOrchestrator(FailingLlm(), max_retries_per_step=0).run("fail")
        self.assertIn("Status: failed", result)
        self.assertIn("[step_2] pending", result)


if __name__ == "__main__":
    unittest.main()
