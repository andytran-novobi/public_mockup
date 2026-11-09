#!/usr/bin/env python3
"""Build the GitHub Pages site for this mockup repo.

Source layout:  <Project>/<FDD>/<mockup>.html
                e.g. WJ/ACCT-08/ACCT-08-mockup-share-wizard.html

The output folder (default: _site) gets:
  * every source file, copied as-is
  * <Project>/index.html        client page listing that project's FDDs  (unless the repo has one)
  * <Project>/<FDD>/index.html  list of that FDD's screens               (unless the repo has one)
  * <team_portal>/index.html    internal page listing every project      (folder name: site-config.json)
  * index.html                  neutral root page that lists nothing     (unless the repo has one)
  * redirect pages for old links                                         (site-config.json -> redirects)
  * <meta name="robots" content="noindex, nofollow"> in every HTML page

Usage:  python _tools/build_site.py [--out FOLDER]
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import posixpath
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = "site-config.json"
MARKER = ".site-build"
NOINDEX = '<meta name="robots" content="noindex, nofollow">'
NOT_PUBLISHED = {CONFIG_FILE, "README.md", "_tools", "_site"}  # repo entries kept out of the site
IGNORE = shutil.ignore_patterns(".DS_Store", "Thumbs.db", "__MACOSX", "__pycache__", ".git")
TICKET = r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b"  # ACCT-08, INV-013, WJ-HT-T2, ME-MANU-012

WARNINGS: list[str] = []


def warn(message: str) -> None:
    WARNINGS.append(message)
    print(f"WARNING: {message}")


# --------------------------------------------------------------------------- helpers

def natural_key(text: str) -> list:
    """Sort 'ACCT-8' before 'ACCT-10'."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def skipped(name: str) -> bool:
    """Hidden or helper entries are never a project, an FDD or a listed screen."""
    return name.startswith((".", "_")) or name == "__MACOSX"


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def href(path: str) -> str:
    """URL-encode a relative POSIX path (spaces, brackets...) for an href attribute."""
    return esc(quote(path, safe="/"))


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                                text=True, encoding="utf-8", check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def last_changed(file: Path) -> str | None:
    """Date of the last commit that changed the file's content; pure moves/renames don't count."""
    log = git("log", "--follow", "-M", "--format=@%cs", "--name-status", "--",
              file.relative_to(ROOT).as_posix())
    date = None
    for line in (log or "").splitlines():
        if line.startswith("@"):
            date = line[1:]
        elif date and line.strip() and not line.startswith("R100"):
            return date
    return None


def last_updated(folder: Path) -> str | None:
    """Newest content change in the folder; files not committed yet use their file date."""
    dates = [last_changed(f) or dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
             for f in folder.rglob("*") if f.is_file()]
    return max(dates) if dates else None


# --------------------------------------------------------------------------- titles

def tag_text(source: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", source, re.S | re.I)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text or None


_TITLE_PREFIXES = [
    re.compile(r"^\[[^\]]*\]\s*"),                     # "[ACCT-08] ..."
    re.compile(rf"^{TICKET}(?:\s*/\s*{TICKET})*\s*"),  # "INV-013 ...", "ME-MANU-012 / ME-MANU-013 ..."
    re.compile(r"^mockups?\b\s*", re.I),               # "Mockup ..."
    re.compile(r"^[-–—:·|]+\s*"),                      # separator left over
]
_HOME_SUFFIX = re.compile(r"\s*[-–—:·|]?\s*\(?\s*(?:mockups?\s+)?(?:home|index)\s*\)?\s*$", re.I)


def clean_title(title: str | None) -> str | None:
    """'[ACCT-08] Mockup — Share Wizard' -> 'Share Wizard'."""
    if not title:
        return None
    text = title.strip()
    changed = True
    while changed:
        changed = False
        for pattern in _TITLE_PREFIXES:
            new = pattern.sub("", text, count=1).strip()
            if new and new != text:
                text, changed = new, True
    return text


def screen_title(path: Path) -> str:
    title = clean_title(tag_text(read_text(path), "title"))
    if title:
        return title
    stem = re.sub(rf"^{TICKET}-mockup-", "", path.stem)
    return stem.replace("-", " ").replace("_", " ").strip().capitalize() or path.name


def home_title(home: Path) -> str | None:
    """Feature name for an FDD, read from its own home page (<h1>, else <title>)."""
    source = read_text(home)
    for text in (tag_text(source, "h1"), tag_text(source, "title")):
        text = clean_title(text)
        if text:
            text = _HOME_SUFFIX.sub("", text).strip()
            if text:
                return text
    return None


# --------------------------------------------------------------------------- discovery

@dataclass
class Screen:
    path: str  # POSIX path from the site root
    title: str


@dataclass
class Fdd:
    project: str
    name: str
    title: str | None
    screens: list[Screen]
    has_home: bool
    updated: str | None

    @property
    def path(self) -> str:
        return f"{self.project}/{self.name}"

    @property
    def count(self) -> int:
        return len(self.screens) or (1 if self.has_home else 0)


@dataclass
class Project:
    name: str
    label: str
    has_home: bool
    fdds: list[Fdd] = field(default_factory=list)
    loose: list[Screen] = field(default_factory=list)  # mockups put straight in the project folder

    @property
    def count(self) -> int:
        return sum(f.count for f in self.fdds) + len(self.loose)


def html_screens(folder: Path) -> list[Path]:
    """HTML files under folder, without its own index.html or hidden/helper folders; overview first."""
    screens = []
    for path in folder.rglob("*.html"):
        rel = path.relative_to(folder)
        if rel.as_posix() != "index.html" and not any(skipped(part) for part in rel.parts):
            screens.append(path)
    return sorted(screens, key=lambda p: (0 if re.search(r"overview|home", p.name, re.I) else 1,
                                          natural_key(p.relative_to(folder).as_posix())))


def discover(cfg: dict, out: Path) -> list[Project]:
    names, titles = cfg.get("project_names") or {}, cfg.get("fdd_titles") or {}
    projects = []
    for pdir in sorted(ROOT.iterdir(), key=lambda p: natural_key(p.name)):
        if not pdir.is_dir() or skipped(pdir.name) or pdir.resolve() == out:
            continue
        project = Project(pdir.name, names.get(pdir.name, pdir.name), (pdir / "index.html").exists())
        for fdir in sorted(pdir.iterdir(), key=lambda p: natural_key(p.name)):
            if not fdir.is_dir() or skipped(fdir.name):
                continue
            home = fdir / "index.html"
            screens = html_screens(fdir)
            if not screens and not home.exists():
                continue
            project.fdds.append(Fdd(
                project=pdir.name,
                name=fdir.name,
                title=titles.get(f"{pdir.name}/{fdir.name}") or (home_title(home) if home.exists() else None),
                screens=[Screen(p.relative_to(ROOT).as_posix(), screen_title(p)) for p in screens],
                has_home=home.exists(),
                updated=last_updated(fdir),
            ))
        for path in sorted(pdir.glob("*.html"), key=lambda p: natural_key(p.name)):
            if path.name != "index.html" and not skipped(path.name):
                project.loose.append(Screen(path.relative_to(ROOT).as_posix(), screen_title(path)))
        if project.fdds or project.loose:
            projects.append(project)
    return projects


# --------------------------------------------------------------------------- pages

CSS = """
:root{--brand:#714B67;--action:#017e84;--bg:#f4f5f7;--card:#fff;--text:#111827;--muted:#6b7280;--line:#e5e7eb}
*{box-sizing:border-box;margin:0;padding:0}
[hidden]{display:none!important}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  font-size:14px;line-height:1.5;color:var(--text);background:var(--bg)}
a{color:inherit;text-decoration:none}
.bar{position:sticky;top:0;z-index:10;background:var(--brand);color:#fff;padding:10px 16px;
  display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}
.bar a{color:#fff;opacity:.85}.bar a:hover{opacity:1;text-decoration:underline}
.bar .sep{opacity:.5}
.bar .tag{margin-left:auto;background:rgba(255,255,255,.18);padding:2px 10px;border-radius:999px;font-size:11px}
.wrap{max-width:1080px;margin:0 auto;padding:28px 16px 56px}
.center{max-width:560px;text-align:center;padding-top:96px}
h1{font-size:24px;line-height:1.3}
h1 .muted{color:var(--muted);font-weight:400}
.sub{color:var(--muted);margin-top:6px}
h2.section{font-size:16px;margin-top:32px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-top:18px}
.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;
  box-shadow:0 1px 3px rgba(0,0,0,.06);transition:box-shadow .15s,transform .1s}
a.card:hover{box-shadow:0 6px 16px rgba(0,0,0,.12);transform:translateY(-1px)}
.card .id{font-size:11px;font-weight:700;letter-spacing:.4px;color:var(--action)}
.card h3{font-size:15px;margin-top:2px}
.card .meta,.card .file{color:var(--muted);font-size:12px;margin-top:6px;word-break:break-all}
.note{background:#fff3cd;border-left:4px solid #ffac00;color:#6b4f00;padding:10px 12px;border-radius:6px;
  margin-top:16px;font-size:13px}
.tools{margin-top:18px}
.tools input{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:6px;font-size:14px;background:#fff}
.project{margin-top:28px}
.project h2{font-size:17px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.project h2 .count{font-size:12px;font-weight:400;color:var(--muted)}
.project h2 .link{font-size:12px;font-weight:400;color:var(--action);margin-left:auto}
.tablewrap{overflow-x:auto;margin-top:10px;border:1px solid var(--line);border-radius:8px;background:#fff}
table{width:100%;border-collapse:collapse}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;letter-spacing:.4px;text-transform:uppercase;color:var(--muted);background:#fafafa;font-weight:600}
tbody tr:last-child td{border-bottom:0}
td.id a,td.title a{color:var(--action);font-weight:700}
td.id{white-space:nowrap}
td.num,td.date{white-space:nowrap;color:var(--muted)}
td.act{text-align:right}
button.copy{border:1px solid var(--line);background:#fff;border-radius:6px;padding:3px 10px;font-size:12px;
  cursor:pointer;white-space:nowrap;color:var(--text)}
button.copy:hover{border-color:var(--action);color:var(--action)}
.empty{color:var(--muted);margin-top:20px}
footer{color:var(--muted);font-size:12px;margin-top:36px}
@media (max-width:640px){th.num,td.num,th.date,td.date{display:none}h1{font-size:20px}}
"""

PORTAL_JS = """
const q = document.getElementById('q');
const rows = [...document.querySelectorAll('tr[data-search]')];
const sections = [...document.querySelectorAll('section.project')];
q.addEventListener('input', () => {
  const words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
  rows.forEach(r => { r.hidden = !words.every(w => r.dataset.search.includes(w)); });
  let shown = 0;
  sections.forEach(s => {
    const show = [...s.querySelectorAll('tr[data-search]')].some(r => !r.hidden);
    s.hidden = !show;
    if (show) shown++;
  });
  document.getElementById('nomatch').hidden = shown > 0;
});
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-copy]');
  if (!b) return;
  const url = new URL(b.dataset.copy, location.href).href;
  const done = () => { b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy link'; }, 1200); };
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, () => prompt('Copy this link:', url));
  else prompt('Copy this link:', url);
});
"""


def document(title: str, body: str, script: str = "") -> str:
    script_tag = f"<script>{script}</script>\n" if script else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{NOINDEX}
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
{body}
{script_tag}</body>
</html>
"""


def screen_card(screen: Screen, base: str) -> str:
    rel = posixpath.relpath(screen.path, base)
    return (f'<a class="card" href="{href(rel)}"><h3>{esc(screen.title)}</h3>'
            f'<div class="file">{esc(rel)}</div></a>')


def fdd_page(project: Project, fdd: Fdd) -> str:
    heading = esc(fdd.name) + (f' <span class="muted">— {esc(fdd.title)}</span>' if fdd.title else "")
    meta = plural(fdd.count, "screen") + (f" · updated {fdd.updated}" if fdd.updated else "")
    cards = "\n".join(screen_card(s, fdd.path) for s in fdd.screens)
    body = f"""<header class="bar"><a href="../index.html">{esc(project.label)}</a><span class="sep">›</span><span>{esc(fdd.name)}</span></header>
<main class="wrap">
<h1>{heading}</h1>
<p class="sub">{esc(meta)}</p>
<div class="grid">
{cards}
</div>
</main>"""
    title = f"{fdd.name} — {fdd.title}" if fdd.title else fdd.name
    return document(f"{title} · {project.label}", body)


def project_page(project: Project) -> str:
    cards = []
    for fdd in project.fdds:
        meta = plural(fdd.count, "screen") + (f" · updated {fdd.updated}" if fdd.updated else "")
        label = (f'<div class="id">{esc(fdd.name)}</div><h3>{esc(fdd.title)}</h3>' if fdd.title
                 else f"<h3>{esc(fdd.name)}</h3>")
        cards.append(f'<a class="card" href="{href(fdd.name + "/index.html")}">{label}'
                     f'<div class="meta">{esc(meta)}</div></a>')
    loose = ""
    if project.loose:
        loose = ('<h2 class="section">Other mockups</h2>\n<div class="grid">\n'
                 + "\n".join(screen_card(s, project.name) for s in project.loose) + "\n</div>")
    parts = ([plural(len(project.fdds), "FDD")] if project.fdds else []) + [plural(project.count, "screen")]
    body = f"""<header class="bar"><strong>{esc(project.label)}</strong><span class="sep">›</span><span>Mockups</span></header>
<main class="wrap">
<h1>{esc(project.label)} — Mockups</h1>
<p class="sub">{esc(" · ".join(parts))}</p>
<div class="grid">
{chr(10).join(cards)}
</div>
{loose}
</main>"""
    return document(f"{project.label} · Mockups", body)


def portal_page(projects: list[Project], cfg: dict) -> str:
    sections = []
    for project in projects:
        rows = []
        for fdd in project.fdds:
            search = " ".join([project.name, project.label, fdd.name, fdd.title or ""]).lower()
            rows.append(
                f'<tr data-search="{esc(search)}">'
                f'<td class="id"><a href="{href("../" + fdd.path + "/index.html")}">{esc(fdd.name)}</a></td>'
                f'<td>{esc(fdd.title or "")}</td><td class="num">{fdd.count}</td>'
                f'<td class="date">{esc(fdd.updated or "")}</td>'
                f'<td class="act"><button type="button" class="copy" data-copy="{href("../" + fdd.path + "/")}">Copy link</button></td></tr>')
        for screen in project.loose:
            search = " ".join([project.name, project.label, screen.title, screen.path]).lower()
            rows.append(
                f'<tr data-search="{esc(search)}"><td class="id">—</td>'
                f'<td class="title"><a href="{href("../" + screen.path)}">{esc(screen.title)}</a></td>'
                f'<td class="num">1</td><td class="date"></td>'
                f'<td class="act"><button type="button" class="copy" data-copy="{href("../" + screen.path)}">Copy link</button></td></tr>')
        sections.append(f"""<section class="project">
<h2>{esc(project.label)} <span class="count">{esc(plural(len(project.fdds), "FDD"))} · {esc(plural(project.count, "screen"))}</span>
<a class="link" href="{href("../" + project.name + "/index.html")}">Client page</a>
<button type="button" class="copy" data-copy="{href("../" + project.name + "/")}">Copy link</button></h2>
<div class="tablewrap"><table>
<thead><tr><th>FDD</th><th>Title</th><th class="num">Screens</th><th class="date">Updated</th><th class="act"></th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table></div>
</section>""")
    totals = " · ".join([plural(len(projects), "project"),
                         plural(sum(len(p.fdds) for p in projects), "FDD"),
                         plural(sum(p.count for p in projects), "screen")])
    built = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit = git("rev-parse", "--short", "HEAD") or "local"
    body = f"""<header class="bar"><strong>{esc(cfg["site_title"])}</strong><span class="sep">›</span><span>Team portal</span><span class="tag">Internal link — do not share</span></header>
<main class="wrap">
<h1>All mockups</h1>
<p class="sub">{esc(totals)}</p>
<div class="note">Send clients a <strong>project</strong> or <strong>FDD</strong> link only (use <em>Copy link</em>). Never send the link of this page.</div>
<div class="tools"><input id="q" type="search" placeholder="Filter by project, FDD or title" aria-label="Filter mockups" autocomplete="off"></div>
{chr(10).join(sections) or '<p class="empty">No mockups yet.</p>'}
<p class="empty" id="nomatch" hidden>No match.</p>
<footer>Built {esc(built)} · commit {esc(commit)}</footer>
</main>"""
    return document(f"Team portal · {cfg['site_title']}", body, PORTAL_JS)


def root_page(cfg: dict) -> str:
    body = f"""<main class="wrap center">
<h1>{esc(cfg["site_title"])}</h1>
<p class="sub">Interactive design mockups for review.<br>Please open the link that was shared with you.</p>
</main>"""
    return document(cfg["site_title"], body)


def redirect_page(target: str) -> str:
    url = href(target)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{NOINDEX}
<meta http-equiv="refresh" content="0; url={url}">
<title>Moved</title>
</head>
<body>
<p>This page has moved: <a href="{url}">{esc(target)}</a></p>
<script>location.replace({json.dumps(quote(target, safe="/"))} + location.search + location.hash);</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- build steps

def load_config() -> dict:
    cfg = {"site_title": "Mockups", "team_portal": "", "project_names": {}, "fdd_titles": {}, "redirects": {}}
    path = ROOT / CONFIG_FILE
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
        except json.JSONDecodeError as exc:
            sys.exit(f"{CONFIG_FILE}: invalid JSON ({exc})")
    portal = str(cfg.get("team_portal") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,}", portal):
        sys.exit(f'{CONFIG_FILE}: "team_portal" must be a folder name of 8+ letters, digits, "-" or "_"')
    if (ROOT / portal).exists():
        sys.exit(f'{CONFIG_FILE}: "team_portal" ({portal}) clashes with a folder in the repo')
    return cfg


def prepare_out(out: Path) -> Path:
    out = out.resolve()
    if out == ROOT or out in ROOT.parents:
        sys.exit(f"--out must not be the repo or one of its parent folders: {out}")
    if out.exists():
        if any(out.iterdir()) and not (out / MARKER).exists():
            sys.exit(f"{out} is not empty and was not made by this script; choose another --out")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("Built by _tools/build_site.py; safe to delete.\n", encoding="utf-8")
    return out


def copy_sources(out: Path) -> None:
    for item in sorted(ROOT.iterdir()):
        # "_" folders (e.g. _assets) are published but never listed; "." entries are never published.
        if item.name.startswith(".") or item.name in NOT_PUBLISHED or item.resolve() == out:
            continue
        if item.is_dir():
            shutil.copytree(item, out / item.name, ignore=IGNORE)
        else:
            shutil.copy2(item, out / item.name)


def write_redirects(out: Path, redirects: dict) -> int:
    count = 0
    for old, new in (redirects or {}).items():
        old, new = old.strip("/"), new.strip("/")
        dest = (out / old).resolve()
        if out not in dest.parents:
            warn(f"redirect outside the site skipped: {old}")
            continue
        if not (out / new).is_file():
            warn(f"redirect target not found: {old} -> {new}")
        if dest.exists():
            warn(f"redirect skipped, a real page exists at: {old}")
            continue
        write(dest, redirect_page(posixpath.relpath(new, posixpath.dirname(old) or ".")))
        count += 1
    return count


_ROBOTS = re.compile(r"<meta\b[^>]*\bname\s*=\s*[\"']?robots\b", re.I)
_ANCHORS = [re.compile(p, re.I) for p in (r"<head\b[^>]*>", r"<html\b[^>]*>", r"<!doctype\b[^>]*>")]


def add_noindex(path: Path) -> bool:
    text = path.read_bytes().decode("utf-8", errors="surrogateescape")
    if _ROBOTS.search(text):
        return False
    for anchor in _ANCHORS:
        match = anchor.search(text)
        if match:
            text = text[:match.end()] + "\n" + NOINDEX + text[match.end():]
            break
    else:
        text = NOINDEX + "\n" + text
    path.write_bytes(text.encode("utf-8", errors="surrogateescape"))
    return True


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Build the GitHub Pages site for this mockup repo.")
    parser.add_argument("--out", default=str(ROOT / "_site"), help="output folder (default: _site in the repo)")
    args = parser.parse_args(argv)

    cfg = load_config()
    out = prepare_out(Path(args.out))
    copy_sources(out)
    projects = discover(cfg, out)

    for project in projects:
        for fdd in project.fdds:
            if not fdd.has_home:
                write(out / project.name / fdd.name / "index.html", fdd_page(project, fdd))
        if not project.has_home:
            write(out / project.name / "index.html", project_page(project))
    write(out / cfg["team_portal"] / "index.html", portal_page(projects, cfg))
    if not (out / "index.html").exists():
        write(out / "index.html", root_page(cfg))
    redirects = write_redirects(out, cfg.get("redirects"))
    tagged = sum(add_noindex(page) for page in out.rglob("*.html"))

    print(f"Built {out}")
    for project in projects:
        own = "own page" if project.has_home else "page built"
        print(f"  {project.name}/  ({own})")
        for fdd in project.fdds:
            home = "own home" if fdd.has_home else "list built"
            print(f"    {fdd.name}/  {plural(fdd.count, 'screen')}, {home}  {fdd.title or ''}")
        for screen in project.loose:
            print(f"    {posixpath.basename(screen.path)}  (no FDD folder)")
    print(f"Team portal: {cfg['team_portal']}/index.html")
    print(f"Redirects: {redirects} | noindex added: {tagged} pages | warnings: {len(WARNINGS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
