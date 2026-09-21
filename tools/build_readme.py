#!/usr/bin/env python3
"""Rebuild the Work section of README.md from the repositories it describes.

    GITHUB_TOKEN=... python3 tools/build_readme.py            # rewrite README.md in place
    python3 tools/build_readme.py --check                      # exit 1 if README.md is out of date
    python3 tools/build_readme.py --fixtures DIR               # offline: read the API's answers from DIR
    python3 tools/build_readme.py --summary-file PATH          # also write a commit-message-shaped summary
    python3 tools/build_readme.py --meta-file PATH             # machine-readable record of the run
    python3 tools/build_readme.py --root DIR                   # README.md and profile.config.json live here
    python3 tools/build_readme.py --allow-shrink               # accept a Work section >20% smaller with nothing removed
    python3 tools/build_readme.py --anonymous                  # no token: 60 requests an hour, for a look
    python3 tools/build_readme.py --verbose                    # log every request as "GET <url> -> <status>"

    Environment:
      GITHUB_TOKEN               required unless --fixtures or --anonymous. An empty value is
                                 treated as missing: a misnamed secret expands to "", and an
                                 unauthenticated run would silently drop to 60 requests an hour.
      PROFILE_FIXTURES           the same as --fixtures
      NOW                        a Unix timestamp or an ISO-8601 instant with a zone; stamps
                                 the run record instead of the clock, so a re-run is byte-stable
      GITHUB_REPOSITORY_OWNER    when set (Actions sets it), must match the config's login,
                                 or the run fails: a renamed account is not something to
                                 paper over by listing whoever the config still names
      GITHUB_STEP_SUMMARY        the summary is appended there too

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
Nothing outside the markers is touched, and the run asserts as much before writing.

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
                            {version}, {license} and {description} are API text and are
                            rendered as *plain text*: HTML in them is shown, not
                            interpreted, Markdown punctuation is escaped, newlines
                            become spaces, and they cannot open a heading, a list, a
                            link, a comment or a code span. Do not wrap them in
                            backticks. {name} is ASCII by GitHub's rules and {url} and
                            {live} are validated URLs; they are inserted as they are.
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
headings. A PROFILE.md body may not contain that text; it may link to a sibling
repository. The block also opens with a "<!-- build: sections=N digest=... -->" line:
the section count and a SHA-256 prefix of the sections, so the weekly audit can tell a
hand edit inside the markers from the builder's own output.

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
first, then by name, then by id, so the same inventory in any order renders the same
bytes. Forks, archived repositories, excluded ones, the profile repository itself and
anything not public (private, or "internal" visibility) are skipped, whatever the token
can see -- this page is public. /users/{login}/repos returns only public repositories
anyway, and the count it returns is checked against the account's own public_repos
figure: a dropped page fails the run rather than dropping a section.

WHAT IS A FAILURE

"Could not look" and "nothing changed" must not produce the same result. A 404 for
PROFILE.md or for a release is an answer -- no file, no release -- and is handled.
Anything else exits 2 and leaves README.md byte-identical: a 401 (the token), a 403
or 429 the run cannot wait out (the rate limit resets more than two minutes away),
a 5xx or a network error after three attempts, a redirect off api.github.com, a
response missing a field this tool reads or holding the wrong type in it (schema
drift renders as an error, never as "None"), a non-UTF-8 file, a file over 64 KiB, a
listing that returns no repositories, a listing shorter than the account's public
repository count. So does anything that would write a README this tool could not
read back: a section containing one of the markers or a second section mark, a
display name containing a bracket or a backslash, a body that opens with its own
heading, a README that would exceed 400 KiB (GitHub truncates at 500), markers
missing, duplicated or reversed. A Work section more than 20% smaller than the one
it replaces, with no repository removed, is refused too: a source has collapsed,
and --allow-shrink is how a person says otherwise. A repository that would be
*removed* is first looked up directly: if it still exists under the same name as a
public, unarchived, unexcluded repository, the listing missed it and the run fails
rather than drop the section; a rename, a deletion, an archive or an exclusion is an
answer and the removal proceeds. The workflow commits only when this exits 0 and the
file changed.

THE RUN RECORD

Every run that is not --check writes --meta-file (default meta/last-run.json under
--root): when it finished (UTC), how it ended, which source it used and how many
requests that took, the rate-limit headers from the last response, how many
repositories were listed and selected, how many sections were written, the block's
size before and after, what changed, and every warning. The workflow commits it with
README.md, so the weekly audit can read the last run's outcome from the repository
without trusting `git log` under a shallow checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_HOST = "api.github.com"
API = f"https://{API_HOST}"
API_VERSION = "2022-11-28"
USER_AGENT = "profile-readme-builder"
NOTICE = (
    "<!-- Generated by tools/build_readme.py from each repository's .github/PROFILE.md. "
    "Edit those, not this block. -->"
)
EXIT_OK, EXIT_STALE, EXIT_ERROR = 0, 1, 2

MAX_BLURB_BYTES = 64 * 1024
MAX_README_BYTES = 400 * 1024
REQUEST_TIMEOUT = 20      # seconds, per request
ATTEMPTS = 3              # for 5xx and network errors
MAX_RATE_WAIT = 120       # seconds this run will sleep for a rate limit before giving up
SHRINK_FLOOR = 0.8        # a new block smaller than this fraction of the old one needs a removal or --allow-shrink

TITLE_SEP = " — "
TITLE_SEP_ASCII = " -- "
PLACEHOLDER_NAMES = "version|license|live|description|name|url"
PLACEHOLDER = re.compile(r"\{\{(" + PLACEHOLDER_NAMES + r")\}\}|\{(" + PLACEHOLDER_NAMES + r")\}")
# A URL that can sit inside "[text](...)" without closing it early or escaping out.
HTTP_URL = re.compile(r"https?://[^\s()<>\\\"'`]+")
ATX_HEADING = re.compile(r"#{1,6}(?:\s|$)")
# Each generated section opens with an invisible line naming its repository, so the
# block can be split back into sections without guessing from headings -- a body is
# free to contain "### [see also](https://github.com/<login>/<other>)".
SECTION_MARK = re.compile(r"^<!-- repo: (?P<name>\S+) -->$")
SECTION_MARK_PREFIX = "<!-- repo: "
BUILD_STAMP = re.compile(r"^<!-- build: sections=(?P<sections>\d+) digest=(?P<digest>[0-9a-f]{12}) -->$")

REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+")
OWNER_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
VISIBILITIES = ("public", "private", "internal")


class BuildError(Exception):
    """Anything that means the README must not be rewritten."""


# ------------------------------------------------------------------------------ time


def utc_now() -> str:
    """The run's timestamp: UTC, second precision, explicit Z. From NOW when set, so a
    test or a re-run is byte-stable; never from the local zone, so TZ cannot move it."""
    raw = os.environ.get("NOW", "").strip()
    if not raw:
        moment = datetime.now(timezone.utc)
    elif re.fullmatch(r"\d+(?:\.\d+)?", raw):
        moment = datetime.fromtimestamp(float(raw), tz=timezone.utc)
    else:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise BuildError(f"NOW={raw!r} is neither a Unix timestamp nor an ISO-8601 instant") from error
        if moment.tzinfo is None:
            raise BuildError(f"NOW={raw!r} has no zone; write it as ...Z or ...+00:00")
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- schema


def _field(obj: dict, name: str, types, where: str, *, required: bool = True):
    """obj[name], or a BuildError naming the field: a rename upstream must read as
    an error, not render as 'None'. bool is not accepted where int is asked for."""
    if name not in obj:
        if required:
            raise BuildError(f"{where}: field {name!r} is missing from the API response (schema drift?)")
        return None
    value = obj[name]
    if not isinstance(value, types) or (isinstance(value, bool) and types is int):
        expected = "/".join(t.__name__ for t in (types if isinstance(types, tuple) else (types,)))
        raise BuildError(f"{where}: field {name!r} is {type(value).__name__}, expected {expected} (schema drift?)")
    return value


def validate_repo(repo, where: str = "repository listing") -> dict:
    """The fields this tool reads, present and typed, with the URL rebuilt from the
    name rather than trusted -- so nothing that reaches a link target is free text."""
    if not isinstance(repo, dict):
        raise BuildError(f"{where}: expected a repository object, got {type(repo).__name__}")
    _field(repo, "id", int, where)
    name = _field(repo, "name", str, where)
    if not REPO_NAME.fullmatch(name):
        raise BuildError(f"{where}: repository name {name!r} is not a GitHub repository name")
    full_name = _field(repo, "full_name", str, where)
    owner, slash, rest = full_name.partition("/")
    if not slash or rest != name or not OWNER_NAME.fullmatch(owner):
        raise BuildError(f"{where}: full_name {full_name!r} does not match name {name!r}")
    html_url = _field(repo, "html_url", str, where)
    if html_url.lower() != f"https://github.com/{full_name}".lower():
        raise BuildError(f"{where}: html_url {html_url!r} is not https://github.com/{full_name}")
    for flag in ("fork", "archived", "private"):
        _field(repo, flag, bool, f"{where} {full_name}")
    visibility = _field(repo, "visibility", str, f"{where} {full_name}", required=False)
    if visibility is not None and visibility not in VISIBILITIES:
        raise BuildError(f"{where} {full_name}: visibility {visibility!r} is not one of {VISIBILITIES}")
    for text in ("description", "homepage", "pushed_at"):
        _field(repo, text, (str, type(None)), f"{where} {full_name}")
    licence = _field(repo, "license", (dict, type(None)), f"{where} {full_name}")
    if licence is not None:
        _field(licence, "spdx_id", (str, type(None)), f"{where} {full_name} license")
    return repo


def validate_releases(data, where: str) -> list[dict]:
    if data is None:
        return []
    releases = data if isinstance(data, list) else [data]
    for release in releases:
        if not isinstance(release, dict):
            raise BuildError(f"{where}: expected a release object, got {type(release).__name__}")
        _field(release, "tag_name", str, where)
        _field(release, "draft", bool, where)
        _field(release, "prerelease", bool, where)
        _field(release, "published_at", (str, type(None)), where)
    return releases


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
RATE_LIMIT_HEADERS = ("limit", "remaining", "used", "reset", "resource")


def _open(request: urllib.request.Request):
    return OPENER.open(request, timeout=REQUEST_TIMEOUT)


class GitHub:
    """The API calls this needs. 404 is an answer; anything else is a failure."""

    def __init__(self, token: str | None, verbose: bool = False, log=None) -> None:
        self.token = token
        self.verbose = verbose
        self.log = log or (lambda message: print(message, file=sys.stderr, flush=True))
        self.requests = 0
        self.rate_limit: dict[str, str] = {}

    def describe(self) -> dict:
        return {"kind": "github", "authenticated": bool(self.token), "requests": self.requests,
                "rate_limit": dict(self.rate_limit)}

    def _note(self, url: str, status) -> None:
        if self.verbose:
            self.log(f"GET {url} -> {status}")  # the URL only; never headers or bodies

    def _remember_rate_limit(self, headers) -> None:
        found = {key: headers.get(f"x-ratelimit-{key}") for key in RATE_LIMIT_HEADERS}
        if any(found.values()):
            self.rate_limit = {k: str(v) for k, v in found.items() if v is not None}

    def _get(self, url: str, accept: str) -> tuple[int, bytes, dict]:
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(2 * attempt)
            self.requests += 1
            try:
                with _open(request) as response:
                    body = response.read()
                    lowered = {k.lower(): v for k, v in response.headers.items()}
                    self._remember_rate_limit(lowered)
                    self._note(url, response.status)
                    return response.status, body, lowered
            except urllib.error.HTTPError as error:
                self._remember_rate_limit(error.headers)
                self._note(url, error.code)
                if error.code == 404:
                    return 404, b"", {}
                detail = error.read()[:300].decode("utf-8", "replace")
                last = BuildError(f"GitHub API: HTTP {error.code} for {url}: {detail}")
                if error.code == 401:
                    raise BuildError(f"GitHub API refused the credential (HTTP 401) for {url}: {detail} "
                                     "-- GITHUB_TOKEN is missing, expired or revoked") from None
                if error.code in (403, 429):
                    # Primary or secondary rate limit, or a real 403. Wait when GitHub says
                    # how long and it is short; otherwise fail now -- the next scheduled
                    # run starts with a fresh budget, and this one has nothing else to do.
                    retry_after = (error.headers.get("retry-after") or "").strip()
                    if retry_after.isdigit() and int(retry_after) <= MAX_RATE_WAIT:
                        time.sleep(int(retry_after))
                        continue
                    remaining = (error.headers.get("x-ratelimit-remaining") or "").strip()
                    reset = (error.headers.get("x-ratelimit-reset") or "").strip()
                    if remaining == "0" and reset.isdigit():
                        delay = int(reset) - int(time.time())
                        if 0 <= delay <= MAX_RATE_WAIT:
                            time.sleep(delay + 1)
                            continue
                        raise BuildError(f"GitHub API rate limit exhausted (HTTP {error.code}) for {url}; it resets "
                                         f"at {_iso(int(reset))}, {delay}s away, more than this run waits "
                                         f"({MAX_RATE_WAIT}s)") from None
                    raise last
                if error.code < 500:
                    raise last  # 422 and friends: the same request gets the same answer
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
                self._note(url, f"error {type(error).__name__}")
                last = BuildError(f"GitHub API unreachable: {error!r} for {url}")
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
            repos.extend(validate_repo(repo, f"{url}") for repo in page)
            url = _next_link(headers.get("link", ""))
        seen: set = set()
        unique = []
        for repo in repos:  # a rename between pages must not yield two sections
            if repo["id"] not in seen:
                seen.add(repo["id"])
                unique.append(repo)
        return unique

    def public_repo_count(self, login: str) -> int | None:
        """The account's own count of its public repositories, to check the listing
        against. /users/{login}/repos has no totalCount; this is the next best thing."""
        data, _ = self._json(f"{API}/users/{urllib.parse.quote(login)}")
        if data is None:
            raise BuildError(f"account {login!r} not found")
        if not isinstance(data, dict):
            raise BuildError(f"expected an account object for {login!r}, got {type(data).__name__}")
        return _field(data, "public_repos", int, f"account {login}")

    def get_repo(self, full_name: str) -> dict | None:
        data, _ = self._json(f"{API}/repos/{full_name}")
        return None if data is None else validate_repo(data, f"GET /repos/{full_name}")

    def latest_release(self, full_name: str) -> dict | None:
        """The newest *published* release that is neither a draft nor a prerelease,
        among the last hundred. /releases/latest orders by the release commit's date,
        which is not the same thing once a hotfix is tagged from an older commit."""
        data, _ = self._json(f"{API}/repos/{full_name}/releases?per_page=100")
        return pick_release(validate_releases(data, f"{full_name} releases"))

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
        DIR/user.json              the /users/<login> object; only public_repos is read.
                                   Absent: the completeness check is skipped.
        DIR/repos/<name>.json      GET /repos/<login>/<name>; absent means 404
        DIR/releases/<name>.json   a release object or a list of them; absent means none
        DIR/profiles/<name>.md     .github/PROFILE.md for <name>, as raw bytes; absent means 404
        DIR/fail                   if present, every call raises -- simulates an outage
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.requests = 0

    def describe(self) -> dict:
        return {"kind": "fixtures", "directory": self.directory.as_posix(), "requests": self.requests}

    def _maybe_fail(self) -> None:
        self.requests += 1
        if (self.directory / "fail").exists():
            raise BuildError("fixture outage: HTTP 503 for everything")

    def list_repos(self, login: str) -> list[dict]:
        self._maybe_fail()
        data = json.loads((self.directory / "repos.json").read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise BuildError(f"expected a list in repos.json, got {type(data).__name__}")
        return [validate_repo(repo, "repos.json") for repo in data]

    def public_repo_count(self, login: str) -> int | None:
        self._maybe_fail()
        path = self.directory / "user.json"
        if not path.exists():
            return None
        return _field(json.loads(path.read_text(encoding="utf-8")), "public_repos", int, "user.json")

    def get_repo(self, full_name: str) -> dict | None:
        self._maybe_fail()
        path = self.directory / "repos" / (full_name.split("/")[1] + ".json")
        return validate_repo(json.loads(path.read_text(encoding="utf-8")), path.name) if path.exists() else None

    def latest_release(self, full_name: str) -> dict | None:
        self._maybe_fail()
        path = self.directory / "releases" / (full_name.split("/")[1] + ".json")
        if not path.exists():
            return None
        return pick_release(validate_releases(json.loads(path.read_text(encoding="utf-8")), path.name))

    def raw_file(self, full_name: str, path: str) -> bytes | None:
        self._maybe_fail()
        candidate = self.directory / "profiles" / (full_name.split("/")[1] + ".md")
        return candidate.read_bytes() if candidate.exists() else None


def pick_release(data) -> dict | None:
    """Newest by published_at; a tie goes to the greater tag name, so the answer does
    not depend on the order the API happened to list them in."""
    if data is None:
        return None
    releases = data if isinstance(data, list) else [data]
    published = [r for r in releases if r.get("tag_name") and not r.get("draft") and not r.get("prerelease")]
    if not published:
        return None
    return max(published, key=lambda r: (r.get("published_at") or "", r.get("tag_name") or ""))


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        segment = part.strip().split(";")
        if len(segment) >= 2 and 'rel="next"' in segment[1]:
            return segment[0].strip().strip("<>")
    return None


# -------------------------------------------------------------------------- selection


def load_config(root: Path) -> dict:
    config = json.loads((root / "profile.config.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("login"), str) or not config["login"]:
        raise BuildError("profile.config.json must be an object with a non-empty string 'login'")
    return config


def excluded_names(config: dict) -> set:
    return {name.lower() for name in config.get("exclude", [])} | {config["login"].lower()}


def is_public(repo: dict) -> bool:
    return not repo["private"] and repo.get("visibility", "public") == "public"


def select(repos: list[dict], config: dict) -> list[dict]:
    """Public, owned, not a fork, not archived, not excluded; curated order first,
    then everything else most recently pushed first (never pushed: last), then by
    name, then by id -- a total order, so the listing's order cannot reach the output.
    Also the one predicate for 'would this repository have a section', used when
    deciding whether a removal is real."""
    excluded = excluded_names(config)
    kept = [
        repo
        for repo in repos
        if not repo["fork"]
        and not repo["archived"]
        and is_public(repo)
        and repo["name"].lower() not in excluded
    ]
    order = [name.lower() for name in config.get("order", [])]
    rank = {name: index for index, name in enumerate(order)}

    def key(repo: dict):
        name = repo["name"].lower()
        if name in rank:
            return (0, rank[name], "", name, repo["id"])
        # ISO-8601 stamps sort lexically; invert so newest comes first inside an
        # ascending sort. "0" sorts before every digit-led stamp, so it lands last.
        return (1, 0, _invert(repo.get("pushed_at") or "0"), name, repo["id"])

    return sorted(kept, key=key)


def _invert(stamp: str) -> str:
    return "".join(chr(0x10FFFF - ord(char)) for char in stamp)


# --------------------------------------------------------------------------- escaping

_HTML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_MARKDOWN_SPECIALS = frozenset("\\`*_[]~|")
# What can open a block at the start of a line, per CommonMark: an ATX heading, a
# bullet or ordered list marker (each needs a following space or the end of the
# line), or a thematic break / setext underline made only of "-" or "=". ">" and
# "<" are handled by the HTML escape; "*", "_", "`", "~" and "[" by the specials.
_BLOCK_OPENER = re.compile(r"^(?:#{1,6}(?:\s|$)|[-+](?:\s|$)|\d{1,9}[.)](?:\s|$)|[-=]+\s*$)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_text(value: str) -> str:
    """API text (a description, a tag name, a licence id) as *plain text* inside
    Markdown. HTML is shown rather than interpreted, so "<script>" is six visible
    characters and "<!-- work:end -->" cannot close the block. Markdown punctuation
    is backslash-escaped, so "|", "*", "_", "[x](y)" and an unbalanced backtick are
    literal. Newlines become spaces and control characters go, so a value stays on
    its one line. A leading "# ", "- ", "+ ", "1. " or a bare "---" is escaped so the
    value cannot open a heading, a list or a rule. "$&", "$1" and "\\1" are just
    characters here: substitution is done by a function, never a replacement string.
    Idempotent on text without any of those characters; not meant to be applied twice."""
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    out = []
    for char in text:
        if char in _HTML_ESCAPES:
            out.append(_HTML_ESCAPES[char])
        elif char in _MARKDOWN_SPECIALS:
            out.append("\\" + char)
        else:
            out.append(char)
    text = "".join(out)
    if _BLOCK_OPENER.match(text):
        text = "\\" + text
    return text


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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    stray = _CONTROL.search(text)
    if stray:
        line = text.count("\n", 0, stray.start()) + 1
        raise BuildError(f"{where} contains the control character U+{ord(stray.group()):04X} on line {line}; "
                         "a blurb is text -- tabs and newlines only")
    return text


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
    """What each placeholder becomes. Text fields are escaped for plain-text
    rendering; the two URL fields are validated, never escaped, and the repository
    URL is rebuilt from the validated full_name rather than copied."""
    licence = (repo.get("license") or {}).get("spdx_id")
    if licence in (None, "NOASSERTION"):
        licence = "no licence detected"
    homepage = (repo.get("homepage") or "").strip()
    tag = (release or {}).get("tag_name") or ""
    return {
        "version": escape_text(tag) if tag else "unreleased",
        "license": escape_text(licence),
        "live": homepage if HTTP_URL.fullmatch(homepage) else "",
        "description": escape_text((repo.get("description") or "").strip()),
        "name": repo["name"],
        "url": repo_url(repo),
    }


def repo_url(repo: dict) -> str:
    return "https://github.com/" + urllib.parse.quote(repo["full_name"], safe="/")


def substitute(text: str, values: dict[str, str]) -> str:
    """One pass, so a value containing a placeholder is not substituted again, and
    {{name}} is the way to write a literal {name}. A function, not a replacement
    string: nothing in a value is a backreference."""
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
    name, url = repo["name"], values["url"]
    mark = section_mark(name) + "\n"
    if blurb is None:
        body = values["description"] or "No description yet."
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
    if any(BUILD_STAMP.match(line) for line in section.split("\n")):
        raise BuildError(f"{where} contains a build stamp line, which this tool writes itself")


def render_inner(sections: list[str]) -> str:
    """The sections, one blank line apart, ending in exactly one newline."""
    return "\n".join(section.rstrip("\n") + "\n" for section in sections)


def digest(inner: str) -> str:
    return hashlib.sha256(inner.encode("utf-8")).hexdigest()[:12]


def build_stamp(sections: list[str]) -> str:
    return f"<!-- build: sections={len(sections)} digest={digest(render_inner(sections))} -->"


def render_block(sections: list[str], start: str, end: str) -> str:
    return f"{start}\n{NOTICE}\n{build_stamp(sections)}\n{render_inner(sections)}{end}"


def inner_block(readme: str, start: str, end: str) -> str:
    """What currently sits between the markers, without the markers, the notice or
    the stamp, normalised the way render_inner writes it -- so old and new compare
    like for like."""
    first, last = readme.find(start), readme.find(end)
    if first < 0 or last < 0 or last < first:
        raise BuildError(f"README.md must contain {start!r} before {end!r}")
    lines = [line for line in readme[first + len(start):last].split("\n")
             if line != NOTICE and not BUILD_STAMP.match(line)]
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


def assert_markers_preserved(before: str, after: str, start: str, end: str) -> None:
    """The post-condition of a render: both markers still there, exactly once each,
    and not a byte outside them changed. Cheap, and the one check that would have
    caught a splice bug before it reached the page."""
    for marker in (start, end):
        if before.count(marker) != 1 or after.count(marker) != 1:
            raise BuildError(f"marker {marker!r} count changed by the rebuild; refusing to write")
    if before[:before.find(start)] != after[:after.find(start)]:
        raise BuildError("the rebuild would change text before the start marker; refusing to write")
    if before[before.find(end) + len(end):] != after[after.find(end) + len(end):]:
        raise BuildError("the rebuild would change text after the end marker; refusing to write")


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


def build(root: Path, source, check: bool, summary_file: Path | None, out=None, *,
          allow_shrink: bool = False, report: dict | None = None, config: dict | None = None) -> int:
    out = out if out is not None else sys.stdout  # looked up now, not at import, so a redirected stdout is honoured
    report = report if report is not None else {}
    config = config if config is not None else load_config(root)
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
    report["warnings"] = warnings

    def warn(message: str) -> None:
        warnings.append(message)
        print(f"::warning::{message}", file=out, flush=True)

    listed = source.list_repos(login)
    public_listed = sum(1 for repo in listed if is_public(repo))
    report["repos_public"] = public_listed  # never the non-public count: this record is committed
    expected = source.public_repo_count(login)
    report["public_repos"] = expected
    if expected is not None and public_listed != expected:
        raise BuildError(f"the listing returned {public_listed} public repositories but the account reports "
                         f"{expected}; a page was dropped or the account changed mid-run -- refusing to "
                         "rebuild from a partial inventory")
    repos = select(listed, config)
    report["repos_selected"] = len(repos)
    if not repos:
        raise BuildError(f"no public repositories selected for {login!r}; refusing to write an empty Work section")
    names = {repo["name"].lower() for repo in repos}
    for name in config.get("order", []):
        if name.lower() not in names:
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
    report["sections"] = len(sections)
    report["provenance"] = provenance

    inner = render_inner(sections)
    block = render_block(sections, start, end)
    old_inner = inner_block(readme, start, end)
    updated = splice(readme, block, start, end)
    assert_markers_preserved(readme, updated, start, end)
    report["block_bytes_before"] = len(old_inner.encode("utf-8"))
    report["block_bytes"] = len(inner.encode("utf-8"))
    report["readme_bytes"] = len(updated.encode("utf-8"))
    if report["readme_bytes"] > MAX_README_BYTES:
        raise BuildError(f"the rebuilt README would be {report['readme_bytes']:,} bytes; "
                         f"the limit is {MAX_README_BYTES:,} (GitHub truncates at 500 KiB)")
    changes = describe_change(old_inner, inner, login)

    for line in changes:
        if not line.endswith(": removed"):
            continue
        gone = line[: -len(": removed")]
        if gone.lower() in excluded:
            continue  # the config removed it; that is the whole point of "exclude"
        still = source.get_repo(f"{login}/{gone}")
        if still is None:
            continue  # deleted, or private to this token: an answer
        if still["name"].lower() != gone.lower():
            warn(f"{gone} appears to have been renamed to {still['name']!r}; dropping the old section"
                 + (" -- update profile.config.json" if gone.lower() in curated else ""))
            continue
        if select([still], config):
            raise BuildError(f"{gone} is still a public repository but was absent from the listing; "
                             "refusing to drop its section")

    # The shrink guard compares against a block this tool wrote (one with sections
    # in it), never against a placeholder left in the file for the first run.
    before_bytes, after_bytes = report["block_bytes_before"], report["block_bytes"]
    removed_any = any(line.endswith(": removed") for line in changes)
    generated_before = bool(sections_by_repo(old_inner, login))
    if generated_before and after_bytes < SHRINK_FLOOR * before_bytes and not removed_any:
        message = (f"the Work section would shrink from {before_bytes:,} to {after_bytes:,} bytes "
                   f"({100 - 100 * after_bytes // before_bytes}% smaller) with no repository removed")
        if not allow_shrink:
            raise BuildError(message + "; a source has collapsed. If this is intended, run with "
                             "--allow-shrink (the workflow_dispatch input allow_shrink)")
        warn(message + "; allowed by --allow-shrink")

    if updated != readme and not changes:
        changes = ["block: notice, stamp or spacing restored"]
    report["changes"] = changes
    report["changed"] = updated != readme

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


def write_meta(path: Path, report: dict) -> None:
    """The run record, sorted keys, LF, trailing newline: two runs with the same
    inputs and the same NOW write the same bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def _actions_context() -> dict:
    keys = {"event": "GITHUB_EVENT_NAME", "run_id": "GITHUB_RUN_ID", "run_attempt": "GITHUB_RUN_ATTEMPT",
            "sha": "GITHUB_SHA", "repository": "GITHUB_REPOSITORY"}
    return {key: os.environ.get(var) or None for key, var in keys.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 instead of writing if README.md would change")
    parser.add_argument("--fixtures", type=Path, help="read API answers from this directory instead of GitHub")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="directory holding README.md and profile.config.json")
    parser.add_argument("--summary-file", type=Path, help="write a commit-message-shaped summary here")
    parser.add_argument("--meta-file", type=Path, help="write the run record here (default: ROOT/meta/last-run.json)")
    parser.add_argument("--no-meta", action="store_true", help="do not write a run record")
    parser.add_argument("--allow-shrink", action="store_true",
                        help="accept a Work section over 20%% smaller with no repository removed")
    parser.add_argument("--anonymous", action="store_true", help="talk to the API without GITHUB_TOKEN")
    parser.add_argument("--verbose", action="store_true", help="log each request as 'GET <url> -> <status>' on stderr")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a cp1252 console must not crash the report

    report: dict = {"schema": 1, "status": "error", "exit_code": EXIT_ERROR, "error": None,
                    "finished_at": None, "source": None, "actions": _actions_context()}
    meta_path = None if (args.check or args.no_meta) else (args.meta_file or args.root / "meta" / "last-run.json")

    def fail(message: str) -> int:
        print(f"error: {message}", file=sys.stderr)
        print("README.md left untouched.", file=sys.stderr)
        report["error"] = message
        return EXIT_ERROR

    source = None
    try:
        report["finished_at"] = utc_now()  # validated up front; a bad NOW is a bad run
        fixtures = args.fixtures or (Path(os.environ["PROFILE_FIXTURES"]) if os.environ.get("PROFILE_FIXTURES") else None)
        if fixtures is not None:
            source = Fixtures(fixtures)
        else:
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            if not token and not args.anonymous:
                return fail("GITHUB_TOKEN is not set or is empty (a misnamed secret expands to an empty string); "
                            "an unauthenticated run would silently fall to 60 requests an hour. "
                            "Set it, or pass --anonymous to accept that")
            config = load_config(args.root)
            owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
            if owner and owner.lower() != config["login"].lower():
                return fail(f"profile.config.json names {config['login']!r} but this run belongs to "
                            f"{owner!r}; the account was renamed. Update the login in profile.config.json "
                            f"and rename this repository to {owner}/{owner} so it is the profile again")
            source = GitHub(token or None, verbose=args.verbose)
        code = build(args.root, source, args.check, args.summary_file, allow_shrink=args.allow_shrink, report=report)
        report["status"] = {EXIT_OK: "ok", EXIT_STALE: "stale"}.get(code, "error")
        report["exit_code"] = code
        return code
    except BuildError as error:
        return fail(str(error))
    except Exception as error:  # noqa: BLE001 -- the exit-code contract must hold for the unforeseen too
        return fail(f"unexpected {error!r}")
    finally:
        if source is not None:
            report["source"] = source.describe()
            limits = getattr(source, "rate_limit", None)
            if limits:
                reset = limits.get("reset", "")
                when = f" (resets {_iso(int(reset))})" if reset.isdigit() else ""
                print("rate limit: " + " ".join(f"{k}={v}" for k, v in sorted(limits.items())) + when,
                      file=sys.stderr, flush=True)
        if report["finished_at"] is None:  # NOW was unusable; the record still needs a time
            report["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if meta_path is not None:
            try:
                write_meta(meta_path, report)
            except OSError as error:
                print(f"warning: could not write the run record to {meta_path}: {error}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
