"""OpenRouter client, JSON parsing with retry, logging, and cost tracking."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from actions import AgentAction
from prompts import STRICT_RETRY_APPENDIX

logger = logging.getLogger("mars_sim")

PARSE_FAILURES_LOG = "logs/parse_failures.jsonl"

MODEL_PRICING: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4": (3.0, 15.0),
    "anthropic/claude-3.5-sonnet": (3.0, 15.0),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "google/gemini-2.0-flash-001": (0.10, 0.40),
    "google/gemini-3.1-flash-lite": (0.25, 1.50),
    "x-ai/grok-4-fast": (2.0, 10.0),
}
DEFAULT_PRICING: tuple[float, float] = (3.0, 15.0)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class LLMResult:
    """Result of a single LLM call."""

    raw_text: str
    prompt_tokens: int
    completion_tokens: int
    parsed_action: AgentAction | None = None
    error: str | None = None


@dataclass
class CostTracker:
    """Accumulates token usage and approximate USD cost."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_calls: int = 0
    failed_parses: int = 0
    model: str = ""

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_calls += 1

    def estimate_usd(self) -> float:
        input_rate, output_rate = MODEL_PRICING.get(self.model, DEFAULT_PRICING)
        input_cost = (self.prompt_tokens / 1_000_000) * input_rate
        output_cost = (self.completion_tokens / 1_000_000) * output_rate
        return input_cost + output_cost

    def summary(self) -> str:
        total = self.prompt_tokens + self.completion_tokens
        return (
            f"LLM usage: {self.total_calls} calls, "
            f"{self.prompt_tokens} prompt + {self.completion_tokens} completion "
            f"= {total} total tokens, ~${self.estimate_usd():.4f} USD (approximate), "
            f"{self.failed_parses} parse failures"
        )


cost_tracker = CostTracker()


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console logging for the simulation."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def create_openrouter_client() -> OpenAI:
    """Create an OpenAI-compatible client pointed at OpenRouter."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://github.com/mars-sim",
            "X-Title": "mars-sim",
        },
    )


def strip_json_fences(text: str) -> str:
    """Remove markdown code fences if the model wrapped JSON."""
    text = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def _extract_json_object(text: str) -> str | None:
    """Extract first JSON object from text."""
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


def _fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] (common LLM mistake)."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _log_parse_failure(raw: str, error: str) -> None:
    """Append parse failure to debug log."""
    os.makedirs(os.path.dirname(PARSE_FAILURES_LOG) or ".", exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "raw_preview": raw[:500],
    }
    with open(PARSE_FAILURES_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def safe_parse_action(raw: str) -> tuple[AgentAction | None, str | None]:
    """Parse and validate LLM output; return (action, error_message)."""
    candidates: list[str] = []
    cleaned = strip_json_fences(raw)
    if cleaned:
        candidates.append(cleaned)
    extracted = _extract_json_object(cleaned)
    if extracted and extracted not in candidates:
        candidates.append(extracted)
    fixed = _fix_trailing_commas(cleaned)
    if fixed not in candidates:
        candidates.append(fixed)
    if extracted:
        fixed_ext = _fix_trailing_commas(extracted)
        if fixed_ext not in candidates:
            candidates.append(fixed_ext)

    last_error = "empty response"
    for attempt_text in candidates:
        try:
            data = json.loads(attempt_text)
            return AgentAction.model_validate(data), None
        except json.JSONDecodeError as exc:
            last_error = f"JSON decode: {exc}"
        except ValidationError as exc:
            last_error = f"Validation: {exc}"
        except TypeError as exc:
            last_error = f"Type: {exc}"

    _log_parse_failure(raw, last_error)
    return None, last_error


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an API error warrants backoff retry."""
    status = getattr(exc, "status_code", None)
    if status in RETRYABLE_STATUS_CODES:
        return True
    msg = str(exc).lower()
    return "429" in msg or "500" in msg or "502" in msg or "503" in msg


def call_agent_llm(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> LLMResult:
    """Single LLM call with JSON mode fallback and API backoff."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    base_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 800,
    }

    last_error: str | None = None
    for api_attempt in range(3):
        should_retry = False
        for use_json_mode in (True, False):
            try:
                call_kwargs = dict(base_kwargs)
                if use_json_mode:
                    call_kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**call_kwargs)
                raw = response.choices[0].message.content or ""
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
                cost_tracker.add(prompt_tokens, completion_tokens)
                return LLMResult(
                    raw_text=raw,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except Exception as exc:
                last_error = str(exc)
                if not use_json_mode:
                    if _is_retryable_error(exc) and api_attempt < 2:
                        sleep_s = 2 ** api_attempt
                        logger.warning(
                            "API error (attempt %d), retrying in %ds: %s",
                            api_attempt + 1,
                            sleep_s,
                            exc,
                        )
                        time.sleep(sleep_s)
                        should_retry = True
                        break
                    logger.error("LLM call failed: %s", exc)
                    return LLMResult(
                        raw_text="",
                        prompt_tokens=0,
                        completion_tokens=0,
                        error=last_error,
                    )
                logger.debug(
                    "JSON mode failed, retrying without response_format: %s",
                    exc,
                )
        if should_retry:
            continue

    return LLMResult(raw_text="", prompt_tokens=0, completion_tokens=0, error=last_error)


def call_with_retry(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> LLMResult:
    """Call LLM with retries on JSON parse failure (up to 3 attempts)."""
    total_prompt = 0
    total_completion = 0
    last_raw = ""
    last_parse_error: str | None = None

    prompts_to_try = [
        user_prompt,
        user_prompt + "\n\n" + STRICT_RETRY_APPENDIX,
    ]

    for attempt_idx, prompt in enumerate(prompts_to_try):
        result = call_agent_llm(client, model, system_prompt, prompt)
        total_prompt += result.prompt_tokens
        total_completion += result.completion_tokens

        if result.error:
            result.prompt_tokens = total_prompt
            result.completion_tokens = total_completion
            cost_tracker.failed_parses += 1
            return result

        last_raw = result.raw_text
        action, parse_error = safe_parse_action(result.raw_text)
        if action is not None:
            result.parsed_action = action
            result.prompt_tokens = total_prompt
            result.completion_tokens = total_completion
            return result

        last_parse_error = parse_error
        if attempt_idx == 0:
            logger.warning("Invalid JSON on first attempt; retrying with strict prompt.")

    # Third attempt: minimal repair prompt with parse error
    if last_parse_error and last_raw:
        repair_prompt = (
            f"Fix this invalid JSON response. Error: {last_parse_error}\n\n"
            f"Original response:\n{last_raw[:1500]}\n\n"
            f"{STRICT_RETRY_APPENDIX}"
        )
        repair_result = call_agent_llm(client, model, system_prompt, repair_prompt)
        total_prompt += repair_result.prompt_tokens
        total_completion += repair_result.completion_tokens

        if not repair_result.error:
            action, parse_error = safe_parse_action(repair_result.raw_text)
            if action is not None:
                repair_result.parsed_action = action
                repair_result.prompt_tokens = total_prompt
                repair_result.completion_tokens = total_completion
                return repair_result
            last_parse_error = parse_error

    cost_tracker.failed_parses += 1
    final = LLMResult(
        raw_text=last_raw,
        prompt_tokens=total_prompt,
        completion_tokens=total_completion,
        error=last_parse_error or "Invalid JSON after retry",
    )
    return final


def reset_cost_tracker(model: str) -> CostTracker:
    """Reset module-level tracker for a new run (mutates in place for import safety)."""
    cost_tracker.prompt_tokens = 0
    cost_tracker.completion_tokens = 0
    cost_tracker.total_calls = 0
    cost_tracker.failed_parses = 0
    cost_tracker.model = model
    return cost_tracker


def log_jsonl(path: str, record: dict[str, Any]) -> None:
    """Append one JSON object as a line to a JSONL file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
