import contextlib
import json
import tempfile
from pathlib import Path

import ask
from modelpedia.ingest import chat
from modelpedia.ingest import prompt as promptlib

ANSWER = "considered: []\nfindings: []\n"


@contextlib.contextmanager
def workspace(prompts):
    original_ask, original_credentials = ask.ask, ask.credentials
    ask.credentials = lambda: "Basic test"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "prompts").mkdir()
        for name, body in prompts.items():
            (root / "prompts" / ("%s.txt" % name)).write_text(body, encoding="utf-8")
        try:
            yield root / "prompts", root / "replies"
        finally:
            ask.ask, ask.credentials = original_ask, original_credentials


def replying(text, finish_reason="stop", reasoning=""):
    def stub(prompt, header, settings, retries=chat.RETRIES, delay=chat.DELAY, images=()):
        return chat.Reply(text, finish_reason, reasoning, 10, 5, 0.1)
    return stub


def refusing(message):
    def stub(prompt, header, settings, retries=chat.RETRIES, delay=chat.DELAY, images=()):
        raise ask.Refused(message)
    return stub


def exploding(prompt, header, settings, retries=chat.RETRIES, delay=chat.DELAY, images=()):
    raise AssertionError("no request should have been sent")


def log_rows(target):
    return [json.loads(line) for line in
            (target / ask.LOG).read_text(encoding="utf-8").splitlines()]


def test_no_arguments_prints_usage_and_exits_2():
    assert ask.main(["ask.py"]) == 2


def test_an_unknown_command_prints_usage_and_exits_2():
    assert ask.main(["ask.py", "bogus"]) == 2


def test_an_unknown_option_is_refused():
    assert ask.main(["ask.py", "run", "--wat", "1"]) == 1


def test_an_option_without_a_value_is_refused():
    assert ask.main(["ask.py", "run", "--limit"]) == 1


def test_think_takes_only_the_known_efforts():
    assert ask.main(["ask.py", "run", "--think", "hard"]) == 1


def test_boolean_flags_need_no_value():
    given = ask.options(["--force", "--limit", "2", "--dry-run"])
    assert given["--force"] is True and given["--dry-run"] is True and given["--limit"] == "2"


def test_thinking_off_reaches_the_template_rather_than_the_effort_knob():
    body = json.loads(chat.request_body("hi", chat.Settings(think=chat.THINK_OFF)))
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body


def test_an_effort_is_sent_as_reasoning_effort():
    body = json.loads(chat.request_body("hi", chat.Settings(think="low")))
    assert body["reasoning_effort"] == "low" and "chat_template_kwargs" not in body


def test_a_reply_carries_its_usage():
    reply = chat.reply_from({"choices": [{"message": {"content": "a: 1"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 30, "completion_tokens": 4}}, 1.47)
    assert reply.text == "a: 1" and reply.prompt_tokens == 30 and reply.seconds == 1.5
    assert reply.state() == chat.OK


def test_a_response_without_choices_is_unreadable():
    try:
        chat.reply_from({"usage": {}})
    except chat.Unreadable:
        return
    raise AssertionError("a response with no choices was accepted")


def test_running_out_of_tokens_is_truncated_even_with_text():
    reply = chat.reply_from({"choices": [{"message": {"content": "considered:"},
                                          "finish_reason": "length"}]})
    assert reply.state() == chat.TRUNCATED


def test_thinking_that_answers_nothing_is_empty_not_ok():
    reply = chat.reply_from({"choices": [{"message": {"content": None, "reasoning": "hm"},
                                          "finish_reason": "stop"}]})
    assert reply.state() == chat.EMPTY and reply.reasoning == "hm"


def test_an_inline_thinking_block_never_reaches_the_answer():
    assert chat.without_thinking("<think>weighing it up</think>\nfindings: []") == "findings: []"


def test_a_server_complaint_is_read_out_of_its_envelope():
    assert chat.complaint_in(json.dumps({"error": {"message": "context length exceeded"}})) \
        == "context length exceeded"


def test_a_dry_run_sends_nothing():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = exploding
        assert ask.run(source, target, chat.Settings(), dry=True) == 0
        assert not target.exists()


def test_an_answer_is_written_under_the_name_of_its_prompt():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = replying(ANSWER)
        assert ask.run(source, target, chat.Settings()) == 0
        assert (target / "AAA.txt").read_text(encoding="utf-8").strip() == ANSWER.strip()
        assert log_rows(target)[0]["state"] == chat.OK


def test_a_paper_already_answered_is_not_asked_again():
    with workspace({"AAA": "prompt", "BBB": "prompt"}) as (source, target):
        ask.ask = replying(ANSWER)
        assert ask.run(source, target, chat.Settings(), only="AAA") == 0
        ask.ask = exploding
        assert ask.run(source, target, chat.Settings(), only="AAA") == 0


def test_force_asks_again():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = replying(ANSWER)
        assert ask.run(source, target, chat.Settings()) == 0
        ask.ask = replying("considered: []\nfindings: [x]\n")
        assert ask.run(source, target, chat.Settings(), force=True) == 0
        assert "[x]" in (target / "AAA.txt").read_text(encoding="utf-8")


def test_a_truncated_answer_is_kept_apart_and_reported():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = replying("considered:", finish_reason="length")
        assert ask.run(source, target, chat.Settings()) == ask.UNUSABLE
        assert (target / ("AAA" + ask.TRUNCATED_SUFFIX)).exists()
        assert not (target / "AAA.txt").exists()


def test_a_refused_request_writes_no_answer_and_exits_nonzero():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = refusing("HTTP 400: context length exceeded")
        assert ask.run(source, target, chat.Settings()) == 1
        assert not (target / "AAA.txt").exists()
        assert log_rows(target)[0]["state"] == "failed"


def test_a_prompt_that_does_not_exist_is_named_and_skipped():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = exploding
        assert ask.run(source, target, chat.Settings(), only="ZZZ") == 1


def test_a_name_that_does_not_exist_fails_the_run_even_when_the_rest_answered():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = replying(ANSWER)
        assert ask.run(source, target, chat.Settings(), only="AAA,ZZZ") == 1
        assert (target / "AAA.txt").exists()


def test_a_missing_prompt_directory_is_refused():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = exploding
        assert ask.run(source.parent / "absent", target, chat.Settings()) == 1


def test_a_prompt_with_page_images_carries_them_as_content_parts():
    body = json.loads(chat.request_body("instructions", chat.Settings(),
                                        ["data:image/jpeg;base64,AAAA"]))
    parts = body["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "instructions"}
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_without_images_the_content_stays_a_plain_string():
    body = json.loads(chat.request_body("instructions", chat.Settings()))
    assert body["messages"][0]["content"] == "instructions"


def test_a_pdf_that_is_not_there_fails_its_paper_and_not_the_run():
    with workspace({"AAA": "prompt", "BBB": "prompt"}) as (source, target):
        ask.ask = replying(ANSWER)
        assert ask.run(source, target, chat.Settings(), pdfs=str(source / "absent")) == 1
        assert not (target / "AAA.txt").exists()
        assert log_rows(target)[0]["state"] == "failed"


def test_a_truncation_that_never_reached_max_tokens_is_the_window_not_the_cap():
    short = chat.Reply("half a record", "length", "", 130000, 900, 5.0)
    assert short.hit_the_window(24000)
    used_up = chat.Reply("half a record", "length", "", 30000, 24000, 5.0)
    assert not used_up.hit_the_window(24000)
    assert not chat.Reply("done", "stop", "", 10, 10, 1.0).hit_the_window(24000)


def test_each_arm_lands_in_its_own_run_directory():
    from modelpedia import paths
    assert ask.default_out(paths.PROMPTS, None) == paths.RUNS / "text-medium"
    assert ask.default_out(paths.PAGE_PROMPTS, "corpus/pdf") == paths.RUNS / "pages-medium"
    assert ask.default_out(paths.PROMPTS, "corpus/pdf") == paths.RUNS / "text-and-pages-medium"


def test_a_different_thinking_budget_is_a_different_run():
    from modelpedia import paths
    assert ask.default_out(paths.PROMPTS, None, "xhigh") == paths.RUNS / "text-xhigh"
    assert ask.default_out(paths.PAGE_PROMPTS, "corpus/pdf", "off") == paths.RUNS / "pages-off"
    assert ask.default_out(paths.PROMPTS, None, "low") == paths.RUNS / "text-low"


def test_an_unusable_answer_and_a_failed_request_exit_differently():
    with workspace({"AAA": "prompt"}) as (source, target):
        ask.ask = replying("", finish_reason="stop")
        assert ask.run(source, target, chat.Settings()) == ask.UNUSABLE
    with workspace({"BBB": "prompt"}) as (source, target):
        ask.ask = refusing("HTTP 503")
        assert ask.run(source, target, chat.Settings()) == 1


def prompt_like(instructions, title, paper):
    return "".join([instructions, promptlib.PAPER_TITLE, title, "\n",
                    promptlib.PAPER_OPEN, "\n", paper, "\n", promptlib.PAPER_CLOSE])


AUGUST = "instructions, registry as it stood in august"
GROWN = "instructions, registry after it grew"

PAPER_A = prompt_like(AUGUST, "What does CLIP look at?", "paper one")
PAPER_B = prompt_like(AUGUST, "A different paper entirely", "paper two")
PAPER_C = prompt_like(GROWN, "What does CLIP look at?", "paper one")


def test_two_papers_asked_under_one_registry_share_a_context_sha():
    whole_a, context_a = ask.fingerprints(PAPER_A)
    whole_b, context_b = ask.fingerprints(PAPER_B)
    assert context_a == context_b
    assert whole_a != whole_b


def test_one_paper_asked_after_the_base_grew_gets_a_new_context_sha():
    whole_a, context_a = ask.fingerprints(PAPER_A)
    whole_c, context_c = ask.fingerprints(PAPER_C)
    assert context_a != context_c
    assert whole_a != whole_c


def test_a_prompt_with_neither_title_nor_paper_is_all_context():
    pages = "instructions only, images follow"
    whole, context = ask.fingerprints(pages)
    assert whole == context


def test_every_logged_attempt_records_which_instructions_produced_it():
    with workspace({"p1": PAPER_A, "p2": PAPER_B}) as (source, target):
        ask.ask = replying(ANSWER)
        assert ask.run(source, target, chat.Settings()) == 0
        rows = log_rows(target)
    assert len(rows) == 2
    assert all(row["context_sha"] for row in rows)
    assert len({row["context_sha"] for row in rows}) == 1
    assert len({row["prompt_sha"] for row in rows}) == 2


def test_a_refused_attempt_records_the_instructions_too():
    with workspace({"p1": PAPER_A}) as (source, target):
        ask.ask = refusing("endpoint said no")
        ask.run(source, target, chat.Settings())
        rows = log_rows(target)
    assert rows[0]["state"] == "failed"
    assert rows[0]["context_sha"] == ask.fingerprints(PAPER_A)[1]


def test_every_prompt_directory_gets_a_run_directory_of_its_own():
    from modelpedia import paths as p
    assert ask.default_out(p.PROMPTS, None).name == "text-medium"
    assert ask.default_out(p.PAGE_PROMPTS, "corpus/pdf").name == "pages-medium"
    assert ask.default_out(p.PROMPTS, "corpus/pdf").name == "text-and-pages-medium"
    assert ask.default_out(p.TAGS, None).name == "tags-medium"
    assert ask.default_out(p.FACET_PROMPTS, None).name == "facets-medium"
    assert ask.default_out(p.ENTITY_PROMPTS, None).name == "entities-medium"


def test_two_prompt_directories_never_share_a_run_directory():
    from modelpedia import paths as p
    sources = (p.PROMPTS, p.TAGS, p.FACET_PROMPTS, p.ENTITY_PROMPTS)
    outs = [ask.default_out(s, None) for s in sources]
    assert len(set(outs)) == len(outs)
