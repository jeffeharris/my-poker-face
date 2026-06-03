---
purpose: Full results of the 2026-06-03 legacy/dead-code deep sweep — dead Flask routes, feature-flag terminality, and frontend dead exports — backing TRIAGE LC-05/07/09
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# Legacy Deep Sweep — Full Results (2026-06-03)

Full per-item output of the verify-gated deep sweep (44 agents) referenced by the
**Legacy / Dead-Code Cleanup batch** in [`docs/TRIAGE.md`](/docs/TRIAGE.md) (LC-05,
LC-07, LC-09). Each candidate was enumerated then adversarially verified against the
code. **These are candidates with evidence — verify intent before deleting** (some
are intentional test/external surface or near-term-planned API).

**Summary:** 24/231 routes dead · 11/16 flags terminal · 136/249 exports dead.

## Dead Flask routes (24) — zero callers

- **`GET /admin/api/summary -> api_summary`** — grep 'api/summary' across react/react/src, flask_app, poker, tests, scripts returned ONLY the route definition itself (admin_dashboard_routes.py:68). No fetch/adminFetch caller, no url_for, no test. adminFetch does not add an /admin prefix (utils/api.ts:32 fetches `${API_URL}${endpoint}` verbatim), so the exact string …
- **`POST /admin/api/playground/cleanup -> api_playground_cleanup`** — grep 'playground/cleanup' and 'api_playground_cleanup' across react, flask_app, poker, tests, scripts returned ONLY the route definition (admin_dashboard_routes.py:349,351). No React caller, no backend caller, no test. Not an SSR/health/webhook endpoint — orphaned POST AJAX endpoint.
- **`POST /api/cash/stakes/<stake_id>/default -> default_stake`** — No frontend caller: grep of entire react/ tree for 'stakes/...default', 'defaultStake', "/default'" returns nothing; api.ts has NO '/default' function. No internal backend caller: grep flask_app/poker/cash_mode finds only its own def (cash_routes.py:2668) plus a passive comment at cash_routes.py:4764. Only references t…
- **`GET /api/coach/metrics/overview`** — Zero callers. Repo-wide grep for 'metrics/overview' and function name 'coach_metrics_overview' returns only the decorator at coach_routes.py:926 and a doc table row at docs/technical/COACH_SYSTEM.md:455. No frontend fetch, no internal caller, no url_for. @_admin_required admin endpoint with no admin panel wiring (Admin…
- **`GET /api/coach/metrics/skills`** — Zero callers. grep 'metrics/skills' and 'coach_metrics_skills' returns only the decorator at coach_routes.py:939 and doc row COACH_SYSTEM.md:456. No frontend fetch, no internal caller. Admin-only endpoint with no UI consumer.
- **`GET /api/coach/metrics/advancement`** — Zero callers. grep 'metrics/advancement' and 'coach_metrics_advancement' returns only the decorator at coach_routes.py:952 and doc row COACH_SYSTEM.md:457. No frontend fetch, no internal caller. Admin-only endpoint with no UI consumer.
- **`POST /api/experiments/chat/set-type -> set_experiment_type`** — Whole-repo grep for 'set-type' returns ONLY the route definition (experiment_routes.py:1834). No frontend fetch, no Python caller, no url_for/redirect (none exist in the file). Frontend setExperimentType is a React useState setter, unrelated to the HTTP endpoint.
- **`GET /api/experiments/<int:experiment_id>/cost-trends -> get_experiment_cost_trends`** — Whole-repo grep for 'cost-trends' returns ONLY the route definition (experiment_routes.py:2638). No frontend fetch (frontend cost-trends/costTrends grep empty), no Python caller, no url_for.
- **`POST /api/experiments/<int:experiment_id>/regenerate-summary -> regenerate_summary`** — Whole-repo grep for 'regenerate-summary' returns ONLY the route definition (experiment_routes.py:2916). No frontend fetch (regenerate-summary/regenerateSummary grep empty), no Python caller, no url_for.
- **`GET /api/experiments/<int:experiment_id>/trajectory-viewer -> trajectory_viewer`** — The only frontend trajectory-viewer caller (DebugTools.tsx:293) targets a DIFFERENT route `/api/game/${gameId}/trajectory-viewer` (debug_routes.py:566), not /api/experiments/<id>/trajectory-viewer. Grep for 'experiments/.*trajectory' finds no frontend link/iframe/href to the experiments variant; no Python caller, no ur…
- **`GET /api/personality/<name> -> get_personality`** — Zero callers. Repo-wide grep for /api/personality/<name> with no suffix yields only PUT (PersonalityManager.tsx:2412 method:'PUT') and DELETE (2439 method:'DELETE') of the bare path, plus tests that PUT/DELETE only (test_personality_route_idor.py:90/98/...). No GET of the single path in react/ or tests/. Frontend loads…
- **`POST /api/prompt-debug/captures/<int:capture_id>/tags -> update_capture_tags`** — Repo-wide grep for `captures/.*tags` / `/tags'` in *.ts/*.tsx/*.js/*.py returns ONLY the route definition itself (prompt_debug_routes.py:587). No frontend fetch, no backend internal caller, no url_for, no test. Note: capture LABELS are managed via a separate blueprint (capture_label_routes.py); this tags/notes endpoint…
- **`POST /api/prompt-debug/cleanup -> cleanup_captures`** — Repo-wide grep for `prompt-debug/cleanup` / `cleanup_captures` across *.py/*.ts/*.tsx returns nothing outside prompt_debug_routes.py. Frontend `cleanup` hits are all unrelated (timer/unmount cleanup, CSS comments). No internal caller, url_for, or test. Not a health/webhook/external entrypoint.
- **`GET /api/prompt-debug/analysis -> list_decision_analyses`** — Frontend only ever fetches `/api/prompt-debug/analysis-stats` (DecisionAnalyzer.tsx:368, PromptDebugger.tsx:150); grep for `prompt-debug/analysis` shows zero hits on the bare `/analysis` list path. The only `list_decision_analyses` references (poker/repositories/decision_analysis_repository.py:199, tests/test_repositor…
- **`GET /api/prompt-debug/analysis/<int:analysis_id> -> get_decision_analysis`** — No frontend fetch of `prompt-debug/analysis/<id>` anywhere in react/react/src. `get_decision_analysis` references (decision_analysis_repository.py:141, test_decision_analysis_repository.py:55-66, and the route file's own get_capture at line 266) all call the REPOSITORY method, not this HTTP route. The detail UI renders…
- **`GET /api/game/<game_id>/decision-quality -> get_game_decision_quality`** — Repo-wide grep for `decision-quality` across *.ts/*.tsx/*.js/*.py/*.json/*.html finds zero callers of this URL; the only `decision-quality` hits are prose in code comments (tiered_bot_controller.py:497, decision_analyzer.py:970) referring to scoring, not the route. No frontend fetch (no `/game/${id}/decision-quality` c…
- **`GET /api/prompt-presets/<int:preset_id> -> get_prompt_preset`** — No caller anywhere. grep for `prompt-presets/${` in react/react/src returns only PromptPresetManager.tsx:175 (PUT) and :202 (DELETE) — neither is a GET. No `url_for('prompt_preset...)` in flask_app or frontend (grep returned nothing). No socket emit/redirect referencing the route (grep `prompt_preset.` excluding repo/b…
- **`GET /api/replay-experiments/<int:experiment_id>/captures -> get_replay_captures`** — Repo-wide grep for the URL pattern `replay-experiments.*captures` returns ONLY the route definition itself (flask_app/routes/replay_experiment_routes.py:270). No fetch/adminFetch/adminAPI call constructs this path anywhere in react/react/src. The `get_replay_experiment_captures` repo method IS called (experiments/run_r…
- **`GET /api/replay-experiments/<int:experiment_id>/captures/<int:capture_id> -> get_capture_replay_comparison`** — No frontend constructs a path of the form `/api/replay-experiments/${id}/captures/${captureId}`. Grep for `replay-experiments.*captures` and `captures/${` in react/react/src yields no match for this prefix (the only `captures/${...}` hits are the unrelated `/api/prompt-debug/captures/${...}` in DecisionAnalyzer/Interro…
- **`GET /api/models -> get_available_models`** — No frontend reference to the bare `/api/models`. All frontend model calls target the SEPARATE admin blueprint `/admin/api/models` (UnifiedSettings.tsx:191, ModelManager.tsx:46, PricingManager.tsx:381 — that path is served by admin_dashboard_routes.py:165). Frontend model config uses `/api/user-models` instead. The rout…
- **`GET /settings/<game_id> -> settings`** — Grep for `/settings/`, `url_for('stats.settings')`, `stats.settings` across react/react/src, flask_app, poker returns only the definition itself (stats_routes.py:169). Zero callers, no dynamic construction, no redirect. Docstring (line 171) says 'Deprecated: Settings are now handled in React.'
- **`POST /api/game/<game_id>/chat-suggestions -> get_chat_suggestions`** — No api.ts wrapper exists (api.ts only defines getTargetedChatSuggestions and getPostRoundChatSuggestions; no getChatSuggestions). Grep for the bare 'chat-suggestions' fetch in react/react/src yields only the targeted-/post-round- variants and CSS classnames (quick-chat-suggestions/winner-chat-suggestions). No `fetch(..…
- **`GET /api/test/snapshot/<game_id> -> snapshot_game`** — grep for `api/test/snapshot` / `test/snapshot` / `/snapshot/` / `snapshot_game` across react/react (incl. e2e), flask_app, poker, tests, scripts, docs returns ZERO references outside its own definition (flask_app/routes/test_routes.py:110) and unrelated `@vitest/snapshot` package-lock hits. The only textual mention is …
- **`GET /api/groups -> list_groups`** — grep across react/react/src, flask_app, poker, and tests/ for 'api/groups', 'list_groups', 'get_all_groups', and url_for finds only the route definition itself (flask_app/routes/user_routes.py:127,129,137) and the repo method (poker/repositories/user_repository.py:345). get_all_groups() is invoked only inside list_grou…

## Uncertain routes (18) — no static caller found, NOT confirmed dead

- `GET /admin/ -> dashboard` — No React caller (grep for 'admin/' page nav in react/react/src returned nothing) and no backend url_for/redirect to it. The only textual refs ('<a href="/admin/…
- `GET /admin/costs -> costs` — Only textual reference is '<a href="/admin/costs">Cost Analysis</a>' inside the unused _LEGACY_DEBUG_HTML string (line 1902); that string is never rendered/retu…
- `GET /admin/performance -> performance` — Only ref is '<a href="/admin/performance">' in the dead _LEGACY_DEBUG_HTML string (line 1903). No React/backend/test caller. Deliberate 'moved to React' redirec…
- `GET /admin/prompts -> prompts` — This is the PAGE route (not the /api/prompts/templates API). Only ref is '<a href="/admin/prompts">' in dead _LEGACY_DEBUG_HTML (line 1904). React calls /admin/…
- `GET /admin/models -> models` — PAGE route (not /api/models). Only ref is '<a href="/admin/models">' in dead _LEGACY_DEBUG_HTML (line 1905). React calls /admin/api/models (ModelManager.tsx:46,…
- `POST /admin/pricing/bulk -> bulk_add_pricing` — grep '/pricing' in react returned PricingManager and useAdminResource hits for /admin/pricing, /admin/pricing/providers, /admin/pricing/${id} — but NO explicit …
- `GET /admin/pricing/models/<provider> -> list_models_for_provider` — grep '/pricing' in react found /admin/pricing, /admin/pricing/providers, /admin/pricing/${id}, /admin/pricing/bulk-adjacent calls, but NO literal '/admin/pricin…
- `GET /debug -> debug_page_redirect` — No inbound reference found anywhere: grep for bare '/debug' / debug_page_redirect / navigate('/debug') across flask_app, react/react/src, tests returns only the…
- `GET /api/experiments/prompt-options -> get_prompt_options` — No frontend caller and no internal Python caller; only reference outside the route def is tests/test_experiment_routes.py:156 self.client.get('/api/experiments/…
- `POST /api/game/<game_id>/retry -> api_retry_game` — No frontend fetch and no backend/url_for caller. Only reference outside its own def (game_routes.py:2092) is tests/test_game_route_auth.py:251 which iterates al…
- `GET /messages/<game_id> -> get_messages` — No frontend fetch (messages arrive via /api/game-state and socket; no `/messages/${...}` fetch found anywhere). No internal caller. Not referenced even in the a…
- `GET /api/game/<game_id>/llm-configs -> api_game_llm_configs` — Self-described 'debug endpoint' (game_routes.py:2219). No frontend fetch and no internal caller; the only repo reference outside its def is tests/test_game_rout…
- `GET /api/avatar-stats -> get_avatar_stats` — No reference found anywhere: zero hits in react/react/src, zero in flask_app/poker (the get_avatar_stats grep hits are the repository method poker/repositories/…
- `GET /api/character-images/status/<personality_name> -> character_images_status` — No caller found: zero hits for 'character-images/status' or 'character_images_status' in react/react/src, flask_app, poker, tests, or docs. Plausibly dead, but …
- `GET /personalities -> personalities_page` — No href/redirect/url_for targets the Flask /personalities anywhere (grep for href="/personalities", '/personalities', redirect('/personalities') in *.html/*.py …
- `POST /api/personality -> create_personality` — No production frontend caller: the manual-create flow (PersonalityManager.tsx handleCreateManual:2497) only mutates local state then relies on the PUT handleSav…
- `GET /api/user-avatar/<public_id>/full -> serve_user_avatar_full` — No clean caller. The base /api/user-avatar/<id> URL is built server-side (user_avatar_service.py:56) and the frontend winner views append '/full' to any player'…
- `POST /api/test/reset -> reset_state` — react/react/e2e/helpers.ts:224 `page.request.post(`${BACKEND_URL}/api/test/reset`)` exists inside exported `resetTestState()`, so a route-level caller is wired.…

## Feature-flag terminality (16)

- **CHIP_CUSTODY_ENABLED** — `active` — Defined cash_mode/economy_flags.py:253 as `_env_flag("CHIP_CUSTODY_ENABLED", False)` — code default False. docker-compose.prod.yml has NO CHIP_CUSTODY_ENABLED entry, so prod inherits the False default (custody ledger writes off in prod). However dev docker-compose.yml:47 passes `${CHIP_CUSTODY_ENABLED:-0}` AND the repo…
- **COMMENTARY_ENABLED** — `terminal_on_dead_else` — Defined poker/config.py:42 as a hardcoded constant `COMMENTARY_ENABLED = True` with NO os.getenv override and no prod/compose/.env override anywhere. Sole guard is `if not COMMENTARY_ENABLED: return None` at poker/memory/commentary_generator.py:398 and the default at memory_manager.py:900. Since the module global is al…
- **CSRF_PROTECTION_ENABLED** — `active` — Defined flask_app/config.py:71 as `os.environ.get('CSRF_PROTECTION_ENABLED', 'false' if is_development else 'true')`. Default flips on FLASK_ENV: ON in prod (docker-compose.prod.yml:14 sets FLASK_ENV=production -> default 'true'), OFF in dev (.env.example:82 FLASK_ENV=development -> 'false') and off under tests. Enforc…
- **DOSSIER_SCOUTING_GATE_ENABLED** — `terminal_on_dead_else` — Defined cash_mode/economy_flags.py:202 as a hardcoded `bool = True` with NO _env_flag wrapper and no prod/compose/.env override. Comment says 'Flip to False to show every read immediately again (zero residual effect)'. Used at coach_routes.py:386 (`if not ...DOSSIER_SCOUTING_GATE_ENABLED: <ungated>`), character_routes.…
- **GUEST_FREE_CHAT_ENABLED** — `terminal_off_dead_then` — Defined poker/guest_limits.py:45 as `_bool_env('GUEST_FREE_CHAT_ENABLED', False)` — default False. No prod/compose/.env/.env.example/deploy.sh override (greps found none in prod config), so it is effectively always False in prod. Comment frames it as an opt-in 'escape hatch'. Used at guest_limits.py:145 `if GUEST_FREE_…
- **GUEST_LIMITS_ENABLED** — `active` — Defined poker/guest_limits.py:48 as `GUEST_LIMITS_ENABLED = not is_development_mode()` (is_development_mode reads FLASK_ENV/FLASK_DEBUG, poker/config.py:9). True in prod (docker-compose.prod.yml:14-15 FLASK_ENV=production, FLASK_DEBUG=0 -> limits enforced) and False in dev (.env.example FLASK_ENV=development). Gates re…
- **MODERATION_ENABLED** — `active` — Default "true" (opt-out semantics) read at runtime in core/moderation.py:52 is_enabled(): returns False if MODERATION_ENABLED in (0/false/no/off), ELSE returns bool(OPENAI_API_KEY). Not defined in cash_mode/economy_flags.py. Prod (docker-compose.prod.yml) does NOT set MODERATION_ENABLED, so it defaults on, but prod DOE…
- **PRESENCE_AUTHORITY_ENABLED** — `terminal_on_dead_else` — Default True (cash_mode/economy_flags.py:234, _env_flag default=True). Prod docker-compose.prod.yml sets no env for it -> committed default True. docker-compose.yml dev sets ${PRESENCE_AUTHORITY_ENABLED:-1} (default 1) and dev .env explicitly =1 with comment 'keep the authority flip durable... prod uses docker-compose.…
- **PRESENCE_SHADOW_WRITE_ENABLED** — `terminal_off_dead_then` — Default False (cash_mode/economy_flags.py:220). Prod sets no env -> False; docker-compose.yml dev uses ${...:-0}; dev .env does not set it. Crucially it is only ever consulted AFTER PRESENCE_AUTHORITY_ENABLED, which is always True: presence_transitions.py:66-75 _mode() returns 'authority' before ever testing shadow (li…
- **PRESTIGE_SEEKING_ENABLED** — `terminal_off_dead_then` — Default False (cash_mode/economy_flags.py:304, _env_flag default=False). Prod docker-compose.prod.yml sets NO env override -> committed default False in production. Only the dev .env sets PRESTIGE_SEEKING_ENABLED=1 (gitignored, dev-only). Gates the marquee-attractiveness term in lobby.py:2150 (and consulted in sim_runn…
- **RAKE_ENABLED** — `terminal_on_dead_else` — Default True (cash_mode/economy_flags.py:149), a plain module global with NO _env_flag/os.getenv hook -> not env-overridable at all. Prod and dev compose set nothing. Docs (lines 20-24) say 'Default ON' and rake recycles to the bank pool. Only branch: compute_rake() at economy_flags.py:315 'if not RAKE_ENABLED: return …
- **REGEN_ENABLED** — `terminal_off_dead_then` — Default False (cash_mode/economy_flags.py:74), a plain module global with NO env hook (no _env_flag/os.getenv). Module docstring lines 7-13 say passive regen is RETIRED in favor of SIDE_HUSTLE_ENABLED (True), 'production runs with it off'. No prod/dev compose or .env override exists. Only consumer: cash_mode/bankroll.p…
- **REPUTATION_DEMEANOR_ENABLED** — `terminal_on_dead_else` — Default: plain module constant `REPUTATION_DEMEANOR_ENABLED: bool = True` at cash_mode/economy_flags.py:175. NOT env-bound (no os.getenv/os.environ for it) and absent from .env, .env.example, .env.prod.example, docker-compose.yml, docker-compose.prod.yml, deploy.sh — so prod runs the hardcoded True. Gates flask_app/han…
- **SARCASM_DETECTION_ENABLED** — `terminal_on_dead_else` — Default: plain module constant `SARCASM_DETECTION_ENABLED = True` at flask_app/handlers/chat_relationship.py:38. NOT env-bound and absent from all prod/runtime config (env files, both compose files, deploy.sh). Gates flask_app/handlers/chat_relationship.py:47 `if not SARCASM_DETECTION_ENABLED: return True` in _perceive…
- **SIDE_HUSTLE_ENABLED** — `terminal_on_dead_else` — Default: plain module constant `SIDE_HUSTLE_ENABLED: bool = True` at cash_mode/economy_flags.py:78. NOT env-bound and absent from all prod/runtime config; the comment at line 73 even states 'production runs with it off' regarding the retired REGEN_ENABLED (=False), with side-hustle as the live replacement faucet. Gates…
- **WORLD_TICKER_ENABLED** — `active` — Genuinely env-bound: read at call time via os.environ.get('WORLD_TICKER_ENABLED', 'true').lower() != 'false' in flask_app/services/ticker_service.py:76 (is_enabled), default true. Not set in any prod/compose/env file, so prod defaults ON (the read-driven else-fallback in flask_app/routes/cash_routes.py:5372-5404 is dea…

## Frontend dead exports (136) — confirmed no real importer


### components/admin (32)

- `src/components/admin/AdminDashboard.tsx:252 default` — AdminDashboard imported only as named `{ AdminDashboard }` in AdminRoutes.tsx:4. No `import AdminDashboard from` (default) anywhere; App.tsx…
- `src/components/admin/AdminMenuContainer.tsx:69 default` — Imported only as named `{ AdminMenuContainer }` in AdminRoutes.tsx:12 and AdminDashboard.tsx:17. No default import exists. The `export defau…
- `src/components/admin/AdminSidebar.tsx:168 default` — Imported as named `{ AdminSidebar }` / `type AdminTab` in AdminRoutes.tsx, AdminDashboard.tsx, adminSidebarItems.tsx. No default import. `ex…
- `src/components/admin/CaptureSelector.tsx:819 default` — Imported as named `{ CaptureSelector }` in ReplayConfigPreview.tsx:18 and ReplayDesigner.tsx:13. No default import. `export default` is dead…
- `src/components/admin/CaptureSettings.tsx:422 default` — No importer of CaptureSettings component at all (named or default) anywhere in src; only an unrelated `captureSettings` state var in Unified…
- `src/components/admin/CashWhereaboutsPanel.tsx:374 default` — Imported as named `{ CashWhereaboutsPanel }` in AdminDashboard.tsx:12. No default import. `export default` is dead.
- `src/components/admin/DebugTools.tsx:353 default` — Imported as named `{ DebugTools }` in AdminDashboard.tsx:10. No default import. `export default` is dead.
- `src/components/admin/ModelManager.tsx:234 default` — No importer of ModelManager component at all (named or default) anywhere in src. Component is orphaned; `export default` is dead.
- `src/components/admin/PricingManager.tsx:1097 default` — Imported as named `{ PricingManager }` in UnifiedSettings.tsx:10. No default import. `export default` is dead.
- `src/components/admin/PromptPresetManager.tsx:470 default` — Imported as named `{ PromptPresetManager }` in AdminDashboard.tsx:8. No default import. `export default` is dead.
- `src/components/admin/ReplayDesigner.tsx:498 default` — No importer of ReplayDesigner component at all (named or default) anywhere in src. Component is orphaned; `export default` is dead.
- `src/components/admin/ReplayResults.tsx:415 default` — Imported as named `{ ReplayResults }` in AdminRoutes.tsx:9. No default import. `export default` is dead.
- `src/components/admin/TemplateEditor.tsx:702 default` — Imported as named `{ TemplateEditor }` in AdminDashboard.tsx:9. No default import. `export default` is dead. (Note: a different TemplateEdit…
- `src/components/admin/UnifiedSettings.tsx:1500 default` — Imported as named `{ UnifiedSettings }` / `type SettingsCategory` in AdminRoutes.tsx:11 and AdminDashboard.tsx:16. No default import. `expor…
- `src/components/admin/UserManagement.tsx:338 default` — Imported as named `{ UserManagement }` in AdminDashboard.tsx:18. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/ConfigPreview.tsx:1293 default` — ConfigPreview imported only as named `{ ConfigPreview }` in ExperimentDesigner.tsx:5 and MobileExperimentDesign.tsx:4. No default import. `e…
- `src/components/admin/ExperimentDesigner/ExperimentCard.tsx:112 default` — Imported as named `{ ExperimentCard }` in MobileExperimentList.tsx:3. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/ExperimentChat.tsx:362 default` — Imported as named `{ ExperimentChat }` (via barrel in AdminRoutes/AdminDashboard and directly in MobileExperimentDesign.tsx:3). No default i…
- `src/components/admin/ExperimentDesigner/ExperimentDesigner.tsx:462 default` — Imported as named `{ ExperimentDesigner }` via barrel in AdminRoutes.tsx:7/AdminDashboard.tsx:7. No default import. `export default` is dead…
- `src/components/admin/ExperimentDesigner/ExperimentDetail.tsx:1274 default` — Imported as named `{ ExperimentDetail }` in AdminRoutes.tsx:8 and via barrel index. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/ExperimentList.tsx:342 default` — Imported as named `{ ExperimentList }` in ExperimentDesigner.tsx:7. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/MobileExperimentDesign.tsx:166 default` — Imported as named `{ MobileExperimentDesign }` in ExperimentDesigner.tsx:8. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/MobileExperimentList.tsx:284 default` — Imported as named `{ MobileExperimentList }` in ExperimentList.tsx:10. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/ReplayConfigPreview.tsx:434 default` — Imported as named `{ ReplayConfigPreview }` in ExperimentDesigner.tsx:6. No default import. `export default` is dead.
- `src/components/admin/ExperimentDesigner/types.ts:273 ExperimentDetail` — This is the `interface ExperimentDetail` (distinct from the component). It is never imported as a type anywhere — only comments/CSS and the …
- `src/components/admin/ExperimentDesigner/types.ts:312 ChatResponse` — Interface declared at types.ts:312 but never referenced or imported anywhere (no `: ChatResponse`, `<ChatResponse`, or import). Genuinely un…
- `src/components/admin/HandReplay/index.ts:2 default` — index.ts:2 is `export { HandReplayBrowser as default }`. The only consumer (AdminDashboard.tsx:15) imports the named `{ HandReplayBrowser }`…
- `src/components/admin/HandReplay/types.ts:97 PHASE_ORDER` — `export const PHASE_ORDER` declared at HandReplay/types.ts:97; grep across all of src shows the identifier appears only in that one file (co…
- `src/components/admin/shared/FilterGroup.tsx:22 default` — Imported as named `{ FilterGroup }` in PricingManager.tsx:17 and DecisionAnalyzer.tsx:11 (used as JSX). No default import. `export default F…
- `src/components/admin/shared/FilterSheetContent.tsx:44 default` — Imported as named `{ FilterSheetContent }` in PricingManager.tsx:16 and DecisionAnalyzer.tsx:10. No default import. `export default` is dead…
- `src/components/admin/shared/MobileFilterBar.tsx:41 default` — Imported as named `{ MobileFilterBar }` in PricingManager.tsx:15, MobileExperimentList.tsx:5, DecisionAnalyzer.tsx:9. No default import. `ex…
- `src/components/admin/shared/MobileFilterSheet.tsx:97 default` — Imported as named `{ MobileFilterSheet }` in UnifiedSettings.tsx:9, PricingManager.tsx:14, MobileExperimentList.tsx:4, DecisionAnalyzer.tsx:…

### components/chat (2)

- `Chat (src/components/chat/Chat/index.ts)` — Barrel `components/chat/Chat/index.ts` does `export { Chat } from './Chat'`. Grep across react/react/src for `components/chat/Chat`, bare `C…
- `QuickChatSuggestions (src/components/chat/QuickChatSuggestions/index.ts)` — This specific barrel export is dead/shadowed, though the underlying component is live. There exist BOTH a file `components/chat/QuickChatSug…

### components/debug (32)

- `src/components/debug/DebugPanel.tsx:34 - DebugPanel` — No components/debug/index.ts barrel exists. grep for 'DebugPanel' (excluding ElasticityDebugPanel/PromptDebugger) finds only its own definit…
- `src/components/debug/ElasticityDebugPanel.tsx:33 - ElasticityDebugPanel` — grep 'ElasticityDebugPanel' across src returns only the component file itself (def + CSS import + props interface). No barrel, no external i…
- `src/components/debug/ElasticityDemo.tsx:21 - ElasticityDemo` — grep 'ElasticityDemo' returns only its own file (def + CSS import). No importer anywhere, no barrel re-export.
- `src/components/debug/PromptDebugger/index.ts:1 - PromptDebugger` — grep 'debug/PromptDebugger' for import statements returns nothing. The only references to 'PromptDebugger' outside the dir are a code commen…
- `src/components/debug/PromptDebugger/index.ts:3 - PromptCapture` — No file imports from the debug/PromptDebugger path. The 'PromptCapture' type used in the app is independently defined/exported in components…
- `src/components/debug/PromptDebugger/index.ts:4 - CaptureStats` — No importer of debug/PromptDebugger path. 'CaptureStats' used in app is a separate local interface in admin/DecisionAnalyzer/types.ts, admin…
- `src/components/debug/PromptDebugger/index.ts:5 - CaptureFilters` — No importer of debug/PromptDebugger. 'CaptureFilters' used in app is defined in admin/DecisionAnalyzer/types.ts and admin/ExperimentDesigner…
- `src/components/debug/PromptDebugger/index.ts:6 - ReplayResponse` — No importer of debug/PromptDebugger. 'ReplayResponse' used in app is defined in admin/DecisionAnalyzer/types.ts; this barrel's copy is unuse…
- `src/components/debug/PromptDebugger/index.ts:7 - ProviderInfo` — No importer of debug/PromptDebugger. App's 'ProviderInfo' originates in types/llm.ts (re-exported by admin/DecisionAnalyzer/types.ts). This …
- `src/components/debug/PromptDebugger/types.ts:9 - CaptureListResponse` — PromptDebugger/types.ts is only imported within the orphaned PromptDebugger dir (PromptDebugger.tsx), which itself has no importers. Capture…
- `src/components/debug/PromptDebugger/types.ts:13 - InterventionOperation` — Only the orphaned PromptDebugger dir references its own types.ts. InterventionOperation used in app is defined in admin/DecisionAnalyzer/typ…
- `src/components/debug/PromptDebugger/types.ts:14 - InterventionTrace` — Orphaned PromptDebugger dir only. App's InterventionTrace is defined in admin/DecisionAnalyzer/types.ts (used by PipelineTracePanel.tsx). Th…
- `src/components/debug/PromptDebugger/types.ts:15 - StrategyPipelineSnapshot` — Orphaned PromptDebugger dir only. App's StrategyPipelineSnapshot is defined in admin/DecisionAnalyzer/types.ts (used by PipelineTracePanel.t…
- `src/components/debug/PromptPlayground/index.ts:5 - PlaygroundCapture` — Barrel does 'export * from ./types', but no external file imports PlaygroundCapture from the PromptPlayground path. Only the PromptPlaygroun…
- `src/components/debug/PromptPlayground/index.ts:24 - PlaygroundCaptureDetail` — Re-exported via 'export *' but no external importer references PlaygroundCaptureDetail from this path; only used internally within the Promp…
- `src/components/debug/PromptPlayground/index.ts:47 - ConversationMessage` — No external import of ConversationMessage from PromptPlayground path. The ConversationMessage used in app is a separate definition in admin/…
- `src/components/debug/PromptPlayground/index.ts:52 - PlaygroundStats` — Re-exported via 'export *'; no external file imports PlaygroundStats from this path.
- `src/components/debug/PromptPlayground/index.ts:58 - PlaygroundFilters` — Re-exported via 'export *'; no external file imports PlaygroundFilters from this path.
- `src/components/debug/PromptPlayground/index.ts:67 - PlaygroundListResponse` — Re-exported via 'export *'; no external file imports PlaygroundListResponse from this path.
- `src/components/debug/PromptPlayground/index.ts:74 - ReplayResponse` — Re-exported via 'export *'; no external importer from PromptPlayground path. App's ReplayResponse is defined in admin/DecisionAnalyzer/types…
- `src/components/debug/PromptPlayground/index.ts:89 - PlaygroundMode` — Re-exported via 'export *'; no external file imports PlaygroundMode from this path.
- `src/components/debug/PromptPlayground/index.ts:92 - TemplateSummary` — No external import of TemplateSummary from PromptPlayground path. The TemplateSummary used in app is a separate local interface in admin/Tem…
- `src/components/debug/PromptPlayground/index.ts:100 - PromptTemplate` — Re-exported via 'export *'; no external file imports PromptTemplate from this path.
- `src/components/debug/PromptPlayground/index.ts:108 - TemplatePreviewResponse` — Re-exported via 'export *'; no external file imports TemplatePreviewResponse from this path.
- `src/components/debug/PromptPlayground/index.ts:116 - TemplateUpdateResponse` — Re-exported via 'export *'; no external file imports TemplateUpdateResponse from this path.
- `src/components/debug/PromptPlayground/index.ts:125 - ImageReplayResponse` — Re-exported via 'export *'; no external file imports ImageReplayResponse from this path.
- `src/components/debug/PromptPlayground/index.ts:137 - ImageProvider` — Re-exported via 'export *'; no external import of ImageProvider from PromptPlayground path (the admin/UnifiedSettings.tsx matches are unrela…
- `src/components/debug/PromptPlayground/index.ts:144 - ImageModel` — Re-exported via 'export *'; no external import of ImageModel from PromptPlayground path.
- `src/components/debug/PromptPlayground/index.ts:150 - SizePreset` — Re-exported via 'export *'; no external file imports SizePreset from this path.
- `src/components/debug/PromptPlayground/PromptPlayground.tsx:914 - default` — PromptPlayground is consumed as a NAMED export via index.ts ('export { PromptPlayground } from ./PromptPlayground'; AdminDashboard imports {…
- `src/components/debug/PromptPlayground/ReferenceImageInput.tsx:253 - default` — ReferenceImageInput is imported as a NAMED export by PromptPlayground.tsx:13 ('import { ReferenceImageInput }'). The default export at line …
- `src/components/debug/PromptPlayground/TemplateEditor.tsx:407 - default` — The debug PromptPlayground/TemplateEditor.tsx has ZERO importers anywhere (neither named nor default; AdminDashboard imports a different fil…

### components/settings (3)

- `src/components/settings/CoachSettings.tsx:127 - default` — CoachSettings is used live, but only via its NAMED export: SettingsPage.tsx:6 `import { CoachSettings } from './CoachSettings'` and rendered…
- `src/components/settings/GameplaySettings.tsx:120 - default` — GameplaySettings is live via its NAMED export only: SettingsPage.tsx:5 `import { GameplaySettings } from './GameplaySettings'`, rendered at …
- `src/components/settings/ProfileSettings.tsx:354 - default` — ProfileSettings is live via its NAMED export only: SettingsPage.tsx:4 `import { ProfileSettings } from './ProfileSettings'`, rendered at lin…

### components/stats (1)

- `PressureStats (src/components/stats/index.ts:1)` — grep of react/react/src finds no importer of the 'components/stats' barrel nor of './PressureStats' directly. Hits are only: the component's…

### misc (66)

- `CARD_REVEAL_DELAY_MS` — Defined at src/config/timing.ts:19. Grep across react/react/src for the symbol returns only the declaration line — zero importers. config/ h…
- `WINNER_DISMISS_MS` — Defined at src/config/timing.ts:20. Case-insensitive grep for winner_dismiss across react/react/src matches only the declaration. No config …
- `WINNER_DISMISS_SHOWDOWN_MS` — Defined at src/config/timing.ts:21. Grep matches only the declaration line; no importers anywhere in react/react/src. No config/index.ts bar…
- `GAME_MODES` — Defined at react/react/src/constants/gameModes.ts:22. grep across react/react/src finds GAME_MODES only at its definition site (line 22) plu…
- `useAdminMutation` — grep across react/react/src for 'useAdminMutation' returns only its definition (hooks/useAdminResource.ts:135) and a JSDoc @example comment …
- `useGameState` — grep for 'useGameState' returns only the definition at hooks/useGameState.ts:40. No importers, no module-path imports ('useGameState''), no …
- `useMediaQuery` — grep for 'useMediaQuery' returns only the definition at hooks/useMediaQuery.ts:10. No importers, no module-path imports, no hooks/index.ts b…
- `usePolling` — grep for 'usePolling' returns only the definition at hooks/usePolling.ts:8. No importers, no module-path imports, no barrel, no test referen…
- `useSocket` — grep for 'useSocket' returns only the definition at hooks/useSocket.ts:21 plus one code COMMENT in components/cash/Lobby.tsx:76 ('matches us…
- `PlayerShowdownInfo` — types/game.ts:105 export has zero importers (grep for imports returned nothing). The PlayerShowdownInfo usages in WinnerAnnouncement.tsx:24 …
- `PotBreakdown` — types/game.ts:114 export has zero importers. Usages at WinnerAnnouncement.tsx:32 and MobileWinnerAnnouncement.tsx:27 are LOCAL `interface Po…
- `WinnerInfoAlt` — types/game.ts:142 export has zero references anywhere except its own definition (grep -rnw returned only the declaration line). Comment says…
- `PlayerSummary` — types/stats.ts:1 export has zero importers. The PlayerSummary at components/stats/PressureStats.tsx:8 is a LOCAL interface redeclaration. On…
- `LeaderboardEntry` — types/stats.ts:17 export has zero importers anywhere. Only referenced internally by SessionSummary (stats.ts:28-32), which is itself never i…
- `SessionSummary` — types/stats.ts:22 export has zero importers. All SessionSummary imports point to components/cash/CashOutSummary (a different, separately-def…
- `getActivePack` — Only appears at definition site utils/cards.ts:53. No importer imports it; useDeckPack.tsx imports setActivePack and UnifiedSettings.tsx imp…
- `createDeck` — Only at utils/cards.ts:144. Zero importers across src (grep found only definition). No barrel re-export, no dynamic use.
- `shuffleDeck` — Only at utils/cards.ts:194. No importers; no re-export; no dynamic/JSX references.
- `cardToString` — Only at utils/cards.ts:244. No importers anywhere; not re-exported via cards/index.ts; no string/dynamic refs.
- `drawCard` — Only at utils/cards.ts:249. No importers; no barrel re-export; no dynamic use. (Card.tsx imports parseCard/cardFromBackend/getCardImagePathF…
- `drawCards` — Only at utils/cards.ts:258. No importers; no re-export; no dynamic/JSX references.
- `formatNumber` — Only at utils/formatters.ts:44. Consumers of utils/formatters import formatDate/formatLatency/formatCost/formatCompactCurrency/truncate but …
- `formatPercent` — Only at utils/formatters.ts:51. No importers across src; consumers import other formatters helpers only. No re-export, no dynamic/JSX refs.
- `DeckCard` — Defined at src/components/cards/Card.tsx:115. grep across src finds only the definition line — no other reference. NOT re-exported in src/co…
- `submitAction` — src/components/cash/api.ts:86. Word-boundary grep across all .ts/.tsx in src/ returns zero importers outside api.ts. No index.ts barrel exis…
- `topUp` — src/components/cash/api.ts:90. The exported async helper has zero importers. The earlier 'topUp' grep hits are all the unrelated local varia…
- `CashModeEntry` — src/components/cash/CashModeEntry.tsx:52. Zero JSX/import references. The only two hits are JSDoc comments: Lobby.tsx:4 ('Replaces CashModeE…
- `CashTableState` — src/components/cash/types.ts:13. Word-boundary grep across src/ returns zero importers outside types.ts. Checked multi-line `} from './types…
- `PlayerBankrollState` — src/components/cash/types.ts:25. Zero importers anywhere in src/ (word-boundary grep). Not present in any multi-line type-import block from …
- `LobbyTick` — src/components/cash/types.ts:496. Zero importers in src/. Distinct from `LobbyEvent`/`LobbyTable`/`LobbySeat` which ARE imported (interhandT…
- `StakeOfferRequest` — src/components/cash/types.ts:671. Zero importers in src/. StakeOfferModal.tsx imports other names from './types' and `offerStake` from './ap…
- `CharacterDetailCardProps` — Only defined and used internally in components/character/CharacterDetailCard.tsx (declared line 77, used as the props annotation line 359). …
- `RelationshipKind` — Only referenced inside components/character/CharacterDetailCard.tsx (def line 30; used 68, 119). No external or barrel consumer. The re-expo…
- `CharacterCardPreview` — No importer anywhere and no route wired — the only mention of a route is a comment example in CharacterCardPreview.tsx:5 ('/preview/dossier'…
- `dossierFromLobbySeat` — Only defined in components/character/dossierFromPlayer.ts:79. No importer via barrel or direct module path anywhere in src. The re-export is…
- `LobbyAISeat` — Only used internally in components/character/dossierFromPlayer.ts as the parameter type of the (itself dead) dossierFromLobbySeat (def line …
- `PersonalityBlock` — Only used internally in components/character/dossierFromPlayer.ts (def line 42, used 36 and 54). No external or barrel importer.
- `DossierRelationship` — Only used internally within components/character/api.ts (def line 13, used line 257 as a field of DossierResponse). No importer of the symbo…
- `DossierCashPairStats` — Only used internally in components/character/api.ts (def line 21, used line 258 inside DossierResponse). No external/barrel importer.
- `DossierPersonality` — Only used internally in components/character/api.ts (def line 38, used line 234 inside DossierResponse). No importer via barrel or direct pa…
- `MobileActionButtons` — The barrel re-export line (index.ts:2) is consumed by no one: the only import of the mobile barrel ('../mobile') is ResponsiveGameLayout.tsx…
- `FloatingChat` — The barrel re-export line (index.ts:3) is unused. Real importers go directly to the file: components/mobile/MobilePokerTable.tsx:9 `import {…
- `MobileWinnerAnnouncement` — The barrel re-export line (index.ts:4) is unused. Real importers go directly to the file: components/mobile/MobilePokerTable.tsx:10, compone…
- `BackButtonProps` — Exported as type via shared/index.ts:9 but 0 external refs anywhere; only used locally inside BackButton.tsx as its own prop type. No import…
- `BottomSheetProps` — Type re-exported at shared/index.ts:12 but 0 external refs; used only locally within BottomSheet.tsx.
- `PageHeaderProps` — Type re-exported at shared/index.ts:15 but 0 external refs; used only locally in PageHeader.tsx.
- `MobileHeader` — Defined/exported in shared/MobileHeader.tsx and re-exported at shared/index.ts:17, but grep finds 0 references (no imports, no JSX usage) an…
- `ChatToggle` — Defined in shared/MobileHeader.tsx:161, re-exported at shared/index.ts:17, but 0 references anywhere outside its own file and the barrel. No…
- `MobileHeaderProps` — Type re-exported at shared/index.ts:19 but 0 external refs; used only locally in MobileHeader.tsx as the MobileHeader param type (and Mobile…
- `PotDisplayProps` — Type re-exported at shared/index.ts:20 but 0 external refs; used only locally in MobileHeader.tsx as PotDisplay's param type.
- `ChatToggleProps` — Type re-exported at shared/index.ts:21 but 0 external refs; used only locally in MobileHeader.tsx as ChatToggle's param type.
- `GameInfoDisplayProps` — Type re-exported at shared/index.ts:22 but 0 external refs; used only locally in MobileHeader.tsx as GameInfoDisplay's param type.
- `UserBadge` — Defined in shared/UserBadge.tsx, re-exported at shared/index.ts:25, but grep finds 0 references (imports or JSX) anywhere outside its own fi…
- `UserBadgeProps` — Type re-exported at shared/index.ts:26 but 0 external refs; only local to UserBadge.tsx (itself dead).
- `PageLayoutProps` — Type re-exported at shared/index.ts:29 but 0 external refs; used only locally in PageLayout.tsx.
- `MenuBarProps` — Type re-exported at shared/index.ts:32 but 0 external refs; used only locally in MenuBar.tsx.
- `ResponsiveGameLayoutProps` — Type re-exported at shared/index.ts:35 but 0 external refs; used only locally in ResponsiveGameLayout.tsx.
- `ThemedSelect` — Defined in shared/ThemedSelect.tsx, re-exported (named) at shared/index.ts:37, but grep finds 0 references anywhere outside its own file and…
- `ThemedSelectProps` — Type re-exported at shared/index.ts:38 but 0 external refs; only local to ThemedSelect.tsx (itself dead).
- `UserDropdownProps` — Type re-exported at shared/index.ts:41 but 0 external refs; used only locally in UserDropdown.tsx.
- `ActionBadgeProps` — Type re-exported at shared/index.ts:46 but 0 external refs; used only locally in ActionBadge.tsx.
- `UpgradeBannerProps` — Type re-exported at shared/index.ts:51 but 0 external refs; used only locally in UpgradeBanner.tsx.
- `GuestLimitModalProps` — Type re-exported at shared/index.ts:54 but 0 external refs; used only locally in GuestLimitModal.tsx.
- `default` — ThemedSelect.tsx:43 'export default ThemedSelect' — no default import of ThemedSelect anywhere; grep finds 0 references to ThemedSelect outs…
- `LoadingIndicator` — Zero importers anywhere in src. grep for LoadingIndicator outside its own directory returns nothing; no dynamic/string usage; no test import…
- `quotesForMood` — Defined in quote-flavor.ts:425 but never imported anywhere. grep finds only the definition line; consumers of quote-flavor.ts import `pickQu…
