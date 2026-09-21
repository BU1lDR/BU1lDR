#!/usr/bin/env python3
"""Regenerate the hostile fixture's inputs. Run from the repository root:

    python3 fixtures/hostile/make.py

then render it and review the result before pinning it as the expected snapshot:

    d=$(mktemp -d); cp fixtures/hostile/README.in.md "$d/README.md"; cp fixtures/hostile/config.json "$d/profile.config.json"
    NOW=2026-09-21T12:00:00Z python3 tools/build_readme.py --root "$d" --fixtures fixtures/hostile --no-meta
    cp "$d/README.md" fixtures/hostile/expected/README.md

One fixture, every attacker-controlled string the brief lists, plus the repositories
that must never appear: private, internal, forked, archived, the profile itself.
scripts/acceptance.sh renders it and compares byte-for-byte with expected/README.md,
then shuffles it and requires the same sha256.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGIN = "hostile-owner"
START, END = "<!-- work:start -->", "<!-- work:end -->"


def repo(name, *, rid, pushed="2026-05-01T00:00:00Z", fork=False, archived=False, private=False,
         description="", homepage=None, licence="MIT", visibility=None):
    return {
        "id": rid, "name": name, "full_name": f"{LOGIN}/{name}", "html_url": f"https://github.com/{LOGIN}/{name}",
        "fork": fork, "archived": archived, "private": private, "description": description, "homepage": homepage,
        "pushed_at": pushed, "license": {"spdx_id": licence} if licence else None,
        "visibility": visibility or ("private" if private else "public"),
    }


HOSTILE = (
    "Title $& $' $` $1 \\1 | un`balanced <script>alert(1)</script> <img src=x onerror=1> "
    '<a href="https://evil.example">x</a> <details><summary>s</summary></details> <picture></picture> '
    "[x](y) *stars* _under_ snake_case_name :shortcode: &amp; "
    "مرحبا بالعالم "          # RTL
    "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466 café\r\n"             # grapheme clusters, CRLF
    f"# not a heading {END} {START} <!-- repo: ghost --> <!-- build: sections=0 digest=000000000000 --> "
    + "x" * 300                                                                                # a 300-character run
)

REPOS = [
    repo("curated", rid=101, pushed="2026-06-01T00:00:00Z", description=HOSTILE, homepage="https://example.com/live?a=1&b=2"),
    repo("fallback", rid=102, description="# Heading\n- item | `tick` <b>x</b> \\1 $1"),
    repo("empty-desc", rid=103, description=None, licence=None),
    repo("Zulu-Upper", rid=104, description="An upper-case name sorts by its lower-case form. ~~strike~~ #1 ---"),
    repo("secret-project", rid=105, private=True, description="TOP SECRET DESCRIPTION 7f3a"),
    repo("internal-tool", rid=106, private=False, visibility="internal", description="INTERNAL ONLY DESCRIPTION 9c2e"),
    repo("forked-thing", rid=107, fork=True, description="FORK DESCRIPTION 5d1b"),
    repo("old-archive", rid=108, archived=True, description="ARCHIVED DESCRIPTION 3e8a"),
    repo(LOGIN, rid=109, description="SELF DESCRIPTION 1a2b"),
    repo("never-pushed", rid=110, pushed=None, description="Never pushed, so it sorts last."),
]

RELEASES = [
    {"tag_name": "v9.9.9", "published_at": "2026-09-01T00:00:00Z", "draft": True, "prerelease": False},
    {"tag_name": "v2.0.0-rc.1", "published_at": "2026-08-01T00:00:00Z", "draft": False, "prerelease": True},
    {"tag_name": "v1.0_beta|<1>&2`3$1", "published_at": "2026-07-01T00:00:00Z", "draft": False, "prerelease": False},
    {"tag_name": "v1.0_alpha", "published_at": "2026-07-01T00:00:00Z", "draft": False, "prerelease": False},  # same date; the greater tag wins
    {"tag_name": "v0.9.0", "published_at": "2026-06-01T00:00:00Z", "draft": False, "prerelease": False},
]

README_IN = (
    "# Hostile fixture\n\n"
    "Text outside the markers must survive byte for byte: $1 $& \\1 `tick <b>bold</b> <!-- not a marker --> "
    "and a decoy that is not a marker: <!-- work:start-->.\n\n"
    "## Work\n\n"
    f"{START}\nplaceholder that the first run replaces\n{END}\n\n"
    "Tail outside the markers. Also untouched. مرحبا\n"
)

PROFILE = (
    "<!-- A PROFILE.md that leans on every placeholder. {live} {version} {license} -->\n"
    "# Curated $1 -- {version} on {license} $1 \\1 ([live]({live}))\n\n"
    "{license} · {version} · {name} · {url}\n\n"
    "{description}\n\n"
    "Second paragraph with `code`, **bold**, `{version}` in a code span (renders with the escapes, as documented), "
    "{{version}} as a literal, and a link to [a sibling](https://github.com/hostile-owner/fallback).\n"
)

CONFIG = {
    "login": LOGIN, "exclude": [], "order": ["curated"], "profile_path": ".github/PROFILE.md",
    "max_blurb_chars": 1400, "snippets_dir": "snippets", "markers": [START, END],
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    public = sum(1 for r in REPOS if not r["private"] and r["visibility"] == "public")
    write(ROOT / "repos.json", json.dumps(REPOS, indent=2, ensure_ascii=False) + "\n")
    write(ROOT / "user.json", json.dumps({"login": LOGIN, "id": 1, "public_repos": public}, indent=2) + "\n")
    write(ROOT / "config.json", json.dumps(CONFIG, indent=2) + "\n")
    write(ROOT / "README.in.md", README_IN)
    write(ROOT / "profiles" / "curated.md", PROFILE)
    write(ROOT / "releases" / "curated.json", json.dumps(RELEASES, indent=2) + "\n")
    (ROOT / "repos").mkdir(exist_ok=True)
    (ROOT / "repos" / ".keep").write_text("", encoding="utf-8")
    print(f"wrote the hostile fixture: {len(REPOS)} repositories, {public} public")


if __name__ == "__main__":
    main()
