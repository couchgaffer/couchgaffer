"""Keyless access to the Foundry model deployments, and the checked call every agent makes
(docs/decisions/0001-models.md)."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Any

from agent_framework import Agent, FunctionInvocationContext, Message, SupportsChatGetResponse, function_middleware
from openai import AsyncOpenAI
from pydantic import BaseModel

from watchparty.agents.checks import undash

# One model call: the structured answer (None when it did not fit the schema), input and output tokens.
type Runner[T: BaseModel] = Callable[[list[Message]], Awaitable[tuple[T | None, int, int]]]

# The tool calls that failed in the current summary (#84), for its fallback log line. Agent Framework hands a
# tool's exception to the model as "Error: Function failed." and the run goes on, so the failure never reaches
# the caller: without this record, a broken match tool reads as failed checks.
tool_failures: ContextVar[list[str] | None] = ContextVar("tool_failures", default=None)


def one_line(text: str, limit: int = 200) -> str:
    """Model-written or remote text, safe for a log line: no newline can forge another line, and it is capped."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _record_tool_failure(tool: str, exc: Exception) -> None:
    collecting = tool_failures.get()
    if collecting is None:  # no caller is collecting: manager and host bids
        return
    collecting.append(f"{tool}: {type(exc).__name__} {one_line(str(exc))}")


@function_middleware
async def record_tool_failures(context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
    try:
        await call_next()
    except Exception as exc:
        _record_tool_failure(context.function.name, exc)
        raise


def foundry_client(endpoint: str) -> AsyncOpenAI:
    """Entra ID via the az login session locally, managed identity on Azure. No API keys exist.

    max_retries=0: callers own the deadline, and a hidden SDK retry would triple a timeout (#6).
    """
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")

    async def api_key() -> str:  # the credential caches tokens; the thread hop only matters on refresh
        return await asyncio.to_thread(token)

    return AsyncOpenAI(base_url=f"{endpoint.rstrip('/')}/openai/v1/", api_key=api_key, max_retries=0)


def agent_runner[T: BaseModel](
    client: AsyncOpenAI, model: str, name: str, instructions: str, schema: type[T], max_tokens: int,
    tools: Sequence[Any] = (),
) -> Runner[T]:  # fmt: skip
    """An Agent Framework agent on a Foundry deployment, with the settings the latency gate chose (#6)."""
    from agent_framework.openai import OpenAIChatClient

    return chat_runner(
        OpenAIChatClient(model=model, async_client=client), name, instructions, schema, max_tokens, tools
    )


def chat_runner[T: BaseModel](
    chat: SupportsChatGetResponse[Any], name: str, instructions: str, schema: type[T], max_tokens: int,
    tools: Sequence[Any] = (),
) -> Runner[T]:  # fmt: skip
    """The agent on any chat client: Foundry in the app, a scripted one in the tests."""
    agent = Agent(chat, instructions=instructions, name=name, tools=list(tools) or None,
                  middleware=[record_tool_failures])  # fmt: skip
    options: Any = {"response_format": schema, "reasoning": {"effort": "none"}, "store": False,
                    "max_tokens": max_tokens}  # fmt: skip

    async def run(messages: list[Message]) -> tuple[T | None, int, int]:
        response = await agent.run(messages, options=options)
        usage = response.usage_details or {}
        value = response.value if isinstance(response.value, schema) else None
        return value, int(usage.get("input_token_count") or 0), int(usage.get("output_token_count") or 0)

    return run


def english_first[T: BaseModel](run: Runner[T], text: str, copy: str) -> Runner[T]:
    """For an English manager there is no copy to write, but the English-first schema (#16, #36) invites the
    model to fill the copy field. With the text empty, the copy is the answer: move it, then check it as usual.
    Otherwise drop it: the checks never read an English manager's copy, so nothing may show it (#49)."""

    async def placed(messages: list[Message]) -> tuple[T | None, int, int]:
        answer, tokens_in, tokens_out = await run(messages)
        if answer is not None and getattr(answer, copy):
            answer = answer.model_copy(update={text: getattr(answer, text) or getattr(answer, copy), copy: ""})
        return answer, tokens_in, tokens_out

    return placed


class FailedChecks(ValueError):
    """The answer broke the checks on both attempts. Carries the second attempt's problems, so a fallback can
    say why (#84)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(problems)  # in args, so a copy or a pickle rebuilds the same error
        self.problems = problems

    def __str__(self) -> str:  # the problems quote model text: one line each, capped
        return "the answer failed the checks twice: " + "; ".join(one_line(p) for p in self.problems)


def undashed[T: BaseModel](answer: T) -> T:
    """Every text field without em or en dashes: rewritten in code, never sent back for a retry (#82)."""
    return answer.model_copy(update={field: undash(value) for field, value in answer if isinstance(value, str)})


async def checked[T: BaseModel](run: Runner[T], request: str, problems: Callable[[T], list[str]]) -> tuple[T, str]:
    """Code checks the answer; one retry with the problems fed back. Returns the answer and "ok" or "retried".

    Raises FailedChecks after a second failure, so the caller's code fallback takes over.
    """
    messages, found = [Message("user", [request])], list[str]()
    for attempt in range(2):
        answer, _, _ = await run(messages)
        answer = undashed(answer) if answer else None  # before the checks, so they read what is shown (#82)
        found = problems(answer) if answer else ["the answer was not valid JSON for the schema"]
        if answer and not found:
            return answer, "ok" if attempt == 0 else "retried"
        feedback = "Your answer broke these rules: " + "; ".join(found) + ". Write a corrected answer."
        reply = answer.model_dump_json() if answer else "(no valid answer)"
        messages = [*messages, Message("assistant", [reply]), Message("user", [feedback])]
    raise FailedChecks(found)
