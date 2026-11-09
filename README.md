# Mockups

Interactive HTML mockups, published with GitHub Pages at
https://andytran-novobi.github.io/public_mockup/

## Folder layout

```
<Project>/<FDD>/<mockup>.html      e.g. WJ/ACCT-08/ACCT-08-mockup-share-wizard.html
```

- One folder per project (`WJ`, `NEL`, …) and one sub-folder per FDD ticket (`ACCT-08`, `INV-013`, …).
- Use short folder names with no spaces or brackets: `INV-013`, not `[INV-013] Virtual Locations…`.
- An FDD folder can have its own `index.html` (mockup home). If it has none, a list page is built for it.
- Folders whose name starts with `_` (for example `_assets` for shared images) are published but not listed.

## Add mockups

1. Copy the mockup files into `<Project>/<FDD>/`.
2. Commit and push to `main`.
3. GitHub Actions builds and deploys the site (about 1–2 minutes). No index page to edit by hand.

Optional settings in `site-config.json`:

| Key | Use | Example |
|---|---|---|
| `project_names` | Display name per project folder | `{"NEL": "NEL"}` |
| `fdd_titles` | Title for an FDD that has no home page | `{"NEL/INV-013": "Virtual Locations for SMT Machines"}` |
| `redirects` | Keep old links working after a move | `{"old.html": "WJ/ACCT-08/new.html"}` |

## Who sees what

| Page | Address | Lists |
|---|---|---|
| Root | `/public_mockup/` | Nothing (neutral page) |
| Project | `/public_mockup/<Project>/` | That project's FDDs |
| FDD | `/public_mockup/<Project>/<FDD>/` | That FDD's screens |
| Team portal | folder set in `site-config.json` → `team_portal` | Every project (internal only) |

- Send clients a project or FDD link only. Never send the team portal link.
- This repo is public: anyone can browse its files on GitHub. Do not commit confidential data.
- Every page is published with `noindex, nofollow`, so search engines skip it.

## Build locally

```
python _tools/build_site.py
python -m http.server 8000 -d _site
```

Then open http://localhost:8000/.
