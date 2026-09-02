import base64
import json
import re
from typing import NamedTuple

ENDPOINT = "https://ttt.mi2.ai/v1"
MODEL = "Qwen/Qwen3.8-27B-FP8"
MAX_TOKENS = 24000
TEMPERATURE = 0.0
TIMEOUT = 900
THINK = "medium"
THINK_OFF = "off"
EFFORTS = (THINK_OFF, "low", "medium", "xhigh")

RETRIES = 3
DELAY = 2.0
RETRYABLE_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)

OK = "ok"
TRUNCATED = "truncated"
EMPTY = "empty"
STATES = (OK, TRUNCATED, EMPTY)

THINK_BLOCK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)


class Unreadable(Exception):
    pass


class Settings(NamedTuple):
    model: str = MODEL
    max_tokens: int = MAX_TOKENS
    temperature: float = TEMPERATURE
    think: str = THINK
    timeout: int = TIMEOUT


class Reply(NamedTuple):
    text: str
    finish_reason: str
    reasoning: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float

    def state(self):
        if self.finish_reason and self.finish_reason != "stop":
            return TRUNCATED
        if not self.text.strip():
            return EMPTY
        return OK

    def hit_the_window(self, max_tokens):
        return self.state() == TRUNCATED and self.completion_tokens < max_tokens


def authorization(username, password):
    token = base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
    return "Basic %s" % token


def data_uri(payload, kind="image/png"):
    return "data:%s;base64,%s" % (kind, base64.b64encode(payload).decode("ascii"))


def content_of(prompt, images):
    if not images:
        return prompt
    return ([{"type": "text", "text": prompt}]
            + [{"type": "image_url", "image_url": {"url": uri}} for uri in images])


def request_body(prompt, settings=Settings(), images=()):
    body = {
        "model": settings.model,
        "messages": [{"role": "user", "content": content_of(prompt, images)}],
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "stream": False,
    }
    if settings.think == THINK_OFF:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        body["reasoning_effort"] = settings.think
    return json.dumps(body).encode("utf-8")


def without_thinking(text):
    return THINK_BLOCK.sub("", text or "").strip()


def reply_from(payload, seconds=0.0):
    if not isinstance(payload, dict):
        raise Unreadable("response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise Unreadable("response carries no choices: %s" % str(payload)[:200])
    first = choices[0] or {}
    message = first.get("message") or {}
    usage = payload.get("usage") or {}
    return Reply(without_thinking(message.get("content")),
                 str(first.get("finish_reason") or ""),
                 str(message.get("reasoning") or message.get("reasoning_content") or ""),
                 int(usage.get("prompt_tokens") or 0),
                 int(usage.get("completion_tokens") or 0),
                 round(seconds, 1))


def retryable(status):
    return status in RETRYABLE_STATUS


def complaint_in(payload):
    try:
        document = json.loads(payload)
    except (ValueError, TypeError):
        return str(payload or "")[:300]
    error = document.get("error") if isinstance(document, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error or document)[:300]
