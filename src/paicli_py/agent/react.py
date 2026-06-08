from __future__ import annotations

from paicli_py.llm import Message
from paicli_py.memory import MemoryManager
from paicli_py.prompt import PromptAssembler, PromptContext
from paicli_py.skill import SkillContextBuffer, SkillRegistry
from paicli_py.tool import ToolInvocation, ToolRegistry


class Agent:
    def __init__(
        self,
        llm_client,
        tool_registry: ToolRegistry | None = None,
        memory_manager: MemoryManager | None = None,
        mention_expander=None,
        image_expander=None,
        skill_registry: SkillRegistry | None = None,
        skill_buffer: SkillContextBuffer | None = None,
        prompt_assembler: PromptAssembler | None = None,
        max_iterations: int = 12,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry or ToolRegistry()
        self.memory = memory_manager or MemoryManager()
        self.mention_expander = mention_expander
        self.image_expander = image_expander
        self.skill_registry = skill_registry
        self.skill_buffer = skill_buffer
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.max_iterations = max_iterations
        self.history: list[Message] = [Message.system(self._build_system_prompt(""))]

    def run(self, user_input: str) -> str:
        expanded_input = self.mention_expander.expand(user_input) if self.mention_expander else user_input
        images = []
        if self.image_expander is not None:
            expanded_input, images = self.image_expander.expand(expanded_input)
        if self.skill_buffer is not None and not self.skill_buffer.is_empty():
            expanded_input = self.skill_buffer.drain() + "\n\n" + expanded_input
        self.memory.add_user_message(user_input)
        memory_context = self.memory.context_for(user_input)
        self.history[0] = Message.system(self._build_system_prompt(memory_context))
        self.history.append(Message.user(expanded_input, images))

        for _ in range(self.max_iterations):
            response = self.llm_client.chat(self.history, self.tool_registry.tool_definitions())
            if response.has_tool_calls:
                self.history.append(Message.assistant(response.content, response.tool_calls))
                invocations = [
                    ToolInvocation(call.id, call.name, call.arguments)
                    for call in response.tool_calls
                ]
                results = self.tool_registry.execute_tools(invocations)
                for result in results:
                    self.memory.add_tool_result(result.name, result.result)
                    self.history.append(Message.tool(result.id, result.result))
                continue

            self.history.append(Message.assistant(response.content))
            self.memory.add_assistant_message(response.content)
            return response.content
        return "Stopped: reached max agent iterations."

    def _build_system_prompt(self, memory_context: str) -> str:
        skill_index = self.skill_registry.format_index() if self.skill_registry is not None else ""
        if skill_index:
            skill_index = skill_index + "\nUse load_skill when one matches the task."
        if memory_context:
            memory_context = "## Relevant Long-Term Memory\n" + memory_context
        return self.prompt_assembler.assemble(PromptContext(
            mode="agent",
            skill_index=skill_index,
            project_context=memory_context,
        ))
