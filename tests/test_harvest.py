import csv
import json
import tempfile
from pathlib import Path

from modelpedia.commands import check
from modelpedia.commands import harvest
from modelpedia import paths
from modelpedia.ingest import manifest as store
from modelpedia.ingest import batching
from modelpedia.ingest import openreview
from modelpedia.ingest import ranking
from modelpedia.ingest import screen
from modelpedia.ingest import text
from tests.helpers import AUDIT, AUDIT_REVIEWS, OPTIMISER, Reply, screened_row


def manifest_line(paper_id, tier, **extra):
    row = {"id": paper_id, "tier": tier, "venue": "V/2026"}
    row.update(extra)
    return json.dumps(row) + "\n"

def test_harvest_downloads_only_tiers_it_was_asked_for():
    assert screen.WEAK not in harvest.DOWNLOAD_TIERS
    assert screen.STRONG in harvest.DOWNLOAD_TIERS

def test_the_manifest_store_takes_its_path_so_a_second_venue_can_have_its_own():
    with tempfile.TemporaryDirectory() as directory:
        first = Path(directory) / "iclr.jsonl"
        second = Path(directory) / "icml.jsonl"
        first.write_text(manifest_line("a", "strong"), encoding="utf-8")
        second.write_text(manifest_line("b", "weak"), encoding="utf-8")
        assert [row["id"] for row in store.load(first).rows] == ["a"]
        assert [row["id"] for row in store.load(second).rows] == ["b"]
        assert store.load(Path(directory) / "absent.jsonl").rows == ()

def test_the_store_reports_damage_instead_of_printing_it():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.jsonl"
        path.write_text('{"id": "a", "tier": "strong"}\n'
                        + manifest_line("b", "weak")
                        + manifest_line("b", "strong"), encoding="utf-8")
        held = store.load(path)
        assert [row["id"] for row in held.rows] == ["b"]
        assert held.repeated == 1
        assert len(held.complaints) == 1 and "lacks venue" in held.complaints[0]

def test_counting_seen_ids_never_builds_the_rows_it_throws_away():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.jsonl"
        path.write_text(manifest_line("a", "strong")
                        + '{"id": "damaged", "tier": "strong"}\n'
                        + manifest_line("c", "weak"), encoding="utf-8")
        assert store.ids_in(path) == {"a", "c"}

def test_a_manifest_row_is_rejected_at_read_time_when_a_consumer_would_crash_on_it():
    assert store.row_complaint({"id": "aaa", "tier": "weak", "venue": "V/2026"}) is None
    assert "lacks venue" in store.row_complaint({"id": "aaa", "tier": "weak"})
    assert "lacks id and tier" in store.row_complaint({"venue": "V/2026"})
    assert "is not a record" in store.row_complaint([1, 2])
    assert "cannot become a filename" in store.row_complaint(
        {"id": "../../etc/passwd", "tier": "weak", "venue": "V/2026"})

def test_a_row_a_consumer_would_crash_on_never_reaches_the_consumer():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                '{"id": "../../etc/passwd", "tier": "strong", "venue": "V/2026"}\n'
                '{"id": "novenue", "tier": "strong"}\n'
                '{"id": "good", "tier": "strong", "venue": "V/2026"}\n',
                encoding="utf-8")
            assert [row["id"] for row in harvest.manifest_rows()] == ["good"]
            assert harvest.show_stats() == 0
            assert harvest.harvest_pdfs(ids=["../../etc/passwd"]) == 1
    finally:
        harvest.MANIFEST = original

def test_every_harvested_row_records_which_rules_screened_it():
    screening = screen.screen(*AUDIT)

    row = screened_row()
    assert row["rules_version"] == screen.RULES_VERSION
    assert store.row_complaint(row) is None

def test_an_explicit_id_list_selects_rows_and_reports_the_ones_it_cannot_find():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                manifest_line("aaa", "weak") + manifest_line("bbb", "strong"),
                encoding="utf-8")
            assert [row["id"] for row in harvest.selected_rows((), ["bbb", "aaa"])] == ["bbb", "aaa"]
            assert [row["id"] for row in harvest.selected_rows((), ["bbb", "ccc"])] == ["bbb"]
            assert [row["id"] for row in harvest.selected_rows(("strong",), None)] == ["bbb"]
    finally:
        harvest.MANIFEST = original

def test_an_id_file_rejects_anything_that_is_not_an_identifier():
    with tempfile.TemporaryDirectory() as directory:
        good = Path(directory) / "good.txt"
        good.write_text("# comment\naaa\n\nbbb\naaa\n", encoding="utf-8")
        assert store.read_ids(good) == ["aaa", "bbb"]
        hostile = Path(directory) / "hostile.txt"
        hostile.write_text("aaa\n../../etc/passwd\n", encoding="utf-8")
        try:
            store.read_ids(hostile)
            raise AssertionError("accepted a path as an identifier")
        except ValueError:
            pass
    assert harvest.main(["harvest.py", "pdfs", "--ids", str(hostile)]) == 1

class Note:
    def __init__(self, paper_id, title, abstract="", keywords=()):
        self.id = paper_id
        self.content = {"title": {"value": title},
                        "abstract": {"value": abstract},
                        "keywords": {"value": list(keywords)}}

    def to_json(self):
        return {"id": self.id, "content": self.content}

class FakeSubmissions:
    def __init__(self, notes=()):
        self.notes = list(notes)

    def get_all_notes(self, **query):
        return self.notes

    def get_notes(self, **query):
        return ([], len(self.notes)) if query.get("with_count") else []

def harvested(notes, already="", venue_id="V/2026"):
    kept = (harvest.MANIFEST, harvest.META, harvest.connect)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.META = Path(directory) / "meta"
            if already:
                harvest.MANIFEST.write_text(already, encoding="utf-8")
            harvest.connect = lambda generation=openreview.API2: FakeSubmissions(notes)
            code = harvest.harvest_meta(venue_id)
            return (code, list(store.load(harvest.MANIFEST).rows),
                    sorted(path.name for path in harvest.META.glob("*.json")))
    finally:
        harvest.MANIFEST, harvest.META, harvest.connect = kept

def test_harvesting_metadata_screens_each_note_and_writes_one_row_per_paper():
    code, rows, metas = harvested([Note("aaa", *AUDIT), Note("bbb", *OPTIMISER)])
    assert code == 0
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert rows[0]["tier"] == screen.POSSIBLE
    assert rows[1]["tier"] == screen.WEAK
    assert metas == ["aaa.json", "bbb.json"]
    assert all(row["venue"] == "V/2026" for row in rows)
    assert all(row["pdf"].endswith(row["id"]) for row in rows)

def review_line(paper_id, review_id, fields, rating=None, venue_id="V/2026"):
    return json.dumps(store.review_row(paper_id, venue_id, review_id, rating, fields)) + "\n"

def with_corpus(manifest_lines, review_lines, run):
    kept = (harvest.MANIFEST, harvest.REVIEWS)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.REVIEWS = Path(directory) / "reviews"
            harvest.REVIEWS.mkdir()
            harvest.MANIFEST.write_text("".join(manifest_lines), encoding="utf-8")
            (harvest.REVIEWS / store.store_name("V/2026")).write_text(
                "".join(review_lines), encoding="utf-8")
            return run(Path(directory))
    finally:
        harvest.MANIFEST, harvest.REVIEWS = kept

LINE_SEPARATOR = "\u2028"

def test_a_record_carrying_a_unicode_line_separator_is_still_one_row():
    def check(directory):
        held = store.load_reviews(harvest.REVIEWS)
        assert held.complaints == ()
        assert held.count("aaa") == 2
        assert LINE_SEPARATOR in " ".join(held.texts("aaa"))
        return 0

    with_corpus([manifest_line("aaa", "weak")],
                [review_line("aaa", "r1", {"summary": "a shortcut%sand a second clause" % LINE_SEPARATOR}),
                 review_line("aaa", "r2", {"summary": "plain prose"})], check)

class FakeReviewer:
    def __init__(self, refuse_first=0, refuse_after=None, reviews_per_paper=2):
        self.refuse_first = refuse_first
        self.refuse_after = refuse_after
        self.reviews_per_paper = reviews_per_paper
        self.asked = []

    def get_notes(self, **query):
        return ([], 1) if query.get("with_count") else []

    def get_all_notes(self, **query):
        forum = query["forum"]
        self.asked.append(forum)
        spent = (len(self.asked) <= self.refuse_first
                 or self.refuse_after is not None and len(self.asked) > self.refuse_after)
        if spent:
            raise RuntimeError("quota spent")
        return [Reply("%s-r%d" % (forum, number),
                      {"summary": "the paper analyzes a model"})
                for number in range(self.reviews_per_paper)]

class NoClock:
    def __init__(self, elapsed=0.0):
        self.elapsed = elapsed
        self.paused = []
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds:
            self.paused.append(seconds)
        self.now += self.elapsed

def fetched(papers, reviewer, limit=None, pause=None, clock=None):
    clock = clock or NoClock()
    kept = (harvest.MANIFEST, harvest.REVIEWS, harvest.connect, batching.time)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.REVIEWS = Path(directory) / "reviews"
            harvest.MANIFEST.write_text(
                "".join(manifest_line(paper, "weak") for paper in papers), encoding="utf-8")
            harvest.connect = lambda generation=openreview.API2: reviewer
            batching.time = clock
            code = harvest.harvest_reviews("V/2026", limit=limit, delay=0, pause=pause)
            return code, store.load_reviews(harvest.REVIEWS), clock.paused
    finally:
        harvest.MANIFEST, harvest.REVIEWS, harvest.connect, batching.time = kept

def test_one_command_keeps_fetching_batch_after_batch_until_the_venue_is_done():
    reviewer = FakeReviewer()
    code, held, paused = fetched(["p%d" % n for n in range(7)], reviewer, limit=3, pause=3600)
    assert code == 0
    assert reviewer.asked == ["p%d" % n for n in range(7)]
    assert sorted(held.by_paper) == ["p%d" % n for n in range(7)]
    assert paused == [3600, 3600]

def test_a_quota_running_out_mid_batch_gives_the_batch_up_instead_of_grinding_through_it():
    reviewer = FakeReviewer(refuse_after=4)
    code, held, paused = fetched(["p%d" % n for n in range(200)], reviewer,
                                 limit=200, pause=3600)
    assert code == 1
    assert sorted(held.by_paper) == ["p%d" % n for n in range(4)]
    assert len(reviewer.asked) == 4 + batching.GIVE_UP_AFTER * (1 + batching.DEAD_BATCHES)
    assert paused == [3600] * batching.DEAD_BATCHES
    assert len(reviewer.asked) < 200

def test_a_paper_left_unasked_by_a_given_up_batch_is_the_first_one_asked_next_time():
    reviewer = FakeReviewer(refuse_after=4)
    fetched(["p%d" % n for n in range(200)], reviewer, limit=200, pause=3600)
    resumed = 4 + batching.GIVE_UP_AFTER
    assert reviewer.asked[:5] == ["p0", "p1", "p2", "p3", "p4"]
    assert reviewer.asked[resumed] == "p4"

class FakeAttachments:
    def __init__(self, refuse_after=None, not_a_pdf=()):
        self.refuse_after = refuse_after
        self.not_a_pdf = set(not_a_pdf)
        self.asked = []

    def get_notes(self, **query):
        return ([], 1) if query.get("with_count") else []

    def get_attachment(self, field_name, id):
        self.asked.append(id)
        if self.refuse_after is not None and len(self.asked) > self.refuse_after:
            raise RuntimeError("quota spent")
        return b"not a pdf at all" if id in self.not_a_pdf else b"%PDF-1.7 body"

def downloaded(papers, connection, limit=None, pause=None, on_disk=()):
    clock = NoClock()
    kept = (harvest.MANIFEST, harvest.PDFS, harvest.connect, batching.time)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.PDFS = Path(directory) / "pdf"
            harvest.PDFS.mkdir()
            for paper in on_disk:
                (harvest.PDFS / ("%s.pdf" % paper)).write_bytes(b"%PDF-1.7 held")
            harvest.MANIFEST.write_text(
                "".join(manifest_line(paper, "strong") for paper in papers), encoding="utf-8")
            harvest.connect = lambda generation=openreview.API2: connection
            batching.time = clock
            code = harvest.harvest_pdfs(limit=limit, delay=0, pause=pause)
            return code, sorted(path.stem for path in harvest.PDFS.glob("*.pdf")), clock.paused
    finally:
        harvest.MANIFEST, harvest.PDFS, harvest.connect, batching.time = kept

def test_pdfs_keep_downloading_batch_after_batch_like_the_reviews_do():
    connection = FakeAttachments()
    code, on_disk, paused = downloaded(["p%d" % n for n in range(7)], connection,
                                       limit=3, pause=3600)
    assert code == 0
    assert on_disk == ["p%d" % n for n in range(7)]
    assert paused == [3600, 3600]

def test_a_pdf_already_on_disk_is_not_asked_for_again():
    connection = FakeAttachments()
    code, on_disk, _ = downloaded(["p0", "p1", "p2"], connection, on_disk=["p1"])
    assert connection.asked == ["p0", "p2"]
    assert on_disk == ["p0", "p1", "p2"]
    assert code == 0

def test_a_response_that_is_not_a_pdf_counts_as_answered_so_the_run_can_end():
    connection = FakeAttachments(not_a_pdf=["p1"])
    code, on_disk, paused = downloaded(["p0", "p1", "p2"], connection, limit=3, pause=3600)
    assert connection.asked == ["p0", "p1", "p2"]
    assert on_disk == ["p0", "p2"]
    assert paused == []
    assert code == 1

def test_a_pdf_quota_running_out_mid_batch_gives_the_batch_up_too():
    connection = FakeAttachments(refuse_after=2)
    code, on_disk, paused = downloaded(["p%d" % n for n in range(100)], connection,
                                       limit=100, pause=3600)
    assert on_disk == ["p0", "p1"]
    assert len(connection.asked) == 2 + batching.GIVE_UP_AFTER * (1 + batching.DEAD_BATCHES)
    assert code == 1

def test_pdfs_ask_each_venue_through_the_api_generation_that_answers_for_it():
    asked = []

    class PerVenue:
        def __init__(self, venue):
            self.venue = venue

        def get_notes(self, **query):
            return ([], 1) if query.get("with_count") else []

        def get_attachment(self, field_name, id):
            asked.append((self.venue, id))
            return b"%PDF-1.7 body"

    kept = (harvest.MANIFEST, harvest.PDFS, harvest.source_for, batching.time)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.PDFS = Path(directory) / "pdf"
            harvest.PDFS.mkdir()
            harvest.MANIFEST.write_text(
                manifest_line("aaa", "strong")
                + json.dumps({"id": "ccc", "tier": "strong", "venue": "W/2023"}) + "\n",
                encoding="utf-8")
            harvest.source_for = lambda venue: (PerVenue(venue), openreview.API2)
            batching.time = NoClock()
            assert harvest.harvest_pdfs(delay=0) == 0
    finally:
        harvest.MANIFEST, harvest.PDFS, harvest.source_for, batching.time = kept

    assert sorted(asked) == [("V/2026", "aaa"), ("W/2023", "ccc")]

def test_a_pause_without_a_batch_size_is_refused_for_pdfs_as_well():
    assert harvest.main(["harvest.py", "pdfs", "--pause", "3600"]) == 1

def test_a_tier_reaches_every_conference_until_venue_narrows_it():
    kept = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                manifest_line("aaa", "strong")
                + json.dumps({"id": "ccc", "tier": "strong", "venue": "W/2025"}) + "\n",
                encoding="utf-8")
            everywhere = harvest.selected_rows((screen.STRONG,), None)
            narrowed = harvest.selected_rows((screen.STRONG,), None, "W/2025")
            assert sorted(row["id"] for row in everywhere) == ["aaa", "ccc"]
            assert [row["id"] for row in narrowed] == ["ccc"]
            try:
                harvest.selected_rows((screen.STRONG,), None, "X/1999")
                raise AssertionError("an absent venue should be refused, not answered empty")
            except ValueError as error:
                assert "X/1999" in str(error) and "W/2025" in str(error)
    finally:
        harvest.MANIFEST = kept

def test_a_limit_without_a_pause_is_still_one_batch_and_then_stop():
    reviewer = FakeReviewer()
    code, held, paused = fetched(["p0", "p1", "p2"], reviewer, limit=2)
    assert code == 0
    assert reviewer.asked == ["p0", "p1"]
    assert paused == []

def test_the_pause_is_measured_from_the_start_of_the_batch_not_its_end():
    clock = NoClock()
    kept = batching.time
    try:
        batching.time = clock
        clock.now = 900.0
        batching.sleep_until_next_batch(3600, 0.0, 4)
    finally:
        batching.time = kept
    assert clock.paused == [2700.0]

def test_a_batch_slower_than_the_pause_starts_the_next_one_without_waiting():
    clock = NoClock()
    kept = batching.time
    try:
        batching.time = clock
        clock.now = 5000.0
        batching.sleep_until_next_batch(3600, 0.0, 4)
    finally:
        batching.time = kept
    assert clock.paused == []

def test_a_paper_whose_request_failed_is_asked_about_again_next_batch():
    reviewer = FakeReviewer(refuse_first=2)
    code, held, _ = fetched(["p0", "p1", "p2"], reviewer, limit=3, pause=60)
    assert code == 1
    assert reviewer.asked == ["p0", "p1", "p2", "p0", "p1"]
    assert sorted(held.by_paper) == ["p0", "p1", "p2"]

def test_batches_that_answer_for_nothing_stop_the_loop_instead_of_waiting_out_the_clock():
    reviewer = FakeReviewer(refuse_first=10 ** 6)
    code, held, paused = fetched(["p0", "p1"], reviewer, limit=1, pause=3600)
    assert code == 1
    assert held.by_paper == {}
    assert len(paused) == batching.DEAD_BATCHES - 1

def test_a_pause_without_a_batch_size_is_refused_rather_than_ignored():
    assert harvest.main(["harvest.py", "reviews", "V/2026", "--pause", "3600"]) == 1
    assert harvest.main(["harvest.py", "reviews", "V/2026", "--pause", "0", "--limit", "5"]) == 1

def test_a_venue_identifier_becomes_exactly_one_store_file_name():
    assert store.store_name("ICLR.cc/2025/Conference") == "ICLR.cc-2025-Conference.jsonl"
    assert store.store_name("ICLR.cc/2023/Conference") != store.store_name("ICLR.cc/2024/Conference")

def test_a_review_store_row_survives_the_round_trip():
    def check(directory):
        held = store.load_reviews(harvest.REVIEWS)
        assert held.count("aaa") == 2
        assert held.rating("aaa") == 7.0
        assert "printed text" in " ".join(held.texts("aaa"))
        return 0

    with_corpus([manifest_line("aaa", "weak")],
                [review_line("aaa", "r1", {"summary": "relies on printed text"}, 8.0),
                 review_line("aaa", "r2", {"summary": "a shortcut"}, 6.0)], check)

def test_a_paper_the_fetch_found_no_review_for_is_not_asked_about_again():
    def check(directory):
        path = harvest.REVIEWS / store.store_name("V/2026")
        assert store.reviewed_ids(path) == {"aaa", "bbb"}
        assert store.load_reviews(harvest.REVIEWS).count("bbb") == 0
        return 0

    with_corpus([manifest_line("aaa", "weak"), manifest_line("bbb", "weak")],
                [review_line("aaa", "r1", {"summary": "a shortcut"}),
                 review_line("bbb", "bbb-none", {})], check)

def test_the_same_review_written_by_two_runs_is_counted_once():
    def check(directory):
        assert store.load_reviews(harvest.REVIEWS).count("aaa") == 1
        return 0

    with_corpus([manifest_line("aaa", "weak")],
                [review_line("aaa", "r1", {"summary": "a shortcut"}),
                 review_line("aaa", "r1", {"summary": "a shortcut"})], check)

def test_rescreen_folds_the_reviews_in_without_touching_the_network():
    def check(directory):
        assert harvest.rescreen() == 0
        row = list(store.load(harvest.MANIFEST).rows)[0]
        assert row["reviews"] == len(AUDIT_REVIEWS)
        assert row["tier"] == screen.STRONG
        assert row["title"] == AUDIT[0]
        assert row["rules_version"] == screen.RULES_VERSION
        assert screen.side_score(row["subscores"], screen.REVIEW) > 0
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1])],
                [review_line("aaa", "r%d" % number, {"summary": text})
                 for number, text in enumerate(AUDIT_REVIEWS)], check)

def test_rescreen_keeps_every_field_the_fetch_wrote():
    def check(directory):
        harvest.rescreen()
        row = list(store.load(harvest.MANIFEST).rows)[0]
        assert row["pdf"] == "https://openreview.net/pdf?id=aaa"
        assert row["keywords"] == ["interpretability"]
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1],
                               keywords=["interpretability"],
                               pdf="https://openreview.net/pdf?id=aaa")], [], check)

def test_rank_writes_one_row_per_paper_ordered_by_the_total():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target) == 0
        with target.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["id"] for row in rows] == ["aaa", "bbb"]
        assert [int(row["pos"]) for row in rows] == [1, 2]
        assert float(rows[0]["total"]) > float(rows[1]["total"])
        assert float(rows[0]["abstract"]) + float(rows[0]["review"]) == float(rows[0]["total"])
        assert rows[0]["url"].endswith("forum?id=aaa")
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1]),
                 manifest_line("bbb", "weak", title=OPTIMISER[0], abstract=OPTIMISER[1])],
                [review_line("aaa", "r%d" % number, {"summary": text})
                 for number, text in enumerate(AUDIT_REVIEWS)], check)

def two_venue_corpus():
    return [manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1]),
            manifest_line("bbb", "weak", title=OPTIMISER[0], abstract=OPTIMISER[1]),
            json.dumps({"id": "ccc", "tier": "weak", "venue": "W/2025",
                        "title": AUDIT[0], "abstract": AUDIT[1]}) + "\n"]

def test_ranking_writes_one_file_per_venue_beside_the_combined_one():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target) == 0
        assert target.exists()
        for venue, expected in (("V/2026", ["aaa", "bbb"]), ("W/2025", ["ccc"])):
            beside = ranking.venue_target(target, venue)
            assert beside.exists(), beside
            with beside.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            assert [row["id"] for row in rows] == expected
            assert {row["venue"] for row in rows} == {venue}
        return 0

    with_corpus(two_venue_corpus(), [], check)

def test_a_position_in_a_per_venue_file_is_that_venue_s_own_position():
    def check(directory):
        target = directory / "ranking.csv"
        harvest.rank(target, "W/2025")
        with target.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["id"] for row in rows] == ["ccc"]
        assert rows[0]["pos"] == "1"
        assert not ranking.venue_target(target, "V/2026").exists()
        return 0

    with_corpus(two_venue_corpus(), [], check)

def test_a_venue_the_manifest_does_not_hold_is_refused_not_written_empty():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target, "X/1999") == 1
        assert not target.exists()
        return 0

    with_corpus(two_venue_corpus(), [], check)

def test_a_half_fetched_venue_is_marked_in_the_table_not_in_a_footnote():
    assert ranking.reviewed_share([{"reviews": 2}, {"reviews": 0}]) == 0.5
    assert ranking.reviewed_share([{"reviews": 2}]) == 1.0
    assert ranking.reviewed_share([]) == 0.0
    row = ranking.venue_row("V/2026", [{"reviews": 0, "tier": screen.WEAK, "total": 1.0},
                                       {"reviews": 3, "tier": screen.STRONG, "total": 9.0}])
    assert "50.0%" in row

def test_one_venue_alone_gets_no_redundant_copy_of_itself():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target) == 0
        assert list(directory.glob("ranking-*.csv")) == []
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1])], [], check)

def test_the_ranking_is_replaced_in_one_step_like_every_other_artifact():
    def check(directory):
        target = directory / "ranking.csv"
        target.write_text("stare, nieprawdziwe dane\n", encoding="utf-8")
        assert harvest.rank(target) == 0
        assert list(directory.glob("*%s" % paths.PARTIAL)) == []
        with target.open(encoding="utf-8") as handle:
            assert [row["id"] for row in csv.DictReader(handle)] == ["aaa"]
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1])], [], check)

def test_every_ranking_column_comes_from_a_rule_set_or_is_named_once():
    for rules in screen.RULESETS:
        for name in rules.groups:
            assert name in ranking.RANK_COLUMNS
    assert len(set(ranking.RANK_COLUMNS)) == len(ranking.RANK_COLUMNS)

def test_a_freshly_harvested_row_says_it_has_seen_no_review_yet():
    _, rows, _ = harvested([Note("aaa", *AUDIT)])
    assert rows[0]["reviews"] == 0
    assert rows[0]["rating"] is None
    assert rows[0]["score"] == screen.screen(*AUDIT).score

def test_harvesting_records_the_rules_that_screened_each_row():
    _, rows, _ = harvested([Note("aaa", *AUDIT)])
    assert rows[0]["rules_version"] == screen.RULES_VERSION
    assert store.row_complaint(rows[0]) is None

def test_harvesting_skips_papers_the_manifest_already_holds():
    _, rows, metas = harvested([Note("aaa", *AUDIT), Note("bbb", *AUDIT)],
                               already=manifest_line("aaa", "weak"))
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert rows[0]["tier"] == "weak"
    assert metas == ["bbb.json"]

def test_one_unusable_identifier_from_the_api_does_not_end_the_whole_harvest():
    code, rows, metas = harvested([Note("aaa", *AUDIT),
                                   Note("../../etc/passwd", *AUDIT),
                                   Note("bbb", *AUDIT)])
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert metas == ["aaa.json", "bbb.json"]
    assert code == 1

def test_harvesting_closes_an_interrupted_last_line_before_appending_to_it():
    _, rows, _ = harvested([Note("bbb", *AUDIT)],
                           already='{"id": "aaa", "tier": "weak", "venue": "V/2026"}')
    assert [row["id"] for row in rows] == ["aaa", "bbb"]

def test_a_paper_identifier_is_never_used_as_a_path():
    assert store.safe_id("aBcD3f") == "aBcD3f"
    for hostile in ("../../etc/passwd", "a/b", "", None, "x" * 70):
        try:
            store.safe_id(hostile)
            raise AssertionError("accepted %r" % (hostile,))
        except ValueError:
            pass

def test_an_interrupted_manifest_line_does_not_swallow_the_next_row():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                manifest_line("kept", "weak") + '{"id": "interrupted", "tier"',
                encoding="utf-8")
            assert store.close_unterminated_line(harvest.MANIFEST)
            with harvest.MANIFEST.open("a", encoding="utf-8") as log:
                log.write(manifest_line("next", "weak"))
            assert [row["id"] for row in harvest.manifest_rows()] == ["kept", "next"]
    finally:
        harvest.MANIFEST = original

def test_a_manifest_that_already_ends_cleanly_is_left_alone():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(manifest_line("kept", "weak"), encoding="utf-8")
            assert not store.close_unterminated_line(harvest.MANIFEST)
            assert harvest.MANIFEST.read_text(encoding="utf-8") == manifest_line("kept", "weak")
    finally:
        harvest.MANIFEST = original

def test_only_a_real_pdf_is_accepted_as_one():
    assert harvest.is_pdf(b"%PDF-1.7\nrest")
    assert not harvest.is_pdf(b"<!doctype html><html>403</html>")
    assert not harvest.is_pdf(b"")
    assert not harvest.is_pdf(None)

def test_pdf_download_uses_the_documented_attachment_endpoint():
    class Connection:
        calls = []

        def get_attachment(self, **arguments):
            self.calls.append(arguments)
            return b"%PDF-1.7 body"

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        connection = Connection()
        assert harvest.fetch_pdf(connection, "paper-id", target, delay=0)
        assert connection.calls == [{"field_name": "pdf", "id": "paper-id"}]
        assert target.read_bytes().startswith(b"%PDF")

def test_pdf_download_retries_a_transient_failure():
    class Connection:
        attempts = 0

        def get_attachment(self, **arguments):
            self.attempts += 1
            if self.attempts == 1:
                raise Exception({"status": 503})
            return b"%PDF-1.7 body"

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        connection = Connection()
        assert harvest.fetch_pdf(connection, "paper-id", target, retries=2, delay=0)
        assert connection.attempts == 2

def test_one_damaged_manifest_line_does_not_lose_the_rest(monkeypatch=None):
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / "manifest.jsonl"
        manifest.write_text(manifest_line("a", "strong")
                            + '{"id": "b", "tier": "wea\n'
                            + manifest_line("c", "weak"), encoding="utf-8")
        original = harvest.MANIFEST
        harvest.MANIFEST = manifest
        try:
            assert {row["id"] for row in harvest.manifest_rows()} == {"a", "c"}
            assert store.ids_in(manifest) == {"a", "c"}
        finally:
            harvest.MANIFEST = original

def with_manifest(body, rows):
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(rows, encoding="utf-8")
            return body()
    finally:
        harvest.MANIFEST = original

def test_an_option_without_a_value_is_refused_instead_of_widening_the_download():
    taken = []
    original = harvest.harvest_pdfs
    harvest.harvest_pdfs = lambda *args, **kw: taken.append((args, kw)) or 0
    try:
        for argv in (["h", "pdfs", "--ids"], ["h", "pdfs", "--limit"], ["h", "pdfs", "--tier"]):
            assert harvest.main(argv) == 1
        assert taken == []
    finally:
        harvest.harvest_pdfs = original

def test_an_unknown_option_or_tier_is_refused():
    assert harvest.main(["h", "pdfs", "--everything"]) == 1
    assert harvest.main(["h", "pdfs", "--tier", "enormous"]) == 1
    assert harvest.main(["h", "pdfs", "--limit", "bad"]) == 1
    assert harvest.main(["h", "pdfs", "--limit", "0"]) == 1

def test_the_default_tiers_are_still_used_when_no_option_is_given():
    assert harvest.chosen_tiers(None) == harvest.DOWNLOAD_TIERS
    assert harvest.chosen_tiers("weak,strong") == ("weak", "strong")
    assert harvest.positive("5") == 5 and harvest.positive(None) is None

def test_a_manifest_row_of_the_wrong_shape_is_skipped_not_crashed_on():
    rows = '42\n"text"\n{"id": "a"}\n' + manifest_line("b", "strong")
    kept, weak = with_manifest(
        lambda: (harvest.manifest_rows(), harvest.selected_rows(("weak",), None)), rows)
    assert [row["id"] for row in kept] == ["b"]
    assert weak == []

def test_a_repeated_manifest_id_is_collapsed_to_its_last_row():
    rows = manifest_line("a", "weak") + manifest_line("a", "strong")
    kept = with_manifest(lambda: harvest.manifest_rows(), rows)
    assert len(kept) == 1 and kept[0]["tier"] == "strong"

def test_requesting_only_ids_the_manifest_does_not_hold_is_a_failure():
    rows = manifest_line("real", "strong")
    assert with_manifest(lambda: harvest.harvest_pdfs(ids=["ghost"]), rows) == 1
    assert with_manifest(lambda: harvest.selected_rows((), ["real", "ghost"]), rows) != []
