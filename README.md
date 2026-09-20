# Aryan Verma

**Second-year B.Tech Information Technology** — Dr Akhilesh Das Gupta Institute of Professional Studies (ADGIPS), affiliated to GGSIPU, Delhi (2025–2029, CGPA 9.04/10). Delhi, India · IST (UTC+5:30).

I build security tooling in Python, and CI that tries to falsify what the README claims about it. **Open to cybersecurity internships** — remote or Delhi NCR, available immediately.

[Portfolio](https://bu1ldr.github.io/Portfolio/) · [Résumé (PDF)](https://bu1ldr.github.io/Portfolio/assets/resume.pdf) · [LinkedIn](https://www.linkedin.com/in/aryanver) · [Certifications on Credly](https://www.credly.com/users/aryan_v) · [aryanverma102007@gmail.com](mailto:aryanverma102007@gmail.com)

---

## Work

### [secscan](https://github.com/BU1lDR/security-scanner) — SCA + SAST + DAST in one Python CLI

MIT, v1.2.0.

- Every outbound request passes one authorization gate holding two separate allowlists: **Scope** (hosts you are permitted to test) and **Egress** (the tool's own data sources, such as OSV). The one raw-socket probe that bypasses HTTP — the TLS certificate check — takes that gate as a *positional* argument, so omitting it is a `TypeError` rather than a silently ungated request. `--active` without `--i-am-authorized` degrades to a passive scan instead of firing.
- The suite runs offline, and CI proves that rather than asking you to trust it: it runs a second time with sockets blocked and only loopback allowed. Matched secrets are redacted at `Finding` construction, before anything can reach a report or a log (`AKIA****************`), and SCA findings carry per-advisory fix boundaries (`Bump flask from 0.12 to 0.12.3`). Exit codes `0/1/2`, so a calling script can act on the result.
- A weekly workflow asks OSV whether its own declared dependency floors are vulnerable. It found `cryptography>=42` admitting a version carrying fifteen advisories, four of them HIGH, so the floor moved. The same habit caught the scanner reporting its own SAST rule definitions as findings — nearly every hit on its own source was the rule pack matching the string literals that describe it.

### [file-integrity-checker](https://github.com/BU1lDR/file-integrity-checker) — baseline a directory, then report what changed

MIT. Stdlib-only Python CLI built from a roadmap.sh brief; `requirements.txt` is a one-line comment saying so.

- SHA-256 hashes a tree into a versioned JSON baseline, then reports modified, new and deleted files on later runs; skips symlinks, rejects path traversal in baseline entries, exits `0/1/2` for cron.
- Much of the suite feeds it hostile input and asserts that it refuses to run rather than trusting the file it was handed — absolute POSIX, Windows, UNC and drive-relative paths, `..` traversal, MD5 declared as the algorithm, wrong-length and non-hex digests, an unsupported schema version. The baseline carries a `.sha256` sidecar so tampering is detected, and the README calls that digest unkeyed rather than implying it stops an attacker.
- CI tests the two badges at the top of that README instead of displaying them: Python 3.8 through 3.14, each in its own official Docker image, plus Linux, macOS and Windows — with nothing installed in any job, because an empty environment is the only thing that actually tests standard-library-only. One step fails the build if discovery collects no tests, since `unittest discover` exits 0 when it finds nothing.

### [Retail customer segmentation](https://github.com/BU1lDR/retail-customer-segmentation) — 1,067,371 transaction lines

UCI Online Retail II (CC BY 4.0), deliberately not committed.

- Nine individually logged cleaning steps take 1,067,371 raw lines down to £20.5M of reportable revenue, of which £17.5M — 85.4% — carries a customer ID and is therefore all the segmentation can actually use.
- Names the 683 high-value accounts worth £1,689,620 that have stopped ordering. The top 20% of identified customers, 1,170 of them, hold 77.2% of identified revenue. RFM quintile segmentation is cross-checked against K-Means, which agrees on 80.5% of customers and is weakest on Loyal at 60.9% purity; k=4 was kept even though the silhouette score preferred k=2.
- Shows its own 12-month CLV projection running 2.91× high on a like-for-like basis, instead of quoting that projection as a result, and states what the data cannot support: 22.9% of cleaned lines carry no customer ID at all. Every figure above is a named key in a committed `outputs/facts.json`, and CI fails if the README or the generated report drifts from it.

### [Portfolio](https://github.com/BU1lDR/Portfolio) — hand-written site, no framework and no build step ([live](https://bu1ldr.github.io/Portfolio/))

HTML, CSS and JavaScript. Nothing installed at runtime beyond a webfont.

- Deployed to GitHub Pages by an Actions workflow that asks the Pages API for its own base path, substitutes it into `404.html`, greps to confirm the substitution took, and fails the deploy rather than publish a 404 page that cannot find its CSS.
- A Content-Security-Policy with no `unsafe-inline` anywhere, styles included, audited in CI against the pages it governs — and a link checker that fails the build on a dead link, added after a repo this site linked into was deleted.
- A wireframe torii gate drawn on a 2D canvas through a hand-rolled perspective projection, and an in-page shell with command history, tab completion, and typo suggestions from a hand-written edit distance that deliberately will not suggest the hidden commands.

---

## Tools

- **Used in the repos above** — Python · Bash · Linux · Git & GitHub Actions · pytest & unittest · asyncio & httpx · pandas · NumPy · scikit-learn · Jupyter · HTML, CSS & JavaScript · Docker (official images as Actions job containers)
- **Coursework and Cisco labs** — C · C++ · SQL · Nmap · Wireshark · Burp Suite · sqlmap · Metasploit · Hydra · Shodan
- **Private hackathon repo, no public artifact** — FastAPI · Uvicorn · Pydantic · React · Tailwind CSS · Vite

## Experience

- **Data Analytics Intern** — IBM SkillsBuild × BharatCares, remote, Aug–Sep 2026 (completed). The segmentation study above was the capstone deliverable.
- **Backend Lead** — Smart India Hackathon 2026, *अर्थNiti* (ArthNiti). FastAPI backend for a business-advice and financial-planning assistant aimed at rural micro-entrepreneurs. Private repo, prototype stage.

## Certifications

Seven with verifiable badges on [Credly](https://www.credly.com/users/aryan_v):

- **Cisco Networking Academy** (2026) — Junior Cybersecurity Analyst career path · Introduction to Cybersecurity · Networking Basics
- **IBM SkillsBuild** (2026) — Data Fundamentals · Getting Started with Data · Generative AI Essentials: Using LLMs to Work with Data · Make Agentic AI Work for You

Four more issued as certificates rather than badges, copies on my [portfolio](https://bu1ldr.github.io/Portfolio/):

- **Cisco Networking Academy** (2026) — Getting Started with Cisco Packet Tracer
- **IBM SkillsBuild** (2026) — Lab: Troubleshoot Your Code Using IBM Bob
- **AWS** (2026) — Foundations of Prompt Engineering
- **E&ICT Academy, IIT Kanpur** (2025) — Fundamentals of C Programming

---

Delhi, India · IST (UTC+5:30) · [aryanverma102007@gmail.com](mailto:aryanverma102007@gmail.com)
