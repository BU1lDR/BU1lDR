"""tools/build_readme.py against a fixture account, offline.

Every case builds a throwaway root -- README.md with markers, profile.config.json,
a fixtures directory standing in for the API -- and calls build() in-process. The
network client is exercised separately with a faked opener, so the "404 is an
answer, anything else is a failure" rule is pinned on the code that implements it,
not only on the fixture double.

Nothing here reaches the network. The CI job runs this suite with HTTPS_PROXY pointed
at a closed port, so a test that did would fail loudly rather than pass by luck.
"""

from __future__ import annotations

import contextlib
import email.message
import hashlib
import io
import json
import os
import random
import sys
import tempfile
import unittest
import urllib.error
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_readme as br  # noqa: E402

LOGIN = "someone"
START, END = "<!-- work:start -->", "<!-- work:end -->"


def repo(name, *, pushed="2026-01-01T00:00:00Z", fork=False, archived=False, private=False,
         description="", homepage=None, licence="MIT", repo_id=None, visibility=True, owner=LOGIN):
    data = {
        "id": repo_id or zlib.crc32(name.encode()),  # stable across processes, unlike hash()
        "name": name,
        "full_name": f"{owner}/{name}",
        "html_url": f"https://github.com/{owner}/{name}",
        "fork": fork,
        "archived": archived,
        "private": private,
        "description": description,
        "homepage": homepage,
        "pushed_at": pushed,
        "license": {"spdx_id": licence} if licence else None,
    }
    if visibility is True:
        data["visibility"] = "private" if private else "public"
    elif visibility:
        data["visibility"] = visibility
    return data


def release(tag, published="2026-02-01T00:00:00Z", *, draft=False, prerelease=False):
    return {"tag_name": tag, "published_at": published, "draft": draft, "prerelease": prerelease}


README_TEMPLATE = (
    "# Name\n\nIntro that must survive. $1 $& \\1 <!-- not a marker -->\n\n## Work\n\n"
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

    def user(self, public_repos):
        (self.fixtures / "user.json").write_text(json.dumps({"login": LOGIN, "public_repos": public_repos}), encoding="utf-8")

    def repo_detail(self, item, under=None):
        (self.fixtures / "repos" / f"{under or item['name']}.json").write_text(json.dumps(item), encoding="utf-8")

    def profile(self, name, text):
        (self.fixtures / "profiles" / f"{name}.md").write_bytes(text.encode("utf-8") if isinstance(text, str) else text)

    def release(self, name, data):
        (self.fixtures / "releases" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def run_build(self, check=False, **kwargs):
        """Run the builder and, on every path, assert the marker invariant: exactly
        one START and one END before and after, whatever else happened."""
        before = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertEqual((before.count(START), before.count(END)), (1, 1))
        out = io.StringIO()
        self.report = {}
        try:
            code = br.build(self.root, br.Fixtures(self.fixtures), check, self.root / "summary.md", out=out,
                            report=self.report, **kwargs)
        finally:
            after = (self.root / "README.md").read_text(encoding="utf-8")
            self.assertEqual((after.count(START), after.count(END)), (1, 1))
        return code, out.getvalue(), after

    def readme_bytes(self):
        return (self.root / "README.md").read_bytes()

    def summary(self):
        return (self.root / "summary.md").read_text(encoding="utf-8")

    @staticmethod
    def sections(readme):
        """{repo name: section text} for what sits between the markers."""
        return br.sections_by_repo(br.inner_block(readme, START, END), LOGIN)

    def assert_refused(self, message_fragment=""):
        before = self.readme_bytes()
        with self.assertRaises(br.BuildError) as caught:
            self.run_build()
        self.assertIn(message_fragment, str(caught.exception))
        self.assertEqual(self.readme_bytes(), before)
        return caught.exception


class Selection(Harness):
    def test_forks_archived_private_internal_excluded_and_self_are_skipped(self):
        self.repos(
            repo("first"),
            repo("a-fork", fork=True),
            repo("old", archived=True),
            repo("secret", private=True),
            repo("secret-no-visibility-field", private=True, visibility=False),
            repo("internal-only", private=False, visibility="internal"),
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

    def test_same_pushed_at_is_broken_by_name_then_id_not_by_listing_order(self):
        self.uncurated()
        same = "2026-04-01T00:00:00Z"
        self.repos(repo("delta", pushed=same, description="d"), repo("Bravo", pushed=same, description="b"),
                   repo("alpha", pushed=same, description="a"))
        _, _, readme = self.run_build()
        self.assertEqual(list(self.sections(readme)), ["alpha", "Bravo", "delta"])

    def test_curated_name_that_matches_nothing_is_warned_about_in_output_and_summary(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        _, out, _ = self.run_build()
        self.assertIn("::warning::profile.config.json orders 'second'", out)
        self.assertIn("Warnings:\n  profile.config.json orders 'second'", self.summary())

    def test_no_repositories_is_an_error_and_readme_is_untouched(self):
        self.repos(repo("a-fork", fork=True))
        self.assert_refused("no public repositories")

    def test_listing_shorter_than_the_accounts_public_count_is_refused(self):
        self.uncurated()
        self.repos(repo("first", description="a"), repo("second", description="b"), repo("hidden", private=True))
        self.user(3)  # the account says three public; the listing has two -- a page was dropped
        self.assert_refused("returned 2 public repositories but the account reports 3")
        self.user(2)
        code, _, _ = self.run_build()
        self.assertEqual(code, 0)
        self.assertEqual(self.report["public_repos"], 2)


class PrivateDataLeak(Harness):
    """1.9: nothing about a private, internal, forked or archived repository -- name,
    description or count -- reaches the README, the summary, the log or the run
    record, whatever the listing contained."""

    SECRETS = ("secret-project", "TOP SECRET 7f3a", "internal-tool", "INTERNAL ONLY 9c2e",
               "hidden-no-vis", "HIDDEN 4b4b", "forked-thing", "FORK 5d1b", "old-archive", "ARCHIVED 3e8a")

    def test_private_and_internal_repositories_leave_no_trace(self):
        self.uncurated()
        self.repos(
            repo("pub", description="public words"),
            repo("secret-project", private=True, description="TOP SECRET 7f3a"),
            repo("internal-tool", private=False, visibility="internal", description="INTERNAL ONLY 9c2e"),
            repo("hidden-no-vis", private=True, visibility=False, description="HIDDEN 4b4b"),
            repo("forked-thing", fork=True, description="FORK 5d1b"),
            repo("old-archive", archived=True, description="ARCHIVED 3e8a"),
        )
        self.user(3)  # pub, forked-thing, old-archive are the public ones GitHub would count
        code, out, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertEqual(list(self.sections(readme)), ["pub"])
        surfaces = {"README.md": readme, "summary": self.summary(), "stdout": out,
                    "run record": json.dumps(self.report)}
        for surface, text in surfaces.items():
            for secret in self.SECRETS:
                self.assertNotIn(secret, text, f"{secret!r} leaked into {surface}")
        self.assertEqual((self.report["repos_public"], self.report["repos_selected"], self.report["sections"]), (3, 1, 1))


class Rendering(Harness):
    def test_profile_md_drives_heading_and_body_with_placeholders(self):
        self.repos(repo("first", homepage="https://x.example/"))
        self.release("first", release("v2.0.0"))
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
            release("v3.0.0-rc1", "2026-05-01T00:00:00Z", prerelease=True),
            release("v9.9.9", "2026-06-01T00:00:00Z", draft=True),
            release("v2.1.0", "2026-03-01T00:00:00Z"),   # first eligible in list order
            release("v2.0.1", "2026-04-01T00:00:00Z"),   # but published later
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
        self.repos(repo("bare", description=""), repo("shouty", description="# Not a heading please"),
                   repo("nulled", description=None))
        _, _, readme = self.run_build()
        sections = self.sections(readme)
        self.assertEqual(sections["bare"], "### [bare](https://github.com/someone/bare)\n\nNo description yet.\n")
        self.assertEqual(sections["nulled"], "### [nulled](https://github.com/someone/nulled)\n\nNo description yet.\n")
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

    def test_text_outside_the_markers_is_untouched_and_notice_and_stamp_come_first(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        _, _, readme = self.run_build()
        head, tail = README_TEMPLATE.split(f"{START}\nstale\n{END}")
        self.assertTrue(readme.startswith(head + START + "\n" + br.NOTICE + "\n<!-- build: sections=1 digest="))
        self.assertTrue(readme.endswith(END + tail))
        self.assertNotIn("stale", readme)
        inner = br.inner_block(readme, START, END)
        self.assertNotIn(br.NOTICE, inner)
        self.assertNotIn("<!-- build:", inner)
        self.assertTrue(inner.startswith(br.section_mark("first") + "\n### [first]("), inner)
        self.assertTrue(inner.endswith("\n") and not inner.endswith("\n\n"))

    def test_build_stamp_names_the_section_count_and_the_digest_of_the_sections(self):
        self.uncurated()
        self.repos(repo("a", description="x"), repo("b", description="y"))
        _, _, readme = self.run_build()
        stamp = next(line for line in readme.split("\n") if line.startswith("<!-- build:"))
        match = br.BUILD_STAMP.match(stamp)
        self.assertEqual(match.group("sections"), "2")
        self.assertEqual(match.group("digest"), hashlib.sha256(br.inner_block(readme, START, END).encode()).hexdigest()[:12])


class Idempotency(Harness):
    """1.2: the same inputs in any order, on any clock, in any zone, are the same bytes."""

    def inventory(self):
        same = "2026-04-01T00:00:00Z"
        return [
            repo("second", pushed=same, description="two"),
            repo("first", pushed=same, homepage="https://x.example/"),
            repo("gamma", pushed=same, description="g"),
            repo("beta", pushed=same, description="b"),
            repo("alpha", pushed="2026-05-01T00:00:00Z", description="a"),
            repo("never", pushed=None, description="n"),
            repo("also-never", pushed=None, description="n2"),
        ]

    def releases(self):
        return [release("v1.0.0", "2026-01-01T00:00:00Z"), release("v1.0.1", "2026-02-01T00:00:00Z"),
                release("v1.1.0-rc1", "2026-03-01T00:00:00Z", prerelease=True),
                release("v1.0.2", "2026-02-01T00:00:00Z")]  # ties v1.0.1 on date; the greater tag wins

    def render(self, seed):
        inventory, releases = self.inventory(), self.releases()
        if seed is not None:
            random.Random(seed).shuffle(inventory)
            random.Random(seed).shuffle(releases)
        self.repos(*inventory)
        self.release("first", releases)
        self.profile("first", "# first — t ([live]({live}))\n\n{version} {license}\n")
        self.profile("second", "# second — t\n\nbody\n")
        (self.root / "README.md").write_text(README_TEMPLATE, encoding="utf-8", newline="\n")
        code, _, _ = self.run_build()
        self.assertEqual(code, 0)
        return hashlib.sha256(self.readme_bytes()).hexdigest()

    def test_shuffled_inventory_and_releases_render_identical_bytes(self):
        reference = self.render(None)
        for seed in range(6):
            self.assertEqual(self.render(seed), reference, f"shuffle seed {seed} changed the output")
        self.assertIn("v1.0.2 MIT", self.readme_bytes().decode())

    def test_second_run_writes_nothing_and_the_file_is_byte_identical(self):
        self.render(None)
        first = self.readme_bytes()
        code, out, _ = self.run_build()
        self.assertEqual((code, self.readme_bytes()), (0, first))
        self.assertIn("current", out)

    def test_now_and_tz_do_not_reach_the_readme(self):
        stamps = []
        for now, tz in (("2026-12-31T23:59:59Z", "UTC"), ("2027-01-01T00:00:00Z", "Asia/Kolkata"),
                        ("1893456000", "America/New_York")):
            with mock.patch.dict(os.environ, {"NOW": now, "TZ": tz}):
                if hasattr(os, "tzset"):
                    os.tzset()
                stamps.append((self.render(None), br.utc_now()))
        try:
            if hasattr(os, "tzset"):
                os.tzset()
        finally:
            pass
        self.assertEqual(len({digest for digest, _ in stamps}), 1, "the clock or the zone changed README.md")
        self.assertEqual([stamp for _, stamp in stamps],
                         ["2026-12-31T23:59:59Z", "2027-01-01T00:00:00Z", "2030-01-01T00:00:00Z"])


class Markers(Harness):
    """1.3: the markers survive every render, and nothing fetched can forge them."""

    def write_readme(self, text):
        (self.root / "README.md").write_text(text, encoding="utf-8", newline="\n")

    def test_end_marker_missing_is_an_error_and_the_file_is_untouched(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        for broken in (f"# Name\n\n{START}\nstale\n", f"# Name\n\nstale\n{END}\n", "# Name\n\nno markers\n"):
            self.write_readme(broken)
            with self.assertRaises(br.BuildError):
                br.build(self.root, br.Fixtures(self.fixtures), False, None, out=io.StringIO())
            self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), broken)

    def test_reversed_or_duplicated_markers_are_an_error(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        for broken in (f"# Name\n\n{END}\nstale\n{START}\n", f"# Name\n\n{START}\na\n{END}\n{START}\nb\n{END}\n",
                       f"# Name\n\n{START}\n{START}\n{END}\n"):
            self.write_readme(broken)
            with self.assertRaises(br.BuildError):
                br.build(self.root, br.Fixtures(self.fixtures), False, None, out=io.StringIO())
            self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), broken)

    def test_marker_text_inside_a_profile_body_is_refused(self):
        self.repos(repo("first"))
        self.profile("first", f"# first — t\n\nbody mentions {END} literally\n")
        self.assert_refused("contains the README marker")
        self.profile("first", f"# first — t\n\nbody mentions {START} literally\n")
        self.assert_refused("contains the README marker")

    def test_marker_text_in_a_description_is_escaped_into_harmless_literal_text(self):
        self.uncurated()
        self.repos(repo("first", description=f"ends the block? {END} and {START}"))
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("ends the block? &lt;!-- work:end --&gt; and &lt;!-- work:start --&gt;\n", readme)
        self.profile("first", "# first — t\n\n{description}\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("\n\nends the block? &lt;!-- work:end --&gt; and &lt;!-- work:start --&gt;\n", readme)

    def test_section_mark_and_build_stamp_text_are_refused_from_a_profile_and_escaped_from_a_description(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody with <!-- repo: other --> in it\n")
        self.assert_refused("delimit sections")
        self.profile("first", "# first — t\n\n<!-- build: sections=9 digest=000000000000 -->\n")
        self.assert_refused("build stamp")
        self.profile("first", f"# first — t\n\nquoting the header:\n{br.NOTICE}\n")
        self.assert_refused("notice line")
        self.repos(repo("first", description="<!-- repo: ghost -->"))
        self.profile("first", "# first — t\n\n{description}\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertEqual(list(self.sections(readme)), ["first"])
        self.assertIn("&lt;!-- repo: ghost --&gt;", readme)

    def test_post_condition_refuses_a_splice_that_touched_the_outside(self):
        before = f"intro\n{START}\nold\n{END}\ntail\n"
        br.assert_markers_preserved(before, f"intro\n{START}\nnew\n{END}\ntail\n", START, END)
        for after in (f"INTRO\n{START}\nnew\n{END}\ntail\n", f"intro\n{START}\nnew\n{END}\nTAIL\n",
                      f"intro\n{START}\nnew\n{END}\n{END}\ntail\n", f"intro\nnew\n{END}\ntail\n"):
            with self.assertRaises(br.BuildError):
                br.assert_markers_preserved(before, after, START, END)


HOSTILE_DESCRIPTION = (
    "Title $& $' $` $1 \\1 | un`balanced <script>alert(1)</script> <img src=x onerror=1> "
    '<a href="https://evil.example">x</a> <details><summary>s</summary></details> <picture></picture> '
    "[x](y) *stars* _under_ snake_case_name :shortcode: &amp; مرحبا بالعالم\n# not a heading "
    f"{END} <!-- repo: ghost --> " + "x" * 300
)
HOSTILE_ESCAPED = (
    "Title $&amp; $' $\\` $1 \\\\1 \\| un\\`balanced &lt;script&gt;alert(1)&lt;/script&gt; &lt;img src=x onerror=1&gt; "
    '&lt;a href="https://evil.example"&gt;x&lt;/a&gt; &lt;details&gt;&lt;summary&gt;s&lt;/summary&gt;&lt;/details&gt; '
    "&lt;picture&gt;&lt;/picture&gt; \\[x\\](y) \\*stars\\* \\_under\\_ snake\\_case\\_name :shortcode: &amp;amp; "
    "مرحبا بالعالم # not a heading &lt;!-- work:end --&gt; &lt;!-- repo: ghost --&gt; " + "x" * 300
)


class HostileInput(Harness):
    def test_every_hostile_string_in_one_description_renders_as_exactly_this(self):
        self.uncurated()
        self.repos(repo("first", description=HOSTILE_DESCRIPTION), repo("second", description="# Heading\n- item"))
        self.release("first", release("v1.0_beta|<1>&2`3$1"))
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertEqual(self.sections(readme)["first"],
                         f"### [first](https://github.com/someone/first)\n\n{HOSTILE_ESCAPED}\n")
        self.assertEqual(self.sections(readme)["second"],
                         "### [second](https://github.com/someone/second)\n\n\\# Heading - item\n")
        self.profile("first", "# first — {version} $1 \\1\n\n{description}\n\n`{version}` is literal here.\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertEqual(self.sections(readme)["first"],
                         "### [first](https://github.com/someone/first) — v1.0\\_beta\\|&lt;1&gt;&amp;2\\`3$1 $1 \\1\n\n"
                         f"{HOSTILE_ESCAPED}\n\n`v1.0\\_beta\\|&lt;1&gt;&amp;2\\`3$1` is literal here.\n")

    def test_escape_text_rules(self):
        cases = {
            "": "",
            "plain words": "plain words",
            "a\r\nb\rc\nd": "a b c d",
            "  padded  ": "padded",
            "tab\tand\x00nul\x1b[0m": "tab\tandnul\\[0m",
            "# heading": "\\# heading",
            "> quote": "&gt; quote",
            "- item": "\\- item",
            "+ item": "\\+ item",
            "1. item": "1\\. item",
            "12) item": "12\\) item",
            "1.": "1\\.",
            "a\u202eb\u202c c": "ab c",
            "soft\u00adhyphen zero\u200bwidth \ufeffbom": "softhyphen zerowidth bom",
            "\U0001F468\u200d\U0001F469\u200d\U0001F467": "\U0001F468\u200d\U0001F469\u200d\U0001F467",
            "= rule": "= rule",
            "---": "\\---",
            "===": "\\===",
            "-item": "-item",
            "-": "\\-",
            "1.2.3 version": "1.2.3 version",
            "#1 on a list": "#1 on a list",
            "<!-- x -->": "&lt;!-- x --&gt;",
            "$& $1 $` $' \\1": "$&amp; $1 $\\` $' \\\\1",
            "a|b": "a\\|b",
            "`code": "\\`code",
            "~~strike~~": "\\~\\~strike\\~\\~",
            "&amp; &lt;": "&amp;amp; &amp;lt;",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(br.escape_text(value), expected)

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
        self.assertNotIn("\ufeff", readme)

    def test_non_utf8_profile_is_an_error(self):
        self.repos(repo("first"))
        self.profile("first", b"# caf\xe9 \xe2\x80\x94 tail\n\nbody\n")
        self.assert_refused("is not UTF-8")

    def test_control_characters_in_a_profile_are_an_error_but_tabs_are_fine(self):
        self.repos(repo("first"))
        self.profile("first", b"# first \xe2\x80\x94 tail\n\nbody with a \x01 in it\n")
        self.assert_refused("control character U+0001 on line 3")
        self.profile("first", b"# first \xe2\x80\x94 tail\n\nbody\x1b[0m\n")
        self.assert_refused("control character U+001B")
        self.profile("first", b"# first \xe2\x80\x94 tail\n\n\tindented\tbody\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("\n\n\tindented\tbody\n", readme)

    def test_bidirectional_controls_in_a_profile_are_an_error_but_rtl_text_is_fine(self):
        self.repos(repo("first"))
        self.profile("first", "# first — tail\n\nbody \u202e evil\n")
        self.assert_refused("bidirectional control character U+202E on line 3")
        self.profile("first", "# first — tail\n\n\u2066isolated\u2069\n")
        self.assert_refused("bidirectional control character U+2066")
        self.profile("first", "# first — مرحبا\n\nمرحبا بالعالم\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("— مرحبا\n\nمرحبا بالعالم\n", readme)

    def test_a_body_ending_inside_a_fence_or_html_block_is_refused(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nIntro\n\n```\ncode never closed\n")
        self.assert_refused("unclosed code fence (```)")
        self.profile("first", "# first — t\n\n~~~~\nx\n~~~\n")   # a shorter run does not close it
        self.assert_refused("unclosed code fence (~~~~)")
        self.profile("first", "# first — t\n\n<pre>\nraw\n")
        self.assert_refused("unclosed <pre> block")
        self.profile("first", "# first — t\n\n<SCRIPT src=x>\nalert(1)\n")
        self.assert_refused("unclosed <script> block")
        self.profile("first", "# first — t\n\n```py\ncode with ``` inside\n```\n\n<pre>one line</pre>\n\n"
                              "<details><summary>s</summary>\n\nfine\n\n</details>\n\n~~~\nx\n~~~~\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("```py\ncode with ``` inside\n```\n", readme)
        self.assertIsNone(br.open_block("```\n```"))
        self.assertEqual(br.open_block("````\n```"), "code fence (````)")
        self.assertIsNone(br.open_block("<pre>a</pre>"))
        self.assertEqual(br.open_block("<textarea>\n"), "<textarea> block")

    def test_placeholders_in_the_display_name_arrive_escaped_and_are_accepted(self):
        self.repos(repo("first"))
        self.release("first", release("v1_0|x"))
        self.profile("first", "# demo {version} — tail {version}\n\nbody\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("### [demo v1\\_0\\|x](https://github.com/someone/first) — tail v1\\_0\\|x\n", readme)
        self.repos(repo("first", homepage="https://x.example/a]b"))
        self.profile("first", "# demo {live} — tail\n\nbody\n")
        self.assert_refused("unescaped bracket after substitution")

    def test_repository_names_with_underscores_render_as_text_not_emphasis(self):
        self.uncurated()
        self.repos(repo("__init__", description="d"), repo("_private_", description="p"), repo("plain_name"))
        self.profile("plain_name", "# {name} — t\n\n{name} at {url}\n")
        _, _, readme = self.run_build()
        sections = self.sections(readme)
        self.assertEqual(sections["__init__"], "### [\\_\\_init\\_\\_](https://github.com/someone/__init__)\n\nd\n")
        self.assertTrue(sections["_private_"].startswith("### [\\_private\\_](https://github.com/someone/_private_)\n"))
        self.assertEqual(sections["plain_name"],
                         "### [plain\\_name](https://github.com/someone/plain_name) — t\n\nplain\\_name at https://github.com/someone/plain_name\n")

    def test_oversized_profile_and_oversized_readme_are_refused(self):
        self.repos(repo("first"))
        self.profile("first", b"# first \xe2\x80\x94 t\n\n" + b"x" * (br.MAX_BLURB_BYTES + 1))
        self.assert_refused("bytes; the limit is")
        with mock.patch.object(br, "MAX_README_BYTES", 200):
            self.profile("first", "# first — t\n\n" + "y" * 150 + "\n")
            self.assert_refused("rebuilt README would be")

    def test_live_placeholder_without_a_valid_homepage_is_refused_but_escaped_or_commented_is_fine(self):
        self.repos(repo("first", homepage=None))
        self.profile("first", "# first — site ([live]({live}))\n\nbody\n")
        self.assert_refused("uses {live}")
        for bad in ("bu1ldr.github.io/x) not a url", "https://x.example/a)b", "https://x.example/a\\b",
                    'https://x.example/a"b', "javascript:alert(1)", "https://x.example/ space",
                    "https://x.example/\x01", "https://x.example/\u202e", "https://x.example/\u200b"):
            self.repos(repo("first", homepage=bad))
            self.assert_refused("uses {live}")
        self.repos(repo("first", homepage=None))
        self.profile("first", "<!-- placeholders: {live} {version} -->\n# first — t\n\nwrite {{live}} for a literal\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("write {live} for a literal\n", readme)

    def test_long_blurb_warns_but_renders(self):
        self.config({"login": LOGIN, "order": [], "markers": [START, END], "max_blurb_chars": 60})
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\n" + "words " * 30 + "\n")
        code, out, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("characters against a limit of 60", out)
        self.assertIn("words words", readme)
        self.assertIn("Warnings:\n  first: .github/PROFILE.md is", self.summary())

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

    def test_body_opening_with_a_real_heading_is_refused_but_a_description_cannot_open_one(self):
        self.repos(repo("first", description="# via placeholder"))
        self.profile("first", "# first — t\n\n## Not the title form\n\nbody\n")
        self.assert_refused("opens with a heading")
        self.profile("first", "# first — t\n\n{description}\n")
        code, _, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("\n\n\\# via placeholder\n", readme)
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


class SchemaGuard(Harness):
    """2.5 / 1.4: a field the API renamed, dropped or retyped is an error before anything
    is rendered, and the README is untouched."""

    def test_each_required_repository_field_is_checked(self):
        self.uncurated()
        good = repo("first", description="ok")
        for field in ("id", "name", "full_name", "html_url", "fork", "archived", "private", "description",
                      "homepage", "pushed_at", "license"):
            broken = dict(good)
            del broken[field]
            self.repos(broken)
            with self.subTest(missing=field):
                self.assert_refused(f"field {field!r} is missing")
        for field, wrong in (("id", "1"), ("id", True), ("name", 3), ("fork", "no"), ("private", 0),
                             ("description", 5), ("license", "MIT"), ("visibility", 1)):
            broken = dict(good, **{field: wrong})
            self.repos(broken)
            with self.subTest(retyped=field):
                self.assert_refused(f"field {field!r} is")
        self.repos(dict(good, license={"name": "MIT"}))
        self.assert_refused("field 'spdx_id' is missing")

    def test_names_urls_and_visibility_values_are_checked(self):
        self.uncurated()
        self.repos(dict(repo("first"), name="bad name"))
        self.assert_refused("is not a GitHub repository name")
        self.repos(dict(repo("first"), full_name="someone/other"))
        self.assert_refused("does not match name")
        self.repos(dict(repo("first"), html_url="https://evil.example/someone/first"))
        self.assert_refused("is not https://github.com/someone/first")
        self.repos(dict(repo("first"), visibility="secret"))
        self.assert_refused("visibility 'secret' is not one of")
        self.repos("not an object")
        self.assert_refused("expected a repository object")

    def test_release_fields_are_checked(self):
        self.uncurated()
        self.repos(repo("first", description="x"))
        for bad in ({"tag_name": "v1"}, {"tag_name": "v1", "draft": False, "prerelease": "no", "published_at": None},
                    [{"tag_name": 1, "draft": False, "prerelease": False, "published_at": None}], ["v1"]):
            self.release("first", bad)
            with self.subTest(release=bad):
                self.assert_refused("first.json")

    def test_config_login_is_required(self):
        (self.root / "profile.config.json").write_text(json.dumps({"order": []}), encoding="utf-8")
        self.repos(repo("first"))
        with self.assertRaises(br.BuildError):
            br.build(self.root, br.Fixtures(self.fixtures), False, None, out=io.StringIO())


class ShrinkGuard(Harness):
    def two_sections(self):
        self.uncurated()
        long = "# first — t\n\n" + "words " * 40 + "\n\n" + "more " * 40 + "\n"
        self.repos(repo("first"), repo("second"))
        self.profile("first", long)
        self.profile("second", long.replace("first", "second"))
        code, _, _ = self.run_build()
        self.assertEqual(code, 0)

    def test_a_collapsed_source_with_nothing_removed_is_refused_unless_allowed(self):
        self.two_sections()
        self.profile("first", "# first — t\n\nshort\n")
        self.assert_refused("would shrink from")
        code, out, readme = self.run_build(allow_shrink=True)
        self.assertEqual(code, 0)
        self.assertIn("allowed by --allow-shrink", out)
        self.assertIn("\n\nshort\n", readme)

    def test_a_shrink_explained_by_a_verified_removal_is_fine(self):
        self.two_sections()
        self.repos(repo("first"))  # second deleted; get_repo answers 404
        code, out, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("second: removed", out)
        self.assertEqual(list(self.sections(readme)), ["first"])

    def test_the_first_render_over_a_placeholder_block_is_never_a_shrink(self):
        self.uncurated()
        (self.root / "README.md").write_text(README_TEMPLATE.replace("stale", "x" * 5000), encoding="utf-8", newline="\n")
        self.repos(repo("first", description="tiny"))
        code, _, _ = self.run_build()
        self.assertEqual(code, 0)


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
        self.assertIn("oldname is now listed as 'newname' (renamed)", out)
        # Both uncurated with the same pushed_at: by name, "first" before "newname".
        self.assertEqual(list(self.sections(readme)), ["first", "newname"])

    def test_removal_proceeds_on_a_case_only_rename_or_a_transfer(self):
        self.uncurated()
        self.repos(repo("Foo", description="f", repo_id=7), repo("moved", description="m", repo_id=8),
                   repo("keep", description="k"))
        self.run_build()
        self.repos(repo("foo", description="f", repo_id=7), repo("keep", description="k"))
        self.repo_detail(repo("foo", description="f", repo_id=7), under="Foo")            # GitHub looks names up case-insensitively
        self.repo_detail(repo("moved", description="m", repo_id=8, owner="otherorg"))    # the 301 to the new owner, followed
        code, out, readme = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("Foo is now listed as 'foo' (renamed)", out)
        self.assertIn("moved has moved to 'otherorg/moved'", out)
        self.assertEqual(list(self.sections(readme)), ["foo", "keep"])
        for line in ("foo: added", "Foo: removed", "moved: removed"):
            self.assertIn(line, out)

    def test_a_failed_write_leaves_readme_whole_and_no_temp_file(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        before = self.readme_bytes()
        with mock.patch.object(br.os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                self.run_build()
        self.assertEqual(self.readme_bytes(), before)
        self.assertFalse((self.root / "README.md.tmp").exists())
        code, _, _ = self.run_build()
        self.assertEqual(code, 0)

    def test_notice_or_stamp_repair_is_reported_as_a_change(self):
        self.repos(repo("first"))
        self.profile("first", "# first — t\n\nbody\n")
        _, _, readme = self.run_build()
        (self.root / "README.md").write_text(readme.replace(br.NOTICE + "\n", ""), encoding="utf-8", newline="\n")
        _, out, _ = self.run_build()
        self.assertIn("block: notice, stamp or spacing restored", out)
        stamp = next(line for line in readme.split("\n") if line.startswith("<!-- build:"))
        (self.root / "README.md").write_text(readme.replace(stamp + "\n", ""), encoding="utf-8", newline="\n")
        _, out, _ = self.run_build()
        self.assertIn("block: notice, stamp or spacing restored", out)

    def test_outage_is_an_error_not_an_empty_section(self):
        self.repos(repo("first"))
        (self.fixtures / "fail").write_text("", encoding="utf-8")
        self.assert_refused("outage")

    def test_crlf_readme_is_refused(self):
        self.repos(repo("first"))
        (self.root / "README.md").write_bytes(README_TEMPLATE.replace("\n", "\r\n").encode())
        with self.assertRaises(br.BuildError):
            br.build(self.root, br.Fixtures(self.fixtures), False, None, out=io.StringIO())

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

    def test_report_records_counts_sizes_changes_and_warnings(self):
        self.uncurated()
        self.repos(repo("first", description="a"), repo("second", description="b"))
        self.run_build()
        report = self.report
        self.assertEqual((report["repos_public"], report["repos_selected"], report["sections"]), (2, 2, 2))
        self.assertEqual(report["block_bytes_before"], len("stale\n"))
        self.assertEqual(report["block_bytes"], len(br.inner_block(self.readme_bytes().decode(), START, END).encode()))
        self.assertEqual(report["readme_bytes"], len(self.readme_bytes()))
        self.assertEqual(report["changes"], ["first: added", "second: added"])
        self.assertTrue(report["changed"])
        self.assertEqual(len(report["warnings"]), 2)
        self.assertEqual(report["provenance"], ["first: description", "second: description"])
        self.assertIsNone(report["public_repos"])


class Main(Harness):
    """The command line: exit codes, the token rule, the owner check, the run record."""

    def setUp(self):
        super().setUp()
        # main() reports on stdout and stderr; keep both out of the test log, where an
        # "error:" line from a passing test reads like a failing one.
        for stream in ("sys.stdout", "sys.stderr"):
            patcher = mock.patch(stream, new_callable=io.StringIO)
            patcher.start()
            self.addCleanup(patcher.stop)

    def argv(self, *extra):
        return ["--root", str(self.root), "--fixtures", str(self.fixtures), *extra]

    def meta(self):
        return json.loads((self.root / "meta" / "last-run.json").read_text(encoding="utf-8"))

    def test_main_maps_errors_to_exit_2_and_records_them(self):
        # main() reports on stderr; keep that out of the test log, where an "error:"
        # line from a passing test reads like a failing one.
        self.repos(repo("a-fork", fork=True))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(br.main(self.argv()), br.EXIT_ERROR)
        self.assertIn("no public repositories", err.getvalue())
        self.assertIn("README.md left untouched.", err.getvalue())
        meta = self.meta()
        self.assertEqual((meta["status"], meta["exit_code"]), ("error", 2))
        self.assertIn("no public repositories", meta["error"])
        self.assertEqual(meta["source"]["kind"], "fixtures")
        (self.fixtures / "repos.json").unlink()  # an unforeseen exception, not a BuildError
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(br.main(self.argv()), br.EXIT_ERROR)
        self.assertIn("unexpected", err.getvalue())
        self.assertEqual(self.meta()["status"], "error")

    def test_a_good_run_records_ok_and_the_record_is_stable_under_now(self):
        self.uncurated()
        self.repos(repo("first", description="a"))
        with mock.patch.dict(os.environ, {"NOW": "2026-09-21T12:00:00Z"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(br.main(self.argv()), br.EXIT_OK)
            first = (self.root / "meta" / "last-run.json").read_bytes()
            self.assertEqual(br.main(self.argv()), br.EXIT_OK)
            second = (self.root / "meta" / "last-run.json").read_bytes()
        meta = json.loads(first)
        self.assertEqual((meta["status"], meta["exit_code"], meta["finished_at"]), ("ok", 0, "2026-09-21T12:00:00Z"))
        self.assertEqual((meta["sections"], meta["changed"]), (1, True))
        self.assertEqual(json.loads(second)["changed"], False)
        self.assertEqual(json.loads(second)["finished_at"], "2026-09-21T12:00:00Z")
        self.assertTrue(first.endswith(b"\n") and b"\r" not in first)
        self.assertEqual(json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + "\n", first.decode())

    def test_check_and_no_meta_write_no_record(self):
        self.uncurated()
        self.repos(repo("first", description="a"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(br.main(self.argv("--check")), br.EXIT_STALE)
            self.assertFalse((self.root / "meta").exists())
            self.assertEqual(br.main(self.argv("--no-meta")), br.EXIT_OK)
            self.assertFalse((self.root / "meta").exists())
            self.assertEqual(br.main(self.argv("--meta-file", str(self.root / "elsewhere.json"))), br.EXIT_OK)
        self.assertEqual(json.loads((self.root / "elsewhere.json").read_text())["status"], "ok")

    def test_bad_now_is_an_error_with_a_record(self):
        self.uncurated()
        self.repos(repo("first", description="a"))
        with mock.patch.dict(os.environ, {"NOW": "yesterday"}), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(br.main(self.argv()), br.EXIT_ERROR)
        self.assertIn("NOW='yesterday'", err.getvalue())
        self.assertEqual(self.meta()["status"], "error")
        self.assertRegex(self.meta()["finished_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        with mock.patch.dict(os.environ, {"NOW": "2026-01-01T00:00:00"}), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(br.main(self.argv()), br.EXIT_ERROR)
        self.assertIn("has no zone", err.getvalue())

    def test_missing_or_empty_token_fails_before_any_request(self):
        self.uncurated()
        self.repos(repo("first", description="a"))
        for token in ({}, {"GITHUB_TOKEN": ""}, {"GITHUB_TOKEN": "   "}):
            env = {"PROFILE_FIXTURES": ""}
            env.update(token)
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(br, "_open", side_effect=AssertionError("network was touched")) as opened, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                os.environ.pop("PROFILE_FIXTURES", None)
                if not token:
                    os.environ.pop("GITHUB_TOKEN", None)
                self.assertEqual(br.main(["--root", str(self.root)]), br.EXIT_ERROR)
            self.assertIn("GITHUB_TOKEN is not set or is empty", err.getvalue())
            opened.assert_not_called()
        self.assertEqual(self.meta()["status"], "error")
        self.assertEqual(self.readme_bytes().decode(), README_TEMPLATE)

    def test_profile_fixtures_env_is_the_same_as_the_flag(self):
        self.uncurated()
        self.repos(repo("first", description="a"))
        with mock.patch.dict(os.environ, {"PROFILE_FIXTURES": str(self.fixtures)}), \
             contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop("GITHUB_TOKEN", None)
            self.assertEqual(br.main(["--root", str(self.root)]), br.EXIT_OK)

    def test_owner_mismatch_is_an_error_in_network_mode_only(self):
        self.uncurated()
        self.repos(repo("first", description="a"))
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY_OWNER": "renamed"}), \
             mock.patch.object(br, "_open", side_effect=AssertionError("network was touched")), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            os.environ.pop("PROFILE_FIXTURES", None)
            self.assertEqual(br.main(["--root", str(self.root)]), br.EXIT_ERROR)
        self.assertIn("names 'someone' but this run belongs to 'renamed'", err.getvalue())
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "renamed"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(br.main(self.argv()), br.EXIT_OK)  # fixtures: no owner to compare with
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY_OWNER": "SOMEONE"}), \
             mock.patch.object(br, "_open", side_effect=urllib.error.URLError("offline")), \
             contextlib.redirect_stderr(io.StringIO()) as err, mock.patch.object(br.time, "sleep"):
            os.environ.pop("PROFILE_FIXTURES", None)
            self.assertEqual(br.main(["--root", str(self.root)]), br.EXIT_ERROR)  # case-insensitive match, then offline
        self.assertIn("unreachable", err.getvalue())

    def test_allow_shrink_flag_reaches_the_builder(self):
        self.uncurated()
        self.repos(repo("first"), repo("second"))
        long = "# first — t\n\n" + "words " * 40 + "\n"
        self.profile("first", long)
        self.profile("second", long.replace("first", "second"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(br.main(self.argv()), br.EXIT_OK)
            self.profile("first", "# first — t\n\nshort\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(br.main(self.argv()), br.EXIT_ERROR)
            self.assertEqual(br.main(self.argv("--allow-shrink")), br.EXIT_OK)


class FakeResponse:
    def __init__(self, status, body, content_type, extra=None):
        self.status, self._body = status, body
        self.headers = {"Content-Type": content_type}
        self.headers.update(extra or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"", retry_after=None, headers=None):
    message = email.message.Message()
    if retry_after is not None:
        message["Retry-After"] = str(retry_after)
    for key, value in (headers or {}).items():
        message[key] = str(value)
    return urllib.error.HTTPError("https://api.github.com/x", code, "msg", message, io.BytesIO(body))


FULL = json.dumps([repo("a", repo_id=1)]).encode()


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
            with self.assertRaises(br.BuildError):
                self.client.public_repo_count("nobody")
        self.assertEqual(opened.call_count, 3)
        self.assertIn("not found", str(caught.exception))

    def test_401_names_the_credential_and_does_not_retry(self):
        with mock.patch.object(br, "_open", side_effect=http_error(401, b'{"message":"Bad credentials"}')) as opened:
            with self.assertRaises(br.BuildError) as caught:
                self.client.list_repos("someone")
        self.assertEqual(opened.call_count, 1)
        self.assertIn("GitHub API refused the credential (HTTP 401)", str(caught.exception))
        self.assertIn("GITHUB_TOKEN", str(caught.exception))

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
        with mock.patch.object(br, "_open", side_effect=http_error(429, retry_after=br.MAX_RATE_WAIT + 1)) as opened:
            with self.assertRaises(br.BuildError):
                self.client.raw_file("o/r", "x")
        self.assertEqual(opened.call_count, 1)

    def test_exhausted_primary_limit_waits_for_a_near_reset_and_fails_on_a_far_one(self):
        ok = FakeResponse(200, b"# t\n", "application/vnd.github.raw+json")
        with mock.patch.object(br.time, "time", return_value=1_000_000):
            near = http_error(403, headers={"X-RateLimit-Remaining": 0, "X-RateLimit-Reset": 1_000_045})
            with mock.patch.object(br, "_open", side_effect=[near, ok]):
                self.assertEqual(self.client.raw_file("o/r", "x"), b"# t\n")
            self.sleep.assert_any_call(46)
            far = http_error(403, headers={"X-RateLimit-Remaining": 0, "X-RateLimit-Reset": 1_003_600})
            with mock.patch.object(br, "_open", side_effect=far) as opened:
                with self.assertRaises(br.BuildError) as caught:
                    self.client.raw_file("o/r", "x")
        self.assertEqual(opened.call_count, 1)
        self.assertIn("rate limit exhausted", str(caught.exception))
        self.assertIn("1970-01-12T14:46:40Z", str(caught.exception))

    def test_rate_limit_headers_are_remembered_from_the_last_response(self):
        first = FakeResponse(200, FULL, "application/json",
                             {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999", "X-RateLimit-Used": "1",
                              "X-RateLimit-Reset": "1700000000", "X-RateLimit-Resource": "core"})
        second = FakeResponse(200, b'{"public_repos": 1}', "application/json",
                              {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4998", "X-RateLimit-Used": "2",
                               "X-RateLimit-Reset": "1700000000", "X-RateLimit-Resource": "core"})
        with mock.patch.object(br, "_open", side_effect=[first, second]):
            self.client.list_repos("someone")
            self.assertEqual(self.client.public_repo_count("someone"), 1)
        self.assertEqual(self.client.rate_limit["remaining"], "4998")
        self.assertEqual(self.client.requests, 2)
        self.assertEqual(self.client.describe()["kind"], "github")

    def test_5xx_is_retried_three_times_then_fails(self):
        with mock.patch.object(br, "_open", side_effect=http_error(503)) as opened:
            with self.assertRaises(br.BuildError):
                self.client.get_repo("o/r")
        self.assertEqual(opened.call_count, 3)

    def test_network_errors_are_retried_and_named(self):
        with mock.patch.object(br, "_open", side_effect=TimeoutError("timed out")) as opened:
            with self.assertRaises(br.BuildError) as caught:
                self.client.get_repo("o/r")
        self.assertEqual(opened.call_count, 3)
        self.assertIn("GitHub API unreachable", str(caught.exception))

    def test_a_directory_at_the_profile_path_is_an_error_not_a_body(self):
        listing = FakeResponse(200, b'[{"name": "PROFILE.md"}]', "application/json; charset=utf-8")
        with mock.patch.object(br, "_open", return_value=listing):
            with self.assertRaises(br.BuildError) as caught:
                self.client.raw_file("o/r", ".github")
        self.assertIn("not a regular file", str(caught.exception))

    def test_non_list_listing_and_non_object_account_are_errors(self):
        with mock.patch.object(br, "_open", return_value=FakeResponse(200, b'{"message": "odd"}', "application/json")):
            with self.assertRaises(br.BuildError):
                self.client.list_repos("someone")
        with mock.patch.object(br, "_open", return_value=FakeResponse(200, b'[1]', "application/json")):
            with self.assertRaises(br.BuildError):
                self.client.public_repo_count("someone")
        with mock.patch.object(br, "_open", return_value=FakeResponse(200, b'{"login": "someone"}', "application/json")):
            with self.assertRaises(br.BuildError) as caught:
                self.client.public_repo_count("someone")
        self.assertIn("'public_repos' is missing", str(caught.exception))

    def test_headers_are_sent_and_pagination_follows_next_and_dedupes(self):
        page1 = FakeResponse(200, json.dumps([repo("a", repo_id=1)]).encode(), "application/json")
        page1.headers["Link"] = '<https://api.github.com/user/1/repos?page=2>; rel="next", <https://api.github.com/user/1/repos?page=2>; rel="last"'
        page2 = FakeResponse(200, json.dumps([repo("a", repo_id=1), repo("b", repo_id=2)]).encode(), "application/json")
        with mock.patch.object(br, "_open", side_effect=[page1, page2]) as opened:
            repos = self.client.list_repos("someone")
        self.assertEqual([r["name"] for r in repos], ["a", "b"])  # the duplicate id is dropped
        first_request = opened.call_args_list[0].args[0]
        self.assertEqual(first_request.get_header("Authorization"), "Bearer token")
        self.assertEqual(first_request.get_header("X-github-api-version"), "2022-11-28")
        self.assertEqual(first_request.get_header("User-agent"), br.USER_AGENT)
        self.assertIn("per_page=100", first_request.full_url)
        self.assertEqual(opened.call_args_list[1].args[0].full_url, "https://api.github.com/user/1/repos?page=2")

    def test_verbose_log_names_url_and_status_and_never_the_token(self):
        lines = []
        client = br.GitHub("s3cret", verbose=True, log=lines.append)
        with mock.patch.object(br, "_open", side_effect=[FakeResponse(200, FULL, "application/json"), http_error(404)]):
            client.list_repos("someone")
            client.raw_file("o/r", "x")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("GET https://api.github.com/users/someone/repos?") and lines[0].endswith("-> 200"))
        self.assertTrue(lines[1].endswith("-> 404"))
        self.assertNotIn("s3cret", "".join(lines))

    def test_redirect_off_the_api_host_is_refused(self):
        handler = br._StayOnApiHost()
        with self.assertRaises(br.BuildError):
            handler.redirect_request(mock.Mock(), None, 302, "", {}, "https://evil.example/x")

    def test_listing_entries_are_validated_at_the_client(self):
        with mock.patch.object(br, "_open", return_value=FakeResponse(200, b'[{"id": 1, "name": "a"}]', "application/json")):
            with self.assertRaises(br.BuildError) as caught:
                self.client.list_repos("someone")
        self.assertIn("'full_name' is missing", str(caught.exception))


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
        tied = [{"tag_name": "a", "published_at": "2026-01-01T00:00:00Z"}, {"tag_name": "b", "published_at": "2026-01-01T00:00:00Z"}]
        self.assertEqual(br.pick_release(tied)["tag_name"], "b")
        self.assertEqual(br.pick_release(tied[::-1])["tag_name"], "b")  # the order listed does not decide
        by_id = [{"tag_name": "v1.9.0", "published_at": "2026-01-01T00:00:00Z", "id": 10},
                 {"tag_name": "v1.10.0", "published_at": "2026-01-01T00:00:00Z", "id": 11}]
        self.assertEqual(br.pick_release(by_id)["tag_name"], "v1.10.0")        # the id decides before the tag
        self.assertEqual(br.pick_release(by_id[::-1])["tag_name"], "v1.10.0")
        self.assertEqual(br.pick_release([{"tag_name": "undated"},
                                          {"tag_name": "dated", "published_at": "2026-01-01T00:00:00Z"}])["tag_name"], "dated")

    def test_brace_edge_cases_are_left_as_written_beyond_the_escape_pair(self):
        values = {"version": "v1"}
        self.assertEqual(br.substitute("{{version}}", values), "{version}")
        self.assertEqual(br.substitute("{{{version}}}", values), "{{version}}")
        self.assertEqual(br.substitute("{{ version }}", values), "{{ version }}")
        self.assertEqual(br.substitute("{version}}", values), "v1}")

    def test_substitute_uses_a_function_so_values_are_never_backreferences(self):
        self.assertEqual(br.substitute("{version} / {name}", {"version": "$1 \\1 \\g<0> $&", "name": "\\"}),
                         "$1 \\1 \\g<0> $& / \\")

    def test_utc_now_accepts_epoch_and_iso_and_rejects_the_rest(self):
        with mock.patch.dict(os.environ, {"NOW": "0"}):
            self.assertEqual(br.utc_now(), "1970-01-01T00:00:00Z")
        with mock.patch.dict(os.environ, {"NOW": "2026-09-21T17:30:00+05:30"}):
            self.assertEqual(br.utc_now(), "2026-09-21T12:00:00Z")
        with mock.patch.dict(os.environ, {"NOW": "2026-09-21T12:00:00.250Z"}):
            self.assertEqual(br.utc_now(), "2026-09-21T12:00:00Z")
        for bad in ("soon", "2026-09-21T12:00:00", "2026-13-01T00:00:00Z"):
            with mock.patch.dict(os.environ, {"NOW": bad}), self.assertRaises(br.BuildError):
                br.utc_now()
        with mock.patch.dict(os.environ, {"NOW": ""}):
            self.assertRegex(br.utc_now(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


if __name__ == "__main__":
    unittest.main()
