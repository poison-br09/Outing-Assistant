"""Nemotron failure handling: timeout, API error, invalid structured output, retry, success."""
import httpx
import pytest
from openai import APIError, APITimeoutError
from pydantic import BaseModel

from app.config import Settings
from app.llm.client import LLMClient, LLMUnavailable, extract_json

REQ = httpx.Request("POST", "http://test")


class Out(BaseModel):
    answer: str


def client(retries=1) -> LLMClient:
    return LLMClient(Settings(nvidia_api_key="k", nvidia_nemotron_model="m", llm_max_retries=retries,
                              _env_file=None))


def script(c: LLMClient, *results):
    calls = iter(results)

    def fake(system, user):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r
    c._chat = fake  # type: ignore[method-assign]
    return c


def test_not_configured():
    with pytest.raises(LLMUnavailable) as e:
        LLMClient(Settings(_env_file=None)).complete_json("s", "u", Out)
    assert e.value.reason == "not_configured"


@pytest.mark.parametrize("failure,reason", [
    (APITimeoutError(request=REQ), "timeout"),
    (APIError("boom", request=REQ, body=None), "api_error"),
    ("this is not json", "invalid_output"),
    ('{"wrong_field": 1}', "invalid_output"),
])
def test_failures_exhaust_bounded_retries(failure, reason):
    c = script(client(retries=1), failure, failure)
    with pytest.raises(LLMUnavailable) as e:
        c.complete_json("s", "u", Out)
    assert e.value.reason == reason


def test_retry_then_success_and_think_block_stripped():
    c = script(client(retries=1), APITimeoutError(request=REQ), '<think>hmm {x}</think>\n```json\n{"answer": "hi"}\n```')
    data, info = c.complete_json("s", "u", Out)
    assert data.answer == "hi" and info.used and info.attempts == 2 and info.model == "m"


def test_no_retries_means_single_attempt():
    c = script(client(retries=0), "garbage")  # a second call would raise StopIteration
    with pytest.raises(LLMUnavailable):
        c.complete_json("s", "u", Out)


def test_extract_json():
    assert extract_json('noise {"a": {"b": 1}} tail') == '{"a": {"b": 1}}'
    with pytest.raises(ValueError):
        extract_json("nothing here")
