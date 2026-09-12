# APEX FLOW — Technical Presentation

An 18-slide technical defence deck for this repository, generated entirely from the code at commit `47d7308`
plus measurements taken by running the application.

```
presentation/
├── index.html   # 18 slides — content, diagrams, code excerpts, evidence footnotes
├── deck.css     # design system (tokens inherited from frontend/css/style.css)
├── deck.js      # META (your details) + slide engine: fragments, auto-fit, overview, notes
└── README.md    # this file
```

Companion: **[`docs/PRESENTATION-NOTES.md`](../docs/PRESENTATION-NOTES.md)** — 30-second and 2-minute pitches,
technical decisions, likely questions with answers, honest framing for the weak points, a
claim → file → line → evidence map, and the commands used to reproduce every measurement.

## 0. Personalise the title slide (30 seconds)

Three fields are placeholders: your name, course/institution, reviewer. Set them once at the top of
**`deck.js`** (the `META` object, line 10):

```js
const META = {
  name:     'Nandan Sharma',
  course:   'B.Tech CSE · Final-year project · Thapar University',
  reviewer: 'Prof. <name>',
};
```

Anything you leave empty keeps its placeholder text, gets a dashed amber box on the title slide, and the deck shows
"⚠ 3 placeholders still on this slide" so nobody can present with `<your name>` projected behind them. Filling all
three makes both disappear. (Equivalently, edit the three `data-fill` spans in `index.html`; the print stylesheet drops
the warning banner but keeps the amber boxes, so an unpersonalised PDF is still obviously unpersonalised.)

## Open it

```bash
# simplest: open the file directly (no build, no network, no dependencies)
xdg-open presentation/index.html      # or double-click it

# or serve it (recommended for the projector, so fonts and scroll behave identically)
python3 -m http.server 4173 --directory presentation
# → http://localhost:4173/
```

## Presenting

| Key | Action |
|---|---|
| `→` `Space` `PageDown` `Enter` `j` | next fragment, then next slide |
| `←` `PageUp` `Backspace` `k` | previous fragment, then previous slide |
| click left third / right two-thirds | back / forward (swipe and wheel also work) |
| `O` | overview grid of all slides |
| `N` | toggle this slide's speaker notes |
| `A` | freeze animations (all fragments revealed — useful while explaining a diagram) |
| `F` | fullscreen |
| `1`–`9` with `Alt` | jump to a slide; `#12` in the URL deep-links to slide 12 |
| `?` | show the shortcut strip |

Diagrams reveal step by step: architecture appears layer by layer, request flows appear stage by stage, and the
verification tables appear as one block — so nothing has to be explained before it is visible.

Runtime behaviour is intentionally minimal: `deck.js` measures every slide against the 1280 × 720 stage and, if a
slide is taller than the frame, reflows it wider and scales it down so no text can be clipped on a projector.
It has no dependencies, so it works from `file://`, from a static host, or from the repository.

### If the examiner caps you at 15 slides

Drop **5** (its dependency reasons already appear in the footers of slides 4 and 6), drop **11** (the ops agent is
summarised on slide 13), and merge **3** into **2** (problem → what the system is). Renumbering is automatic: the
counter, overview and deep links read `slides.length` and `data-title`, so nothing else has to change. Do not cut
slides 14–17 — the measured results, the limitations and the ordered plan are the parts that earn academic credit.

## Export to PDF for submission

Print from Chrome/Chromium (⌘P → **Save as PDF**):

- **Background graphics: on** — required, the deck's colour system is CSS backgrounds
- Scale: **100 %** (not "Fit to page"); margins: **None**; headers/footers: **off**
- Paper size does not need setting: the stylesheet declares `@page{size:1280px 724px}`, so each page is one slide at the
  designed aspect ratio (16:9 plus 4 px of rounding slack)

`@media print` reveals every fragment, disables animation, and neutralises the on-screen transform — then re-applies
the *same* shrink factor that `autoFit()` chose for that slide (it is written to `--pz` and used as `zoom`), so a dense
slide prints at the size it projects at instead of being cut off at the page edge. One page break per slide ⇒ exactly
18 pages. If a slide does spill onto a second page, that is the deck refusing to hide text — shorten that slide rather
than turning clipping back on.

## What is on each slide

1. Title — scope, stack, what the evidence base is
2. Problem & motivation — why logistics needs one shared status and proof of delivery
3. Project overview — user → system → processing → output
4. System architecture — five layers, one process, one external API, one CI side-channel
5. Technology stack — 8 runtime dependencies, each with the reason visible in the code
6. Codebase architecture — module map with line counts and the layer contract
7. Anatomy of one write — `POST /api/shipments` across all seven stages
8. Key implementation 1/2 — `@require_role`, the field allowlist, and the one field that escapes it
9. Key implementation 2/2 — OTP: generate → store → verify → cascade, with measured lockout output
10. Algorithms & domain logic — state machine, pricing formula, tracking simulation, route heuristic
11. Self-diagnosing deployment — the Render observer, its redaction, its limits
12. Security & reliability — control matrix: verified / partial / gap
13. Testing & quality — 0 tests, 3 CI jobs, 7 Semgrep rules (6 live, 1 inert)
14. Results — 15 claims measured against a live instance, failures included
15. Challenges & engineering decisions — challenge → decision → cost
16. Limitations — gaps with reproductions, separated from deliberate scope decisions
17. Future improvements — an ordered plan, each item deleting a bullet from slide 16
18. Conclusion

## Was this deck checked?

`docs/PRESENTATION-NOTES.md` § 12 is the red-team pass: ten self-check questions, each answered by a *command* rather
than an opinion (every named path and function resolved against the source, every `%`/`ms` traced, panel lengths,
contrast ratios from the real hex pairs, the type floor, the animation inventory, the external-URL scan) — plus § 12.11,
the ten things that pass actually caught and fixed, and § 12.12, the one thing it could not check without a browser.
If a number on a slide is challenged, § 8 is the claim → file → function → measurement map.

## Accuracy policy used while building this deck

- No capability was claimed that is not present in the code or its docs.
- Simulated, heuristic or stubbed behaviour (GPS interpolation, distance/pricing formulas, the settings page, the
  "Focus Telemetry" button) is labelled as such rather than described as a feature.
- Every "verified" statement traces to a command in `docs/PRESENTATION-NOTES.md` § 9.
- No performance metric, coverage percentage or user number appears anywhere.
