<!-- Rendered into the profile README at github.com/BU1lDR by
     BU1lDR/BU1lDR/tools/build_readme.py, on the hour and on dispatch.
     Format: "# <display name> — <heading tail>", a one-line meta row, then at most
     two short paragraphs. Placeholders filled from the GitHub API: {license}
     {version} {live} {description}. Keep it brief; the detail belongs in README.md,
     which CI holds to the code. No test count here on purpose: that number has one
     home in README.md and one guard in tools/check_test_count.py. -->

# file-integrity-checker — baseline a directory, then report what changed

{license} · Python 3.8+ · standard library only

SHA-256 hashes a tree into a versioned JSON baseline with a `.sha256` sidecar, then reports modified, new and deleted files on later runs. Skips symlinks, rejects path traversal in baseline entries, and exits `0/1/2` for cron and CI. Built from a roadmap.sh brief.

CI tests the badges rather than displaying them: Python 3.8 through 3.14 in official containers, plus Linux, macOS and Windows, with nothing installed in any job. A further job holds the test count quoted in the README and in the repository description to what discovery actually collects.
