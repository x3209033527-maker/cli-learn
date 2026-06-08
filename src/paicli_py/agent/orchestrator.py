from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from paicli_py.llm import Message
from paicli_py.prompt import PromptAssembler, PromptContext
from paicli_py.skill import SkillContextBuffer, SkillRegistry
from paicli_py.tool import ToolInvocation, ToolRegistry


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionStep:
    id: str
    description: str
    type: str = "ANALYSIS"
    dependencies: list[str] | None = None
    result: str = ""
    status: StepStatus = StepStatus.PENDING

    def deps(self) -> list[str]:
        return list(self.dependencies or [])

    def with_status(self, status: StepStatus, result: str | None = None) -> "ExecutionStep":
        return ExecutionStep(self.id, self.description, self.type, self.deps(), self.result if result is None else result, status)


@dataclass(frozen=True)
class ReviewResult:
    approved: bool
    summary: str = ""
    issues: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeamHistoryEntry:
    role: str
    event: str
    step_id: str
    content: str


class TeamHistory:
    def __init__(self, max_entries: int = 40):
        self.max_entries = max(1, max_entries)
        self._entries: list[TeamHistoryEntry] = []
        self._lock = threading.RLock()

    def append(self, role: str, event: str, content: str, step_id: str = "") -> None:
        normalized = _compact_text(content)
        if not normalized:
            return
        with self._lock:
            self._entries.append(TeamHistoryEntry(role, event, step_id, normalized))
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries:]

    def recent(self, limit: int = 12, roles: set[str] | None = None) -> list[TeamHistoryEntry]:
        with self._lock:
            entries = [entry for entry in self._entries if roles is None or entry.role in roles]
            return entries[-max(1, limit):]

    def format_for_role(self, role: str, limit: int = 12) -> str:
        if role == "team-reviewer":
            roles = {"planner", "worker", "reviewer"}
        elif role == "team-worker":
            roles = {"planner", "worker", "reviewer"}
        else:
            roles = {"planner", "reviewer"}
        entries = self.recent(limit, roles)
        if not entries:
            return ""
        lines = ["Team history:"]
        for entry in entries:
            step = f" {entry.step_id}" if entry.step_id else ""
            lines.append(f"- {entry.role}{step} {entry.event}: {entry.content}")
        return "\n".join(lines)


class SubAgent:
    def __init__(self, name: str, mode: str, llm_client, tool_registry: ToolRegistry, prompt_assembler: PromptAssembler | None = None, skill_registry: SkillRegistry | None = None, skill_buffer: SkillContextBuffer | None = None, max_iterations: int = 5):
        self.name = name
        self.mode = mode
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.skill_registry = skill_registry
        self.skill_buffer = skill_buffer
        self.max_iterations = max_iterations

    def execute(self, content: str, tools_enabled: bool | None = None) -> str:
        use_tools = self.mode == "team-worker" if tools_enabled is None else tools_enabled
        messages = [Message.system(self._system_prompt()), Message.user(self._with_skill_context(content))]
        tool_results: list[str] = []
        for _ in range(self.max_iterations):
            response = self.llm_client.chat(messages, self.tool_registry.tool_definitions() if use_tools else [])
            if not response.has_tool_calls:
                return response.content or "\n".join(tool_results).strip()
            messages.append(Message.assistant(response.content, response.tool_calls))
            invocations = [ToolInvocation(call.id, call.name, call.arguments) for call in response.tool_calls]
            for result in self.tool_registry.execute_tools(invocations):
                tool_results.append(result.result)
                messages.append(Message.tool(result.id, result.result))
        return "\n".join(tool_results).strip()

    def _system_prompt(self) -> str:
        skill_index = self.skill_registry.format_index() if self.skill_registry is not None else ""
        return self.prompt_assembler.assemble(PromptContext(mode=self.mode, skill_index=skill_index))

    def _with_skill_context(self, content: str) -> str:
        if self.skill_buffer is not None and not self.skill_buffer.is_empty():
            return self.skill_buffer.drain() + "\n\n" + content
        return content


class MultiAgentCanceled(RuntimeError):
    pass


ProgressHandler = Callable[[str, dict], None]


class AgentOrchestrator:
    MAX_PARALLEL_STEPS = 2

    def __init__(
        self,
        llm_client,
        tool_registry: ToolRegistry | None = None,
        prompt_assembler: PromptAssembler | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_buffer: SkillContextBuffer | None = None,
        max_retries_per_step: int = 2,
        progress_handler: ProgressHandler | None = None,
        cancellation_token=None,
        history: TeamHistory | None = None,
        history_limit: int = 12,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry or ToolRegistry()
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.skill_registry = skill_registry
        self.skill_buffer = skill_buffer
        self.max_retries_per_step = max_retries_per_step
        self.progress_handler = progress_handler
        self.cancellation_token = cancellation_token
        self.history = history or TeamHistory()
        self.history_limit = max(1, history_limit)
        self.planner = self._sub_agent("planner", "team-planner")
        self.reviewer = self._sub_agent("reviewer", "team-reviewer")

    def run(self, user_input: str) -> str:
        try:
            steps = self.create_steps(user_input)
            if not steps:
                return "Multi-Agent planning failed: no executable steps."
            return format_multi_agent_result(self.execute_steps(steps))
        except MultiAgentCanceled:
            self._emit("team.canceled", {})
            return "Multi-Agent canceled."

    def create_steps(self, user_input: str) -> list[ExecutionStep]:
        self._check_canceled()
        self._emit("team.planning", {"input": user_input})
        content = self.planner.execute(f"Create a multi-agent execution plan for:\n{user_input}", tools_enabled=False)
        steps = parse_steps_response(content)
        self.history.append("planner", "created plan", f"{len(steps)} steps")
        self._emit("team.planned", {"steps": len(steps)})
        return steps

    def execute_steps(self, steps: list[ExecutionStep]) -> list[ExecutionStep]:
        current = list(steps)
        while True:
            self._check_canceled()
            ready = executable_steps(current)
            if not ready:
                break
            self._emit("team.batch.started", {"steps": [step.id for step in ready]})
            for step in self._execute_batch(current, ready):
                current = replace_step(current, step)
            self._emit("team.batch.completed", {"steps": [step.id for step in ready]})
        return current

    def _execute_batch(self, all_steps: list[ExecutionStep], batch: list[ExecutionStep]) -> list[ExecutionStep]:
        if len(batch) == 1:
            return [self._run_step(all_steps, batch[0])]
        ordered: list[ExecutionStep | None] = [None] * len(batch)
        max_workers = min(len(batch), self.MAX_PARALLEL_STEPS)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._run_step, all_steps, step): index for index, step in enumerate(batch)}
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
        return [step for step in ordered if step is not None]

    def _run_step(self, all_steps: list[ExecutionStep], step: ExecutionStep) -> ExecutionStep:
        self._check_canceled()
        self._emit("team.step.started", {"step_id": step.id, "description": step.description, "type": step.type})
        worker = self._sub_agent(f"worker-{step.id}", "team-worker")
        context = self._with_history("team-worker", build_step_context(all_steps, step))
        attempt = 0
        last_result = ""
        last_review = ReviewResult(False, "not reviewed")
        while attempt <= self.max_retries_per_step:
            self._check_canceled()
            attempt += 1
            self._emit("team.step.attempt", {"step_id": step.id, "attempt": attempt})
            prompt = f"{context}\n\nCurrent step [{step.id}] ({step.type}): {step.description}"
            if last_review.issues or last_review.suggestions:
                prompt += "\n\nReviewer feedback:\n" + "\n".join([*last_review.issues, *last_review.suggestions])
            last_result = worker.execute(prompt, tools_enabled=True)
            self.history.append("worker", f"attempt {attempt}", last_result, step.id)
            self._check_canceled()
            last_review = self.review_step(step, last_result)
            review_summary = format_review_summary(last_review)
            self.history.append("reviewer", "reviewed", review_summary, step.id)
            self._emit("team.step.reviewed", {"step_id": step.id, "approved": last_review.approved, "summary": last_review.summary})
            if last_review.approved:
                self._emit("team.step.completed", {"step_id": step.id})
                return step.with_status(StepStatus.COMPLETED, last_result)
        failure = last_result or last_review.summary or "review rejected result"
        self._emit("team.step.failed", {"step_id": step.id, "result": failure})
        return step.with_status(StepStatus.FAILED, failure)

    def review_step(self, step: ExecutionStep, result: str) -> ReviewResult:
        prompt = self._with_history("team-reviewer", f"Original step: {step.description}\n\nExecution result:\n{result}")
        content = self.reviewer.execute(prompt, tools_enabled=False)
        return parse_review_response(content)

    def _sub_agent(self, name: str, mode: str) -> SubAgent:
        return SubAgent(name, mode, self.llm_client, self.tool_registry, self.prompt_assembler, self.skill_registry, self.skill_buffer)

    def _check_canceled(self) -> None:
        token = self.cancellation_token
        if token is None:
            return
        throw = getattr(token, "throw_if_canceled", None)
        if callable(throw):
            try:
                throw()
                return
            except RuntimeError as exc:
                raise MultiAgentCanceled(str(exc)) from exc
        canceled = getattr(token, "canceled", False)
        if callable(canceled):
            canceled = canceled()
        if canceled:
            raise MultiAgentCanceled("multi-agent canceled")

    def _emit(self, event: str, payload: dict) -> None:
        if self.progress_handler is None:
            return
        self.progress_handler(event, payload)

    def _with_history(self, role: str, content: str) -> str:
        history = self.history.format_for_role(role, self.history_limit)
        return f"{history}\n\n{content}" if history else content


def parse_steps_response(content: str) -> list[ExecutionStep]:
    try:
        payload = json.loads(_extract_json(content))
    except json.JSONDecodeError:
        return []
    raw_steps = payload.get("steps") or payload.get("tasks") or [] if isinstance(payload, dict) else []
    if not isinstance(raw_steps, list):
        return []
    id_mapping: dict[str, str] = {}
    steps: list[ExecutionStep] = []
    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            continue
        original_id = str(item.get("id") or f"step_{index}")
        step_id = original_id if original_id.startswith("step_") else f"step_{index}"
        id_mapping[original_id] = step_id
        steps.append(ExecutionStep(step_id, str(item.get("description", "")), str(item.get("type", "ANALYSIS")), []))
    normalized: list[ExecutionStep] = []
    for index, item in enumerate(raw_steps):
        if index >= len(steps) or not isinstance(item, dict):
            continue
        dependencies = [id_mapping.get(str(dep), str(dep)) for dep in item.get("dependencies", [])]
        step = steps[index]
        normalized.append(ExecutionStep(step.id, step.description, step.type, dependencies))
    return normalized


def parse_review_response(content: str) -> ReviewResult:
    try:
        payload = json.loads(_extract_json(content))
    except json.JSONDecodeError:
        lowered = content.lower()
        approved = "approved" in lowered or "pass" in lowered
        rejected = "not approved" in lowered or "reject" in lowered or "failed" in lowered
        return ReviewResult(approved and not rejected, content.strip())
    if not isinstance(payload, dict):
        return ReviewResult(False, "review response must be an object")
    return ReviewResult(
        bool(payload.get("approved", False)),
        str(payload.get("summary", "")),
        tuple(str(item) for item in payload.get("issues", []) if item is not None),
        tuple(str(item) for item in payload.get("suggestions", []) if item is not None),
    )


def format_review_summary(review: ReviewResult) -> str:
    status = "approved" if review.approved else "rejected"
    parts = [status]
    if review.summary:
        parts.append(review.summary)
    if review.issues:
        parts.append("issues=" + "; ".join(review.issues))
    if review.suggestions:
        parts.append("suggestions=" + "; ".join(review.suggestions))
    return " | ".join(parts)


def executable_steps(steps: list[ExecutionStep]) -> list[ExecutionStep]:
    status = {step.id: step.status for step in steps}
    return [step for step in steps if step.status == StepStatus.PENDING and all(status.get(dep) == StepStatus.COMPLETED for dep in step.deps())]


def replace_step(steps: list[ExecutionStep], updated: ExecutionStep) -> list[ExecutionStep]:
    return [updated if step.id == updated.id else step for step in steps]


def build_step_context(steps: list[ExecutionStep], current: ExecutionStep) -> str:
    lines = ["Completed dependency results:"]
    for dep_id in current.deps():
        dep = next((step for step in steps if step.id == dep_id), None)
        if dep is None:
            continue
        lines.append(f"- {dep.id}: {dep.description}")
        if dep.result:
            lines.append(dep.result)
    if len(lines) == 1:
        lines.append("none")
    return "\n".join(lines)


def format_multi_agent_result(steps: list[ExecutionStep]) -> str:
    failed = [step for step in steps if step.status == StepStatus.FAILED]
    pending = [step for step in steps if step.status == StepStatus.PENDING]
    status = "failed" if failed else "partial" if pending else "completed"
    lines = ["Multi-Agent Result", f"Status: {status}", f"Steps: {len(steps)}"]
    for step in steps:
        lines.append(f"[{step.id}] {step.status.value} - {step.description}")
        if step.result:
            lines.append(step.result)
    return "\n".join(lines)


def _extract_json(content: str) -> str:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.IGNORECASE | re.DOTALL)
    return fenced.group(1).strip() if fenced else stripped


def _compact_text(content: str, max_chars: int = 500) -> str:
    normalized = re.sub(r"\s+", " ", str(content or "")).strip()
    return normalized if len(normalized) <= max_chars else normalized[:max_chars - 3] + "..."
