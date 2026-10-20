"""Versioned prompts, structured outputs, and D-owned recovery hooks."""
from pathlib import Path
import json

from pydantic import ValidationError
from langchain_core.exceptions import OutputParserException

from agent.ports import AgentError

PROMPT_DIR = Path(__file__).parent / "prompts"


def read_prompt(name: str) -> tuple[str, str]:
    if name not in {"system", "select_tools", "extract_facts", "explain_risk", "draft_message", "rewrite_message"}:
        raise ValueError("알 수 없는 프롬프트")
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    first, _, body = text.partition("\n")
    if not first.startswith("version: "):
        raise ValueError(f"프롬프트 버전 누락: {name}")
    return first.removeprefix("version: ").strip(), body


class StructuredLLM:
    def __init__(self, primary, fallback, harness, fake_error: str = ""):
        self.primary, self.fallback, self.harness = primary, fallback, harness
        self.fake_error = fake_error

    @classmethod
    def from_settings(cls, harness, settings=None):
        from langchain_anthropic import ChatAnthropic
        from langchain_openai import ChatOpenAI
        from shared.config import get_settings
        settings = settings or get_settings()
        if not settings.ANTHROPIC_API_KEY or not settings.OPENAI_API_KEY:
            raise ValueError("기본·대체 모델 API 키가 필요합니다")
        return cls(
            ChatAnthropic(model=settings.LLM_PRIMARY, api_key=settings.ANTHROPIC_API_KEY),
            ChatOpenAI(model=settings.LLM_FALLBACK, api_key=settings.OPENAI_API_KEY),
            harness, settings.FAKE_LLM_ERROR,
        )

    async def generate(self, prompt, schema, payload):
        version, body = read_prompt(prompt)
        system_version, system = read_prompt("system")
        messages = [("system", system + "\n\n" + body),
                    ("human", json.dumps(payload, ensure_ascii=False))]

        async def invoke(model, fake=False):
            if fake and self.fake_error:
                error = RuntimeError("injected LLM error")
                error.status_code = int(self.fake_error)
                raise error
            name = getattr(model, "model_name", getattr(model, "model", "unknown"))
            result = await model.with_structured_output(schema).ainvoke(
                messages, config={"metadata": {
                    "prompt_version": {"system": system_version, prompt: version},
                    "model": name,
                }},
            )
            return schema.model_validate(result)

        try:
            return await self.harness.recover_llm(
                lambda: invoke(self.primary, True), lambda: invoke(self.fallback),
            )
        except (ValidationError, OutputParserException):
            raise
        except Exception as exc:
            raise AgentError(f"LLM 복구 실패: {type(exc).__name__}", "FAILED") from exc
