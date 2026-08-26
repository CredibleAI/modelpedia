import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from modelpedia import atomic
from modelpedia import console
from modelpedia import paths
from modelpedia.ingest import chat
from modelpedia.ingest import prompt as promptlib
from modelpedia.ingest import text as textutil

PROMPTS = paths.PROMPTS
LOG = "_log.jsonl"
TRUNCATED_SUFFIX = ".truncated"
UNUSABLE = 3

URL_VAR = "MODEL_API_URL"
MODEL_VAR = "MODEL_API_MODEL"
USER_VAR = "MODEL_API_USERNAME"
PASSWORD_VAR = "MODEL_API_PASSWORD"

NETWORK = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, OSError)

VALUE_OPTIONS = ("--dir", "--out", "--only", "--limit", "--model", "--max-tokens",
                 "--temperature", "--think", "--timeout", "--delay", "--pdf")
FLAGS = ("--force", "--dry-run")


def fail(message):
    print("ERROR %s" % message)
    return 1


class Refused(Exception):
    pass


def endpoint():
    return os.environ.get(URL_VAR) or chat.ENDPOINT


def credentials():
    username = os.environ.get(USER_VAR)
    password = os.environ.get(PASSWORD_VAR)
    if not username or not password:
        raise SystemExit(fail("%s and %s must be set in the environment" % (USER_VAR, PASSWORD_VAR)))
    return chat.authorization(username, password)


def call(path, header, body=None, timeout=chat.TIMEOUT):
    """Raises on every failure, including a refusal from the server: a request that did not
    happen must never reach the caller looking like an answer."""
    request = urllib.request.Request(
        endpoint() + path, data=body,
        headers={"Authorization": header, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        note = chat.complaint_in(error.read().decode("utf-8", errors="replace"))
        if chat.retryable(error.code):
            raise
        raise Refused("HTTP %d: %s" % (error.code, note))


def ask(prompt, header, settings, retries=chat.RETRIES, delay=chat.DELAY, images=()):
    body = chat.request_body(prompt, settings, images)
    last = None
    for attempt in range(retries):
        started = time.time()
        try:
            payload = call("/chat/completions", header, body, settings.timeout)
            return chat.reply_from(payload, time.time() - started)
        except NETWORK as error:
            last = error
            if attempt + 1 == retries:
                break
            wait = delay * (2 ** attempt)
            print("  RETRY in %.1fs: %s" % (wait, error))
            time.sleep(wait)
    raise Refused("%d attempts failed, last: %s" % (retries, last))


def options(rest):
    given, index = {}, 0
    while index < len(rest):
        flag = rest[index]
        if flag in FLAGS:
            given[flag] = True
            index += 1
            continue
        if flag not in VALUE_OPTIONS:
            raise ValueError("unknown option %r; expected one of %s"
                             % (flag, ", ".join(VALUE_OPTIONS + FLAGS)))
        if index + 1 >= len(rest):
            raise ValueError("%s needs a value" % flag)
        given[flag] = rest[index + 1]
        index += 2
    return given


def positive(value, flag):
    if value is None:
        return None
    if not str(value).isdigit() or int(value) < 1:
        raise ValueError("%s takes a positive whole number, not %r" % (flag, value))
    return int(value)


def number(value, flag, most=None):
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError("%s takes a number, not %r" % (flag, value))
    if parsed < 0 or (most is not None and parsed > most):
        raise ValueError("%s takes a number between 0 and %s, not %r"
                         % (flag, most if most is not None else "infinity", value))
    return parsed


def effort(value):
    if value is None:
        return None
    if value not in chat.EFFORTS:
        raise ValueError("--think takes %s, not %r" % ("/".join(chat.EFFORTS), value))
    return value


def settings_from(given):
    temperature = number(given.get("--temperature"), "--temperature", 2.0)
    return chat.Settings(
        model=given.get("--model") or os.environ.get(MODEL_VAR) or chat.MODEL,
        max_tokens=positive(given.get("--max-tokens"), "--max-tokens") or chat.MAX_TOKENS,
        temperature=chat.TEMPERATURE if temperature is None else temperature,
        think=effort(given.get("--think")) or chat.THINK,
        timeout=positive(given.get("--timeout"), "--timeout") or chat.TIMEOUT)


def chosen(folder, only):
    available = {path.stem: path for path in sorted(folder.glob("*.txt"))}
    if not only:
        return [available[name] for name in sorted(available)], []
    wanted = [name.strip() for name in only.split(",") if name.strip()]
    return ([available[name] for name in wanted if name in available],
            [name for name in wanted if name not in available])


JPEG_QUALITY = 80


def page_uris(pdf):
    """The paper as pictures. JPEG rather than PNG: measured on a 29-page ICLR paper, both cost
    the model exactly 1927 tokens per page, and JPEG is a third of the bytes on the wire."""
    images = textutil.page_images(pdf)
    return [chat.data_uri(textutil.jpeg_bytes(image, JPEG_QUALITY), "image/jpeg")
            for image in images]


def default_out(source, pdfs, think=chat.THINK):
    """One directory per run, named for what the run varied: the input (text, page images, both)
    and the thinking budget, always both, never abbreviated.

    The first version left the budget out when it matched the current default, which made the
    directory name depend on a constant. Changing that constant to `medium` on 2026-08-21 silently
    pointed a fresh run at the directory holding the old `low` answers, mixed nine files into it
    and skipped the 21 papers it should have redone. The prompt version stays out of the name
    on purpose -- `prompt_sha` in the log carries it exactly, and a name cannot."""
    folder = Path(source).name
    with_pages = folder == Path(paths.PAGE_PROMPTS).name
    if with_pages and pdfs:
        name = "pages"
    elif pdfs:
        name = "text-and-pages"
    elif folder == Path(paths.PROMPTS).name:
        name = "text"
    else:
        name = folder[len("prompts-"):] if folder.startswith("prompts-") else folder
    return paths.RUNS / ("%s-%s" % (name, think))


def fingerprint(body):
    """Which prompt produced this answer. Two runs of one paper differ by the prompt far more
    often than by anything else, and a run nobody can attribute is a run nobody can compare."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def fingerprints(body):
    """The whole prompt, and the instructions without the paper. The first tells two runs of one
    paper apart; the second tells two states of the base apart, and only it can, because the paper
    text dominates the whole-prompt hash and makes every paper differ from every other regardless.
    ICLR 2025 and ICLR 2024 were asked from the same directory under instructions that had already
    diverged after 25488 identical characters, and nothing in the log said so."""
    return fingerprint(body), fingerprint(promptlib.instructions(body))


def log_row(handle, name, settings, state, reply, note="", prompt_sha="", context_sha=""):
    handle.write(json.dumps({
        "prompt": name, "prompt_sha": prompt_sha, "context_sha": context_sha,
        "model": settings.model, "think": settings.think,
        "max_tokens": settings.max_tokens, "state": state,
        "finish_reason": reply.finish_reason if reply else "",
        "prompt_tokens": reply.prompt_tokens if reply else 0,
        "completion_tokens": reply.completion_tokens if reply else 0,
        "reasoning_chars": len(reply.reasoning) if reply else 0,
        "seconds": reply.seconds if reply else 0.0, "note": note},
        ensure_ascii=False) + "\n")
    handle.flush()


def report(counts, failures, seconds, prompt_tokens, completion_tokens, target,
           windowed=0):
    print("\n%s written to %s"
          % (console.plural(counts[chat.OK], "answer"), target))
    for state in (chat.TRUNCATED, chat.EMPTY):
        if counts[state]:
            print("  %-10s %d" % (state, counts[state]))
    if failures:
        print("  %-10s %d" % ("failed", len(failures)))
    for name, why in failures:
        print("  FAILED %-14s %s" % (name, why))
    if seconds:
        seconds.sort()
        print("  seconds per answer: min %.1f, median %.1f, max %.1f"
              % (seconds[0], seconds[len(seconds) // 2], seconds[-1]))
        print("  tokens: %d in, %d out" % (sum(prompt_tokens), sum(completion_tokens)))
    if counts[chat.TRUNCATED]:
        print("  a truncated answer is kept as <name>%s, which extract.py collect ignores"
              % TRUNCATED_SUFFIX)
        if counts[chat.TRUNCATED] - windowed:
            print("  %d ran out of max_tokens: raise --max-tokens or lower --think and ask again"
                  % (counts[chat.TRUNCATED] - windowed))
        if windowed:
            print("  %d filled the model's own context window, so raising --max-tokens will not"
                  % windowed)
            print("  help; send fewer pages, or send the text instead of the images")


def run(source, target, settings, only=None, limit=None, force=False, dry=False,
        delay=0.0, pdfs=None):
    folder = Path(source)
    if not folder.is_dir():
        return fail("%s is not a directory; run: python3 extract.py prompts" % source)
    prompts, missing = chosen(folder, only)
    for name in missing:
        print("  WARN %s has no prompt in %s, skipped" % (name, folder))
    if not prompts:
        return fail("no prompts in %s" % folder)

    target = Path(target)
    held = [path for path in prompts if (target / path.name).exists() and not force]
    todo = [path for path in prompts if path not in held]
    capped = len(todo) - limit if limit and len(todo) > limit else 0
    if capped:
        todo = todo[:limit]

    print("%d prompts in %s, %d already answered, asking %d%s"
          % (len(prompts), folder, len(held), len(todo),
             ", %d held back by --limit" % capped if capped else ""))
    print("  %s, think %s, max_tokens %d, temperature %s%s"
          % (settings.model, settings.think, settings.max_tokens, settings.temperature,
             ", pages rendered from %s" % pdfs if pdfs else ""))
    if dry:
        print("\nnothing sent; drop --dry-run to ask")
        return 1 if missing else 0
    if not todo:
        return 1 if missing else 0

    target.mkdir(parents=True, exist_ok=True)
    atomic.clear_partials(target)
    header = credentials()
    counts = {state: 0 for state in chat.STATES}
    failures, seconds, prompt_tokens, completion_tokens = [], [], [], []
    windowed = 0
    with (target / LOG).open("a", encoding="utf-8") as log:
        for number, path in enumerate(todo, start=1):
            prompt = path.read_text(encoding="utf-8", errors="replace")
            prompt_sha, context_sha = fingerprints(prompt)
            try:
                images = page_uris(Path(pdfs) / ("%s.pdf" % path.stem)) if pdfs else ()
                reply = ask(prompt, header, settings, images=images)
            except (Refused, chat.Unreadable, textutil.MissingTool, OSError) as error:
                failures.append((path.stem, str(error)))
                log_row(log, path.stem, settings, "failed", None, str(error),
                        prompt_sha, context_sha)
                continue
            state = reply.state()
            counts[state] += 1
            windowed += 1 if reply.hit_the_window(settings.max_tokens) else 0
            seconds.append(reply.seconds)
            prompt_tokens.append(reply.prompt_tokens)
            completion_tokens.append(reply.completion_tokens)
            if state == chat.OK:
                atomic.write_text(target / path.name, reply.text + "\n")
            elif state == chat.TRUNCATED:
                atomic.write_text(target / (path.stem + TRUNCATED_SUFFIX), reply.text + "\n")
            log_row(log, path.stem, settings, state, reply,
                    "context window full" if reply.hit_the_window(settings.max_tokens) else "",
                    prompt_sha, context_sha)
            print("  %-14s %-9s %5d tokens out, %5.1fs   %d/%d%s"
                  % (path.stem, state, reply.completion_tokens, reply.seconds, number, len(todo),
                     "   %d pages" % len(images) if images else ""))
            time.sleep(delay)

    report(counts, failures, seconds, prompt_tokens, completion_tokens, target, windowed)
    if missing:
        print("  %-10s %d asked for by name and not on disk: %s"
              % ("missing", len(missing), ", ".join(missing)))
    if counts[chat.OK]:
        print("\nnext: python3 extract.py collect %s" % target)
    if failures or missing:
        return 1
    if counts[chat.TRUNCATED] or counts[chat.EMPTY]:
        return UNUSABLE
    return 0


def doctor(settings):
    print("%-22s %s" % ("endpoint", endpoint()))
    print("%-22s %s / %s" % ("credentials",
                             "set" if os.environ.get(USER_VAR) else "MISSING " + USER_VAR,
                             "set" if os.environ.get(PASSWORD_VAR) else "MISSING " + PASSWORD_VAR))
    header = credentials()
    try:
        served = [entry.get("id") for entry in (call("/models", header, timeout=60).get("data") or [])]
    except (Refused,) + NETWORK as error:
        return fail("cannot reach the endpoint: %s" % error)
    print("%-22s %s" % ("models served", ", ".join(str(name) for name in served) or "none"))
    if settings.model not in served:
        return fail("%s is not served here; pass --model or set %s" % (settings.model, MODEL_VAR))

    probe = chat.Settings(model=settings.model, max_tokens=200, temperature=0.0,
                          think=settings.think, timeout=120)
    try:
        reply = ask("Return the YAML mapping {a: 1} and nothing else.", header, probe, retries=1)
    except (Refused, chat.Unreadable) as error:
        return fail("the model did not answer: %s" % error)
    print("%-22s %s in %.1fs, %d tokens out, %d thinking chars"
          % ("round trip", reply.state(), reply.seconds, reply.completion_tokens,
             len(reply.reasoning)))
    print("%-22s %r" % ("answer", reply.text[:60]))
    if reply.state() != chat.OK:
        return fail("a %d-token probe came back %s; --think %s costs more than that"
                    % (probe.max_tokens, reply.state(), probe.think))
    return 0


USAGE = """usage:
  python3 ask.py doctor [--model M] [--think off|low|medium|xhigh]
                                     endpoint, served models, one 200-token round trip
  python3 ask.py run [options]       corpus/prompts -> corpus/runs/text-<think>, one per paper,
                                     resumable: a paper already answered is not asked again

  --dir D          prompts to send, default corpus/prompts (corpus/tags for tagging prompts)
  --out D          where answers land; by default one directory per run under corpus/runs,
                   named <input>-<think>: text-medium, pages-low, text-and-pages-xhigh
  --only a,b       these prompt names only
  --limit N        stop after N papers
  --force          ask again even where an answer is already on disk
  --dry-run        print the plan and send nothing
  --model M        default Qwen/Qwen3.8-27B-FP8, or $MODEL_API_MODEL
  --think T        off, low, medium, xhigh; default medium. Thinking is charged against max_tokens:
                   at low it costs 6-8k tokens per paper, at off nothing and 2-4x faster
  --max-tokens N   default 24000; at 12000 two of four ICLR papers with findings were cut off
  --temperature T  default 0.0
  --timeout N      seconds per request, default 900
  --delay N        seconds between requests, default 0
  --pdf D          send the paper as page images rendered from the PDFs in D, instead of relying
                   on the text inside the prompt. Pair it with: extract.py prompts --pages

  MODEL_API_USERNAME and MODEL_API_PASSWORD must be set in the environment.
  MODEL_API_URL overrides the endpoint.

  exit codes: 0 nothing to report; 1 a request failed or a name was not on disk, so asking again
  may help; 3 every request came back but some answers were empty or truncated, and asking again
  with the same settings cannot help, because temperature 0 makes them deterministic."""


def main(argv):
    console.line_buffered()
    if len(argv) < 2:
        print(USAGE)
        return 2
    command, rest = argv[1], argv[2:]
    try:
        given = options(rest)
        settings = settings_from(given)
        limit = positive(given.get("--limit"), "--limit")
        delay = number(given.get("--delay"), "--delay") or 0.0
    except ValueError as error:
        return fail(str(error))

    if command == "doctor":
        return doctor(settings)
    if command == "run":
        source = given.get("--dir") or PROMPTS
        target = given.get("--out") or default_out(source, given.get("--pdf"),
                                                 settings.think)
        return run(source, target, settings,
                   only=given.get("--only"), limit=limit, force=bool(given.get("--force")),
                   dry=bool(given.get("--dry-run")), delay=delay, pdfs=given.get("--pdf"))
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
