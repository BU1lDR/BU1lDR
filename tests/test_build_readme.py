"""tools/build_readme.py against a fixture account, offline.

Every case builds a throwaway root -- README.md with markers, profile.config.json,
a fixtures directory standing in for the API -- and calls build() in-process. The
network client is exercised separately with a faked opener, so the "404 is an
answer, anything else is a failure" rule is pinned on the code that implements it,
not only on the fixture double.
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_readme as br  # noqa: E402

LOGIN = "someone"
START, END = "<!-- work:start -->", "<!-- work:end -->"


def repo(name, *, pushed="2026-01-01T00:00:00Z", fork=False, archived=False, private=False,
         description="", homepage=None, licence="MIT", repo_id=None, visibility=True):
    data = {
        "id": repo_id or abs(hash(name)) % 10_000_000,
        "name": name,
        "full_name": f"{LOGIN}/{name}",
        "html_url": f"https://github.com/{LOGIN}/{name}",
        "fork": fork,
        "archived": archived,
        "private": private,
        "description": description,
        "homepage": homepage,
        "pushed_at": pushed,
        "license": {"spdx_id": licence} if licence else None,
    }
    if visibility:
        data["visibility"] = "private" if private else "public"
    return data


README_TEMPLATE = (
    "# Name\n\nIntro that must survive.\n\n## Work\n\n"
    f"{START}\nstale\n{END}\n\n### In progress\n\nStatic tail.\n"
)


class Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fixtures = self.root / "fx"
        for sub in ("releases", "profiles", "repos"):
            (self.fixtures / sub).mkdir(parents=True)
        (self.root / "snippets").mkdir()
        self.config({"login": LOGIN, "exclude": ["scratch"], "order": ["second", "first"], "markers": [START, END]})
        (self.root / "README.md").write_text(README_TEMPLATE, encoding="utf-8", newline="\n")

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, data):
        (self.root / "profile.config.json").write_text(json.dumps(data), encoding="utf-8")

    def uncurated(self):
        self.config({"login": LOGIN, "order": [], "markers": [START, END]})

    def repos(self, *items):
        (self.fixtures / "repos.json").write_text(json.dumps(list(items)), encoding="utf-8")

    def repo_detail(self, item, under=None):
        (self.fixtures / "repos" / f"{under or item['name']}.json").write_text(json.dumps(item), encoding="utf-8")

    def profile(self, name, text):
        (self.fixtures / "profiles" / f"{name}.md").write_bytes(text.encode("utf-8") if isinstance(text, str) else text)

    def release(self, name, data):
        (self.fixtures / "releases" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def run_build(self, check=False):
        out = io.StringIO()
        code = br.build(self.root, br.Fixtures(self.fixtures), check, self.root / "summary.md", out=out)
        return code, out.getvalue(), (self.root / "README.md").read_text(encoding="utf-8")

    def readme_bytes(self):
        return (self.root / "README.md").read_bytes()

    def summary(self):
        return (self.root / "summary.md").read_text(encoding="utf-8")

    @staticmethod
    def sections(readme):
        """{repo name: section text} for what sits between the markers."""
        return br.sections_by_repo(br.inner_block(readme, START, END), LOGIN)

    def assert_refused(self, message_fragment=""):
        before = (self.root / "README.md").read_text(encoding="utf-8")
        with self.assertRaises(br.BuildError) as caught:
            self.run_build()
        self.assertIn(message_fragment, str(caught.exception))
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), before)


class Selection(Harness):
    def test_forks_archived_private_excluded_and_self_are_skipped(self):
        self.repos(
            repo("first"),
            repo("a-fork", fork=True),
            repo("old", archived=True),
            repo("secret", private=True),
            repo("secret-no-visibility-field", private=True, visibility=False),
            repo("scratch"),
            repo(LOGIN, description="Profile README"),
        )
        self.profile("first", "# first — t\n\nbody\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertEqual(list(self.sections(readme)), ["first"])

    def test_visibility_field_absent_defaults_to_public(self):
        self.uncurated()
        self.repos(repo("plain", visibility=False, description="x"))
        _, _, readme = self.run_build()
        self.assertEqual(list(self.sections(readme)), ["plain"])

    def test_curated_order_then_newest_pushed_first_then_never_pushed(self):
        self.repos(
            repo("zeta", pushed="2026-03-01T00:00:00Z"),
            repo("first", pushed="2026-01-01T00:00:00Z"),
            repo("alpha", pushed="2026-05-01T00:00:00Z"),
            repo("never", pushed=None),
            repo("second", pushed="2026-02-01T00:00:00Z"),
        )
        self.profile("first", "# first — t\n\nbody\n")
        self.profile("second", "# second — t\n\nbody\n")
        _, _, readme = self.run_build()
        self.assertEqual(list(self.sections(readme)), ["second", "first", "alpha", "zeta", "never"])

    def test_curated_name_that_matches_nothing_is_warned_about_in_output_and_summary(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        _, out, _ = self.run_build()
        self.assertIn("::warning::profile.config.json orders 'second'", out)
        self.assertIn("Warnings:\n  profile.config.json orders 'second'", self.summary())

    def test_no_repositories_is_an_error_and_readme_is_untouched(self):
        self.repos(repo("a-fork", fork=True))
        self.assert_refused("no public repositories")


class Rendering(Harness):
    def test_profile_md_drives_heading_and_body_with_placeholders(self):
        self.repos(repo("first", homepage="https://x.example/"))
        self.release("first", {"tag_name": "v2.0.0", "published_at": "2026-02-01T00:00:00Z"})
        self.profile("first", "<!-- rendered elsewhere -->\n\n# Shown Name — does a thing ([live]({live}))\n\n"
                              "{license}, {version}.\n\n- a bullet\n- another\n")
        _, _, readme = self.run_build()
        self.assertEqual(self.sections(readme)["first"],
            "### [Shown Name](https://github.com/someone/first) — does a thing ([live](https://x.example/))\n\n"
            "MIT, v2.0.0.\n\n- a bullet\n- another\n")

    def test_ascii_double_hyphen_separator_is_accepted(self):
        self.repos(repo("first"))
        self.profile("first", "# Shown -- tail\n\nbody\n")
        _, _, readme = self.run_build()
        self.assertTrue(self.sections(readme)["first"].startswith("### [Shown](https://github.com/someone/first) — tail\n"))

    def test_no_release_renders_unreleased_and_missing_licence_is_named(self):
        self.repos(repo("first", licence=None))
        self.profile("first", "# first — thing\n\n{license}, {version}.\n")
        _, _, readme = self.run_build()
        self.assertIn("no licence detected, unreleased.", readme)

    def test_noassertion_licence_and_name_url_description_placeholders(self):
        self.repos(repo("first", licence="NOASSERTION", description="Words about it"))
        self.profile("first", "# first — t\n\n{license} / {name} / {url} / {description}\n")
        _, _, readme = self.run_build()
        self.assertIn("no licence detected / first / https://github.com/someone/first / Words about it\n", readme)

    def test_release_choice_ignores_drafts_and_prereleases_and_uses_publish_date(self):
        self.repos(repo("first"))
        self.release("first", [
            {"tag_name": "v3.0.0-rc1", "prerelease": True, "published_at": "2026-05-01T00:00:00Z"},
            {"tag_name": "v9.9.9", "draft": True, "published_at": "2026-06-01T00:00:00Z"},
            {"tag_name": "v2.1.0", "published_at": "2026-03-01T00:00:00Z"},   # first eligible in list order
            {"tag_name": "v2.0.1", "published_at": "2026-04-01T00:00:00Z"},   # but published later
        ])
        self.profile("first", "# first — t\n\n{version}\n")
        _, _, readme = self.run_build()
        self.assertIn("\n\nv2.0.1\n", readme)

    def test_substitution_is_single_pass_and_double_braces_escape(self):
        self.repos(repo("first", description="has {url} in it"))
        self.profile("first", "# first — t\n\n{description} / {{version}} / {version}\n")
        _, _, readme = self.run_build()
        self.assertIn("has {url} in it / {version} / unreleased\n", readme)

    def test_fallback_chain_profile_then_snippet_then_description(self):
        self.uncurated()
        self.repos(
            repo("first", description="from the API"),
            repo("second", description="ignored"),
            repo("third", description="ignored too"),
        )
        self.profile("third", "# third — from PROFILE.md\n\nbody\n")
        (self.root / "snippets" / "third.md").write_text("# third — from a snippet\n\nloses\n", encoding="utf-8")
        (self.root / "snippets" / "second.md").write_text("# second — from a snippet\n\nsnippet body\n", encoding="utf-8")
        _, out, readme = self.run_build()
        sections = self.sections(readme)
        self.assertEqual(sections["first"], "### [first](https://github.com/someone/first)\n\nfrom the API\n")
        self.assertIn("— from a snippet\n\nsnippet body\n", sections["second"])
        self.assertIn("— from PROFILE.md\n\nbody\n", sections["third"])
        self.assertIn("::warning::first: rendered from the GitHub description", out)
        self.assertIn("Warnings:\n  first: rendered from the GitHub description", self.summary())

    def test_description_only_repo_with_empty_description_gets_a_placeholder_body(self):
        self.uncurated()
        self.repos(repo("bare", description=""), repo("shouty", description="# Not a heading please"))
        _, _, readme = self.run_build()
        sections = self.sections(readme)
        self.assertEqual(sections["bare"], "### [bare](https://github.com/someone/bare)\n\nNo description yet.\n")
        self.assertIn("\n\n\\# Not a heading please\n", sections["shouty"])

    def test_curated_repository_without_a_source_is_an_error(self):
        self.repos(repo("first", description="only this"))
        self.assert_refused("curated repository but has no usable .github/PROFILE.md")

    def test_empty_or_comment_only_profile_counts_as_absent(self):
        self.repos(repo("first", description="d"))
        self.profile("first", "<!-- only a comment -->\n")
        self.assert_refused("curated repository but has no usable")
        self.uncurated()
        self.profile("first", b"")
        _, out, readme = self.run_build()
        self.assertIn("is empty once comments are stripped", out)
        self.assertEqual(self.sections(readme)["first"], "### [first](https://github.com/someone/first)\n\nd\n")

    def test_text_outside_the_markers_is_untouched_and_notice_is_first(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        _, _, readme = self.run_build()
        self.assertTrue(readme.startswith("# Name\n\nIntro that must survive.\n\n## Work\n\n" + START + "\n" + br.NOTICE + "\n"))
        self.assertTrue(readme.endswith(END + "\n\n### In progress\n\nStatic tail.\n"))
        self.assertNotIn("stale", readme)
        inner = br.inner_block(readme, START, END)
        self.assertNotIn(br.NOTICE, inner)
        self.assertTrue(inner.startswith(br.section_mark("first") + "\n### [first]("), inner)
        self.assertTrue(inner.endswith("\n") and not inner.endswith("\n\n"))


class HostileInput(Harness):
    def test_crlf_and_lone_cr_profiles_are_normalised_and_the_next_run_is_current(self):
        self.repos(repo("first"))
        self.profile("first", b"# first \xe2\x80\x94 tail\r\n\r\nline one\r\nline two\rline three\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertNotIn(b"\r", self.readme_bytes())
        self.assertIn("— tail\n\nline one\nline two\nline three\n", readme)
        _, out, _ = self.run_build()
        self.assertIn("current", out)

    def test_bom_profile_still_has_its_title_recognised(self):
        self.repos(repo("first"))
        self.profile("first", b"\xef\xbb\xbf<!-- note -->\n# Shown \xe2\x80\x94 tail\n\nbody\n")
        _, _, readme = self.run_build()
        self.assertTrue(self.sections(readme)["first"].startswith("### [Shown](https://github.com/someone/first) — tail\n"))
        self.assertNotIn("﻿", readme)

    def test_non_utf8_profile_is_an_error(self):
        self.repos(repo("first"))
        self.profile("first", b"# caf\xe9 \xe2\x80\x94 tail\n\nbody\n")
        self.assert_refused("is not UTF-8")

    def test_oversized_profile_and_oversized_readme_are_refused(self):
        self.repos(repo("first"))
        self.profile("first", b"# first \xe2\x80\x94 t\n\n" + b"x" * (br.MAX_BLURB_BYTES + 1))
        self.assert_refused("bytes; the limit is")
        with mock.patch.object(br, "MAX_README_BYTES", 200):
            self.profile("first", "# first — t\n\n" + "y" * 150 + "\n")
            self.assert_refused("rebuilt README would be")

    def test_marker_text_inside_a_section_is_refused(self):
        self.repos(repo("first"))
        self.profile("first", f"# first — t\n\nbody mentions {END} literally\n")
        self.assert_refused("contains the README marker")

    def test_section_mark_text_is_refused_from_every_source(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody with <!-- repo: other --> in it\n")
        self.assert_refused("delimit sections")
        self.repos(repo("first", description="<!-- repo: ghost -->"))
        self.profile("first", "# first — t\n\n{description}\n")
        self.assert_refused("delimit sections")
        self.uncurated()
        (self.fixtures / "profiles" / "first.md").unlink()
        self.assert_refused("delimit sections")

    def test_live_placeholder_without_a_valid_homepage_is_refused_but_escaped_or_commented_is_fine(self):
        self.repos(repo("first", homepage=None))
        self.profile("first", "# first — site ([live]({live}))\n\nbody\n")
        self.assert_refused("uses {live}")
        self.repos(repo("first", homepage="bu1ldr.github.io/x) not a url"))
        self.assert_refused("uses {live}")
        self.repos(repo("first", homepage=None))
        self.profile("first", "<!-- placeholders: {live} {version} -->\n# first — t\n\nwrite {{live}} for a literal\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("write {live} for a literal\n", readme)

    def test_live_pointing_at_github_is_a_warning_not_an_error(self):
        self.repos(repo("first", homepage="https://github.com/someone/first"))
        self.profile("first", "# first — t ([live]({live}))\n\nbody\n")
        code, out, _ = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("points at github.com", out)

    def test_bracket_or_backslash_in_display_name_is_refused(self):
        self.repos(repo("first"))
        self.profile("first", "# Foo [v2] — tail\n\nbody\n")
        self.assert_refused("bracket or backslash")
        self.profile("first", "# Foo\\ — tail\n\nbody\n")
        self.assert_refused("bracket or backslash")

    def test_body_with_no_title_line_is_refused(self):
        self.repos(repo("first"))
        self.profile("first", "first — a tail without the hash\n\nbody\n")
        self.assert_refused("first line must be")

    def test_body_opening_with_a_real_heading_is_refused_but_a_hashtag_is_not(self):
        self.repos(repo("first", description="# via placeholder"))
        self.profile("first", "# first — t\n\n## Not the title form\n\nbody\n")
        self.assert_refused("opens with a heading")
        self.profile("first", "# first — t\n\n{description}\n")
        self.assert_refused("opens with a heading")
        self.profile("first", "# first — t\n\n#1 on the roadmap.sh list\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("\n\n#1 on the roadmap.sh list\n", readme)

    def test_own_repo_link_inside_a_body_does_not_split_the_section(self):
        self.config({"login": LOGIN, "order": ["first", "second"], "markers": [START, END]})
        self.repos(repo("first"), repo("second"))
        self.profile("first", "# first — t\n\nsee\n### [also](https://github.com/someone/second)\nend\n")
        self.profile("second", "# second — t\n\nbody\n")
        _, _, readme = self.run_build()
        sections = self.sections(readme)
        self.assertEqual(list(sections), ["first", "second"])
        self.assertIn("### [also](https://github.com/someone/second)\nend\n", sections["first"])
        self.assertEqual(sections["second"], "### [second](https://github.com/someone/second) — t\n\nbody\n")

    def test_legacy_block_without_marks_is_still_split_by_heading(self):
        legacy = ("### [a](https://github.com/someone/a) — t\n\nbody a\n\n"
                  "### [b](https://github.com/someone/b)\n\nbody b\n")
        self.assertEqual(list(br.sections_by_repo(legacy, LOGIN)), ["a", "b"])
        marked = br.render_inner([br.render_section(repo("a"), None, "# a — t\n\nbody a\n"),
                                  br.render_section(repo("b", description="body b"), None, None)])
        # Same sections, same order, only the marks differ: the one-time transition message.
        self.assertEqual(br.describe_change(legacy, marked, LOGIN), ["block: changed outside any repository section"])


class Lifecycle(Harness):
    def test_second_run_changes_nothing_and_check_agrees(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        code, _, first = self.run_build()
        self.assertEqual(code, 0)
        code, out, second = self.run_build()
        self.assertEqual((code, first), (0, second))
        self.assertIn("current", out)
        self.assertEqual(self.run_build(check=True)[0], br.EXIT_OK)

    def test_check_reports_stale_without_writing(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        code, out, readme = self.run_build(check=True)
        self.assertEqual(code, br.EXIT_STALE)
        self.assertIn("first: added", out)
        self.assertEqual(readme, README_TEMPLATE)

    def test_summary_names_added_changed_removed_and_not_the_unchanged(self):
        self.uncurated()
        self.repos(repo("first", description="a"), repo("gone", description="b"), repo("same", description="c"))
        self.run_build()
        self.repos(repo("first", description="new words"), repo("fresh", description="d"), repo("same", description="c"))
        self.run_build()
        summary = self.summary()
        for line in ("first: changed", "fresh: added", "gone: removed"):
            self.assertIn(line, summary)
        self.assertNotIn("same: changed", summary)
        self.assertTrue(summary.startswith("Rebuild README from repository state\n"))
        self.assertIn("Sources:\n", summary)

    def test_order_change_is_named_even_alongside_an_addition(self):
        self.config({"login": LOGIN, "order": ["a", "b"], "markers": [START, END]})
        self.repos(repo("a", description="x"), repo("b", description="y"))
        self.profile("a", "# a — t\n\nbody\n")
        self.profile("b", "# b — t\n\nbody\n")
        self.run_build()
        self.config({"login": LOGIN, "order": ["b", "a"], "markers": [START, END]})
        self.repos(repo("a", description="x"), repo("b", description="y"), repo("c", description="z"))
        _, out, _ = self.run_build()
        self.assertIn("order: changed", out)
        self.assertIn("c: added", out)

    def test_removal_is_refused_when_the_repository_still_exists_publicly(self):
        self.uncurated()
        self.repos(repo("first", description="a"), repo("flaky", description="b"))
        self.run_build()
        self.repos(repo("first", description="a"))
        self.repo_detail(repo("flaky", description="b"))
        with self.assertRaises(br.BuildError) as caught:
            self.run_build()
        self.assertIn("still a public repository", str(caught.exception))
        self.assertIn("flaky", (self.root / "README.md").read_text(encoding="utf-8"))

    def test_removal_proceeds_when_gone_private_archived_excluded_or_renamed(self):
        self.uncurated()
        self.repos(*(repo(n, description=n) for n in ("first", "deleted", "hidden", "boxed", "noisy", "oldname")))
        self.run_build()
        self.config({"login": LOGIN, "order": [], "exclude": ["noisy"], "markers": [START, END]})
        self.repos(repo("first", description="first"), repo("newname", description="renamed"))
        self.repo_detail(repo("hidden", description="hidden", private=True))
        self.repo_detail(repo("boxed", description="boxed", archived=True))
        self.repo_detail(repo("noisy", description="noisy"))
        self.repo_detail(repo("newname", description="renamed"), under="oldname")  # GitHub's 301 answer
        code, out, readme = self.run_build()
        self.assertEqual(code, 0)
        for line in ("deleted: removed", "hidden: removed", "boxed: removed", "noisy: removed",
                     "oldname: removed", "newname: added"):
            self.assertIn(line, out)
        self.assertIn("oldname appears to have been renamed to 'newname'", out)
        # Both uncurated with the same pushed_at, so the listing order is kept.
        self.assertEqual(list(self.sections(readme)), ["first", "newname"])

    def test_notice_only_repair_is_reported_as_a_change(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        _, _, readme = self.run_build()
        (self.root / "README.md").write_text(readme.replace(br.NOTICE + "\n", ""), encoding="utf-8", newline="\n")
        _, out, _ = self.run_build()
        self.assertIn("block: notice line or spacing restored", out)

    def test_missing_marker_is_an_error(self):
        self.repos(repo("first"))
        (self.root / "README.md").write_text("# Name\n\nno markers here\n", encoding="utf-8")
        with self.assertRaises(br.BuildError):
            self.run_build()

    def test_outage_is_an_error_not_an_empty_section(self):
        self.repos(repo("first"))
        (self.fixtures / "fail").write_text("", encoding="utf-8")
        self.assert_refused("outage")

    def test_crlf_readme_is_refused(self):
        self.repos(repo("first"))
        (self.root / "README.md").write_bytes(README_TEMPLATE.replace("\n", "\r\n").encode())
        with self.assertRaises(br.BuildError):
            self.run_build()

    def test_summary_is_written_before_readme_and_appended_to_step_summary(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        step = self.root / "step.md"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(step)}):
            self.run_build()
            self.run_build()
        text = step.read_text(encoding="utf-8")
        self.assertEqual(text.count("### README build"), 2)
        self.assertIn("first: added", text)
        self.assertIn("No section changed.", text)
        self.assertIn("Sources:\n  first: .github/PROFILE.md", text)

    def test_main_maps_errors_to_exit_2(self):
        # main() reports on stderr; keep that out of the test log, where an "error:"
        # line from a passing test reads like a failing one.
        argv = ["--root", str(self.root), "--fixtures", str(self.fixtures)]
        self.repos(repo("a-fork", fork=True))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(br.main(argv), br.EXIT_ERROR)
        self.assertIn("no public repositories", err.getvalue())
        (self.fixtures / "repos.json").unlink()  # an unforeseen exception, not a BuildError
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(br.main(argv), br.EXIT_ERROR)
        self.assertIn("unexpected", err.getvalue())


class FakeResponse:
    def __init__(self, status, body, content_type):
        self.status, self._body = status, body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"", retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://api.github.com/x", code, "msg", headers, io.BytesIO(body))


class GitHubClient(unittest.TestCase):
    """The rule 'a 404 is an answer, anything else is a failure', pinned on the class
    that talks to the network, with the opener faked and sleep silenced."""

    def setUp(self):
        self.client = br.GitHub("token")
        patcher = mock.patch.object(br.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_404_on_a_file_is_none_and_404_on_the_account_is_an_error(self):
        with mock.patch.object(br, "_open", side_effect=http_error(404)) as opened:
            self.assertIsNone(self.client.raw_file("o/r", ".github/PROFILE.md"))
            with self.assertRaises(br.BuildError) as caught:
                self.client.list_repos("nobody")
        self.assertEqual(opened.call_count, 2)
        self.assertIn("not found", str(caught.exception))

    def test_403_fails_at_once_unless_a_short_retry_after_is_given(self):
        with mock.patch.object(br, "_open", side_effect=http_error(403, b"rate limited")) as opened:
            with self.assertRaises(br.BuildError):
                self.client.raw_file("o/r", "x")
        self.assertEqual(opened.call_count, 1)
        ok = FakeResponse(200, b"# t\n", "application/vnd.github.raw+json; charset=utf-8")
        with mock.patch.object(br, "_open", side_effect=[http_error(429, retry_after=3), ok]) as opened:
            self.assertEqual(self.client.raw_file("o/r", "x"), b"# t\n")
        self.assertEqual(opened.call_count, 2)
        self.sleep.assert_any_call(3)

    def test_5xx_is_retried_three_times_then_fails(self):
        with mock.patch.object(br, "_open", side_effect=http_error(503)) as opened:
            with self.assertRaises(br.BuildError):
                self.client.get_repo("o/r")
        self.assertEqual(opened.call_count, 3)

    def test_a_directory_at_the_profile_path_is_an_error_not_a_body(self):
        listing = FakeResponse(200, b'[{"name": "PROFILE.md"}]', "application/json; charset=utf-8")
        with mock.patch.object(br, "_open", return_value=listing):
            with self.assertRaises(br.BuildError) as caught:
                self.client.raw_file("o/r", ".github")
        self.assertIn("not a regular file", str(caught.exception))

    def test_non_list_listing_is_an_error(self):
        with mock.patch.object(br, "_open", return_value=FakeResponse(200, b'{"message": "odd"}', "application/json")):
            with self.assertRaises(br.BuildError):
                self.client.list_repos("someone")

    def test_authorization_header_is_sent_and_pagination_follows_next(self):
        page1 = FakeResponse(200, json.dumps([{"id": 1, "name": "a"}]).encode(), "application/json")
        page1.headers["Link"] = '<https://api.github.com/user/1/repos?page=2>; rel="next", <https://api.github.com/user/1/repos?page=2>; rel="last"'
        page2 = FakeResponse(200, json.dumps([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]).encode(), "application/json")
        with mock.patch.object(br, "_open", side_effect=[page1, page2]) as opened:
            repos = self.client.list_repos("someone")
        self.assertEqual([r["name"] for r in repos], ["a", "b"])  # the duplicate id is dropped
        first_request = opened.call_args_list[0].args[0]
        self.assertEqual(first_request.get_header("Authorization"), "Bearer token")
        self.assertEqual(opened.call_args_list[1].args[0].full_url, "https://api.github.com/user/1/repos?page=2")

    def test_redirect_off_the_api_host_is_refused(self):
        handler = br._StayOnApiHost()
        with self.assertRaises(br.BuildError):
            handler.redirect_request(mock.Mock(), None, 302, "", {}, "https://evil.example/x")


class Helpers(unittest.TestCase):
    def test_next_link_parsing(self):
        header = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"'
        self.assertEqual(br._next_link(header), "https://api.github.com/x?page=2")
        self.assertIsNone(br._next_link('<https://api.github.com/x?page=1>; rel="prev"'))
        self.assertIsNone(br._next_link(""))

    def test_split_blurb_skips_leading_comments_and_tolerates_no_title(self):
        self.assertEqual(br.split_blurb("<!-- a -->\n<!-- b\nc -->\n\n# T — x\n\nbody\n"), ("T — x", "body"))
        self.assertEqual(br.split_blurb("just a body\n"), (None, "just a body"))
        self.assertFalse(br.has_content("<!-- only -->\n\n"))

    def test_splice_rejects_duplicate_or_reversed_markers(self):
        with self.assertRaises(br.BuildError):
            br.splice(f"{END} mid {START}", f"{START}\n{END}", START, END)
        with self.assertRaises(br.BuildError):
            br.splice(f"{START} a {END} {START} b {END}", f"{START}\n{END}", START, END)
        with self.assertRaises(br.BuildError):
            br.splice(f"{START} a {END}", f"{START}\n{END}\n{END}", START, END)

    def test_pick_release_handles_none_object_empty_list_ties_and_missing_dates(self):
        self.assertIsNone(br.pick_release(None))
        self.assertIsNone(br.pick_release([]))
        self.assertIsNone(br.pick_release([{"tag_name": "v1", "prerelease": True}]))
        self.assertEqual(br.pick_release({"tag_name": "v1"})["tag_name"], "v1")
        self.assertEqual(br.pick_release([{"tag_name": "a", "published_at": "2026-01-01T00:00:00Z"},
                                          {"tag_name": "b", "published_at": "2026-01-01T00:00:00Z"}])["tag_name"], "a")
        self.assertEqual(br.pick_release([{"tag_name": "undated"},
                                          {"tag_name": "dated", "published_at": "2026-01-01T00:00:00Z"}])["tag_name"], "dated")

    def test_brace_edge_cases_are_left_as_written_beyond_the_escape_pair(self):
        values = {"version": "v1"}
        self.assertEqual(br.substitute("{{version}}", values), "{version}")
        self.assertEqual(br.substitute("{{{version}}}", values), "{{version}}")
        self.assertEqual(br.substitute("{{ version }}", values), "{{ version }}")
        self.assertEqual(br.substitute("{version}}", values), "v1}")


if __name__ == "__main__":
    unittest.main()
