---
purpose: Plan for migrating the admin god-components off their bespoke CSS classes onto the shared admin-* design system, deleting the duplicate styling.
type: guide
created: 2026-06-06
last_updated: 2026-06-06
---

# Admin Style Consistency Sweep (T3-90 / T3-52 follow-on)

## Goal

The admin god-components each ship their own prefixed CSS classes
(`.pm-*`, `.us-*`, `.prm-*`, `.chip-ledger-*`, `.debugger-*`) that re-implement
buttons, alerts, inputs, selects, checkboxes, form rows, modals, and loading
spinners — styling the shared admin design system **already provides**. This
sweep migrates those bespoke controls onto the shared `admin-*` classes and
deletes the now-dead per-component CSS, so admin tooling looks and behaves
consistently and the CSS stops drifting.

This is the deliberately-deferred visual half of the admin god-component
campaign (the structural folder-splits are tracked separately under
T3-48/49/50/77/78). Each split kept its classes verbatim to avoid coupling a
risky visual change to a structural refactor; this doc is that visual pass.

## The shared system already exists

`react/react/src/components/admin/AdminShared.css` defines a mature kit:

- **Buttons**: `admin-btn` + `--primary --secondary --danger --ghost --success --icon --sm --lg`
- **Alerts**: `admin-alert` + `--error --info --success --warning` (children: `__icon`, `__content`, `__dismiss`)
- **Inputs / selects / textarea**: `admin-input` (+`--error`), `admin-select` (chevron + dark options; **must be combined with `admin-input` for the box base**), `admin-textarea`
- **Checkbox**: `admin-checkbox` (custom input+label: `__input` with `:checked::after`, `__label`)
- **Forms**: `admin-form-group`, `admin-form-row`, `admin-label` (+`--required`)
- **Modal**: `admin-modal` (+`--lg --xl`), `admin-modal-overlay` (children: `__header __title __close __body __footer`)
- **Loading**: `admin-loading` (`__spinner`, `__text`), `admin-spinner`, `admin-skeleton`
- **Misc**: `admin-card`, `admin-badge`, `admin-table`, `admin-tabs`/`admin-tab`, `admin-empty`, `admin-divider`, `admin-text-*`

## Core principle — it is SURGICAL, not a prefix swap

Only migrate the classes that **re-implement a shared widget**. Component-specific
**layout** classes (containers, tables, grids, filter rows, slide-out panels,
sort indicators) have no shared equivalent and **stay bespoke**. Bulk
find-and-replace of a whole prefix is wrong.

### Two tiers of difficulty

1. **Clean / flat (do these first)** — leaf controls with no BEM children map 1:1:
   - `*-btn` (+variants) → `admin-btn` (+variants)
   - `*-input` → `admin-input`
   - `*-select` → **`admin-input admin-select`** (select needs the input base or the box loses border/padding/background)
2. **Compound (own pass, needs markup restructuring)** — the bespoke widget and the
   `admin-*` widget use **different internal structure**, so you must rewrite the
   subtree, not rename a class:
   - alerts: prm `__message/__close` vs admin `__content/__dismiss`
   - modals: simple title+actions vs admin `__header/__body/__footer`
   - checkboxes: native input vs admin custom `__input`/`__label` (+`::after` check)
   - forms: `__group` vs flat `admin-form-group`
   - loading: `__spinner` vs `admin-loading`/`admin-spinner`

## Per-component workflow (verified loop)

1. Classify the component's classes: `grep -oE '\.<prefix>-[a-z-]+' Component.css` → split into
   widget-dupes vs layout.
2. Migrate the **clean leaf controls** first (btn/input/select). Map exact
   `className="..."` strings (they're unique tokens, so a literal find/replace per
   file is safe). Keep any bespoke variant with no `admin-*` equivalent (e.g.
   `prm-btn--save-all`) as a thin modifier applied **alongside** `admin-btn`.
3. Delete the now-dead bespoke CSS rules; keep layout + retained-modifier rules.
4. Verify: `tsc --noEmit` + `eslint` + `npm run build` all exit 0, and load the
   page in the browser (guest "Jeff" — see Verification below) to confirm it
   renders without console errors.
5. **User eyeballs** the result on :5175 (the aesthetic "looks right" call —
   a class swap intentionally changes pixels, so there's no automated parity check).
6. Commit the slice, then tackle that component's compound widgets in a follow-up.

## Verification

Admin is auth-gated but reachable in dev as a guest named **Jeff**:
Play Now → `/login` → type "Jeff" → "Play as Guest" (guest_jeff is granted admin
in dev). Then navigate to the admin page. Note: an agent driving Playwright can
read the **DOM/accessibility snapshot, console, and computed styles** but **not
pixel screenshots** (the browser runs in a sandbox), so the human does the visual
sign-off. Pricing lives at `/admin/settings/pricing` (a UnifiedSettings section,
not a sidebar tab).

## Gotchas

- `admin-select` must be `admin-input admin-select` or the box has no border/padding.
- `admin-select` forces dark `<option>` colors — that's the intended design-system look (shared by all admin dropdowns).
- Compound widgets are **not** flat swaps — restructure the markup; do them as their own passes.
- Keep no-equivalent bespoke variants as thin modifiers over the admin base.
- Commit scoped to the component's folder (the `admin-menus` branch has had
  concurrent work — stage only the files you changed).

## Status & remaining work

| Component | Prefix | Leaf controls (btn/input/select) | Compound widgets (alert/modal/form/checkbox/loading) |
|---|---|---|---|
| PricingManager | `prm-` | ✅ done — `c02d83be` (kept `prm-btn--save-all`) | ☐ pending |
| ChipLedgerPanel | `chip-ledger-` (56) | ☐ | ☐ |
| DecisionAnalyzer | `debugger-` (10, mostly layout) | ☐ (small) | ☐ |
| UnifiedSettings | `us-` (105) | ☐ | ☐ |
| PersonalityManager | `pm-` (115) | ☐ | ☐ |

Suggested order: finish the clean leaf-control passes across all components first
(fast, low-risk, immediate consistency win), then do the compound-widget
restructuring passes component by component. Optionally promote recurring
no-equivalent variants (e.g. a "save-all"/warning button) into a real
`admin-btn--*` variant so they can be migrated too.
