import unittest
import tempfile

from paicli_py.agent import PlanExecuteAgent, PlanReviewDecision, parse_plan_response
from paicli_py.llm import ChatResponse, ToolCall
from paicli_py.plan import ExecutionPlan, PlanCycleError, Task
from paicli_py.tool import ToolRegistry


class PlanLlm:
    def __init__(self):
        self.messages = []

    def chat(self, messages, tools):
        self.messages.append(list(messages))
        if not tools:
            return ChatResponse(
                """```json
                {
                  "summary": "create answer",
                  "tasks": [
                    {
                      "id": "task_1",
                      "description": "write answer file",
                      "type": "FILE_WRITE",
                      "dependencies": []
                    },
                    {
                      "id": "task_2",
                      "description": "summarize result",
                      "type": "ANALYSIS",
                      "dependencies": ["task_1"]
                    }
                  ]
                }
                ```"""
            )
        last = messages[-1]
        if last.role == "tool":
            return ChatResponse("file written")
        if "Current task [task_1]" in last.content:
            return ChatResponse(
                "",
                [ToolCall("call_1", "write_file", {"path": "answer.txt", "content": "42"})],
            )
        self.final_task_prompt = last.content
        return ChatResponse("final summary")


class SequencedPlanLlm:
    def __init__(self, plans, task_responses):
        self.plans = list(plans)
        self.task_responses = list(task_responses)
        self.plan_prompts = []

    def chat(self, messages, tools):
        if not tools:
            self.plan_prompts.append(messages[-1].content)
            return ChatResponse(self.plans.pop(0))
        return self.task_responses.pop(0)


class ExecutionPlanTest(unittest.TestCase):
    def test_topological_order_and_batches(self):
        plan = ExecutionPlan("demo")
        plan.add_task(Task("a", "first"))
        plan.add_task(Task("b", "second", dependencies=["a"]))
        plan.add_task(Task("c", "third", dependencies=["a"]))
        self.assertEqual(["a", "b", "c"], plan.execution_order())
        batches = plan.execution_batches()
        self.assertEqual([["a"], ["b", "c"]], [[task.id for task in batch] for batch in batches])

    def test_cycle_detection(self):
        plan = ExecutionPlan("bad")
        plan.add_task(Task("a", "a", dependencies=["b"]))
        plan.add_task(Task("b", "b", dependencies=["a"]))
        with self.assertRaises(PlanCycleError):
            plan.execution_order()

    def test_parse_plan_response_accepts_fenced_json(self):
        plan = parse_plan_response(
            """```json
            {"summary": "demo", "steps": [{"id": "s1", "description": "read", "dependencies": []}]}
            ```"""
        )
        self.assertEqual("demo", plan.goal)
        self.assertEqual(["s1"], plan.execution_order())

    def test_plan_execute_agent_plans_and_executes_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = PlanLlm()
            agent = PlanExecuteAgent(llm, ToolRegistry(tmp))
            result = agent.run("create answer")
            self.assertIn("Status: completed", result)
            self.assertIn("final summary", result)
            self.assertIn("file written", llm.final_task_prompt)
            with open(f"{tmp}/answer.txt", encoding="utf-8") as handle:
                self.assertEqual("42", handle.read())

    def test_plan_review_supplement_replans_before_execution(self):
        first_plan = '{"summary":"first","tasks":[{"id":"a","description":"old","dependencies":[]}]}'
        second_plan = '{"summary":"second","tasks":[{"id":"b","description":"new","dependencies":[]}]}'
        llm = SequencedPlanLlm([first_plan, second_plan], [ChatResponse("done")])
        decisions = [PlanReviewDecision.supplement("use the new path"), PlanReviewDecision.execute()]

        def review(goal, plan):
            return decisions.pop(0)

        result = PlanExecuteAgent(llm, review_handler=review).run("goal")
        self.assertIn("Plan: second", result)
        self.assertIn("new", result)
        self.assertIn("use the new path", llm.plan_prompts[1])

    def test_plan_review_cancel_skips_execution(self):
        plan_json = '{"summary":"first","tasks":[{"id":"a","description":"old","dependencies":[]}]}'
        llm = SequencedPlanLlm([plan_json], [ChatResponse("should not run")])
        result = PlanExecuteAgent(llm, review_handler=lambda _goal, _plan: PlanReviewDecision.cancel()).run("goal")
        self.assertEqual("Plan canceled.", result)
        self.assertEqual(1, len(llm.plan_prompts))
        self.assertEqual(1, len(llm.task_responses))

    def test_early_failure_replans_once(self):
        bad_plan = '{"summary":"bad","tasks":[{"id":"bad","description":"bad tool","dependencies":[]}]}'
        good_plan = '{"summary":"good","tasks":[{"id":"good","description":"finish","dependencies":[]}]}'
        llm = SequencedPlanLlm(
            [bad_plan, good_plan],
            [
                ChatResponse("", [ToolCall("missing", "missing_tool", {})]),
                ChatResponse("recovered"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = PlanExecuteAgent(llm, ToolRegistry(tmp)).run("goal")
        self.assertIn("Plan: good", result)
        self.assertIn("recovered", result)
        self.assertIn("Previous plan failed early", llm.plan_prompts[1])


if __name__ == "__main__":
    unittest.main()
