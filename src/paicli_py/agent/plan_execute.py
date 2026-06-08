from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from paicli_py.llm import Message
from paicli_py.plan import ExecutionPlan, Task
from paicli_py.plan.execution_plan import TaskStatus
from paicli_py.prompt import PromptAssembler, PromptContext
from paicli_py.skill import SkillContextBuffer, SkillRegistry
from paicli_py.tool import ToolInvocation, ToolRegistry


class PlanParseError(RuntimeError):
    pass


class PlanReviewAction(str, Enum):
    EXECUTE = "execute"
    SUPPLEMENT = "supplement"
    CANCEL = "cancel"


@dataclass(frozen=True)
class PlanReviewDecision:
    action: PlanReviewAction
    feedback: str = ""

    @staticmethod
    def execute() -> "PlanReviewDecision":
        return PlanReviewDecision(PlanReviewAction.EXECUTE)

    @staticmethod
    def supplement(feedback: str) -> "PlanReviewDecision":
        return PlanReviewDecision(PlanReviewAction.SUPPLEMENT, feedback)

    @staticmethod
    def cancel() -> "PlanReviewDecision":
        return PlanReviewDecision(PlanReviewAction.CANCEL)


@dataclass(frozen=True)
class PlanTaskResult:
    task_id: str
    result: str
    failed: bool = False


class PlanExecuteAgent:
    MAX_PARALLEL_TASKS = 4

    def __init__(
        self,
        llm_client,
        tool_registry: ToolRegistry | None = None,
        prompt_assembler: PromptAssembler | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_buffer: SkillContextBuffer | None = None,
        review_handler: Callable[[str, ExecutionPlan], PlanReviewDecision] | None = None,
        max_task_iterations: int = 5,
        max_replans: int = 1,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry or ToolRegistry()
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.skill_registry = skill_registry
        self.skill_buffer = skill_buffer
        self.review_handler = review_handler or (lambda _goal, _plan: PlanReviewDecision.execute())
        self.max_task_iterations = max_task_iterations
        self.max_replans = max_replans

    def run(self, goal: str) -> str:
        return self._run_with_replanning(goal, self.max_replans)

    def _run_with_replanning(self, goal: str, replans_remaining: int) -> str:
        plan = self.review_plan(goal, self.create_plan(goal))
        if plan is None:
            return "Plan canceled."
        result = self.execute_plan(plan)
        failed = [task for task in plan.tasks.values() if task.status == TaskStatus.FAILED]
        if failed and replans_remaining > 0 and _plan_progress(plan) < 0.5:
            failure_summary = "\n".join(f"- {task.id}: {task.result}" for task in failed)
            replanned_goal = f"{goal}\n\nPrevious plan failed early. Replan around these failures:\n{failure_summary}"
            return self._run_with_replanning(replanned_goal, replans_remaining - 1)
        return result

    def create_plan(self, goal: str) -> ExecutionPlan:
        prompt = self.prompt_assembler.assemble(PromptContext(mode="planner"))
        response = self.llm_client.chat([Message.system(prompt), Message.user(goal)], [])
        return parse_plan_response(response.content, fallback_goal=goal)

    def review_plan(self, goal: str, plan: ExecutionPlan) -> ExecutionPlan | None:
        while True:
            decision = self.review_handler(goal, plan)
            if decision is None or decision.action == PlanReviewAction.EXECUTE:
                return plan
            if decision.action == PlanReviewAction.CANCEL:
                return None
            feedback = decision.feedback.strip()
            if not feedback:
                return plan
            plan = self.create_plan(f"{goal}\n\nSupplemental planning feedback:\n{feedback}")

    def execute_plan(self, plan: ExecutionPlan) -> str:
        plan.execution_order()
        completed: list[PlanTaskResult] = []
        batches = plan.execution_batches()
        for batch in batches:
            results = self._execute_batch(plan, batch)
            for result in results:
                task = plan.tasks[result.task_id]
                task.result = result.result
                task.status = TaskStatus.FAILED if result.failed else TaskStatus.COMPLETED
                completed.append(result)
            if any(result.failed for result in results):
                break
        return format_plan_result(plan, completed)

    def _execute_batch(self, plan: ExecutionPlan, batch: list[Task]) -> list[PlanTaskResult]:
        if len(batch) == 1:
            return [self._execute_task(plan, batch[0])]
        results: list[PlanTaskResult | None] = [None] * len(batch)
        max_workers = min(len(batch), self.MAX_PARALLEL_TASKS)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(self._execute_task, plan, task): index
                for index, task in enumerate(batch)
            }
            for future in as_completed(future_to_index):
                results[future_to_index[future]] = future.result()
        return [result for result in results if result is not None]

    def _execute_task(self, plan: ExecutionPlan, task: Task) -> PlanTaskResult:
        task.status = TaskStatus.RUNNING
        messages = [
            Message.system(self._task_system_prompt()),
            Message.user(self._task_user_prompt(plan, task)),
        ]
        tool_results: list[str] = []
        for _ in range(self.max_task_iterations):
            response = self.llm_client.chat(messages, self.tool_registry.tool_definitions())
            if not response.has_tool_calls:
                result = response.content or "\n".join(tool_results).strip()
                return PlanTaskResult(task.id, result)
            messages.append(Message.assistant(response.content, response.tool_calls))
            invocations = [
                ToolInvocation(call.id, call.name, call.arguments)
                for call in response.tool_calls
            ]
            results = self.tool_registry.execute_tools(invocations)
            for result in results:
                tool_results.append(result.result)
                messages.append(Message.tool(result.id, result.result))
                if _tool_result_failed(result.result):
                    return PlanTaskResult(task.id, result.result, failed=True)
        return PlanTaskResult(task.id, "\n".join(tool_results).strip(), failed=False)

    def _task_system_prompt(self) -> str:
        skill_index = self.skill_registry.format_index() if self.skill_registry is not None else ""
        return self.prompt_assembler.assemble(PromptContext(
            mode="plan",
            skill_index=skill_index,
        ))

    def _task_user_prompt(self, plan: ExecutionPlan, task: Task) -> str:
        lines = [
            f"Goal: {plan.goal}",
            f"Current task [{task.id}] ({task.type}): {task.description}",
        ]
        if task.dependencies:
            lines.append("Dependency results:")
            for dependency in task.dependencies:
                dep = plan.tasks.get(dependency)
                if dep is None:
                    continue
                lines.append(f"- {dep.id}: {dep.description}")
                if dep.result:
                    lines.append(dep.result)
        else:
            lines.append("Dependency results: none")
        lines.append("Execute this task. For ANALYSIS or VERIFICATION tasks, answer directly when context is sufficient.")
        content = "\n".join(lines)
        if self.skill_buffer is not None and not self.skill_buffer.is_empty():
            return self.skill_buffer.drain() + "\n\n" + content
        return content


def parse_plan_response(content: str, fallback_goal: str = "") -> ExecutionPlan:
    try:
        payload = json.loads(_extract_json(content))
    except json.JSONDecodeError as exc:
        raise PlanParseError(f"invalid planner JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanParseError("planner JSON must be an object")
    raw_tasks = payload.get("tasks") or payload.get("steps") or []
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanParseError("planner JSON must include non-empty tasks or steps")
    plan = ExecutionPlan(str(payload.get("summary") or payload.get("goal") or fallback_goal))
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            raise PlanParseError(f"task {index} must be an object")
        task_id = str(item.get("id") or f"task_{index}")
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise PlanParseError(f"task {task_id} dependencies must be a list")
        plan.add_task(Task(
            task_id,
            str(item.get("description", "")),
            str(item.get("type", "ANALYSIS")),
            [str(dep) for dep in dependencies],
        ))
    plan.execution_order()
    return plan


def format_plan_result(plan: ExecutionPlan, results: list[PlanTaskResult]) -> str:
    failed = [result for result in results if result.failed]
    lines = [
        f"Plan: {plan.goal}",
        f"Tasks: {len(plan.tasks)}",
        "Status: failed" if failed else "Status: completed",
    ]
    for task_id in plan.execution_order():
        task = plan.tasks[task_id]
        status = task.status.value if hasattr(task.status, "value") else str(task.status)
        lines.append(f"[{task.id}] {status} - {task.description}")
        if task.result:
            lines.append(task.result)
    return "\n".join(lines)


def _extract_json(content: str) -> str:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.IGNORECASE | re.DOTALL)
    return fenced.group(1).strip() if fenced else stripped


def _tool_result_failed(result: str) -> bool:
    normalized = result.lower().strip()
    return (
        normalized.startswith("unknown tool:")
        or normalized.startswith("policy denied:")
        or normalized.startswith("tool failed:")
    )


def _plan_progress(plan: ExecutionPlan) -> float:
    if not plan.tasks:
        return 0.0
    completed = sum(1 for task in plan.tasks.values() if task.status == TaskStatus.COMPLETED)
    return completed / len(plan.tasks)
