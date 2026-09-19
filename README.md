# Aryan Verma

**Second-year B.Tech Information Technology** — Dr Akhilesh Das Gupta Institute of Professional Studies (ADGIPS), affiliated to GGSIPU, Delhi (2025–2029, CGPA 9.04/10). Delhi, India · IST (UTC+5:30).

I build security tooling in Python. **Open to cybersecurity internships** — remote or Delhi NCR. Resume on request.

[Portfolio](https://bu1ldr.github.io/Portfolio/) · [LinkedIn](https://www.linkedin.com/in/aryanver) · [Certifications on Credly](https://www.credly.com/users/aryan_v) · [aryanverma102007@gmail.com](mailto:aryanverma102007@gmail.com)

---

## Work

### [secscan](https://github.com/BU1lDR/security-scanner) — SCA + SAST + DAST in one Python CLI

MIT. Dependency CVEs resolved against OSV.dev, SAST rules for dangerous sinks and hardcoded secrets, passive DAST over TLS, security headers, cookie flags and exposed files, plus opt-in detection-only active checks.

- Every outbound request passes one authorization gate holding two separate allowlists: **Scope** (hosts you are permitted to test) and **Egress** (the tool's own data sources, such as OSV).
- The one raw-socket probe that bypasses HTTP — the TLS certificate check — takes that gate as a *positional* argument, so omitting it is a `TypeError` rather than a silently ungated request. `--active` without `--i-am-authorized` degrades to a passive scan instead of firing.
- 353 tests, none of which touch the network. Matched secrets are redacted before they reach a report or a log (`AKIA****************`), and SCA findings carry per-advisory fix boundaries (`Bump flask from 0.12 to 0.12.3`). Exit codes `0/1/2`, so a calling script can act on the result.

### [Retail customer segmentation](https://github.com/BU1lDR/retail-customer-segmentation) — 1,067,371 transaction lines

IBM SkillsBuild × BharatCares capstone. Jupyter, pandas, scikit-learn. UCI Online Retail II (CC BY 4.0), deliberately not committed.

- 1,067,371 raw lines cleaned through nine individually logged steps down to £20.5M of usable revenue, then RFM segmentation cross-checked against K-Means, CLV estimation and a cohort retention matrix.
- Names the 683 high-value accounts worth £1,689,620 that have stopped ordering. The top 20% of identified customers hold 77.2% of identified revenue.
- Shows its own 12-month CLV projection running 2.91× high on a like-for-like basis, instead of quoting that projection as a result, and states what the data cannot support: 22.9% of lines carry no customer ID.

### [Portfolio](https://github.com/BU1lDR/Portfolio) — hand-written site, zero runtime dependencies ([live](https://bu1ldr.github.io/Portfolio/))

HTML, CSS and JavaScript. No framework, no bundler, nothing installed at runtime.

- Deployed to GitHub Pages by an Actions workflow that asks the Pages API for its own base path, substitutes it into `404.html`, greps to confirm the substitution took, and fails the deploy rather than publish a 404 page that cannot find its CSS.
- A wireframe torii gate drawn on a 2D canvas through a hand-rolled perspective projection; an in-page shell with command history, tab completion and typo suggestions; matrix rain and a couple of dozen easter eggs with a scoreboard.
- `prefers-reduced-motion` collapses every animation, the terminal log is `aria-live`, there is a skip link, and the site is usable from the keyboard alone. Canonical URL, Open Graph, JSON-LD `Person`, sitemap, `robots.txt`, `security.txt`.

### [file_integrity_checker](https://github.com/BU1lDR/my_projects) — baseline a directory, then report what changed

Stdlib-only Python CLI, in the `file_integrity_checker/` directory of `my_projects`, built from a roadmap.sh brief. SHA-256 hashes a tree into a versioned JSON baseline, then reports modified, new and deleted files on later runs; skips symlinks, rejects path traversal in baseline entries, exits `0/1/2` for cron. Of its 39 unittest cases, roughly twenty feed it hostile baselines — absolute POSIX and Windows paths, `..` traversal, MD5 declared as the algorithm, wrong-length and non-hex digests, an unsupported schema version — and assert that it refuses to run rather than trusting the file it was handed.

---

## Tools

- **Used in the repos above** — Python · Bash · Linux · Git & GitHub Actions · pytest & unittest · asyncio & httpx · pandas · NumPy · scikit-learn · Jupyter · HTML, CSS & JavaScript
- **Coursework, Cisco labs and a private hackathon repo, no public artifact yet** — C · C++ · SQL · Nmap · Wireshark · Burp Suite · sqlmap · Metasploit · Hydra · Shodan · Docker · FastAPI · Uvicorn · Pydantic · React · Tailwind CSS · Vite

## Experience

- **Data Analytics Intern** — IBM SkillsBuild × BharatCares, remote, Aug–Sep 2026. The segmentation study above was the capstone deliverable.
- **Backend Lead** — Smart India Hackathon 2026, *अर्थNiti* (ArthNiti). FastAPI backend for a business-advice and financial-planning assistant aimed at rural micro-entrepreneurs. Python, FastAPI, React, Tailwind, Vite. Private repo, prototype stage.

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
