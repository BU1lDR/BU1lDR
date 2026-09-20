#!/usr/bin/env python3
"""Rebuild the Work section of README.md from the repositories it describes.

    python3 tools/build_readme.py                          # rewrite README.md in place
    python3 tools/build_readme.py --check                  # exit 1 if README.md is out of date
    python3 tools/build_readme.py --summary-file PATH      # also write a commit-message-shaped summary
    python3 tools/build_readme.py --fixtures DIR           # offline: read the API's answers from DIR
    python3 tools/build_readme.py --root DIR               # README.md and profile.config.json live here

WHY THIS EXISTS

This README is a claims document with no CI of its own, describing repositories whose
CI holds their own READMEs to the truth. Every sentence in the Work section is a copy
of a fact one of those repositories owns -- a version string, a licence, a live URL,
the prose about what the code does -- and a copy no commit in those repositories can
reach. On 2026-09-20 two verification passes found six published claims here that had
gone false exactly that way.

So the Work section is generated. Each repository owns its own paragraph in
.github/PROFILE.md; this script lists the account's public repositories, reads that
file from each, fills in the facts the API owns, and rewrites the block between the
two markers in README.md. A repository that appears, appears here; one that is
deleted, disappears; one whose PROFILE.md changes, changes here on the next run.
Nothing outside the markers is touched.

WHAT EACH REPOSITORY CONTROLS

    .github/PROFILE.md      first line "# <display name> — <heading tail>" (an em dash,
                            U+2014, with a space each side; " -- " is accepted and
                            rendered as the em dash), then the body, verbatim. Leading
                            HTML comments are ignored, so the file can explain itself.
                            A UTF-8 BOM and CRLF line endings are normalised away. A
                            file with a body but no "# " title is an error; a file that
                            is empty once comments are stripped counts as absent.
                            Placeholders, in title or body, replaced in one pass:
                              {version}      newest published release tag, or "unreleased"
                              {license}      SPDX id GitHub detected, or "no licence detected"
                              {live}         the repository's homepage URL; using it when the
                                             repository has no valid http(s) homepage is an error
                              {description}  the repository description
                              {name} {url}   the repository's name and URL
                              {{version}}    a literal "{version}" (same for the others);
                                             braces beyond that pair are left as written
    snippets/<name>.md      same format, kept in THIS repository; used only when the
                            repository has no .github/PROFILE.md
    (neither)               "### [<name>](<url>)" with the GitHub description as body,
                            so a new project appears the hour it is created -- with a
                            ::warning:: annotation on the run and in its summary. For a
                            repository named in the config's "order" list this is an
                            error instead: a curated section that has lost its source
                            must not quietly shrink to one line.

Each generated section opens with an invisible "<!-- repo: <name> -->" line, which is
how the block is split back into sections on the next run without guessing from
headings. A body may not contain that text; a body may link to a sibling repository.

BREVITY

The profile is a summary, not a second README. docs/PROFILE.template.md is the
shape: a tagline, a one-line meta row, at most two short paragraphs. Detail belongs
in the repository's own README, where its CI can hold it to the code. The config's
"max_blurb_chars" is the soft ceiling: a longer blurb still renders, with a warning
on the run and in its summary, because length is a judgement and the profile must not
stop updating over one.

ORDER AND SCOPE

profile.config.json names the account, the repositories to leave out, and the order of
the first few sections. Repositories not in that list follow, most recently pushed
first. Forks, archived repositories, excluded ones and the profile repository itself
are skipped. Only public repositories are listed: /users/{login}/repos returns nothing
else, and this page is public.

WHAT IS A FAILURE

"Could not look" and "nothing changed" must not produce the same result. A 404 for
PROFILE.md or for a release is an answer -- no file, no release -- and is handled.
Any other error (rate limit, network, a 5xx, a non-UTF-8 file, a redirect off
api.github.com, a file over 64 KiB) exits 2 and leaves README.md untouched. So does
anything that would write a README this tool could not read back: a section
containing one of the markers or a second section mark, a display name containing a
bracket or a backslash, a body that opens with its own heading, a README that would
exceed 400 KiB (GitHub truncates at 500). A listing that returns no repositories
exits 2. A repository that would be *removed* is first looked up directly: if it still
exists under the same name as a public, unarchived, unexcluded repository, the listing
missed it and the run fails rather than drop the section; a rename, a deletion, an
archive or an exclusion is an answer and the removal proceeds. Markers missing from
README.md exit 2. The workflow commits only when this exits 0 and the file changed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_HOST = "api.github.com"
API = f"https://{API_HOST}"
NOTICE = (
    "<!-- Generated by tools/build_readme.py from each repository's .github/PROFILE.md. "
    "Edit those, not this block. -->"
)
EXIT_OK, EXIT_STALE, EXIT_ERROR = 0, 1, 2

MAX_BLURB_BYTES = 64 * 1024
MAX_README_BYTES = 400 * 1024

TITLE_SEP = " — "
TITLE_SEP_ASCII = " -- "
PLACEHOLDER_NAMES = "version|license|live|description|name|url"
PLACEHOLDER = re.compile(r"\{\{(" + PLACEHOLDER_NAMES + r")\}\}|\{(" + PLACEHOLDER_NAMES + r")\}")
HTTP_URL = re.compile(r"https?://[^\s()<>]+")
ATX_HEADING = re.compile(r"#{1,6}(?:\s|$)")
# Each generated section opens with an invisible line naming its repository, so the
# block can be split back into sections without guessing from headings -- a body is
# free to contain "### [see also](https://github.com/<login>/<other>)".
SECTION_MARK = re.compile(r"^<!-- repo: (?P<name>\S+) -->$")
SECTION_MARK_PREFIX = "<!-- repo: "


class BuildError(Exception):
    """Anything that means the README must not be rewritten."""


# --------------------------------------------------------------------------- sources


class _StayOnApiHost(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header on every redirect. GitHub only ever
    redirects these endpoints within api.github.com (a renamed repository, for one), so
    anywhere else is refused rather than handed the token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname != API_HOST:
            raise BuildError(f"refusing to follow a redirect off {API_HOST}: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(_StayOnApiHost)


def _open(request: urllib.request.Request):
    return OPENER.open(request, timeout=30)


class GitHub:
    """The API calls this needs. 404 is an answer; anything else is a failure."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    def _get(self, url: str, accept: str) -> tuple[int, bytes, dict]:
        headers = {
            "Accept": accept,
            "User-Agent": "profile-readme-builder",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        last: Exception | None = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 * attempt)
            try:
                with _open(request) as response:
                    body = response.read()
                    return response.status, body, {k.lower(): v for k, v in response.headers.items()}
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    return 404, b"", {}
                detail = error.read()[:300].decode("utf-8", "replace")
                last = BuildError(f"HTTP {error.code} for {url}: {detail}")
                if error.code in (403, 429):
                    # The rate limit. GitHub says wait retry-after seconds; honour a short
                    # one, otherwise fail now -- this run has nothing better to do.
                    wait = (error.headers.get("retry-after") or "").strip()
                    if wait.isdigit() and int(wait) <= 60:
                        time.sleep(int(wait))
                        continue
                    raise last
                if error.code < 500:
                    raise last  # 401, 422 and friends: the same request gets the same answer
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
                last = BuildError(f"{error!r} for {url}")
        assert last is not None
        raise last

    def _json(self, url: str):
        status, body, headers = self._get(url, "application/vnd.github+json")
        if status == 404:
            return None, headers
        try:
            return json.loads(body.decode("utf-8")), headers
        except ValueError as error:
            raise BuildError(f"non-JSON body from {url}: {error}") from error

    def list_repos(self, login: str) -> list[dict]:
        url = f"{API}/users/{urllib.parse.quote(login)}/repos?" + urllib.parse.urlencode(
            {"per_page": 100, "type": "owner", "sort": "full_name"}
        )
        repos: list[dict] = []
        while url:
            page, headers = self._json(url)
            if page is None:
                raise BuildError(f"account {login!r} not found")
            if not isinstance(page, list):
                raise BuildError(f"expected a list from {url}, got {type(page).__name__}")
            repos.extend(page)
            url = _next_link(headers.get("link", ""))
        seen: set = set()
        unique = []
        for repo in repos:  # a rename between pages must not yield two sections
            if repo.get("id") not in seen:
                seen.add(repo.get("id"))
                unique.append(repo)
        return unique

    def get_repo(self, full_name: str) -> dict | None:
        data, _ = self._json(f"{API}/repos/{full_name}")
        return data

    def latest_release(self, full_name: str) -> dict | None:
        """The newest *published* release that is neither a draft nor a prerelease,
        among the last hundred. /releases/latest orders by the release commit's date,
        which is not the same thing once a hotfix is tagged from an older commit."""
        data, _ = self._json(f"{API}/repos/{full_name}/releases?per_page=100")
        return pick_release(data)

    def raw_file(self, full_name: str, path: str) -> bytes | None:
        status, body, headers = self._get(
            f"{API}/repos/{full_name}/contents/{urllib.parse.quote(path)}",
            "application/vnd.github.raw+json",
        )
        if status == 404:
            return None
        content_type = headers.get("content-type", "")
        if not content_type.startswith("application/vnd.github.raw"):
            # A directory or a dangling symlink at that path answers 200 with JSON.
            raise BuildError(f"{full_name}/{path} is not a regular file (content-type {content_type!r})")
        return body


class Fixtures:
    """The same answers, read from a directory. For tests and offline runs.

        DIR/repos.json             the list the /users/<login>/repos endpoint returns
        DIR/repos/<name>.json      GET /repos/<login>/<name>; absent means 404
        DIR/releases/<name>.json   a release object or a list of them; absent means none
        DIR/profiles/<name>.md     .github/PROFILE.md for <name>, as raw bytes; absent means 404
        DIR/fail                   if present, every call raises -- simulates an outage
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _maybe_fail(self) -> None:
        if (self.directory / "fail").exists():
            raise BuildError("fixture outage: HTTP 503 for everything")

    def list_repos(self, login: str) -> list[dict]:
        self._maybe_fail()
        return json.loads((self.directory / "repos.json").read_text(encoding="utf-8"))

    def get_repo(self, full_name: str) -> dict | None:
        self._maybe_fail()
        path = self.directory / "repos" / (full_name.split("/")[1] + ".json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def latest_release(self, full_name: str) -> dict | None:
        self._maybe_fail()
        path = self.directory / "releases" / (full_name.split("/")[1] + ".json")
        return pick_release(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None

    def raw_file(self, full_name: str, path: str) -> bytes | None:
        self._maybe_fail()
        candidate = self.directory / "profiles" / (full_name.split("/")[1] + ".md")
        return candidate.read_bytes() if candidate.exists() else None


def pick_release(data) -> dict | None:
    if data is None:
        return None
    releases = data if isinstance(data, list) else [data]
    published = [r for r in releases if r.get("tag_name") and not r.get("draft") and not r.get("prerelease")]
    if not published:
        return None
    return max(published, key=lambda r: r.get("published_at") or "")


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        segment = part.strip().split(";")
        if len(segment) >= 2 and 'rel="next"' in segment[1]:
            return segment[0].strip().strip("<>")
    return None


# -------------------------------------------------------------------------- selection


def excluded_names(config: dict) -> set:
    return {name.lower() for name in config.get("exclude", [])} | {config["login"].lower()}


def select(repos: list[dict], config: dict) -> list[dict]:
    """Public, owned, not a fork, not archived, not excluded; curated order first,
    then everything else most recently pushed first (never pushed: last). Also the
    one predicate for 'would this repository have a section', used when deciding
    whether a removal is real."""
    excluded = excluded_names(config)
    kept = [
        repo
        for repo in repos
        if not repo.get("fork")
        and not repo.get("archived")
        and not repo.get("private")
        and repo.get("visibility", "public") == "public"
        and repo["name"].lower() not in excluded
    ]
    order = [name.lower() for name in config.get("order", [])]
    rank = {name: index for index, name in enumerate(order)}

    def key(repo: dict):
        name = repo["name"].lower()
        if name in rank:
            return (0, rank[name], "")
        # ISO-8601 stamps sort lexically; invert so newest comes first inside an
        # ascending sort. "0" sorts before every digit-led stamp, so it lands last.
        return (1, 0, _invert(repo.get("pushed_at") or "0"))

    return sorted(kept, key=key)


def _invert(stamp: str) -> str:
    return "".join(chr(0x10FFFF - ord(char)) for char in stamp)


# -------------------------------------------------------------------------- rendering

LEADING_COMMENTS = re.compile(r"^(?:\s*<!--.*?-->\s*)+", re.DOTALL)


def normalise_blurb(raw: bytes, where: str) -> str:
    """Bytes from any source -> text with LF endings and no BOM, or an error naming
    the file. Done once here so the API path and the fixture path agree."""
    if len(raw) > MAX_BLURB_BYTES:
        raise BuildError(f"{where} is {len(raw):,} bytes; the limit is {MAX_BLURB_BYTES:,} "
                         "(GitHub truncates a README at 500 KiB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BuildError(f"{where} is not UTF-8: {error}") from error
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_blurb(text: str) -> tuple[str | None, str]:
    """(title line without the '# ', body). Leading HTML comments are skipped so a
    PROFILE.md can carry a note about where it is rendered."""
    stripped = LEADING_COMMENTS.sub("", text, count=1).lstrip("\n")
    lines = stripped.split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:]).strip("\n")
    return None, stripped.strip("\n")


def has_content(text: str) -> bool:
    title, body = split_blurb(text)
    return bool(title or body)


def placeholders(repo: dict, release: dict | None) -> dict[str, str]:
    licence = (repo.get("license") or {}).get("spdx_id")
    if licence in (None, "NOASSERTION"):
        licence = "no licence detected"
    homepage = (repo.get("homepage") or "").strip()
    return {
        "version": (release or {}).get("tag_name") or "unreleased",
        "license": licence,
        "live": homepage if HTTP_URL.fullmatch(homepage) else "",
        "description": (repo.get("description") or "").strip(),
        "name": repo["name"],
        "url": repo["html_url"],
    }


def substitute(text: str, values: dict[str, str]) -> str:
    """One pass, so a value containing a placeholder is not substituted again, and
    {{name}} is the way to write a literal {name}."""
    def replace(match: re.Match) -> str:
        if match.group(1):
            return "{" + match.group(1) + "}"
        return values[match.group(2)]
    return PLACEHOLDER.sub(replace, text)


def uses_placeholder(text: str, name: str) -> bool:
    return any(m.group(2) == name for m in PLACEHOLDER.finditer(text))


def section_mark(name: str) -> str:
    return f"{SECTION_MARK_PREFIX}{name} -->"


def render_section(repo: dict, release: dict | None, blurb: str | None, where: str = "") -> str:
    values = placeholders(repo, release)
    name, url = repo["name"], repo["html_url"]
    mark = section_mark(name) + "\n"
    if blurb is None:
        body = values["description"] or "No description yet."
        if ATX_HEADING.match(body):
            body = "\\" + body  # a description is not allowed to open a heading inside the section
        return mark + f"### [{name}]({url})" + f"\n\n{body}\n"

    title, body = split_blurb(blurb)
    if title is None:
        raise BuildError(f"{where}: the first line must be '# <display name> — <heading tail>'; "
                         f"got {body.split(chr(10), 1)[0][:60]!r}")
    if uses_placeholder(title + "\n" + body, "live") and not values["live"]:
        raise BuildError(
            f"{where} uses {{live}} but the repository has no valid http(s) homepage "
            f"({(repo.get('homepage') or '')!r}); set one or drop the placeholder"
        )

    title = substitute(title.replace(TITLE_SEP_ASCII, TITLE_SEP, 1), values)
    display, _, tail = title.partition(TITLE_SEP)
    display, tail = display.strip() or name, tail.strip()
    if any(char in display for char in "[]\\"):
        raise BuildError(f"{where}: the display name {display!r} contains a bracket or backslash, which breaks the link")
    body = substitute(body, values).strip("\n")
    first_body_line = body.split("\n", 1)[0] if body else ""
    if ATX_HEADING.match(first_body_line):
        raise BuildError(f"{where}: the body opens with a heading ({first_body_line[:40]!r}); "
                         "only the first line of the file is a heading")
    head = f"### [{display}]({url})" + (f"{TITLE_SEP}{tail}" if tail else "")
    return mark + head + (f"\n\n{body}\n" if body else "\n")


def validate_section(section: str, name: str, where: str, start: str, end: str) -> None:
    """What a rendered section may not contain if the next run is to read it back."""
    for marker in (start, end):
        if marker in section:
            raise BuildError(f"{where} contains the README marker {marker!r}; refusing to write it")
    if section.count(SECTION_MARK_PREFIX) != 1 or not section.startswith(section_mark(name) + "\n"):
        raise BuildError(f"{where} contains {SECTION_MARK_PREFIX!r}, which this tool uses to delimit sections")


def render_inner(sections: list[str]) -> str:
    """The sections, one blank line apart, ending in exactly one newline."""
    return "\n".join(section.rstrip("\n") + "\n" for section in sections)


def render_block(sections: list[str], start: str, end: str) -> str:
    return f"{start}\n{NOTICE}\n{render_inner(sections)}{end}"


def inner_block(readme: str, start: str, end: str) -> str:
    """What currently sits between the markers, without the markers or the notice,
    normalised the way render_inner writes it -- so old and new compare like for like."""
    first, last = readme.find(start), readme.find(end)
    if first < 0 or last < 0 or last < first:
        raise BuildError(f"README.md must contain {start!r} before {end!r}")
    lines = [line for line in readme[first + len(start):last].split("\n") if line != NOTICE]
    inner = "\n".join(lines).strip("\n")
    return inner + "\n" if inner else ""


def splice(readme: str, block: str, start: str, end: str) -> str:
    """Replace whatever sits between the markers, markers included, with block."""
    first, last = readme.find(start), readme.find(end)
    if first < 0 or last < 0:
        raise BuildError(f"README.md must contain both markers {start!r} and {end!r}")
    if last < first:
        raise BuildError("end marker appears before start marker")
    if readme.find(start, first + 1) >= 0 or readme.find(end, last + 1) >= 0:
        raise BuildError("markers must appear exactly once each")
    if block.count(start) != 1 or block.count(end) != 1:
        raise BuildError("the rendered block must contain each marker exactly once")
    return readme[:first] + block + readme[last + len(end):]


# ---------------------------------------------------------------------------- change


def heading_pattern(login: str) -> re.Pattern:
    return re.compile(r"^### \[[^\]]*\]\(https://github\.com/" + re.escape(login) + r"/(?P<name>[^/)]+)\)", re.IGNORECASE)


def sections_by_repo(text: str, login: str) -> dict[str, str]:
    """Split a Work block into {repo name: section text}. Sections written by this
    tool open with a `<!-- repo: name -->` line and are split on those, which no body
    may contain. A block without any marks -- written before the marks existed -- is
    split on headings that link to this account instead, so the first run after the
    change still describes itself per repository."""
    lines = text.split("\n")
    result: dict[str, str] = {}
    current: str | None = None
    if any(SECTION_MARK.match(line) for line in lines):
        for line in lines:
            match = SECTION_MARK.match(line)
            if match:
                current = match.group("name")
                result.setdefault(current, "")
                continue
            if current is not None:
                result[current] += line + "\n"
    else:
        pattern = heading_pattern(login)
        for line in lines:
            match = pattern.match(line)
            if match and match.group("name") not in result:
                current = match.group("name")
                result[current] = ""
            if current is not None:
                result[current] += line + "\n"
    return {name: body.rstrip("\n") + "\n" for name, body in result.items()}


def describe_change(old_block: str, new_block: str, login: str) -> list[str]:
    before, after = sections_by_repo(old_block, login), sections_by_repo(new_block, login)
    lines = []
    for name in after:
        if name not in before:
            lines.append(f"{name}: added")
        elif before[name] != after[name]:
            lines.append(f"{name}: changed")
    for name in before:
        if name not in after:
            lines.append(f"{name}: removed")
    common_before = [name for name in before if name in after]
    common_after = [name for name in after if name in before]
    if common_before != common_after:
        lines.append("order: changed")
    if old_block != new_block and not lines:
        lines.append("block: changed outside any repository section")
    return lines


# ------------------------------------------------------------------------------ main


def build(root: Path, source, check: bool, summary_file: Path | None, out=sys.stdout) -> int:
    config = json.loads((root / "profile.config.json").read_text(encoding="utf-8"))
    start, end = config.get("markers", ["<!-- work:start -->", "<!-- work:end -->"])
    readme_path = root / "README.md"
    # Bytes, not text: text mode would quietly translate CRLF to LF on the way in and
    # this check would never fire.
    raw = readme_path.read_bytes()
    if b"\r" in raw:
        raise BuildError("README.md has CRLF line endings; this tool writes LF")
    readme = raw.decode("utf-8")

    login = config["login"]
    curated = {name.lower() for name in config.get("order", [])}
    excluded = excluded_names(config)
    warnings: list[str] = []

    def warn(message: str) -> None:
        warnings.append(message)
        print(f"::warning::{message}", file=out, flush=True)

    repos = select(source.list_repos(login), config)
    if not repos:
        raise BuildError(f"no public repositories selected for {login!r}; refusing to write an empty Work section")
    listed = {repo["name"].lower() for repo in repos}
    for name in config.get("order", []):
        if name.lower() not in listed:
            warn(f"profile.config.json orders {name!r}, which is not among the listed repositories "
                 "(renamed, deleted, or made private?)")

    profile_path = config.get("profile_path", ".github/PROFILE.md")
    snippets = root / config.get("snippets_dir", "snippets")
    sections, provenance = [], []
    for repo in repos:
        full_name, name = repo["full_name"], repo["name"]
        release = source.latest_release(full_name)
        blurb, origin = None, "description"
        raw_blurb = source.raw_file(full_name, profile_path)
        if raw_blurb is not None:
            text = normalise_blurb(raw_blurb, f"{full_name}/{profile_path}")
            if has_content(text):
                blurb, origin = text, profile_path
            else:
                warn(f"{name}: {profile_path} is empty once comments are stripped; treating it as absent")
        if blurb is None:
            snippet = snippets / f"{name}.md"
            if snippet.exists():
                text = normalise_blurb(snippet.read_bytes(), str(snippet))
                if has_content(text):
                    blurb, origin = text, f"{snippets.name}/{snippet.name}"
                else:
                    warn(f"{name}: {snippets.name}/{snippet.name} is empty; treating it as absent")
        if blurb is None:
            if name.lower() in curated:
                raise BuildError(f"{full_name} is a curated repository but has no usable {profile_path} and no "
                                 f"{snippets.name}/{name}.md; restore one rather than let its section collapse")
            warn(f"{name}: rendered from the GitHub description ({profile_path} not found or empty)")
        where = f"{full_name}/{origin}"
        limit = int(config.get("max_blurb_chars", 0) or 0)
        if blurb and limit and len(blurb) > limit:
            warn(f"{name}: {origin} is {len(blurb):,} characters against a limit of {limit:,}; "
                 "the profile is meant to stay brief -- move the detail into that repository's README")
        if blurb and uses_placeholder(blurb, "live"):
            host = urllib.parse.urlsplit((repo.get("homepage") or "").strip()).hostname or ""
            if host in ("github.com", "www.github.com"):
                warn(f"{name}: {{live}} points at github.com ({repo.get('homepage')}); is that the live site?")
        section = render_section(repo, release, blurb, where=where)
        validate_section(section, name, where, start, end)
        sections.append(section)
        provenance.append(f"{name}: {origin}" + (f", release {release['tag_name']}" if release else ""))

    inner = render_inner(sections)
    block = render_block(sections, start, end)
    updated = splice(readme, block, start, end)
    if len(updated.encode("utf-8")) > MAX_README_BYTES:
        raise BuildError(f"the rebuilt README would be {len(updated.encode('utf-8')):,} bytes; "
                         f"the limit is {MAX_README_BYTES:,} (GitHub truncates at 500 KiB)")
    changes = describe_change(inner_block(readme, start, end), inner, login)

    for line in changes:
        if not line.endswith(": removed"):
            continue
        gone = line[: -len(": removed")]
        if gone.lower() in excluded:
            continue  # the config removed it; that is the whole point of "exclude"
        still = source.get_repo(f"{login}/{gone}")
        if still is None:
            continue  # deleted, or private to this token: an answer
        if (still.get("name") or "").lower() != gone.lower():
            warn(f"{gone} appears to have been renamed to {still.get('name')!r}; dropping the old section"
                 + (" -- update profile.config.json" if gone.lower() in curated else ""))
            continue
        if select([still], config):
            raise BuildError(f"{gone} is still a public repository but was absent from the listing; "
                             "refusing to drop its section")
    if updated != readme and not changes:
        changes = ["block: notice line or spacing restored"]

    if updated == readme:
        print("README.md is current; nothing to write.", file=out)
        _write_summary(summary_file, [], provenance, warnings)
        return EXIT_OK
    if check:
        print("README.md is out of date:", file=out)
        for line in changes:
            print(f"  {line}", file=out)
        return EXIT_STALE
    # Summary first: if writing it fails, main()'s "left untouched" is still true.
    _write_summary(summary_file, changes, provenance, warnings)
    readme_path.write_text(updated, encoding="utf-8", newline="\n")
    print("README.md rewritten:", file=out)
    for line in changes:
        print(f"  {line}", file=out)
    return EXIT_OK


def _write_summary(path: Path | None, changes: list[str], provenance: list[str], warnings: list[str]) -> None:
    """A commit message: subject, the per-repository change list, where each section
    came from, and any warnings. Written even when nothing changed, so a caller can
    read it. The same text goes to the Actions step summary, where a human looks."""
    lines = ["Rebuild README from repository state", ""]
    lines += changes or ["No section changed."]
    lines += ["", "Sources:"] + [f"  {p}" for p in provenance]
    if warnings:
        lines += ["", "Warnings:"] + [f"  {w}" for w in warnings]
    text = "\n".join(lines) + "\n"
    if path is not None:
        path.write_text(text, encoding="utf-8", newline="\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("### README build\n\n```\n" + text + "```\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 instead of writing if README.md would change")
    parser.add_argument("--fixtures", type=Path, help="read API answers from this directory instead of GitHub")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="directory holding README.md and profile.config.json")
    parser.add_argument("--summary-file", type=Path, help="write a commit-message-shaped summary here")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a cp1252 console must not crash the report

    source = Fixtures(args.fixtures) if args.fixtures else GitHub(os.environ.get("GITHUB_TOKEN") or None)
    try:
        return build(args.root, source, args.check, args.summary_file)
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        print("README.md left untouched.", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:  # noqa: BLE001 -- the exit-code contract must hold for the unforeseen too
        print(f"error: unexpected {error!r}", file=sys.stderr)
        print("README.md left untouched.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
