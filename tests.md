# FluxFTP test status

Collected on 2026-11-19: 1148 test cases.

## Test policy

- When resuming FluxFTP work, review `TODO.md` and this test list first.
- For a localized code change, run the affected tests and related tests in that category.
- For broad but non-major revisions, run affected tests in manageable batches; a release alone does not require the full suite.
- Run the full suite at major version transitions (for example, `0.x` to `1.0` or `1.x` to `2.0`) and for a major language rewrite (for example, Python to Rust).
- Before a release, check every test's history. Mark unchanged tests with a previous pass successful for the release without rerunning them; run tests that have never run and record the result; rerun tests affected by code or coverage changes. A previous failure remains failed until a rerun passes.
- Work through the categories below in batches. Status reflects the latest recorded batch; unrun cases remain pending.
- Local API tests bind an OS-assigned ephemeral loopback port (`port=0`) so they do not collide with V:/FluxFTP on port 8765.
- The two 100,001-item queue stress tests passed on 2026-11-19; the separate incremental capacity map remains open in `TODO.md`.

Current totals: 1145 passed, 1 failed, 2 skipped, 0 pending, 0 deferred.

## Artifact provider OAuth batch (2026-11-21)

Added one-time OAuth device sign-in to the GitHub Actions and GitLab pipeline
artifact pickers. The provider opens its HTTPS verification page, FluxFTP shows
the short-lived user code, and a successful sign-in can be kept in the encrypted
credential vault. Client IDs are stored in user preferences; access tokens are
only stored in the encrypted credential vault. Existing manual token entry is
still available.

```text
.venv-test/Scripts/python.exe -m pytest -q tests/test_artifact_oauth.py tests/test_github_artifacts.py tests/test_gitlab_artifacts.py
19 passed in 10.25s (including expiring-token refresh and rotation coverage)
```

Focused OAuth/client/archive/picker regression batch; the historical full-suite
totals above are unchanged. Live OAuth authorization still requires registered
provider applications and project access.

The user then chose GitHub's expiring-token setting for the live setup. FluxFTP
now requests `offline_access`, keeps the access/refresh pair only in the encrypted
vault, and rotates both tokens when an access token nears expiry. The refresh
path has dedicated mocked coverage in the rerun above.

## CLI/API tests away from the live FluxFTP port (2026-11-21)

The first attempt to pin the test listener to 8766 produced two
`RemoteDisconnected` failures. Switched the tests to port 0, which asks Windows
to assign an available loopback port and keeps pytest separate from V:'s
listener on 8765. Latest result:

```text
.venv-test/Scripts/python.exe -m pytest -q tests/test_cli_api.py
3 passed in 8.44s
```

The latest passing port-0 run supersedes the failed fixed-8766 attempt.

## Auto-trading implementation batch (2026-11-21)

Focused batch covering `.fluxftprc` validation and matching, ledger lifecycle,
manifest and marker behavior, automatic queue priority and serialization, plus
the authenticated IRC bridge including HexChat form-encoded POST intake:

```text
.venv-test/Scripts/python.exe -m pytest tests/test_auto_trading_config.py tests/test_trade_ledger.py tests/test_queue_engine.py tests/test_queue_storage_service.py tests/test_cli_api.py -q
50 passed in 10.06s
```

An initial run had 48 passes and one teardown failure because the test replaced
the window's trading service with a stub and did not restore it before closing;
the test now restores the actual service, and the rerun passed. This focused
batch does not alter the historical full-suite totals above.

## Desktop features batch (2026-11-21)

Added deployment revision text snapshots/comparison and workspace preset saving,
application and switching. Test history for the affected areas:

```text
.venv-test/Scripts/python.exe -m pytest tests/test_deployment_compare.py tests/test_deployments.py -q
12 passed in 18.84s

.venv-test/Scripts/python.exe -m pytest tests/test_tools_menu.py tests/test_workspace_presets.py tests/test_deployment_compare.py -q
8 passed in 28.67s

.venv-test/Scripts/python.exe -m pytest tests/test_workspace_presets.py tests/test_deployment_compare.py tests/test_deployments.py tests/test_tools_menu.py -q
17 passed in 53.18s

.venv-test/Scripts/python.exe -m pytest tests/test_deployment_compare.py tests/test_deployments.py tests/test_workspace_presets.py tests/test_workspaces_tray.py tests/test_workspace_site_names.py tests/test_tools_menu.py -q
36 passed in 324.76s

.venv-test/Scripts/python.exe -m pytest tests/test_workspace_presets.py tests/test_workspaces_tray.py tests/test_workspace_site_names.py -q
20 passed in 200.60s
```

An intermediate deployment run exposed that valid UTF-8 files without extensions
were not captured for text diffs. The bounded snapshot selector now includes
extensionless files, and the deployment batch rerun passed. These focused runs
do not change the historical full-suite totals above. Live GitLab/GitHub project
artifact validation remains open and requires project access and credentials.
An earlier combined run was interrupted after the new saved-site preset test
attempted to reconnect its remote pane. The test fixture now keeps that pane in
Local mode while preserving its saved remote site reference; the complete
deployment/workspace/menu batch above passes.

Security review, 2026-11-20: copied the published 0.148.0 Windows package to
`build/qt-prune-test-0.148.0`, removed QtQml, QtQmlMeta, QtQmlModels,
QtQmlWorkerScript, QtQuick and QtNetwork DLLs/bindings, plus Qt Network plugins,
from that isolated copy, then ran packaged `--smoke-test` and `--f6-smoke-test`.
Both exited 0. This is a focused packaging probe, not a complete regression
pass; the published package and local installation were not modified.

Windows Qt Network packaging candidate, 2026-11-21: repackaged an isolated
copy of the published 0.148.0 Windows bundle with the updated packager. The
candidate ZIP contains no Qt Network DLLs, Python bindings, or Network/TLS
plugins; archive integrity passed. Packaged `--smoke-test` and
`--f6-smoke-test` both exited 0. This validates the Network exclusion in the
Windows package layout; it does not validate replacement patched Qt runtime
binaries or Linux packaging.

Qt 6.11.3 CVE-2026-116337 source backport, 2026-11-21: built QtBase's
`tst_qhttpnetworkreply` target (522 Ninja steps) on Ubuntu 24.04/GCC 13.3 with
OpenSSL and GUI modules disabled. Batch 1 (`headerSizeLimit`,
`statusLineSizeLimit`): 8 cases passed. Batch 2 (`parseHeaderVerification`,
`parseEndOfHeader`): 20 cases passed. Batch 3 (`parseHeader`): 9 cases passed.
All 37 cases passed; no failures or skips. This verifies the patched source and
reader behavior, not production PySide6 library compatibility. Patch hashes,
build settings, and adaptation notes are in `scripts/qt-patches/6.11.3/README.md`.

The RushApp compatibility batch passed all 6 focused tests on 2026-11-18, including the existing CLI/API test extended to exercise `rush-script`, plus parser safety, upload/download/FXP routing, path parameters, and the `rush_cmdline` forwarding entry point.

The panel/window layout persistence batch passed all 8 focused tests on 2026-11-18: dock/pane restoration, dock float/tab/hide behavior, docking reset and new-window restoration, floating geometry, and main/dialog geometry persistence.

The Debian debug/F6 batch passed 10 focused Windows Qt tests on 2026-11-19, including the Tools-menu logging flow, Qt warning capture, frozen `_ssl` fallback, and F6 from the queue, logs, and both panes. After the smoke-test shutdown path was corrected, a fresh 0.147.5 Debian package built on the updated Ubuntu bundle passed extracted GUI smoke, debug logging, and F6 shortcut checks on Debian 13 with Qt 6.11.3. The debug log recorded Debian 13, Python 3.13.18, OpenSSL 3.5.9, and one offscreen virtual 800×800 display. F6 uses application shortcut scope and has no monitor-detection dependency; the one-screen packaged test passed, though the original single-monitor report was not reproducible on an interactive GNOME/Wayland desktop here. SonarQube scan passed on 2026-11-19: quality gate OK, 0 new issues, 0 hotspots.

The installed Windows icon check confirmed that `D:\Program Files\FluxFTP\fluxftp.exe` embeds a 32×32 icon that matches `fluxftp/assets/fluxftp.ico` pixel-for-pixel. The diagnostics UI extraction batch passed 10 focused tests across `tests/test_tools_menu.py`, `tests/test_diagnostics.py`, and `tests/test_window_initializer.py` on 2026-11-19.

The deployment/transfer modularization batch split guarded deployment journal/rollback operations into `deployment_rollback.py` and recursive local/remote transfer-tree orchestration into `transfer_service.py`. The focused deployment/transfer batch passed 137 tests. The separate 100,001-item queue search/recovery/cancel regression passed with 7.30 s planning, 10.43 MiB incremental RSS, 1.47 s export, 3.77 s recovery, and 0.95 s cancellation. The import/export/recovery/folder-scan regression also passed; its largest measured GUI event-loop gap was 0.547 s. The broader exploratory batch also exposed one unrelated current workspace-mode assertion failure, recorded per test below. SonarQube Quality Gate: OK, 0 new issues and 0 hotspots.

The automation engine modularization batch moved script schema validation, saved connection preparation, and step execution into `automation_scripts.py`; the focused `tests/test_automation.py` batch passed all 22 tests on 2026-11-19.

The cloud backend modularization batch split provider/path/error utilities, encrypted account persistence and staged edits, and asynchronous backend job coordination into `cloud_registry.py`, `cloud_account_store.py`, and `cloud_jobs.py`. The focused cloud, account, Drive upload, and protocol-adapter batch passed 97 tests on 2026-11-19.

The Preferences connection modularization batch extracted Ident, list mode, cloud/key manager, SSL, connection, data connection, keep-alive and proxy pages plus proxy-profile lifecycle helpers into `preferences_connection_pages.py`. The focused options, list-mode, Ident and SSL batch passed 23 tests on 2026-11-19.

The Preferences display modularization batch extracted nine visual preference pages into `preferences_display_pages.py` and avoids duplicate theme/color refreshes when a changed combo selection emits both `currentTextChanged` and `textActivated`. Reselecting a preset still applies it. The 33-case focused batch passed after adjusting the reselect test to process the queued Qt event between change and reactivation; the new test checks both deduplication and same-preset reapplication. One initial failure exposed that missing event-loop step in the synthetic test; it and the new test both passed on focused rerun. SonarQube quality gate: OK, 0 new issues and 0 hotspots.

The security audit dependency update passed all 19 tests in `tests/test_connection_controls.py` on 2026-11-18, including the new check that blocks discovery through vulnerable miniupnpc versions. PySide6 6.11.3 and setuptools 84.2.1 were installed in the test environment; `pip-audit -r requirements-windows.txt` reported no known vulnerabilities.

The Windows UPnP build batch verified and built miniupnpc 2.3.5 for CPython 3.13 x64 and the local CPython 3.11 test environment. Five upstream response-parser fixtures and all 19 `tests/test_connection_controls.py` cases passed with the patched wheel installed; `pip-audit -r requirements-windows.txt` reported no known vulnerabilities.

The OpenSSL version policy batch passed 33 focused runtime/updater tests on 2026-11-18. Two environment-dependent checks were skipped: the live DLL negotiation test requires CPython 3.13, and the official 4.1 MSI fixture is not cached. The supported 3.x LTS baseline is now 3.5.9; updater selection ignores the end-of-life 3.6 line.

The security audit follow-up on 2026-11-18 passed Python syntax compilation for both release packagers and generated a temporary Windows release archive. Inspection confirmed that the archive labels the dependency inventory as a build-environment license inventory (including test tools), points to Qt 6.11.3 notices, and no longer carries the stale 6.11.2 notice. The temporary archive was removed after inspection. `pip-audit` against the pinned Windows requirements with the locally built CPython 3.11 miniupnpc 2.3.5 wheel reported no known vulnerabilities.

The 0.147.4 native-package audit on 2026-11-18 found duplicate root-level Qt PDF and VirtualKeyboard libraries in both published Linux packages. `scripts/qt_package_prune.py` now removes nested and root-level copies in the portable tarball and Debian packager. FluxFTP 0.147.5 was published with the pruning applied; Ubuntu and Debian package workflow smoke checks passed.

The Linux SQLite packaging follow-up built SQLite 3.54.1 from the official source archive after verifying its SHA3-256. The source-build check confirmed the staged library version and recorded SHA-256. The published 0.147.5 Ubuntu and Debian packages passed their platform packaging and smoke workflows with the staged library.

The local Linux Python runtime check downloaded the immutable CPython 3.13.18 install-only archive, verified its pinned SHA-256, extracted it under the ignored FluxFTP `runtime/` directory, and confirmed `ssl`, `sqlite3`, and `ctypes` imports. It reported OpenSSL 3.5.9 and SQLite 3.53.1. The Linux build venv was recreated from this local interpreter and reported Python 3.13.18 with `sys.base_prefix` inside `runtime/`; no system Python or PATH changes were made. A full PyInstaller package build with this runtime remains to be verified.

The 0.147.5 release-focused Windows batch passed 77 tests with 2 environment-dependent skips under the local CPython 3.13.18 runtime; the only warnings were 12 pyftpdlib timestamp deprecations. The patched miniupnpc 2.3.5 wheel built locally and all 5 upstream parser fixtures passed. The maintained Windows package built with OpenSSL 4.1.0 as default, OpenSSL 3.5.9 as fallback, and SQLite 3.54.1. Both packaged smoke checks passed before and after installing the published build. The Ubuntu portable package and Debian 13 installer passed their release workflow build and smoke checks. All public release assets matched their checksums.

The 0.147.4 release-focused Windows batch passed 78 tests with one environment-dependent skip. The real OpenSSL 3.5.9 pair test had been skipped previously; now that its DLL fixture was available, it exposed a test setup error that claimed the actual 3.5.9 DLLs were 3.5.9. The fixture now claims 3.5.10, and the mismatch check plus the full focused batch passed.

The SQLite 3.54.1 runtime update passed all 9 `tests/test_transfer_history.py` cases on 2026-11-18. The additional queue-buffer regression batch passed 8 cases and exposed 2 stale column assertions; both were corrected and rerun successfully. The packaged Windows smoke test passed with SQLite 3.54.1.

The two stale queue-buffer assertions were updated to locate the Files and Remark columns by header. Both focused reruns passed on 2026-11-18.

The rclone security update passed its 2 patched Go regression tests and all 20 focused Python cloud tests on 2026-11-18. rclone v1.76.0 was rebuilt with Go 1.26.11; `govulncheck ./...` reported no reachable vulnerabilities.

The left-sidebar rendering tests passed. The separate localized batches completed: `tests/test_view_options.py` had 7 passes and one failure (`test_auto_size_manual_width_and_mode_restoration`); `tests/test_layout.py` had 6 passes and one failure (`test_native_tabs_and_dropdown_navigation`); and `tests/test_workspaces_tray.py` passed all 15 tests. On 2026-11-18, both failures were fixed and rerun successfully; the related toolbar-visibility tests also passed (4 focused tests total).

## Previous test history

The archived `build/test-batches/nodeids.txt` contains 1,120 unique IDs. The current list has 1,144 tests: nine previously added IDs, two updater-security tests, two automatic-update workflow tests, the F6 Options regression test, five copy-text/hotkey-manager tests, one sidebar-rendering test, five RushApp compatibility tests, the UPnP vulnerable-parser guard, and two retired archived IDs. The older TODO aggregate reports 1,115 cases, five fewer than that saved ID inventory; this page uses the per-ID inventory and individual batch evidence. Archived results contained 14 failures and three skips. Thirteen failures were rerun and passed for this release; both 100,001-item queue tests are deferred by request pending the incremental queue stress plan; the UI-search test has a prior batch-14 pass.

### Individual historical failures

These failed in earlier batches. Their current release status appears in the final column.

| Test | Earlier result | Current recorded status |
|---|---|---|
| `tests/test_features_0117.py::test_feature_dialog_previews` | Failed in batch 06; screenshot save failed | Passed (release validation 2026-11-16) |
| `tests/test_mouse.py::test_log_actions_and_empty_tabs` | Failed in batch 11 | Passed (release validation 2026-11-16) |
| `tests/test_openssl_updates.py::test_real_windows_pair_loads_and_negotiates_verified_tls` | Failed in batch 12; Python 3.13 OpenSSL DLL unavailable in that runtime | Passed (0.147.4 release-focused batch 2026-11-18; corrected the mismatching manifest fixture) |
| `tests/test_panel_positions.py::test_moved_panels_and_panes_restore_on_next_window` | Failed in batch 13 | Passed (release validation 2026-11-16) |
| `tests/test_panel_positions.py::test_panel_order_collapsed_log_and_bad_state` | Failed in batch 13 | Passed (release validation 2026-11-16) |
| `tests/test_queue_async.py::test_100001_import_export_recovery_and_expansion_remain_responsive` | Failed timing threshold in batch 14 (0.848 s vs 0.75 s); TODO records 1.813 s in another run; isolated reruns passed | Passed (deferred queue regression batch 2026-11-19; 100,001 items; max GUI event-loop gap 0.547 s) |
| `tests/test_themes.py::test_legacy_dark_migration_and_compact_add_button` | Failed in batch 20 | Passed (release validation 2026-11-16) |
| `tests/test_toolbar_visibility.py::test_named_hide_restore_and_shared_rows` | Failed in batch 20 | Passed (release validation 2026-11-16) |
| `tests/test_ui.py::test_reference_style_shell_exposes_quick_connect_and_classic_work_areas` | Failed in batch 21 | Passed (release validation 2026-11-16) |
| `tests/test_view_menu.py::test_view_menu_reference_order_shortcuts_and_submenus` | Failed in batch 22 | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_panel_shortcuts_reveal_focus_and_restore_collapsed_panels` | Failed in batch 22 | Passed (menu regression batch 2026-11-18) |
| `tests/test_window_positions.py::test_main_window_geometry_saved_and_smoke_does_not_overwrite` | Failed in batch 22; a later isolated rerun passed | Passed (release validation 2026-11-16) |
| `tests/test_window_positions.py::test_manual_collapsed_queue_not_expanded_for_transfer` | Failed in batch 23 | Passed (release validation 2026-11-16) |
| `tests/test_workspaces_tray.py::test_reset_layout_preserves_work_and_names` | Failed in batch 23 | Passed (release validation 2026-11-16) |

### Test ID changes since the archived Nov. 9 list

The archived [`nodeids.txt`](build/test-batches/nodeids.txt), dated Nov. 9, contains 1,120 unique test IDs. Comparing it with today's 1,144 categorized IDs shows 26 added IDs and 2 IDs no longer present (net increase: 24). The TODO's earlier aggregate says 1,115 tests, a five-test discrepancy with the archived ID list; the direct ID comparison below uses the saved 1,120-ID inventory.

| Newly collected test | Latest recorded status |
|---|---|
| `tests/test_compare_menu.py::test_pane_filter_toolbar_can_expand` | Passed (previous targeted batch; carried forward) |
| `tests/test_confirm.py::test_title_bar_close_does_not_wait_for_exit_confirmation` | Passed (previous targeted batch; carried forward) |
| `tests/test_confirm.py::test_title_bar_close_requests_cancellation_of_active_transfer` | Passed (previous targeted batch; carried forward) |
| `tests/test_docking.py::test_floating_dock_geometry_is_saved_and_restored` | Passed (previous targeted batch; carried forward) |
| `tests/test_folder_sizes.py::test_directory_size_column_shows_reported_bytes_immediately` | Passed (previous targeted batch; carried forward) |
| `tests/test_folder_sizes.py::test_empty_directory_has_parent_row_that_navigates_up` | Passed (previous targeted batch; carried forward) |
| `tests/test_layout.py::test_portrait_layout_and_site_local_order` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_queue.py::test_paused_queue_close_closes_even_when_save_fails` | Passed (previous targeted batch; carried forward) |
| `tests/test_site_validation.py::test_new_site_defaults_nat_masquerade_off` | Passed (previous targeted batch; carried forward) |
| `tests/test_app_updates.py::test_downloaded_archive_digest_is_rechecked_in_private_windows_staging` | Passed (release validation 2026-11-16) |
| `tests/test_app_updates.py::test_windows_updater_directory_acl_blocks_inheritance_and_low_integrity_writes` | Passed (release validation 2026-11-16) |
| `tests/test_app_updates.py::test_available_release_downloads_automatically_without_prompt` | Passed (updater workflow batch 2026-11-16) |
| `tests/test_app_updates.py::test_verified_download_is_held_until_application_exit` | Passed (updater workflow batch 2026-11-16) |
| `tests/test_view_menu.py::test_function_key_commands_include_f6_options` | Passed (menu regression batch 2026-11-18) |
| `tests/test_tools_menu.py::test_tools_debug_logging_starts_and_exposes_log_path` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_diagnostics.py::test_debug_logging_supports_frozen_ssl_module_without_file_attribute` | Passed (Debian debug/F6 batch 2026-11-19) |
| `tests/test_accessibility.py::test_read_only_text_and_static_labels_can_be_selected_and_copied` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_manager_lists_actions_scopes_and_searches` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_shortcuts_persist_clear_and_restore_factory_defaults` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_conflicting_shortcuts_are_explained_and_not_applied` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_key_sequence_editor_can_clear_selected_shortcut` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_rush_compat.py::test_queue_script_maps_download_fxp_and_bookmark_paths` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_queue_script_resolves_paths_and_uses_transfer_queue` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_rush_cmdline_forwards_script_and_path_parameters` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_transfer_parser_accepts_flags_parameters_and_pascal_quotes` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_transfer_parser_rejects_unknown_flags_options_and_extra_code` | Passed (RushApp compatibility batch 2026-11-18) |

The following IDs were in the archived list but are no longer in today's collection; this records an ID difference and does not by itself establish whether the tests were deleted or renamed:

- `tests/test_confirm.py::test_exit_accept_cancel_and_disabled`
- `tests/test_queue.py::test_paused_queue_close_requires_successful_save`

## Batch history

- Archived batches 01–14: 700 cases, 692 passed, 6 failed, 2 skipped.
- Archived batches 15–19: 250 cases, 249 passed, 1 skipped; the missing batch-15 layout-lock result was later passed in the focused release batch.
- Archived batches 20–23: 170 cases, 162 passed, 8 failed.
- Release-focused rerun: 14 passed. Raw JUnit: `C:\codex\FluxFTP-Test-Reports\release-focused-20261116.xml`.
- Queue, priority, and updater batch: 18 passed initially; the three targeted failures then passed. All 21 unique checks passed. Raw JUnit: `release-queue-updater-20261116.xml` and `release-priority-lock-20261116.xml`.
- Automatic updater workflow batch: 36 passed, 2 skipped across application and OpenSSL updater tests; both new workflow tests passed.
- Elevated installer checksum regression: 4 application updater tests passed after switching the helper to the .NET SHA-256 API.
- Layout history batch: 18 unique cases passed after updating stale assertions and fixing tab visibility, workspace shortcut focus, and manual queue visibility. Seven focused final checks passed. Raw JUnit: `release-layout-history-rerun-20261116.xml` and `release-layout-check-20261116.xml`.
- Two remaining historical failures in feature preview and real Windows TLS passed. Raw JUnit: `release-historical-failures-20261116.xml`.
- The 100,000-file queue stress cases are not part of this release run; follow the incremental plan in `TODO.md`.
- Copy/hotkey validation (2026-11-18): 10 focused accessibility and hotkey tests passed. Related menu regression batch ran 50 unique cases: 48 passed initially, 2 failed, then both passed on focused rerun.
- Debian debug/F6 validation (2026-11-19): 10 focused tests passed. The freshly packaged Debian 13 executable passed GUI, debug-log, and F6 smoke checks; the Qt offscreen backend reported one 800×800 virtual screen.
- F-key validation (2026-11-17): 11 passed. Pane-order regression batch:
  4 passed after restoring stable pane identity when both panes share a mode.
- Release-focused workflow checks (2026-11-17): 77 passed, 1 skipped across
  Tools, layout, compare, OpenSSL, workspace, deployment, artifact, and cloud tests.
- SonarQube quality gate (copy/hotkey change 2026-11-18): OK; zero new issues.

## Queue, transfer, and retries (238 tests: 238 passed, 0 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_bandwidth.py::test_daily_schedule_wraps_midnight_and_combined_limits` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_disjoint_budget_shares_cannot_exceed_caps[counts0]` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_disjoint_budget_shares_cannot_exceed_caps[counts1]` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_disjoint_budget_shares_cannot_exceed_caps[counts2]` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_failed_backend_update_stops_backend_and_releases_lease` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_fair_budget_limits_multiple_threads_and_cancels` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_global_limit_applies_despite_legacy_local_bypass` | Passed (archived batch 02; carried forward) |
| `tests/test_bandwidth.py::test_native_and_real_backend_share_exact_byte_units` | Passed (archived batch 02; carried forward) |
| `tests/test_folder_speed.py::test_collapsed_folder_combines_bytes_across_finished_and_active_children` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_speed.py::test_rolling_speed_survives_file_gaps_then_decays` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_speed.py::test_streaming_folder_displays_cumulative_bytes_without_inventing_total` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_speed.py::test_streaming_worker_rate_spans_file_boundaries` | Passed (archived batch 07; carried forward) |
| `tests/test_large_queue.py::test_large_nested_cloud_folder_plans_every_file` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_large_queue.py::test_large_queue_roundtrip_preserves_order_and_paths` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_large_queue.py::test_large_scan_remains_cancellable` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_marker_queue_cleanup.py::test_marker_waits_for_own_parent_and_preserves_other_rows[cancelled]` | Passed (archived batch 11; carried forward) |
| `tests/test_marker_queue_cleanup.py::test_marker_waits_for_own_parent_and_preserves_other_rows[complete]` | Passed (archived batch 11; carried forward) |
| `tests/test_marker_queue_cleanup.py::test_marker_waits_for_own_parent_and_preserves_other_rows[failed]` | Passed (archived batch 11; carried forward) |
| `tests/test_marker_queue_cleanup.py::test_worker_created_marker_clears_with_completed_parent` | Passed (archived batch 11; carried forward) |
| `tests/test_parallel_folders.py::test_cancelled_scan_does_not_enqueue_files` | Passed (archived batch 13; carried forward) |
| `tests/test_parallel_folders.py::test_folder_files_have_parallel_logins_and_queue_rows[False-False]` | Passed (archived batch 13; carried forward) |
| `tests/test_parallel_folders.py::test_folder_files_have_parallel_logins_and_queue_rows[False-True]` | Passed (archived batch 13; carried forward) |
| `tests/test_parallel_folders.py::test_folder_files_have_parallel_logins_and_queue_rows[True-False]` | Passed (archived batch 13; carried forward) |
| `tests/test_parallel_folders.py::test_folder_files_have_parallel_logins_and_queue_rows[True-True]` | Passed (archived batch 13; carried forward) |
| `tests/test_parallel_folders.py::test_same_destination_cannot_run_concurrently` | Passed (archived batch 13; carried forward) |
| `tests/test_priority.py::test_pending_order_stable_and_respects_eligibility` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_priority.py::test_priority_masks_first_match_regex_and_empty_list` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_priority.py::test_priority_ui_edit_move_delete_persistence` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_priority.py::test_recursive_sibling_order` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_priority.py::test_reorder_affects_waiting_queue` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_queue_100k.py::test_100001_files_bounded_ui_search_recovery_cancel` | Passed (deferred queue regression batch 2026-11-19; 100,001 items) |
| `tests/test_queue_async.py::test_100001_import_export_recovery_and_expansion_remain_responsive` | Passed (deferred queue regression batch 2026-11-19; 100,001 items) |
| `tests/test_queue_async.py::test_atomic_cancel_keeps_previous_file_and_removes_temporary` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_cancelled_gui_install_rolls_back_partial_rows` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_cancelled_startup_retains_recovery_file` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_progress_cancel_signals_worker` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_serialized_writer_coalesces_latest_snapshot` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_settings_snapshot_matches_gui_defaults` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_snapshot_keeps_original_disk_version_after_mutation_and_close` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_handles_chunk_boundaries_and_unicode` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_rejects_invalid_envelopes[{"version":1,"jobs":[],"jobs":[]}]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_rejects_invalid_envelopes[{"version":1,"jobs":[],}]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_rejects_invalid_envelopes[{"version":1,"jobs":[]}x]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_rejects_invalid_envelopes[{"version":1,"jobs":[{},]}]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_rejects_invalid_envelopes[{"version":1}]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_async.py::test_stream_reader_rejects_invalid_envelopes[{"version":true,"jobs":[]}]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_buffer.py::test_buffer_read_error_pauses_without_losing_queue` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_buffer.py::test_cancel_and_clear_restore_include_unloaded_files` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_buffer.py::test_failed_buffer_batch_rolls_back_every_descriptor` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_buffer.py::test_failed_file_retained_while_buffer_drains` | Passed (focused rerun 2026-11-18; uses Files and Remark headers) |
| `tests/test_queue_buffer.py::test_held_large_selection_releases_on_play` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_buffer.py::test_large_folder_has_bounded_rows_and_complete_portable_snapshot` | Passed (archived batch 14; carried forward) |
| `tests/test_queue_buffer.py::test_multiple_batches_copy_every_file_and_release_completed_rows` | Passed (focused rerun 2026-11-18; uses Files and Remark headers) |
| `tests/test_queue_buffer.py::test_recovery_releases_held_and_preserves_future_schedule` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_buffer.py::test_second_buffer_gets_next_available_slot` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_buffer.py::test_spool_order_and_removal` | Passed (release validation 2026-11-16) |
| `tests/test_queue_context.py::test_buffered_placeholder_cannot_be_edited` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_context_copies_paths_and_completion_setting` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_context_reference_order_and_safe_states` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_disk_flag_counts_and_release_overrides` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_folder_popup_survives_repeated_native_context_events` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_information_counts_buffered_files_and_completed_summary` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_save_menu_exports_deferred_recovery_items` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_context.py::test_transfer_selected_releases_only_selection_when_paused` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine_desktop.py::test_desktop_uses_engine_state_and_events` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine_desktop.py::test_selection_cancellation_keeps_other_engine_jobs` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_cancellation_does_not_release_active_slots_until_worker_finishes` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_deferred_retry_waits_for_original_work_and_retry_is_not_duplicated` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_deployments_are_exclusive_in_both_directions` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_destination_conflicts_across_workspaces[False-False-/out/file-/out/file-False]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_destination_conflicts_across_workspaces[False-True-/out/file-/out-False]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_destination_conflicts_across_workspaces[True-False-/out-/out/file-False]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_destination_conflicts_across_workspaces[True-False-/out-/outside/file-True]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_engine_import_does_not_load_qt_or_desktop` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_explicit_reset_clears_budget_and_preserves_job_identity` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_global_and_workspace_limits_and_upload_direction` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_guarded_failures_do_not_retry[crc_job]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_guarded_failures_do_not_retry[deployment]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_guarded_failures_do_not_retry[suppress_retry]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_guarded_failures_do_not_retry[watch]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_manual_order_priority_holds_and_clock` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_retry_budget_shared_across_policies[False-False-1]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_retry_budget_shared_across_policies[False-True-3]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_retry_budget_shared_across_policies[True-False-3]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_retry_budget_shared_across_policies[True-True-3]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_site_limits[max_downloads]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_site_limits[max_transfers_total]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_site_limits[single_connection]` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_engine.py::test_trust_dialog_defers_finish_and_duplicate_finish_is_harmless` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_filter_lock.py::test_filter_paths_status_and_hidden_selection` | Passed (release validation 2026-11-16) |
| `tests/test_queue_filter_lock.py::test_layout_lock_blocks_drag_and_persists` | Passed (release validation 2026-11-16) |
| `tests/test_queue_filter_lock.py::test_queue_stop_and_selected_cancel_signal_fxp` | Passed (release validation 2026-11-16) |
| `tests/test_queue_filter_lock.py::test_search_disk_paging_unicode_and_cancel` | Passed (release validation 2026-11-16) |
| `tests/test_queue_filter_lock.py::test_search_reader_survives_queue_clear_and_releases_file` | Passed (release validation 2026-11-16) |
| `tests/test_queue_folders.py::test_click_folder_toggles_and_cancel_includes_hidden_files` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_folders.py::test_release_progress_is_weighted_by_bytes_and_survives_collapse` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_folders.py::test_remove_folder_and_restore_queue_preserves_children` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_folders.py::test_saved_files_restore_under_folder_and_can_be_reordered` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_folders.py::test_worker_progress_supports_large_files` | Passed (archived batch 15; carried forward) |
| `tests/test_queue_menu.py::test_clear_restore_after_workspace_closed_keeps_files` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_conditional_adds_only_matching_selected_files_held` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_load_is_paused_and_rejects_unsafe_paths_without_changes` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_manual_order_changes_execution_and_survives_recovery` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_multiselect_stability_and_active_guard` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_queue_menu_order_shortcuts_and_focus_conflict` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_recovered_due_schedule_stays_paused` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_retry_selected_preserves_other_paused_jobs` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_scheduled_jobs_wait_persist_and_only_start_when_due` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_menu.py::test_speed_limits_apply_to_shared_limiter_and_cancel` | Passed (menu regression batch 2026-11-18) |
| `tests/test_queue_recovery.py::test_deferred_queue_close_and_save_preserve_items` | Passed (archived batch 16; carried forward) |
| `tests/test_queue_recovery.py::test_delete_queue_keeps_files_and_other_saved_workspace` | Passed (archived batch 16; carried forward) |
| `tests/test_queue_recovery.py::test_load_selected_keeps_other_workspace_saved` | Passed (archived batch 16; carried forward) |
| `tests/test_queue_recovery.py::test_load_to_new_window_and_no_duplicate_load` | Passed (archived batch 16; carried forward) |
| `tests/test_queue_recovery.py::test_recovery_write_failure_retains_saved_items` | Passed (archived batch 16; carried forward) |
| `tests/test_queue_recovery.py::test_start_all_runs_restored_local_copy` | Passed (archived batch 16; carried forward) |
| `tests/test_queue_storage_service.py::test_cancelled_atomic_save_preserves_existing_file` | Passed (release validation 2026-11-16) |
| `tests/test_queue_storage_service.py::test_full_storage_roundtrip_without_qt` | Passed (release validation 2026-11-16) |
| `tests/test_queue_storage_service.py::test_guarded_metadata_validation_does_not_import_desktop` | Passed (release validation 2026-11-16) |
| `tests/test_queue_storage_service.py::test_import_rejects_overlapping_paths_and_duplicate_identity` | Passed (release validation 2026-11-16) |
| `tests/test_queue_storage_service.py::test_pinned_spool_snapshot_survives_removal_and_owner_close` | Passed (release validation 2026-11-16) |
| `tests/test_queue.py::test_bad_queue_is_not_overwritten` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_cancel_never_retries` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_certificate_prompt_keeps_queue_worker_tracked[False]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_certificate_prompt_keeps_queue_worker_tracked[True]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_clear_only_affected_site_and_do_not_retry_active` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_default_repeat_counts[Download-6]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_default_repeat_counts[FXP-1]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_default_repeat_counts[Upload-3]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_deferred_retry_runs_after_original_queue` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_fast_failing_retries_cannot_strand_active_job` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_old_cancellation_does_not_fail_new_batch` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_paused_queue_close_closes_even_when_save_fails` | Passed (previous targeted batch; carried forward) |
| `tests/test_queue.py::test_persistence_excludes_passwords_and_completed_jobs` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_queue_ui_and_status_paths` | Passed (release validation 2026-11-16) |
| `tests/test_queue.py::test_reload_disabled` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_reset_failed_is_explicit_and_resets_budget` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_restore_active_queue_and_run_local_copy` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_restore_credentials_only_on_play` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_retry_switches_share_one_budget[False-False-1]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_retry_switches_share_one_budget[False-True-3]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_retry_switches_share_one_budget[True-False-3]` | Passed (archived batch 14; carried forward) |
| `tests/test_queue.py::test_retry_switches_share_one_budget[True-True-3]` | Passed (archived batch 14; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_all_existing_files_skip_and_clear_the_folder` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_context_delete_removes_leftover_folder_without_job` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_download_folder_skip_cleanup_keeps_failures[cancelled]` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_download_folder_skip_cleanup_keeps_failures[complete]` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_download_folder_skip_cleanup_keeps_failures[failed]` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_folder_delete_guards_hidden_active_descendants` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_nested_skipped_folders_stay_in_log_not_queue` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_skipped_folder_clears_without_deleting_files[Copy]` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_skipped_folder_clears_without_deleting_files[Download]` | Passed (archived batch 19; carried forward) |
| `tests/test_skipped_folder_cleanup.py::test_skipped_folder_clears_without_deleting_files[Upload]` | Passed (archived batch 19; carried forward) |
| `tests/test_transfer_history.py::test_buffered_cancellation_records_every_file_once` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_failure_retry_deduplicates_and_uses_held_queue` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_history_dialog_filters_and_copies_without_loading_entire_store` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_history_persists_pages_search_and_retention` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_history_storage_failure_does_not_break_transfer` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_nested_skips_are_retained_after_rows_clear` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_retry_does_not_duplicate_unloaded_buffered_file` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_selected_cancel_and_auto_rename_history` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_history.py::test_success_is_recorded_before_queue_cleanup_and_no_passwords` | Passed (archived batch 20; carried forward) |
| `tests/test_transfer_options.py::test_ascii_upload_download_and_auto_binary[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_ascii_upload_download_and_auto_binary[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_completion_action_waits_for_successful_queue[Close FluxFTP-False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_completion_action_waits_for_successful_queue[Close FluxFTP-True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_completion_action_waits_for_successful_queue[Disconnect-False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_completion_action_waits_for_successful_queue[Idle-False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_disk_reserve_preserves_existing_destination` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_force_active_buffers_and_local_limit_bypass` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_recursive_filename_conversion_and_collision` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_resume_rolls_back_corrupted_partial_tail` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_shared_speed_budget_and_cancellation` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_stall_retries_are_bounded` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_timeout_reconnects_and_resumes_partial` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_options.py::test_transfer_controls_and_queue_limits` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_ascii_required_and_cloud_atomic_fail_before_transfer` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_atomic_failure_preserves_target[cancel]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_atomic_failure_preserves_target[rename]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_atomic_failure_preserves_target[transfer]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_atomic_hash_failure_never_promotes` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_atomic_upload_preserves_target_until_rename[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_atomic_upload_preserves_target_until_rename[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_conflict_changed_or_cancelled_preserves_destination` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_conflict_decisions[Auto Overwrite]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_conflict_decisions[Auto Rename]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_conflict_decisions[Auto Resume]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_conflict_decisions[Auto Skip]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_crc_policy_survives_verified_download[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_crc_policy_survives_verified_download[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_ftp_hash_checks_complete_reply_range[False-False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_ftp_hash_checks_complete_reply_range[False-True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_ftp_hash_checks_complete_reply_range[True-False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_ftp_hash_checks_complete_reply_range[True-True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_local_hash_resume_detects_bad_prefix_and_preserves_target` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_sftp_atomic_replace_with_hash[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_sftp_real_hash_and_unsupported_atomic_rename` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_unsupported_ftp_hash_is_explicit[Required]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_unsupported_ftp_hash_is_explicit[When available]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_verified_download_keeps_original_on_corruption[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_verified_download_keeps_original_on_corruption[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_verified_fxp` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_transfer_reliability.py::test_verified_resume_retires_unchanged_partial` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_upload_delete_fixes.py::test_ascii_refusal_retries_binary_byte_for_byte[m3u-False]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_ascii_refusal_retries_binary_byte_for_byte[m3u-True]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_ascii_refusal_retries_binary_byte_for_byte[nfo-False]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_ascii_refusal_retries_binary_byte_for_byte[nfo-True]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_ascii_refusal_retries_binary_byte_for_byte[sfv-False]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_ascii_refusal_retries_binary_byte_for_byte[sfv-True]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_nonempty_remote_tree_preserves_siblings[False-False]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_nonempty_remote_tree_preserves_siblings[False-True]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_nonempty_remote_tree_preserves_siblings[True-False]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_nonempty_remote_tree_preserves_siblings[True-True]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_preflights_before_any_mutation[entry0]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_preflights_before_any_mutation[entry1]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_refuses_directory_shortcut` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_refuses_root_and_relative_paths[.]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_refuses_root_and_relative_paths[/]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_refuses_root_and_relative_paths[/folder/..]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_delete_refuses_root_and_relative_paths[relative]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_no_binary_retry_for_unrelated_refusal_or_append[550 Permission denied-False]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_no_binary_retry_for_unrelated_refusal_or_append[553 Error: upload in ASCII mode is not allowed!-True]` | Passed (archived batch 22; carried forward) |
| `tests/test_upload_delete_fixes.py::test_no_binary_retry_for_unrelated_refusal_or_append[553 File already exists-False]` | Passed (archived batch 21; carried forward) |
| `tests/test_upload_delete_fixes.py::test_right_click_delete_confirms_contents_and_calls_recursive_helper` | Passed (archived batch 22; carried forward) |
| `tests/test_verification_reports.py::test_cancelled_export_preserves_existing_report` | Passed (archived batch 22; carried forward) |
| `tests/test_verification_reports.py::test_folder_reports_are_per_file_and_export_json_csv` | Passed (archived batch 22; carried forward) |
| `tests/test_verification_reports.py::test_hash_evidence_contains_actual_checksums_and_no_duplicate_inner_copy` | Passed (archived batch 22; carried forward) |
| `tests/test_verification_reports.py::test_legacy_history_is_unknown_and_csv_is_formula_safe` | Passed (archived batch 22; carried forward) |
| `tests/test_verification_reports.py::test_mismatch_takes_precedence_over_earlier_success` | Passed (archived batch 22; carried forward) |
| `tests/test_verification_reports.py::test_size_only_does_not_claim_checksum_verification` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_corrupt_watch_file_is_preserved` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_stability_resets_on_changes_and_excludes_acknowledged` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_automatic_start_and_cancel_pause` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_ignores_initial_files_but_detects_later_changes` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_preserves_remote_existing_by_default` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_recreated_file_is_detected` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_restored_queue_does_not_duplicate` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_stale_source_pauses_and_keeps_destination` | Passed (archived batch 22; carried forward) |
| `tests/test_watch_upload.py::test_watch_upload_queue_persistence_dedup_and_completion` | Passed (archived batch 22; carried forward) |

## Cloud providers and synchronization (87 tests: 87 passed, 0 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_cloud_account_test.py::test_cancel_and_close_wait_for_worker` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_account_test.py::test_invalid_path_and_failure_retry` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_account_test.py::test_real_backend_listing_without_remote_mutations` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_account_test.py::test_saved_path_and_read_only_request` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_account_test.py::test_test_button_routes_selected_account` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_cancel_edit_discards_draft[False]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_cancel_edit_discards_draft[True]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_cloud_accounts_edit_button_and_double_click` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_edit_existing_s3_preserves_identity_and_credentials` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_edit_refuses_to_overwrite_concurrent_change` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_edit_restores_both_files_if_commit_fails` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_edit.py::test_edit_waits_for_other_operations_before_commit` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_authorization_listener_errors_are_actionable_and_private[bind: address already in use-another application]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_authorization_listener_errors_are_actionable_and_private[bind: An attempt was made to access a socket in a way forbidden by its access permissions.-excluded port range]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_authorization_listener_errors_are_actionable_and_private[bind: Only one usage of each socket address is normally permitted.-another application]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_authorization_listener_errors_are_actionable_and_private[bind: permission denied-excluded port range]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_authorization_listener_errors_are_actionable_and_private[unknown listener failure-port availability]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_expired_authorization_explains_reconnect_without_disclosing_response` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_onedrive_listener_error_names_its_dedicated_port` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_public_s3_root_error_explains_bucket_path` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_errors.py::test_unknown_backend_errors_stay_private` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_oauth_port.py::test_onedrive_uses_separate_port_for_listener_and_exchange[bad_state]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_oauth_port.py::test_onedrive_uses_separate_port_for_listener_and_exchange[cancel]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_oauth_port.py::test_onedrive_uses_separate_port_for_listener_and_exchange[success]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_platform_path.py::test_packaged_cloud_backend_native_name[darwin-rclone]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_platform_path.py::test_packaged_cloud_backend_native_name[linux-rclone]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_platform_path.py::test_packaged_cloud_backend_native_name[win32-rclone.exe]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_streaming.py::test_cloud_folder_uploads_before_descending_and_recovers_unvisited_files` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_cloud_streaming.py::test_noncloud_folder_keeps_buffered_planning` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_cloud_streaming.py::test_pause_returns_cloud_folder_to_recoverable_queue_before_next_file` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_cloud_wizard_recovery.py::test_custom_drive_id_is_preserved` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_wizard_recovery.py::test_drive_choices_show_labels_and_send_ids` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_wizard_recovery.py::test_optionless_response_continues_without_stale_answer[-backend-result]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_wizard_recovery.py::test_optionless_response_continues_without_stale_answer[Failed to query root for drive SECRET: https://example/?token=SECRET-]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud_wizard_recovery.py::test_setup_error_never_echoes_provider_secrets` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_active_cancellation_preserves_existing_download` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_backend_providers_encryption_and_auth` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_cloud_conflicts_recursive_relay_and_cancel` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_cloud_failed_download_preserves_target` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_cloud_file_roundtrip_and_mutations` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_cloud_site_selection_and_command_guards` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_ftp_cloud_relay[from-cloud]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_ftp_cloud_relay[to-cloud]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_guided_webdav_setup` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_microsoft_account_classification[Microsoft OneDrive for Business-business]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_microsoft_account_classification[Microsoft OneDrive-personal]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_microsoft_account_classification[Microsoft SharePoint Online-documentLibrary]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_plaintext_cloud_config_is_refused` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_real_webdav` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_restored_cloud_jobs_do_not_prompt_for_ftp_password` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_setup_question_protocol` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_cloud.py::test_sftp_cloud_relay` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_cancel_and_resume_new_instance` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_changed_source_or_expired_session_restarts[expired]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_changed_source_or_expired_session_restarts[source]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_empty_file_uses_backend_and_verifies_hash` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_finished_file_must_match_checksum` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_fresh_overwrite_is_honored_but_expired_completed_session_is_not_duplicated` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_process_exit_after_ack_recovers_from_durable_session` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_restart_queries_server_after_lost_ack_and_keeps_session_encrypted` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_same_size_skip_requires_matching_hash` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_service_account_jwt_is_signed_and_scope_preserved` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_drive_upload.py::test_session_address_cannot_receive_token_on_other_host` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_github_artifacts.py::test_deployment_editor_exposes_github_actions_import` | Passed (archived batch 08; carried forward) |
| `tests/test_github_artifacts.py::test_github_artifact_download_uses_safe_shared_extractor` | Passed (archived batch 08; carried forward) |
| `tests/test_github_artifacts.py::test_github_provenance_is_validated_and_bound_to_run_url` | Passed (archived batch 08; carried forward) |
| `tests/test_github_artifacts.py::test_github_workflow_run_and_artifact_lookup` | Passed (archived batch 08; carried forward) |
| `tests/test_github_artifacts.py::test_repository_name_and_github_token_key` | Passed (archived batch 08; carried forward) |
| `tests/test_gitlab_artifacts.py::test_artifact_import_rejects_case_insensitive_duplicate_paths` | Passed (archived batch 09; carried forward) |
| `tests/test_gitlab_artifacts.py::test_artifact_import_rejects_unsafe_entries[entry0]` | Passed (archived batch 08; carried forward) |
| `tests/test_gitlab_artifacts.py::test_artifact_import_rejects_unsafe_entries[entry1]` | Passed (archived batch 09; carried forward) |
| `tests/test_gitlab_artifacts.py::test_deployment_editor_exposes_gitlab_pipeline_import` | Passed (archived batch 09; carried forward) |
| `tests/test_gitlab_artifacts.py::test_deployment_profile_requires_consistent_gitlab_provenance` | Passed (archived batch 09; carried forward) |
| `tests/test_gitlab_artifacts.py::test_download_artifact_extracts_to_owned_cache_directory` | Passed (archived batch 08; carried forward) |
| `tests/test_gitlab_artifacts.py::test_gitlab_url_and_token_names_are_normalized` | Passed (archived batch 08; carried forward) |
| `tests/test_gitlab_artifacts.py::test_pipeline_and_job_lookup_filters_success_and_encodes_project` | Passed (archived batch 08; carried forward) |
| `tests/test_sync_extended.py::test_baseline_tracks_deletions_on_both_sides` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_checksum_queue_rejects_changed_content_even_same_metadata` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_checksums_find_same_size_same_time_changes` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_corrupt_baseline_rejected_without_replacing_file` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_delete_metadata_cannot_delete_folders_or_existing_sources` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_mirror_rechecks_both_sides[destination]` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_mirror_rechecks_both_sides[source]` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_mirror_requires_explicit_choice_and_preserves_folders` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_profiles_and_explicit_baseline` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_remote_checksum_scan` | Passed (archived batch 19; carried forward) |
| `tests/test_sync_extended.py::test_remote_mirror_delete_has_guarded_queue_roundtrip` | Passed (archived batch 19; carried forward) |

## Connections and protocols (257 tests: 256 passed, 0 failed, 1 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_ascii_rules.py::test_ascii_download_resume_checks_converted_prefix[False]` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_ascii_download_resume_checks_converted_prefix[True]` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_ascii_list_add_edit_delete_apply_and_cancel` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_ascii_masks_regex_and_filename_matching` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_ascii_resume_upload_refuses_mismatched_prefix` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_ascii_resume_upload_verifies_then_appends[False]` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_ascii_resume_upload_verifies_then_appends[True]` | Passed (archived batch 01; carried forward) |
| `tests/test_ascii_rules.py::test_normalized_reader_handles_split_crlf` | Passed (archived batch 01; carried forward) |
| `tests/test_browse_keepalive_ui.py::test_keepalive_controls_persist_and_site_values_validate` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_keepalive_ui.py::test_pane_keepalive_reuses_socket_and_disable_releases_it` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_keepalive_ui.py::test_site_override_disables_probe_and_single_connection_disables_reuse` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_idle_noop_reuses_connection_and_reconnects_lazily[False]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_idle_noop_reuses_connection_and_reconnects_lazily[True]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_keepalive_does_not_overlap_listing_and_close_discards_late_result` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_old_scheduled_request_cannot_reopen_disconnected_site` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_permission_failure_is_not_retried` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_read_reconnect_is_bounded[error0]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_read_reconnect_is_bounded[error1]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_read_reconnect_is_bounded[error2]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_read_reconnect_is_bounded[error3]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_reuse_refresh_disconnect_and_expired_connection[False]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_reuse_refresh_disconnect_and_expired_connection[True]` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_sftp_persistent_listing_reconnects` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_tls_eof_reconnectable_but_certificate_failure_is_not` | Passed (archived batch 02; carried forward) |
| `tests/test_browse_session.py::test_unsupported_noop_is_disabled_and_timeout_restored` | Passed (archived batch 02; carried forward) |
| `tests/test_compression.py::test_address_and_extension_exceptions_and_buffering` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_compressed_ascii_roundtrip[False]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_compressed_ascii_roundtrip[True]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_compressed_verified_ascii_resume[False]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_compressed_verified_ascii_resume[True]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_compression_controls_persist` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_corrupt_compressed_download_preserves_completed_target` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_empty_compressed_file_roundtrip` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_fxp_negotiates_same_mode_on_both_sides[False]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_fxp_negotiates_same_mode_on_both_sides[True]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_invalid_or_incomplete_stream_rejected[bad]` | Passed (archived batch 03; carried forward) |
| `tests/test_compression.py::test_invalid_or_incomplete_stream_rejected[x\x9cK\xce\xcf+I\xcd+)\x06\x00\x0f:]` | Passed (archived batch 03; carried forward) |
| `tests/test_compression.py::test_invalid_or_incomplete_stream_rejected[x\x9cK\xce\xcf+I\xcd+)\x06\x00\x0f:\x03oextra]` | Passed (archived batch 03; carried forward) |
| `tests/test_compression.py::test_mode_z_listing_upload_download_and_resume[False-False]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_mode_z_listing_upload_download_and_resume[False-True]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_mode_z_listing_upload_download_and_resume[True-False]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_mode_z_listing_upload_download_and_resume[True-True]` | Passed (archived batch 04; carried forward) |
| `tests/test_compression.py::test_stream_roundtrip_and_bounded_inflation` | Passed (archived batch 03; carried forward) |
| `tests/test_compression.py::test_unsupported_compression_falls_back_once` | Passed (archived batch 04; carried forward) |
| `tests/test_connection_controls.py::test_auto_binding_for_passive_and_active_listings` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_connection_options_persist_and_bypass_auth` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_noop_transfer_replies_leave_control_channel_synchronized` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[::1-True--True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[127.0.0.1-True--True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[172.20.1.2-True--True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[172.32.1.2-True--False]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[192.168.1.20-False--False]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[192.168.1.20-True--True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[example.com-False-EXAMPLE.;192.168.-True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[example.com-True--False]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[fd00::1-True--True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_bypass_rules[nas-True--True]` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_proxy_passive_override_can_be_disabled` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_unavailable_active_port_is_reported` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_upnp_does_not_replace_or_delete_other_mappings` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_upnp_failure_is_reported` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_upnp_maps_active_listener_and_cleans_up` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_controls.py::test_upnp_refuses_vulnerable_native_parser_before_discovery` | Passed (Windows UPnP 2.3.5 batch 2026-11-18) |
| `tests/test_connection_diagnostics.py::test_active_ftp_data_check` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_bad_login_and_cancel` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_diagnostic_controls` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_dns_failure_stops_before_login` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_missing_folder_reports_data_stage` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_read_only_diagnostics_passes_real_login_and_listing[False]` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_read_only_diagnostics_passes_real_login_and_listing[True]` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_diagnostics.py::test_tls_failure_preserves_verification` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_operations.py::test_list_marks_shortcuts_as_directories_and_applies_timezone` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_operations.py::test_list_parser_error_does_not_leave_ftp_in_changed_directory` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_operations.py::test_passive_listing_fallback_restores_passive_mode` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_operations.py::test_sftp_mutations_validate_inputs_and_route_operations` | Passed (archived batch 05; carried forward) |
| `tests/test_connection_operations.py::test_stat_listing_parses_multiline_reply_and_strips_reply_codes` | Passed (archived batch 05; carried forward) |
| `tests/test_drftpd_ascii_proxy.py::test_ascii_banner_newlines_spacing_and_disk_safety` | Passed (archived batch 05; carried forward) |
| `tests/test_drftpd_ascii_proxy.py::test_drftpd_catalog_compiles_and_is_in_designer` | Passed (archived batch 05; carried forward) |
| `tests/test_drftpd_ascii_proxy.py::test_proxy_passive_route_and_actionable_failure[False]` | Passed (archived batch 05; carried forward) |
| `tests/test_drftpd_ascii_proxy.py::test_proxy_passive_route_and_actionable_failure[True]` | Passed (archived batch 05; carried forward) |
| `tests/test_ftp_menu.py::test_cancelled_export_preserves_existing_file` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_chmod_and_sftp_timestamp_dispatch` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_directory_cache_scopes_expiry_and_inflight_clear` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_invalid_or_cancelled_chmod_and_stale_dialog_do_not_mutate` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_menu_reference_and_quick_dialog` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_move_conflicts_preflight_cancel_and_partial_failure` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_navigation_cache_manual_refresh_and_clear` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_real_scan_move_and_date[False]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_real_scan_move_and_date[True]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_scan_links_limits_cancellation_and_permission_failure` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ftp_menu.py::test_working_scan_dialogs_export_and_mutation` | Passed (menu regression batch 2026-11-18) |
| `tests/test_ident.py::test_connecting_lifecycle_handles_parallel_attempts` | Passed (archived batch 09; carried forward) |
| `tests/test_ident.py::test_failed_connection_stops_ident` | Passed (archived batch 09; carried forward) |
| `tests/test_ident.py::test_ftp_login_can_query_ident_before_sending_greeting` | Passed (archived batch 09; carried forward) |
| `tests/test_ident.py::test_ident_validation_and_busy_port` | Passed (archived batch 09; carried forward) |
| `tests/test_ident.py::test_ident_waits_for_control_socket_registration` | Passed (archived batch 09; carried forward) |
| `tests/test_ident.py::test_ident_wire_responses_and_connection_scope` | Passed (archived batch 09; carried forward) |
| `tests/test_legacy_replies.py::test_legacy_error_reply_remains_authentication_error` | Passed (archived batch 10; carried forward) |
| `tests/test_legacy_replies.py::test_non_utf8_banner_and_login_reply[FTP \xb7 Unencrypted]` | Passed (archived batch 10; carried forward) |
| `tests/test_legacy_replies.py::test_non_utf8_banner_and_login_reply[FTPS \xb7 AUTH SSL]` | Passed (archived batch 10; carried forward) |
| `tests/test_legacy_replies.py::test_non_utf8_banner_and_login_reply[FTPS \xb7 AUTH TLS]` | Passed (archived batch 10; carried forward) |
| `tests/test_legacy_replies.py::test_non_utf8_banner_and_login_reply[FTPS \xb7 Implicit TLS]` | Passed (archived batch 10; carried forward) |
| `tests/test_legacy_replies.py::test_reply_reader_restored_after_utf8_login` | Passed (archived batch 10; carried forward) |
| `tests/test_protocol_adapter.py::test_adapter_delegates_operations_and_preserves_cancellation_and_errors` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_all_cloud_providers_and_local_capabilities` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[FTP \xb7 Unencrypted-True-True-True-server extension required]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[FTPS \xb7 AUTH SSL-True-True-True-server extension required]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[FTPS \xb7 AUTH TLS-True-True-True-server extension required]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[FTPS \xb7 Implicit TLS-True-True-True-server extension required]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[Google Drive-False-False-False-no native checksum exposed]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[SCP \xb7 SSH-True-False-False-server extension required]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[SFTP \xb7 SSH-True-True-False-server extension required]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_backend_capabilities_are_conservative[unregistered backend-False-False-False-no native checksum exposed]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_conflict_dialog_does_not_offer_unsupported_download_resume[Google Drive]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_conflict_dialog_does_not_offer_unsupported_download_resume[SCP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_contract_import_does_not_load_desktop_or_qt` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_direct_fxp_rejects_ssh_before_network_activity` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_local_routes_and_queued_holds_are_distinct_from_payload_pause` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_native_hashes_require_negotiation_and_cloud_rejects_native_digest` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[FTPS \xb7 AUTH TLS-Amazon S3]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[FTPS \xb7 AUTH TLS-FTP \xb7 Unencrypted]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[FTPS \xb7 AUTH TLS-SCP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[FTPS \xb7 AUTH TLS-SFTP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[Google Drive-Amazon S3]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[Google Drive-FTP \xb7 Unencrypted]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[Google Drive-SCP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[Google Drive-SFTP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SCP \xb7 SSH-Amazon S3]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SCP \xb7 SSH-FTP \xb7 Unencrypted]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SCP \xb7 SSH-SCP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SCP \xb7 SSH-SFTP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SFTP \xb7 SSH-Amazon S3]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SFTP \xb7 SSH-FTP \xb7 Unencrypted]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SFTP \xb7 SSH-SCP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_route_and_pause_depend_on_both_endpoints[SFTP \xb7 SSH-SFTP \xb7 SSH]` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_scp_resume_is_rejected_before_backend_writes` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_protocol_adapter.py::test_sftp_hash_candidates_do_not_claim_extension_is_available` | Passed (cloud backend modularization batch 2026-11-19) |
| `tests/test_recent_reconnect.py::test_recent_requires_success_and_reconnect_targets_clicked_pane` | Passed (archived batch 16; carried forward) |
| `tests/test_recent_reconnect.py::test_relaunch_hides_reconnect_but_retains_recent_history` | Passed (archived batch 16; carried forward) |
| `tests/test_recent_reconnect.py::test_unsaved_reconnect_keeps_profile_and_prompts_without_saving_password` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_listing_preserves_progress_marker` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_names_still_reject_unsafe_components[../escape]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_names_still_reject_unsafe_components[bad\nname]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_names_still_reject_unsafe_components[bad\rname]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_names_still_reject_unsafe_components[bad\x00name]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_names_still_reject_unsafe_components[nested/file]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_remote_names_still_reject_unsafe_components[nested\\file]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_upload_conflict_listing_with_server_marker[False]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_marker_listing.py::test_upload_conflict_listing_with_server_marker[True]` | Passed (archived batch 16; carried forward) |
| `tests/test_remote_routes.py::test_fxp_cancel_interrupts_blocked_control_read[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_fxp_cancel_interrupts_blocked_control_read[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_fxp_destination_waits_for_source_before_preliminary_reply` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_protected_fxp_real_bytes[False-CPSV]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_protected_fxp_real_bytes[False-SSCN]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_protected_fxp_real_bytes[True-CPSV]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_protected_fxp_real_bytes[True-SSCN]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_atomic_upload_preserves_conflict_path[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[False-ftp-ssh]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[False-ssh-ftp]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[False-ssh-ssh]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[False-ssh-tls]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[False-tls-ssh]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[True-ftp-ssh]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[True-ssh-ftp]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[True-ssh-ssh]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[True-ssh-tls]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_bytes_resume[True-tls-ssh]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_routes.py::test_relay_cancel_before_upload` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_remote_shortcuts.py::test_local_plus_file_remains_a_file` | Passed (archived batch 17; carried forward) |
| `tests/test_remote_shortcuts.py::test_remote_shortcut_double_click_uses_exact_server_name[False]` | Passed (archived batch 17; carried forward) |
| `tests/test_remote_shortcuts.py::test_remote_shortcut_double_click_uses_exact_server_name[True]` | Passed (archived batch 17; carried forward) |
| `tests/test_remote_shortcuts.py::test_shortcut_listing_preserves_alias_and_link_metadata` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_all_reference_templates_compile` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_cancellation_no_command_and_output_limit` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_cancelled_prompt_sends_nothing` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_command_keyboard_shortcuts` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_custom_commands_reload_and_literal_credentials_refused` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_designer_edit_and_restore_persistence` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_expansion_references_selection_and_no_recursive_substitution` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines0-entries0-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines1-entries1-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines2-entries2-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines3-entries3-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines4-entries4-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines5-entries5-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_invalid_arguments_do_not_compile[lines6-entries6-<lambda>]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_menu_groups_dispatch_and_gating` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_raw_percent_is_literal_and_password_masked` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_real_ftp_and_ftps_control_and_listing[False]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_real_ftp_and_ftps_control_and_listing[True]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_real_site_commands_multiline_and_failure[False]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_real_site_commands_multiline_and_failure[True]` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_review_cancel_stale_selection_and_dispatch` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_sequential_execution_listing_failure_and_secrets` | Passed (archived batch 17; carried forward) |
| `tests/test_server_commands.py::test_ui_command_runs_and_reports_real_server_response` | Passed (archived batch 17; carried forward) |
| `tests/test_sftp.py::test_sftp_roundtrip` | Passed (archived batch 17; carried forward) |
| `tests/test_sftp.py::test_unknown_ssh_host_rejected` | Passed (archived batch 17; carried forward) |
| `tests/test_site_connection_menu.py::test_create_move_rename_disable_and_remove_reflected_in_both_panes` | Passed (archived batch 17; carried forward) |
| `tests/test_site_folders.py::test_configuration_backup_keeps_empty_site_folders` | Passed (archived batch 17; carried forward) |
| `tests/test_site_folders.py::test_folder_click_does_not_connect_and_duplicate_is_rejected` | Passed (archived batch 17; carried forward) |
| `tests/test_site_folders.py::test_folder_create_move_rename_reopen_and_remove_keeps_sites` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_bad_xml_safe_errors[<!DOCTYPE sites [<!ENTITY s "secret">]><FileZilla3/>]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_bad_xml_safe_errors[<FileZilla3><SECRET>]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_bad_xml_safe_errors[<Unknown/>]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_csv` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_duplicates_and_export_roundtrip` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_filezilla_protocols_and_folders[0-0]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_filezilla_protocols_and_folders[1-3]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_filezilla_protocols_and_folders[3-2]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_filezilla_protocols_and_folders[4-0]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_filezilla_protocols_and_folders[6-4]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_flashfxp_xml` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_native_xml_protocols[0-4]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_native_xml_protocols[1-1]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_native_xml_protocols[2-2]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_native_xml_protocols[3-0]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_native_xml_protocols[4-3]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_sparse_defaults_unknown_modes_and_secure_upgrade` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_ftprush_utf16_and_container_detection` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_invalid_and_unknown_entries_skipped` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_size_limit` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_skip_unsupported_winscp_and_proxy` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_url_list_ipv6_and_percent_encoding` | Passed (archived batch 18; carried forward) |
| `tests/test_site_import.py::test_winscp_tls_mapping_and_defaults[0-4]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_winscp_tls_mapping_and_defaults[1-2]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_winscp_tls_mapping_and_defaults[2-1]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_import.py::test_winscp_tls_mapping_and_defaults[3-0]` | Passed (archived batch 17; carried forward) |
| `tests/test_site_multiselect.py::test_bulk_disable_move_copy_delete_preserves_credentials` | Passed (archived batch 18; carried forward) |
| `tests/test_site_multiselect.py::test_ctrl_shift_and_right_click_preserve_selection` | Passed (archived batch 18; carried forward) |
| `tests/test_site_multiselect.py::test_filtered_select_all_does_not_include_hidden_sites_or_folders` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values0]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values1]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values2]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values3]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values4]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values5]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_invalid_profile_options_are_rejected[values6]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_site_custom_listing_uses_configured_command` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_site_overrides_roundtrip_and_connection` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_site_transfer_policy_and_allow_list` | Passed (archived batch 18; carried forward) |
| `tests/test_site_properties.py::test_web_url_mapping_escapes_names_and_respects_root` | Passed (archived batch 18; carried forward) |
| `tests/test_sshfs_integration.py::test_live_sshfs_mount_and_unmount` | Skipped (archived batch 19; live SSHFS integration skipped) |
| `tests/test_tftp_recovery.py::test_failed_download_keeps_original[cancel]` | Passed (archived batch 20; carried forward) |
| `tests/test_tftp_recovery.py::test_failed_download_keeps_original[error]` | Passed (archived batch 20; carried forward) |
| `tests/test_tftp_recovery.py::test_failed_download_keeps_original[wrong_tid]` | Passed (archived batch 20; carried forward) |
| `tests/test_tftp_recovery.py::test_lost_ack_and_duplicate_ack_do_not_amplify` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp_ui.py::test_keyboard_navigation_and_cancel_responsiveness` | Passed (archived batch 20; carried forward) |
| `tests/test_tftp.py::test_accessible_dialog` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_cancel_before_io` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[False-0]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[False-1]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[False-1500]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[False-512]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[True-0]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[True-1]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[True-1500]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_real_udp_transfer[True-512]` | Passed (archived batch 19; carried forward) |
| `tests/test_tftp.py::test_timeout_preserves_destination` | Passed (archived batch 19; carried forward) |

## File browsing and comparison (89 tests: 89 passed, 0 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_compare_menu.py::test_busy_comparison_does_not_change_rows` | Passed (archived batch 03; carried forward) |
| `tests/test_compare_menu.py::test_compare_options_and_selection` | Passed (archived batch 03; carried forward) |
| `tests/test_compare_menu.py::test_listing_change_clears_comparison_and_filter_survives` | Passed (archived batch 03; carried forward) |
| `tests/test_compare_menu.py::test_pane_filter_toolbar_can_expand` | Passed (previous targeted batch; carried forward) |
| `tests/test_complete_markers.py::test_only_empty_remote_complete_markers_are_skipped[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_complete_markers.py::test_only_empty_remote_complete_markers_are_skipped[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_copy_names_context.py::test_copy_names_for_one_folder_and_select_all_in_right_pane` | Passed (archived batch 05; carried forward) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[ab-Auto Overwrite]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[ab-Auto Rename]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[ab-Auto Resume]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[ab-Auto Skip]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdef-Auto Overwrite]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdef-Auto Rename]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdef-Auto Resume]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdef-Auto Skip]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdefghi-Auto Overwrite]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdefghi-Auto Rename]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdefghi-Auto Resume]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_all_actions_for_all_size_relations[abcdefghi-Auto Skip]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_cancelled_resume_never_overwrites` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[larger-download]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[larger-fxp]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[larger-upload]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[same-download]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[same-fxp]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[same-upload]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[smaller-download]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[smaller-fxp]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_default_conflict_actions[smaller-upload]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_download_resume_failure_preserves_target` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_file_exists_ui_persistence` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_recursive_conflicts_do_not_reapply_manual_skip` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_rejected_upload_resume_overwrite_fallback[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_rejected_upload_resume_overwrite_fallback[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_file_exists.py::test_rename_avoids_existing_numbered_file_and_partial` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_folder_activation.py::test_double_click_enters_real_remote_folder[False]` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_activation.py::test_double_click_enters_real_remote_folder[True]` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_activation.py::test_folder_double_click_busy_guard` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_activation.py::test_remote_folder_double_click_without_prior_selection[False]` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_activation.py::test_remote_folder_double_click_without_prior_selection[True]` | Passed (archived batch 07; carried forward) |
| `tests/test_folder_sizes.py::test_directory_size_column_shows_reported_bytes_immediately` | Passed (previous targeted batch; carried forward) |
| `tests/test_folder_sizes.py::test_empty_directory_has_parent_row_that_navigates_up` | Passed (previous targeted batch; carried forward) |
| `tests/test_folder_sync.py::test_buffered_recovery_preserves_each_sync_fingerprint` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_comparison_rules_directions_and_conflicts` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_ftp_ftps_sync_through_queue[False-False]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_ftp_ftps_sync_through_queue[False-True]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_ftp_ftps_sync_through_queue[True-False]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_ftp_ftps_sync_through_queue[True-True]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_guard_metadata_and_path_escape_rejected` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_preview_cancellation_and_changed_pane_guard` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_preview_is_read_only_and_queues_recursive_copy_with_history` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_real_preview_worker_and_paged_actions` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_remote_symlink_root_is_blocked` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_scan_limit_cancel_and_roots` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_stale_preview_fails_without_overwriting[destination]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_stale_preview_fails_without_overwriting[source]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_stale_preview_fails_without_overwriting[type]` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_two_way_conflicts_are_explicit_and_recovery_keeps_guard` | Passed (archived batch 08; carried forward) |
| `tests/test_folder_sync.py::test_unknown_newer_conflict_stays_skipped_and_changed_pane_cannot_queue` | Passed (archived batch 08; carried forward) |
| `tests/test_list_mode.py::test_list_commands_against_ftp_server[Custom-LIST -a-LIST -a]` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_list_mode.py::test_list_commands_against_ftp_server[Custom-LIST -al-LIST -al]` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_list_mode.py::test_list_commands_against_ftp_server[Default (LIST)-LIST -al-LIST]` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_list_mode.py::test_list_commands_against_ftp_server[Show Hidden Files-LIST -al-LIST -a]` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_list_mode.py::test_list_options_layout_and_persistence` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_list_mode.py::test_list_parser_preserves_names_and_detects_links` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_list_mode.py::test_mlsd_preferred_and_selected_list_used_when_unavailable` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_active_ftp_uses_configured_local_ip_and_port_range` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_changing_data_folder_migrates_and_merges_saved_sites` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_connect_retries_only_during_connection_setup` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_connection_rejects_invalid_active_port_range` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_http_connect_proxy_tunnels_target_without_local_dns` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_passive_listing_fallback_retries_once_in_active_mode` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_proxy_forces_passive_mode_for_active_profile` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_proxy_password_is_saved_when_options_dialog_closes` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_proxy_password_is_written_only_to_encrypted_vault` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_proxy_password_save_failure_does_not_block_dialog_close` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_proxy_profiles_switch_and_encrypt_each_password` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_site_timezone_formats_utc_mlsd_timestamps` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_socks5_proxy_keeps_target_hostname_for_remote_dns` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_options.py::test_window_new_connection_uses_proxy_and_transport_settings` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_ui.py::test_ident_options_persist_and_control_listener` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_openssl_branches.py::test_ssl_options_page_and_switch_persistence` | Passed (Preferences connection modularization batch 2026-11-19) |
| `tests/test_listing_performance.py::test_automatic_utf8_names_roundtrip` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_directory_cache_bounded_by_entries` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_encoding_selection[Automatic-211 End-cp1251]` | Passed (archived batch 10; carried forward) |
| `tests/test_listing_performance.py::test_encoding_selection[Automatic-211-Features\n UTF8\n211 End-utf-8]` | Passed (archived batch 10; carried forward) |
| `tests/test_listing_performance.py::test_encoding_selection[cp1252- UTF8-cp1252]` | Passed (archived batch 10; carried forward) |
| `tests/test_listing_performance.py::test_encoding_selection[shift_jis--shift_jis]` | Passed (archived batch 10; carried forward) |
| `tests/test_listing_performance.py::test_essential_mlsd_requests_facts_once_and_keeps_size_type` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_large_listing_batches_preserve_entries_and_server_order` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_large_local_listing_can_be_superseded_without_stale_rows` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_legacy_listing_download_rename_roundtrip[False-cp1251-\u041c\u043e\u0441\u043a\u0432\u0430.txt]` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_legacy_listing_download_rename_roundtrip[False-cp1252-caf\xe9 \u20ac.txt]` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_legacy_listing_download_rename_roundtrip[False-shift_jis-\u65e5\u672c.txt]` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_legacy_listing_download_rename_roundtrip[True-cp1251-\u041c\u043e\u0441\u043a\u0432\u0430.txt]` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_legacy_listing_download_rename_roundtrip[True-cp1252-caf\xe9 \u20ac.txt]` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_legacy_listing_download_rename_roundtrip[True-shift_jis-\u65e5\u672c.txt]` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_sftp_pipeline_setting` | Passed (archived batch 11; carried forward) |
| `tests/test_listing_performance.py::test_site_listing_options_override_global_and_reject_invalid_values` | Passed (archived batch 11; carried forward) |
| `tests/test_sort.py::test_clicking_visible_header_sorts_numeric_sizes` | Passed (archived batch 19; carried forward) |
| `tests/test_sort.py::test_dates_are_chronological_across_listing_formats` | Passed (archived batch 19; carried forward) |
| `tests/test_sort.py::test_header_global_overrides_and_selection_without_refetch` | Passed (archived batch 19; carried forward) |
| `tests/test_sort.py::test_independent_folder_file_and_location_rules` | Passed (archived batch 19; carried forward) |
| `tests/test_sort.py::test_reference_defaults_and_independent_controls` | Passed (archived batch 19; carried forward) |
| `tests/test_sort.py::test_separate_links_and_tie_order` | Passed (archived batch 19; carried forward) |

## Workspace, layout, and accessibility (175 tests: 175 passed, 0 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_accessibility.py::test_builtin_theme_text_contrast_and_focus_styles` | Passed (archived batch 01; carried forward) |
| `tests/test_accessibility.py::test_logs_do_not_trap_tab_and_workspace_height_has_keyboard_control` | Passed (archived batch 01; carried forward) |
| `tests/test_accessibility.py::test_options_pages_scroll_at_large_fonts_and_close_stays_visible` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_accessibility.py::test_site_fields_follow_visual_keyboard_order_and_password_is_protected` | Passed (archived batch 01; carried forward) |
| `tests/test_accessibility.py::test_read_only_text_and_static_labels_can_be_selected_and_copied` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_manager_lists_actions_scopes_and_searches` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_shortcuts_persist_clear_and_restore_factory_defaults` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_conflicting_shortcuts_are_explained_and_not_applied` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_hotkeys.py::test_key_sequence_editor_can_clear_selected_shortcut` | Passed (copy/hotkey batch 2026-11-18) |
| `tests/test_accessibility.py::test_visible_controls_have_screen_reader_names` | Passed (copy/hotkey follow-up 2026-11-18) |
| `tests/test_colors.py::test_background_ftp_listings_keep_pane_identity_and_redact` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_colors.py::test_defaults_and_independent_themes` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_colors.py::test_legacy_selection_and_invalid_colors_fall_back` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_colors.py::test_log_categories_recolor_existing_text_and_background` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_colors.py::test_picker_cancel_and_custom_color` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_colors.py::test_queue_persists_pane_identity_and_accepts_old_descriptions` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_colors.py::test_trace_listener_receives_only_redacted_messages` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_column_sizing.py::test_double_clicking_a_column_divider_fits_that_column` | Passed (archived batch 03; carried forward) |
| `tests/test_column_sizing.py::test_every_pane_and_queue_divider_is_draggable` | Passed (archived batch 03; carried forward) |
| `tests/test_column_sizing.py::test_fit_and_manual_width_survive_refresh_and_window_resize` | Passed (release validation 2026-11-16) |
| `tests/test_command_bar.py::test_command_bar_layout_and_connection_state` | Passed (archived batch 03; carried forward) |
| `tests/test_command_bar.py::test_enter_cancel_and_no_history` | Passed (archived batch 03; carried forward) |
| `tests/test_command_bar.py::test_inline_command_real_response_in_log` | Passed (archived batch 03; carried forward) |
| `tests/test_confirm.py::test_close_queued_workspace_preserves_files_and_cancel_is_nonmutating` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_connected_workspace_cancel_accept_and_disabled` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_export_and_delete_saved_queue_only` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_local_delete_checkbox_only_controls_local_files` | Passed (release validation 2026-11-16) |
| `tests/test_confirm.py::test_queue_activation_during_prompt_prevents_removal` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_reference_defaults_persistence` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_remove_queue_items_confirm_and_active_guard` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_shutdown_confirm_cancel_disable_and_work_guard` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_shutdown_failure_stays_open` | Passed (archived batch 04; carried forward) |
| `tests/test_confirm.py::test_title_bar_close_does_not_wait_for_exit_confirmation` | Passed (previous targeted batch; carried forward) |
| `tests/test_confirm.py::test_title_bar_close_requests_cancellation_of_active_transfer` | Passed (previous targeted batch; carried forward) |
| `tests/test_confirm.py::test_windows_shutdown_command_has_no_force_flag` | Passed (archived batch 04; carried forward) |
| `tests/test_conflict_dialog.py::test_conflict_queue_worker_and_history` | Passed (archived batch 04; carried forward) |
| `tests/test_conflict_dialog.py::test_reliability_preferences_persist` | Passed (archived batch 04; carried forward) |
| `tests/test_conflict_dialog.py::test_resume_remaining_reprompts_when_unavailable` | Passed (archived batch 04; carried forward) |
| `tests/test_conflict_dialog.py::test_serialized_choices_apply_remaining_and_cancel` | Passed (archived batch 04; carried forward) |
| `tests/test_conflict_dialog.py::test_sync_preserves_approved_conflict_and_hashes` | Passed (archived batch 04; carried forward) |
| `tests/test_conflict_dialog.py::test_verification_failure_is_not_automatically_retried` | Passed (archived batch 04; carried forward) |
| `tests/test_display.py::test_completion_flash_honors_option` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_controls_defaults_and_persistence` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_download_listing_timer_keeps_selection` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_folder_icons_turn_dark_and_refresh_with_theme` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_indicator_separator_and_selection` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_local_hidden_address_size_and_focus` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_sites_grouping_recent_and_menu_visibility` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_toolbar_disconnect_targets_its_own_pane` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_display.py::test_tray_visibility_animation_and_safe_fallback` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_docking.py::test_dock_float_tab_hide_restore_and_return_preserves_widgets` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_docking.py::test_docking_lock_and_reset` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_docking.py::test_docking_restores_in_new_window` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_docking.py::test_floating_dock_geometry_is_saved_and_restored` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_edit_menu.py::test_reference_and_active_pane` | Passed (menu regression batch 2026-11-18) |
| `tests/test_edit_menu.py::test_rename_preflight` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_clone_local_locations_filters_without_queue` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_clone_remote_site_path_and_independent_profile` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_configuration_roundtrip_excludes_secrets_and_keeps_types` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_explicit_tray_action_and_missing_tray_fallback` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_menu_order_shortcuts_and_close_state` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_reject_invalid_backup_before_mutation[format]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_reject_invalid_backup_before_mutation[key]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_reject_invalid_backup_before_mutation[site]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_reject_invalid_backup_before_mutation[transfer]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_reject_invalid_backup_before_mutation[type]` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_restore_creates_recovery_and_fresh_credentials_preserves_data_path` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_restore_ui_cancel_busy_guard_and_success_exits` | Passed (menu regression batch 2026-11-18) |
| `tests/test_file_menu.py::test_restore_write_failure_rolls_back_preferences_and_sites` | Passed (menu regression batch 2026-11-18) |
| `tests/test_font_zoom.py::test_ctrl_wheel_changes_only_target_pane_and_persists` | Passed (archived batch 08; carried forward) |
| `tests/test_font_zoom.py::test_icon_wheel_options_partial_steps_direction_and_limits` | Passed (archived batch 08; carried forward) |
| `tests/test_font_zoom.py::test_icon_wheel_over_buttons_applies_once_to_all_toolbars_and_new_tabs` | Passed (archived batch 08; carried forward) |
| `tests/test_font_zoom.py::test_partial_wheel_steps_limits_and_font_reset` | Passed (archived batch 08; carried forward) |
| `tests/test_font_zoom.py::test_queue_and_logs_zoom_independently_and_plain_wheel_does_not_zoom` | Passed (archived batch 08; carried forward) |
| `tests/test_font_zoom.py::test_workspace_height_wheel_respects_preference_and_layout_lock` | Passed (archived batch 08; carried forward) |
| `tests/test_font_zoom.py::test_workspace_text_zoom_and_disabled_text_option` | Passed (archived batch 08; carried forward) |
| `tests/test_fonts.py::test_application_and_logs_override_stylesheet_and_persist` | Passed (archived batch 08; carried forward) |
| `tests/test_fonts.py::test_cancel_and_default_reset` | Passed (archived batch 08; carried forward) |
| `tests/test_fonts.py::test_independent_fonts_apply_to_all_workspaces_and_new_panes` | Passed (archived batch 08; carried forward) |
| `tests/test_fonts.py::test_invalid_saved_font_uses_default` | Passed (archived batch 08; carried forward) |
| `tests/test_fonts.py::test_legacy_font_deserializes_instead_of_becoming_family_name` | Passed (archived batch 08; carried forward) |
| `tests/test_fonts.py::test_typed_size_accept_cancel_and_reopen` | Passed (archived batch 08; carried forward) |
| `tests/test_header_rows.py::test_bad_saved_order_and_external_drop_rejected` | Passed (archived batch 09; carried forward) |
| `tests/test_header_rows.py::test_move_drop_keyboard_and_restore` | Passed (archived batch 09; carried forward) |
| `tests/test_header_rows.py::test_shared_rows_height_and_icons_persist` | Passed (archived batch 09; carried forward) |
| `tests/test_layout.py::test_captions_and_tab_style` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_layout.py::test_defaults_and_persistence` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_layout.py::test_native_tabs_and_dropdown_navigation` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_layout.py::test_pane_tabs_busy_guard_and_large_icons` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_layout.py::test_portrait_layout_and_site_local_order` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_layout.py::test_toolbar_paint_and_panel_buffer_switches` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_layout.py::test_wrapping_selection_closing_width_and_bottom` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_local_drive_menu.py::test_drive_failure_leaves_recent_paths_usable` | Passed (archived batch 11; carried forward) |
| `tests/test_local_drive_menu.py::test_drive_list_refreshes_and_does_not_leak_into_remote_menu` | Passed (archived batch 11; carried forward) |
| `tests/test_local_drive_menu.py::test_local_dropdown_drives_folders_and_navigation` | Passed (archived batch 11; carried forward) |
| `tests/test_mark_menu.py::test_filter_clear_export_sort_and_refresh` | Passed (menu regression batch 2026-11-18) |
| `tests/test_mark_menu.py::test_mask_cancel_and_text_shortcuts` | Passed (menu regression batch 2026-11-18) |
| `tests/test_mark_menu.py::test_menu_and_keyboard_marks_are_independent_of_selection` | Passed (menu regression batch 2026-11-18) |
| `tests/test_mark_menu.py::test_remote_paths_and_mask_navigation_guard` | Passed (menu regression batch 2026-11-18) |
| `tests/test_mark_menu.py::test_workspace_and_busy_guards` | Passed (menu regression batch 2026-11-18) |
| `tests/test_mouse.py::test_edit_queue_validates_and_source_navigation` | Passed (archived batch 11; carried forward) |
| `tests/test_mouse.py::test_explorer_context_dispatch` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_external_drop_holds_and_recovery_retains_it` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_file_double_click_dispatch_and_folder_navigation` | Passed (archived batch 11; carried forward) |
| `tests/test_mouse.py::test_hold_survives_other_transfers_until_play` | Passed (archived batch 11; carried forward) |
| `tests/test_mouse.py::test_log_actions_and_empty_tabs` | Passed (release validation 2026-11-16) |
| `tests/test_mouse.py::test_mouse_choices_defaults_and_persistence` | Passed (archived batch 11; carried forward) |
| `tests/test_mouse.py::test_paste_gate_and_local_urls` | Passed (archived batch 11; carried forward) |
| `tests/test_mouse.py::test_paste_key_in_new_workspace_and_context_event` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_real_explorer_menu_creation_and_cleanup` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_remote_view_edit_and_changed_file_refusal[False]` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_remote_view_edit_and_changed_file_refusal[True]` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_site_double_click_cancels_single_click` | Passed (archived batch 11; carried forward) |
| `tests/test_mouse.py::test_source_folder_after_original_workspace_closed` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_text_viewer_rejects_binary_invalid_and_large[binary]` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_text_viewer_rejects_binary_invalid_and_large[invalid_utf8]` | Passed (archived batch 12; carried forward) |
| `tests/test_mouse.py::test_text_viewer_rejects_binary_invalid_and_large[oversize]` | Passed (archived batch 12; carried forward) |
| `tests/test_panel_positions.py::test_moved_panels_and_panes_restore_on_next_window` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_panel_positions.py::test_panel_order_collapsed_log_and_bad_state` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_tools_menu.py::test_tools_reference_and_shortcuts` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_tools_menu.py::test_tools_shared_preferences_and_layout` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_tools_menu.py::test_tools_debug_logging_starts_and_exposes_log_path` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_ui.py::test_compare_folder_contents_marks_unique_and_different_files` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_connect_saved_site_prompts_and_remembers_password` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_drop_rejects_urls_and_honors_cancel` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_editing_site_protocol_keeps_custom_port` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_external_drop_copies_folders_and_preserves_sources` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_import_selection_and_failed_save` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_new_site_protocol_defaults_respect_custom_port` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_pane_sites_menu_lists_imported_site_manager_profiles` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_pane_toolbar_exposes_ftp_rush_actions_and_shortcuts` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_preferences_navigation_and_connection_settings` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_ui.py::test_quick_connect_protocol_change_preserves_custom_port` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_reference_style_shell_exposes_quick_connect_and_classic_work_areas` | Passed (release validation 2026-11-16) |
| `tests/test_ui.py::test_single_connection_site_serializes_queue_jobs` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_site_password_uses_secure_vault_and_is_excluded_from_profile` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_site_properties_uses_category_layout_and_saves_description` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_site_selection_and_explicit_connect_and_options_section` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_sites_import_preview_cancel_and_save` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_switch_remote_servers_swaps_profiles_and_directories` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_transfer_queue_runs_parallel_jobs_up_to_site_limit` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_ui_prompts_then_pins_untrusted_ftps_certificate` | Passed (archived batch 21; carried forward) |
| `tests/test_ui.py::test_ui_upload_queue_and_workspaces` | Passed (archived batch 21; carried forward) |
| `tests/test_workspaces_tray.py::test_close_then_restart_does_not_resurrect_workspace` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_context_menu_repeated_native_events` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_corrupt_workspace_session_falls_back` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_duplicate_and_close_others_guard` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_follow_os_changes_live_and_fixed_theme_stays_fixed` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_layout_choice_overrides_hidden_restored_workspace` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_manual_download_held_queue_and_close_guard` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_manual_download_real_ftp` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_name_validation_and_cancel` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_reset_cancel_changes_nothing` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_reset_layout_preserves_work_and_names` | Passed (release validation 2026-11-16) |
| `tests/test_workspaces_tray.py::test_session_restart_names_count_selection_and_splitters` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_smoke_window_isolates_workspace_settings` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_tray_menu_reference_and_shared_actions` | Passed (archived batch 23; carried forward) |
| `tests/test_workspaces_tray.py::test_workspace_double_click_and_all_name_surfaces` | Passed (archived batch 23; carried forward) |

## Security, credentials, and TLS (83 tests: 81 passed, 0 failed, 2 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_credentials.py::test_concurrent_vault_writers_preserve_every_record` | Passed (archived batch 05; carried forward) |
| `tests/test_credentials.py::test_legacy_password_migrates_into_encrypted_file` | Passed (archived batch 05; carried forward) |
| `tests/test_credentials.py::test_plaintext_backend_refused_os_backend_selected_from_chain` | Passed (archived batch 05; carried forward) |
| `tests/test_credentials.py::test_system_vault_smoke_test_removes_temporary_record` | Passed (archived batch 05; carried forward) |
| `tests/test_credentials.py::test_vault_encrypts_and_roundtrips_site_and_proxy_passwords` | Passed (archived batch 05; carried forward) |
| `tests/test_credentials.py::test_vault_rejects_tampered_ciphertext` | Passed (archived batch 05; carried forward) |
| `tests/test_key_manager.py::test_form_background_save_and_worker_unlock` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_generate_store_protection_and_export[ECDSA key for SFTP-521]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_generate_store_protection_and_export[Ed25519 key for SFTP-256]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_generate_store_protection_and_export[RSA key for SFTP-2048]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_generate_store_protection_and_export[X.509 certificate for FTPS (ECDSA)-384]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_generate_store_protection_and_export[X.509 certificate for FTPS (RSA)-2048]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_import_formats_matching_and_weak_rejection` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_key_manager_reference_site_and_prompts` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_managed_ftps_modes[FTPS \xb7 AUTH SSL]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_managed_ftps_modes[FTPS \xb7 AUTH TLS]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_managed_ftps_modes[FTPS \xb7 Implicit TLS]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_managed_sftp_public_key_auth[ECDSA key for SFTP-384]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_managed_sftp_public_key_auth[Ed25519 key for SFTP-256]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_managed_sftp_public_key_auth[RSA key for SFTP-2048]` | Passed (archived batch 10; carried forward) |
| `tests/test_key_manager.py::test_tls_requires_and_receives_managed_certificate` | Passed (archived batch 10; carried forward) |
| `tests/test_openssl_branches.py::test_adapter_hash_cannot_be_replaced` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_branch_selection_preserves_both_channels_and_requires_adapter` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_failed_4_runtime_can_receive_newer_update` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_official_package_url_allowlist[http://github.com/openssl/installer/releases/download/x/a.msi]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_official_package_url_allowlist[https://github.com.evil.test/openssl/installer/releases/download/a]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_official_package_url_allowlist[https://github.com/other/repo/releases/download/x/a.msi]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_official_package_url_allowlist[https://user@github.com/openssl/installer/releases/download/a]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_old_per_executable_selection_migrates_once` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_package_checksum_failure_preserves_selection` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_real_official_package_extract_probe_and_select` | Skipped (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_branches.py::test_release_filter_and_checksums` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_annotated_tag_is_resolved_and_invalid_commit_rejected` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_corrupt_download_does_not_change_selection` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_daily_checks_and_no_downgrade` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_download_source_allowlist[http://api.github.com/repos/python/cpython-bin-deps/tags]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_download_source_allowlist[https://api.github.com.evil.test/repos/python/cpython-bin-deps/tags]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_download_source_allowlist[https://raw.githubusercontent.com/other/repo/library.dll]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_end_of_life_36_line_is_rejected` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_failed_updated_desktop_falls_back_without_loading_bad_tls` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_numeric_stable_release_selection` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_pending_update_is_not_downgraded` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_probe_failure_preserves_previous_runtime` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_ready_replaces_stale_pending_status` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_real_windows_pair_loads_and_negotiates_verified_tls` | Passed (0.147.4 release-focused batch 2026-11-18) |
| `tests/test_openssl_updates.py::test_rejected_runtime_cannot_be_reactivated` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_successful_desktop_handoff_does_not_launch_second_window` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_successful_pair_is_published_only_after_probe` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_tampering_wrong_architecture_and_path_traversal` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_ui_controls_persist_and_manual_check_is_explicit` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_version_rejects_other_branches_prereleases_and_paths[../3.5.9]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_version_rejects_other_branches_prereleases_and_paths[2.0.0]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_version_rejects_other_branches_prereleases_and_paths[3.5.09]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_version_rejects_other_branches_prereleases_and_paths[3.5.9-beta1]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_openssl_updates.py::test_version_rejects_other_branches_prereleases_and_paths[5.0.0]` | Passed (OpenSSL branch update 2026-11-18) |
| `tests/test_site_tls_files.py::test_tls_file_paths_persist_per_server` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_editor_accepts_auth_ssl_and_nat_checkbox[False]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_editor_accepts_auth_ssl_and_nat_checkbox[True]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_nat_type_still_rejected[0]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_nat_type_still_rejected[1]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_nat_type_still_rejected[false]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_text_reports_field_without_contents[description-notes\x00]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_text_reports_field_without_contents[host-host\nNOOP]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_text_reports_field_without_contents[root-/path\x7f]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_invalid_text_reports_field_without_contents[username-user\rPASS]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_new_site_defaults_nat_masquerade_off` | Passed (previous targeted batch; carried forward) |
| `tests/test_site_validation.py::test_typed_nat_setting_validates_and_merges[False]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_typed_nat_setting_validates_and_merges[None]` | Passed (archived batch 18; carried forward) |
| `tests/test_site_validation.py::test_typed_nat_setting_validates_and_merges[True]` | Passed (archived batch 18; carried forward) |
| `tests/test_ssh_trust.py::test_cancel_does_not_save_and_changed_key_never_prompts` | Passed (archived batch 19; carried forward) |
| `tests/test_ssh_trust.py::test_fingerprint_dialog_requires_exact_independent_value` | Passed (archived batch 19; carried forward) |
| `tests/test_ssh_trust.py::test_parallel_trust_does_not_replace_an_existing_key` | Passed (archived batch 19; carried forward) |
| `tests/test_ssh_trust.py::test_unknown_host_verified_once_then_persisted_before_password_auth` | Passed (archived batch 19; carried forward) |
| `tests/test_ssh_trust.py::test_worker_prompt_runs_on_gui_and_observes_cancellation[False]` | Passed (archived batch 19; carried forward) |
| `tests/test_ssh_trust.py::test_worker_prompt_runs_on_gui_and_observes_cancellation[True]` | Passed (archived batch 19; carried forward) |
| `tests/test_trust_service.py::test_failed_persistence_does_not_mutate_live_trust` | Passed (archived batch 21; carried forward) |
| `tests/test_trust_service.py::test_persistence_exception_leaves_site_unchanged` | Passed (archived batch 21; carried forward) |
| `tests/test_trust_service.py::test_service_import_is_headless` | Passed (archived batch 21; carried forward) |
| `tests/test_trust_service.py::test_tls_pin_scope_context_and_mismatch` | Passed (archived batch 21; carried forward) |
| `tests/test_app_updates.py::test_downloaded_archive_digest_is_rechecked_in_private_windows_staging` | Passed (release validation 2026-11-16) |
| `tests/test_app_updates.py::test_windows_updater_directory_acl_blocks_inheritance_and_low_integrity_writes` | Passed (release validation 2026-11-16) |
| `tests/test_app_updates.py::test_available_release_downloads_automatically_without_prompt` | Passed (updater workflow batch 2026-11-16) |
| `tests/test_app_updates.py::test_verified_download_is_held_until_application_exit` | Passed (updater workflow batch 2026-11-16) |
## Automation, diagnostics, and CLI (49 tests: 49 passed, 0 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_associations.py::test_editor_validates_application_and_keeps_cancelled_data` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_empty_lists_crud_order_persistence_and_buttons` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_failed_download_does_not_open_application` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_invalid_rules[value0]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_invalid_rules[value1]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_invalid_rules[value2]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_invalid_rules[value3]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_invalid_rules[value4]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_invalid_saved_data_is_retained` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_launch_passes_filename_as_single_argument_without_shell` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_local_dispatch_and_unmatched_remote_fallback` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_order_masks_semicolons_and_independent_lists` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_remote_binary_edit_upload_conflict_and_cleanup[False]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_remote_binary_edit_upload_conflict_and_cleanup[True]` | Passed (archived batch 01; carried forward) |
| `tests/test_associations.py::test_view_sessions_are_distinct_never_upload_and_close_can_cancel` | Passed (archived batch 01; carried forward) |
| `tests/test_automation.py::test_atomic_save_failure_retains_previous_script` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_busy_queue_defers_schedules` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_cancel_running_script_stops_before_next_step` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_corrupt_configuration_is_preserved` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_due_schedule_claim_and_no_duplicate_start` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_editor_reorders_saves_and_cancel_does_not_mutate` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_failure_stops_steps_disables_schedule_and_redacts` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_ftp_script_live_transfer_commands_and_failure[False]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_ftp_script_live_transfer_commands_and_failure[True]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_one_time_run_is_consumed_and_manual_run_preserves_schedule` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_persistence_restart_interruption_and_second_instance_lock` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_schema_paths_and_repeat_coalescing` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines0]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines1]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines2]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines3]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines4]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines5]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines6]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_scripts_reject_prompted_or_credential_commands[lines7]` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_wizard_close_waits_for_test_and_does_not_open_workspace` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_automation.py::test_wizard_test_invalidation_and_new_workspace` | Passed (automation script extraction batch 2026-11-19) |
| `tests/test_cli_api.py::test_cli_api_auth_listing_transfers_queue_json_and_exit_codes` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_queue_script_maps_download_fxp_and_bookmark_paths` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_queue_script_resolves_paths_and_uses_transfer_queue` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_rush_cmdline_forwards_script_and_path_parameters` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_transfer_parser_accepts_flags_parameters_and_pascal_quotes` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_rush_compat.py::test_transfer_parser_rejects_unknown_flags_options_and_extra_code` | Passed (RushApp compatibility batch 2026-11-18) |
| `tests/test_diagnostics.py::test_missing_certificate_path_is_identified` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_diagnostics.py::test_debug_logging_supports_frozen_ssl_module_without_file_attribute` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_diagnostics.py::test_redact_echoed_passwords_private_keys_and_log_injection` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_diagnostics.py::test_ssh_diagnostics_and_global_failure_do_not_dump_exception_secrets` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_diagnostics.py::test_tls_failure_log_has_stage_and_certificate_reason` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_diagnostics.py::test_verified_tls_control_and_data_logged_without_payload` | Passed (diagnostics UI extraction batch 2026-11-19) |

## Release, packaging, and deployment (14 tests: 14 passed, 0 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_deployments.py::test_changed_backup_blocks_rollback` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_deployment_and_rollback_preserve_originals_and_other_files[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_deployment_and_rollback_preserve_originals_and_other_files[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_deployment_queue_roundtrip_and_evidence` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_interrupted_write_retains_backup_and_cannot_replay` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_later_remote_edits_block_rollback` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_revision_path_traversal_is_rejected` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_stale_local_preview_never_overwrites_remote` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_deployments.py::test_unsupported_native_hash_uses_readback` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_release_tools.py::test_failed_signing_never_claims_verification` | Passed (archived batch 16; carried forward) |
| `tests/test_release_tools.py::test_linux_signature_verified` | Passed (archived batch 16; carried forward) |
| `tests/test_release_tools.py::test_macos_notarization_and_gatekeeper` | Passed (archived batch 16; carried forward) |
| `tests/test_release_tools.py::test_native_backend_digest_must_match` | Passed (archived batch 16; carried forward) |
| `tests/test_release_tools.py::test_windows_signs_then_verifies` | Passed (archived batch 16; carried forward) |

## Core behavior and integration (153 tests: 152 passed, 1 failed, 0 skipped, 0 pending, 0 deferred)

| Test | Status |
|---|---|
| `tests/test_expanded_edge_cases.py::test_background_smoke_does_not_claim_saved_schedules` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_edge_cases.py::test_backup_remaps_site_proxy_reference_without_reusing_secret` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_edge_cases.py::test_calendar_dst_gap_and_repeat_keep_wall_time` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_edge_cases.py::test_source_passive_cpsv_fallback` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_alternate_login_transfers_with_its_own_password` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_calendar_coalesces_and_restores_month_end` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_directional_account_and_proxy_vault` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_gui_reloads_after_background_owner_releases` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_headless_runner_executes_due_script_without_window` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_help_search_and_monitor_baseline` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_ident_returns_distinct_site_identities` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_recursive_download_rejects_remote_link_ancestor` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_recursive_script_upload_download_and_skip[False]` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_recursive_script_upload_download_and_skip[True]` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_source_passive_fxp[False]` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_source_passive_fxp[True]` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_stat_listing_on_control_connection` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_task_xml_quotes_arguments_and_uses_current_user` | Passed (archived batch 06; carried forward) |
| `tests/test_expanded_features.py::test_xdupe_reports_skip_not_success_and_keeps_other_paths` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_active_pause_resume_and_stop` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_backend_owned_transfers_cannot_claim_payload_pause` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_certificate_ca_pin_and_rejection` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_checker_dialog_and_unknown_trust_does_not_write` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_feature_dialog_previews` | Passed (release validation 2026-11-16) |
| `tests/test_features_0117.py::test_hold_and_resume_selected_preserves_other_holds` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_real_ftp_payload_pause_and_play` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_cancel_preserves_destination` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_exec_ack_timeout` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_openssh_roundtrip[empty.bin]` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_openssh_roundtrip[plain.bin]` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_openssh_roundtrip[space and 'quote.bin]` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_rejects_unexpected_records[C0600 1 ../escape\n]` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_rejects_unexpected_records[C0600 9 file\n]` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_rejects_unexpected_records[D0755 0 file\n]` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_tree_and_rejected_exec` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_scp_ui_protocol_and_ssh_key_kind` | Passed (archived batch 06; carried forward) |
| `tests/test_features_0117.py::test_ssh_certification_and_plain_ftp` | Passed (archived batch 06; carried forward) |
| `tests/test_highlights.py::test_colors_across_workspaces_disable_and_skip_precedence` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_controls_editor_order_and_empty_persist` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_corrupt_settings_do_not_break_listing_or_dialog` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_first_match_case_regex_and_empty` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_invalid_saved_rules_rejected[None]` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_invalid_saved_rules_rejected[value1]` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_invalid_saved_rules_rejected[value2]` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_invalid_saved_rules_rejected[value3]` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_invalid_saved_rules_rejected[value4]` | Passed (archived batch 09; carried forward) |
| `tests/test_highlights.py::test_real_mask_and_color_editor` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_cancel_preserves_original` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_connection_can_force_list_when_mlsd_is_available` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_connection_resolves_hexadecimal_ipv4_host` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_forced_utf8_negotiates_after_ftp_login` | Passed (archived batch 10; carried forward) |
| `tests/test_integration.py::test_fxp` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_legacy_listing` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_local_to_local_recursive_transfer_and_resume` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_normalize_hexadecimal_ipv4_host[0X00000001-0.0.0.1]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_normalize_hexadecimal_ipv4_host[0x5f83ca7f-95.131.202.127]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_normalize_hexadecimal_ipv4_host[192.0.2.12-192.0.2.12]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_normalize_hexadecimal_ipv4_host[ftp.example.test-ftp.example.test]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_protected_fxp_does_not_downgrade` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_recursive_transfer` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_roundtrip_resume_and_operations[FTP \xb7 Unencrypted]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_roundtrip_resume_and_operations[FTPS \xb7 AUTH SSL]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_roundtrip_resume_and_operations[FTPS \xb7 AUTH TLS]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_roundtrip_resume_and_operations[FTPS \xb7 Implicit TLS]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_sfv` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_unsafe_names[..]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_unsafe_names[../file]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_unsafe_names[]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_unsafe_names[a/b]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_unsafe_names[a\\b]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_unsafe_names[bad\r\nDELE x]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_untrusted_certificate_rejected` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_untrusted_self_signed_certificate_can_be_trusted_by_site_pin[FTPS \xb7 AUTH SSL]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_untrusted_self_signed_certificate_can_be_trusted_by_site_pin[FTPS \xb7 AUTH TLS]` | Passed (archived batch 09; carried forward) |
| `tests/test_integration.py::test_untrusted_self_signed_certificate_can_be_trusted_by_site_pin[FTPS \xb7 Implicit TLS]` | Passed (archived batch 09; carried forward) |
| `tests/test_job_model.py::test_binding_keeps_widgets_and_secrets_out_of_model` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_buffered_ids_survive_unloading_and_materialization` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_expanded_children_have_distinct_stable_ids` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_identity_survives_recovery_retry_and_clear_undo` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_legacy_upgrade_duplicate_id_rejection_and_snapshot_parity` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_model_is_usable_without_qt` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_snapshot_preserves_guarded_job_metadata` | Passed (archived batch 10; carried forward) |
| `tests/test_job_model.py::test_validated_identity_states_and_changes` | Passed (archived batch 10; carried forward) |
| `tests/test_misc.py::test_bad_crc_preserves_completed_target[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_bad_crc_preserves_completed_target[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_crc_artifacts_cleanup_preserves_foreign_contents` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_crc_queue_retries_only_bad_file[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_crc_queue_retries_only_bad_file[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_empty_folders_and_same_parent` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_enter_created_remote_folder` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_manifest_rejects_escape[../outside]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_manifest_rejects_escape[/absolute]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_manifest_rejects_escape[C:\\outside]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_manifest_rejects_escape[sub/../../outside]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_misc_preferences` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_new_tabs_and_quick_connect` | Failed (deployment/transfer verification batch 2026-11-19; existing workspace-mode persistence assertion) |
| `tests/test_misc.py::test_remove_only_empty_targets_without_source` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_sfv_precedence_and_first_filename` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_verified_download_and_timestamp[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_verified_download_and_timestamp[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_misc.py::test_xcrc_and_unsupported_cached` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_path_dropdown.py::test_bookmarks_and_recent_items_navigate_and_guard_stale_scope` | Passed (archived batch 13; carried forward) |
| `tests/test_path_dropdown.py::test_corrupt_history_and_disconnected_menu` | Passed (archived batch 13; carried forward) |
| `tests/test_path_dropdown.py::test_dropdown_keyboard_and_editable_path` | Passed (archived batch 13; carried forward) |
| `tests/test_path_dropdown.py::test_recents_deduplicate_cap_and_keep_sites_separate` | Passed (archived batch 13; carried forward) |
| `tests/test_resize_lifetime.py::test_pending_column_resize_dies_with_closed_workspace` | Passed (archived batch 17; carried forward) |
| `tests/test_skip_rules.py::test_ftp_recursive_skip[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_ftp_recursive_skip[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_queue_marks_skips_without_failure[False]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_queue_marks_skips_without_failure[True]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_recursive_skip_local[Auto Transfer-expected1]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_recursive_skip_local[Both-expected0]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_recursive_skip_local[Manual Transfer-expected2]` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_skip_list_ui_and_import_transaction` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_skip_modes_masks_and_folder_scope` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_skip_rules.py::test_skip_selected_root_preserves_destination` | Passed (deployment/transfer modularization batch 2026-11-19) |
| `tests/test_themes.py::test_coordinated_themes_preserve_custom_colors_and_update_existing_widgets` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_themes.py::test_header_modes_and_white_command_contrast` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_themes.py::test_legacy_dark_migration_and_compact_add_button` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_themes.py::test_reselecting_preset_restores_header_and_color_defaults` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_themes.py::test_theme_persistence_and_system_palette_restore` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_themes.py::test_theme_text_contrast` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_themes.py::test_theme_activation_does_not_apply_a_changed_selection_twice` | Passed (preferences display modularization batch 2026-11-19) |
| `tests/test_toolbar_icons.py::test_remote_local_icon_tracks_mode` | Passed (archived batch 20; carried forward) |
| `tests/test_toolbar_icons.py::test_toolbar_descriptions_themes_and_direction` | Passed (archived batch 20; carried forward) |
| `tests/test_toolbar_visibility.py::test_named_hide_restore_and_shared_rows` | Passed (layout fixes batch 2026-11-18) |
| `tests/test_toolbar_visibility.py::test_pane_toolbar_visibility_persists_and_applies_to_new_workspaces` | Passed (archived batch 20; carried forward) |
| `tests/test_view_menu.py::test_current_edit_files_tracks_only_editors_and_preserves_sessions` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_function_key_commands_include_f6_options` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_panel_context_menu_can_hide_panel` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_panel_shortcuts_reveal_focus_and_restore_collapsed_panels` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_refresh_targets_last_active_pane_without_duplicate_shortcuts` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_sites_dropdown_context_menu_opens_site_editor` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_menu.py::test_view_menu_reference_order_shortcuts_and_submenus` | Passed (menu regression batch 2026-11-18) |
| `tests/test_view_options.py::test_auto_size_manual_width_and_mode_restoration` | Passed (layout fixes batch 2026-11-18; manual width persisted after auto-size was disabled) |
| `tests/test_view_options.py::test_grid_modes_are_exclusive_and_repaint_all_lists` | Passed (archived batch 22; carried forward) |
| `tests/test_view_options.py::test_listing_metadata_unix_mlsd_and_missing_facts` | Passed (archived batch 22; carried forward) |
| `tests/test_view_options.py::test_reference_defaults_and_live_column_visibility` | Passed (release validation 2026-11-16) |
| `tests/test_view_options.py::test_settings_survive_new_window_and_configuration_backup` | Passed (archived batch 22; carried forward) |
| `tests/test_view_options.py::test_sftp_numeric_ownership` | Passed (archived batch 22; carried forward) |
| `tests/test_view_options.py::test_workspace_columns_selection_and_live_status` | Passed (left-sidebar rendering batch 2026-11-18) |
| `tests/test_view_options.py::test_workspace_overview_fills_sidebar_width` | Passed (left-sidebar rendering batch 2026-11-18) |
| `tests/test_window_initializer.py::test_smoke_window_isolates_settings_initializes_services_and_wires_widgets` | Passed (diagnostics UI extraction batch 2026-11-19) |
| `tests/test_window_positions.py::test_dialog_geometry_is_remembered_per_window_type` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_window_positions.py::test_folder_successes_hide_then_group_clears[Download]` | Passed (archived batch 22; carried forward) |
| `tests/test_window_positions.py::test_folder_successes_hide_then_group_clears[Upload]` | Passed (archived batch 22; carried forward) |
| `tests/test_window_positions.py::test_main_window_geometry_saved_and_smoke_does_not_overwrite` | Passed (panel/window persistence batch 2026-11-18) |
| `tests/test_window_positions.py::test_manual_collapsed_queue_not_expanded_for_transfer` | Passed (release validation 2026-11-16) |
| `tests/test_window_positions.py::test_non_success_remains[Download]` | Passed (archived batch 22; carried forward) |
| `tests/test_window_positions.py::test_non_success_remains[Upload]` | Passed (archived batch 22; carried forward) |
| `tests/test_window_positions.py::test_successful_transfer_clears_after_completion[Download]` | Passed (archived batch 22; carried forward) |
| `tests/test_window_positions.py::test_successful_transfer_clears_after_completion[Upload]` | Passed (archived batch 22; carried forward) |
| `tests/test_window_positions.py::test_successful_upload_does_not_clear_failed_sibling` | Passed (archived batch 23; carried forward) |
| `tests/test_workspace_site_names.py::test_connection_updates_own_workspace_and_refresh_keeps_manual_name` | Passed (release validation 2026-11-16) |
| `tests/test_workspace_site_names.py::test_connections_do_not_replace_workspace_names` | Passed (archived batch 23; carried forward) |
| `tests/test_workspace_site_names.py::test_failed_connection_does_not_rename_workspace` | Passed (archived batch 23; carried forward) |

## Incremental queue capacity stress (2026-11-19)

Named batches used `tests/test_queue_incremental_tmp.py` with the buffered folder planner. Each size asserted bounded visible queue rows and exact item count after exporting and reloading the complete snapshot. The harness was temporary and removed after the runs; it made no application or permanent test changes. All sizes passed; no failing size found. Largest passing size: 150,000 items. Continue at 155,000.

| Size | Result | Planning seconds | Snapshot MiB |
|---:|---|---:|---:|
| 5,000 | Passed | 1.563 | 4.84 |
| 10,000 | Passed | 2.750 | 9.66 |
| 15,000 | Passed | 1.281 | 14.48 |
| 20,000 | Passed | 1.797 | 19.30 |
| 25,000 | Passed | 2.156 | 24.12 |
| 30,000 | Passed | 2.468 | 28.94 |
| 35,000 | Passed | 2.750 | 33.77 |
| 40,000 | Passed | 3.094 | 38.59 |
| 45,000 | Passed | 3.469 | 43.41 |
| 50,000 | Passed | 4.171 | 48.23 |
| 55,000 | Passed | 4.688 | 53.31 |
| 60,000 | Passed | 6.265 | 58.16 |
| 65,000 | Passed | 5.078 | 63.00 |
| 70,000 | Passed | 5.437 | 67.84 |
| 75,000 | Passed | 5.610 | 72.69 |
| 80,000 | Passed | 6.094 | 77.53 |
| 85,000 | Passed | 6.344 | 82.38 |
| 90,000 | Passed | 6.672 | 87.22 |
| 95,000 | Passed | 7.031 | 92.07 |
| 100,000 | Passed | 7.546 | 96.91 |
| 105,000 | Passed | 7.437 | 101.27 |
| 110,000 | Passed | 7.968 | 106.11 |
| 115,000 | Passed | 8.156 | 110.94 |
| 120,000 | Passed | 8.562 | 115.78 |
| 125,000 | Passed | 8.890 | 120.61 |
| 130,000 | Passed | 20.750 | 125.45 |
| 135,000 | Passed | 21.703 | 130.28 |
| 140,000 | Passed | 23.453 | 135.12 |
| 145,000 | Passed | 24.375 | 139.95 |
| 150,000 | Passed | 21.688 | 144.79 |

Batch 1: 5,000–100,000, 20 passed in 411.24 seconds. Batch 2: 105,000–150,000, 10 passed in 459.28 seconds. The 5,000-item baseline was also rerun individually and passed (1.671 seconds planning; 4.84 MiB snapshot). Pytest emitted two pre-existing Python deprecation warnings (`asyncore`/`asynchat`).

Batch 3 (2026-11-19): incremental queue capacity, 155,000–200,000 items in 5,000-item steps. All 10 passed; no first failing size found. Exact exported snapshot counts matched the planned item count at every step and the bounded visible queue row assertion held.

| Size | Result | Planning seconds | Snapshot MiB |
|---:|---|---:|---:|
| 155,000 | Passed | 22.454 | 149.62 |
| 160,000 | Passed | 20.734 | 154.46 |
| 165,000 | Passed | 22.297 | 159.29 |
| 170,000 | Passed | 22.797 | 164.13 |
| 175,000 | Passed | 23.687 | 168.96 |
| 180,000 | Passed | 24.843 | 173.80 |
| 185,000 | Passed | 26.093 | 178.63 |
| 190,000 | Passed | 38.875 | 183.47 |
| 195,000 | Passed | 39.390 | 188.30 |
| 200,000 | Passed | 42.984 | 193.14 |

Batch duration: 755.99 seconds. Pytest emitted the same two Python `asyncore`/`asynchat` deprecation warnings.

Batch 4 (2026-11-19): incremental queue capacity, 205,000–230,000 items in 5,000-item steps. All 6 passed; exact exported snapshot counts matched each planned size and bounded visible queue rows held. Largest passing size is now 230,000; no failing size found. Continue at 235,000.

| Size | Result | Planning seconds | Snapshot MiB |
|---:|---|---:|---:|
| 205,000 | Passed | 40.922 | 197.97 |
| 210,000 | Passed | 36.093 | 202.81 |
| 215,000 | Passed | 36.734 | 207.64 |
| 220,000 | Passed | 38.906 | 212.48 |
| 225,000 | Passed | 39.687 | 217.31 |
| 230,000 | Passed | 37.328 | 222.15 |

Batch duration: 533.85 seconds. Pytest emitted the same two Python `asyncore`/`asynchat` deprecation warnings.

Capacity-map closure (2026-11-19): per user direction, stop the incremental stress map at 230,000 files. All measured sizes from 5,000 through 230,000 in 5,000-file increments passed. No failing capacity was found; larger sizes are intentionally not scheduled.

## Clean full desktop regression checkpoint (2026-11-19)

The full desktop suite collected 1,148 tests. The clean run used the project test environment with Qt offscreen and the rclone no-browser override, so OAuth listener tests did not open a visible browser. At the user's request, pytest was interrupted after the last reported progress marker reached 56%; 28 failure markers had appeared at that checkpoint. The process was responsive but slow and used about 7 GB at its last resource check.

No JUnit XML or per-test completion list was saved by the interrupted run (`build/test-batches/full-desktop-2026-11-19.xml` was not created). Therefore this attempt is not included in the current pass/fail totals, and the exact identities of those 28 failures are not available. The safe resume is to rerun the suite from the beginning with a live result report enabled, using `QT_QPA_PLATFORM=offscreen`, `RCLONE_CONFIG_AUTH_NO_BROWSER=true`, and `.venv-test/Scripts/python.exe -m pytest -q --junitxml=build/test-batches/full-desktop-resume.xml`. The collection inventory was temporary and removed; prior individual test history above remains intact.

## Full desktop regression resume attempt (2026-11-20)

Reran the 1,148-test desktop suite with `QT_QPA_PLATFORM=offscreen`,
`RCLONE_CONFIG_AUTH_NO_BROWSER=true`, and JUnit reporting. The recorded
`.venv-test` interpreter was absent, so the available `.venv` environment was
used. Pytest reached approximately 83%; eleven failure markers had appeared
and several tests were skipped. The run was interrupted after it remained in a
single CPU-active test for several minutes with no progress markers. Pytest did
not write `build/test-batches/full-desktop-resume.xml`, so identities and
tracebacks for the failures are unavailable and this attempt is not included
in pass/fail totals. For the next diagnostic run, use verbose output alongside
JUnit reporting so the active test is visible during slow sections; do not
count this interrupted attempt as a completed suite.

The follow-up verbose diagnostic attempt on 2026-11-20 collected 1,116 tests
(the current checkout count) and reached approximately 15%. All reported tests
passed through `tests/test_conflict_dialog.py`; it was interrupted to change to
named test batches as directed. `build/test-batches/full-desktop-diagnostic.xml`
was not written, so the partial run is not counted in suite totals.

## 25-test desktop diagnostic batches (2026-11-20)

Each batch stores the exact collected node IDs in `fluxftp/build/test-batches/`
and a JUnit result file there. Run with Qt offscreen and rclone's no-browser
override. Only completed 25-test batches are included in these totals.

| Batch | Tests | Result | Duration | Coverage / artifacts |
|---:|---:|---|---:|---|
| 01 | 25 | Passed | 49.32s | First 25 collected node IDs; `desktop-batch-01-tests.txt`, `desktop-batch-01.xml` |
| 02 | 25 | Passed | 39.24s | Collected node IDs 26–50; `desktop-batch-02-tests.txt`, `desktop-batch-02.xml` |
| 03 | 25 | Passed | 11.04s | Collected node IDs 51–75; `desktop-batch-03-tests.txt`, `desktop-batch-03.xml` |
| 04 | 25 | Passed | 29.46s | Collected node IDs 76–100; `desktop-batch-04-tests.txt`, `desktop-batch-04.xml` |
| 05 | 25 | Passed | 32.60s | Collected node IDs 101–125; `desktop-batch-05-tests.txt`, `desktop-batch-05.xml` |
| 06 | 25 | Passed | 80.04s | Collected node IDs 126–150; `desktop-batch-06-tests.txt`, `desktop-batch-06.xml` |
| 07 | 25 | Passed | 61.86s | Collected node IDs 151–175; `desktop-batch-07-tests.txt`, `desktop-batch-07.xml` |
| 08 | 25 | Passed | 33.68s | Collected node IDs 176–200; `desktop-batch-08-tests.txt`, `desktop-batch-08.xml` |
| 09 | 25 | Passed | 23.06s | Collected node IDs 201–225; `desktop-batch-09-tests.txt`, `desktop-batch-09.xml` |
| 10 | 25 | Passed | 87.28s | Collected node IDs 226–250; `desktop-batch-10-tests.txt`, `desktop-batch-10.xml` |
| 11 | 25 | 24 passed, 1 failed | 22.37s | Collected node IDs 251–275; `desktop-batch-11-tests.txt`, `desktop-batch-11.xml`; failure: `tests/test_edit_menu.py::test_reference_and_active_pane` expects Edit-menu order without Options, while the menu includes the Options action required by the F6/Edit integration. |
| 12 | 25 | Passed | 61.53s | Collected node IDs 276–300; `desktop-batch-12-tests.txt`, `desktop-batch-12.xml` |
| 13 | 25 | Passed | 3.55s | Collected node IDs 301–325; `desktop-batch-13-tests.txt`, `desktop-batch-13.xml` |
| 14 | 25 | Passed | 118.40s | Collected node IDs 326–350; `desktop-batch-14-tests.txt`, `desktop-batch-14.xml` |
| 15 | 25 | Passed | 127.31s | Collected node IDs 351–375; `desktop-batch-15-tests.txt`, `desktop-batch-15.xml` |
| 16 | 25 | Passed | 26.31s | Collected node IDs 376–400; `desktop-batch-16-tests.txt`, `desktop-batch-16.xml` |
| 17 | 25 | Passed | 26.87s | Collected node IDs 401–425; `desktop-batch-17-tests.txt`, `desktop-batch-17.xml` |
| 18 | 25 | Passed | 1.74s | Collected node IDs 426–450; `desktop-batch-18-tests.txt`, `desktop-batch-18.xml` |
| 19 | 25 | Passed | 21.67s | Collected node IDs 451–475; `desktop-batch-19-tests.txt`, `desktop-batch-19.xml` |
| 20 | 25 | Passed | 23.87s | Collected node IDs 476–500; `desktop-batch-20-tests.txt`, `desktop-batch-20.xml` |
| 21 | 25 | Passed | 40.81s | Collected node IDs 501–525; `desktop-batch-21-tests.txt`, `desktop-batch-21.xml` |
| 22 | 25 | Passed | 42.33s | Collected node IDs 526–550; `desktop-batch-22-tests.txt`, `desktop-batch-22.xml` |
| 23 | 25 | 24 passed, 1 skipped | 12.34s | Collected node IDs 551–575; `desktop-batch-23-tests.txt`, `desktop-batch-23.xml`; Python printed Windows fatal exception `0x80010108` in `test_real_explorer_menu_creation_and_cleanup` at `explorer_menu.py:154`, although pytest completed with exit code 0. Track as a runtime anomaly, not a pytest failure. |
| 24 | 25 | 24 passed, 1 skipped | 9.72s | Collected node IDs 576–600; `desktop-batch-24-tests.txt`, `desktop-batch-24.xml` |
| 25 | 25 | Passed | 60.10s | Collected node IDs 601–625; `desktop-batch-25-tests.txt`, `desktop-batch-25.xml` |
| 26 | 25 | Passed | 1.00s | Collected node IDs 626–650; `desktop-batch-26-tests.txt`, `desktop-batch-26.xml` |
| 27 | 25 | Passed | 44.69s | Collected node IDs 651–675; `desktop-batch-27-tests.txt`, `desktop-batch-27.xml` |
| 28 | 25 | Passed | 90.43s | Collected node IDs 676–700; `desktop-batch-28-tests.txt`, `desktop-batch-28.xml` |
| 29 | 25 | Passed | 34.90s | Collected node IDs 701–725; `desktop-batch-29-tests.txt`, `desktop-batch-29.xml` |
| 30 | 25 | Passed | 25.74s | Collected node IDs 726–750; `desktop-batch-30-tests.txt`, `desktop-batch-30.xml` |
| 31 | 25 | Passed | 43.75s | Collected node IDs 751–775; `desktop-batch-31-tests.txt`, `desktop-batch-31.xml` |
| 32 | 25 | Passed | 3.30s | Collected node IDs 776–800; `desktop-batch-32-tests.txt`, `desktop-batch-32.xml` |
| 33 | 25 | Passed | 11.68s | Collected node IDs 801–825; `desktop-batch-33-tests.txt`, `desktop-batch-33.xml` |
| 34 | 25 | Passed | 26.07s | Collected node IDs 826–850; `desktop-batch-34-tests.txt`, `desktop-batch-34.xml` |
| 35 | 25 | Passed | 9.96s | Collected node IDs 851–875; `desktop-batch-35-tests.txt`, `desktop-batch-35.xml` |
| 36 | 25 | Passed | 15.72s | Collected node IDs 876–900; `desktop-batch-36-tests.txt`, `desktop-batch-36.xml` |
| 37 | 25 | 24 passed, 1 skipped | 59.11s | Collected node IDs 901–925; `desktop-batch-37-tests.txt`, `desktop-batch-37.xml` |
| 38 | 25 | Passed | 11.14s | Collected node IDs 926–950; `desktop-batch-38-tests.txt`, `desktop-batch-38.xml` |
| 39 | 25 | Passed | 107.61s | Collected node IDs 951–975; `desktop-batch-39-tests.txt`, `desktop-batch-39.xml` |
| 40 | 25 | Passed | 11.88s | Collected node IDs 976–1000; `desktop-batch-40-tests.txt`, `desktop-batch-40.xml` |
| 41 | 25 | Passed | 25.19s | Collected node IDs 1001–1025; `desktop-batch-41-tests.txt`, `desktop-batch-41.xml` |
| 42 | 25 | Passed | 34.33s | Collected node IDs 1026–1050; `desktop-batch-42-tests.txt`, `desktop-batch-42.xml` |
| 43 | 25 | Passed | 19.76s | Collected node IDs 1051–1075; `desktop-batch-43-tests.txt`, `desktop-batch-43.xml` |
| 44 | 25 | Passed | 101.74s | Collected node IDs 1076–1100; `desktop-batch-44-tests.txt`, `desktop-batch-44.xml` |
| 45 | 16 | Passed | 81.55s | Collected node IDs 1101–1116; `desktop-batch-45-tests.txt`, `desktop-batch-45.xml` |

Full diagnostic sweep complete: all 1,116 collected tests were run in 45
batches. Totals before the focused stale-expectation fix: 1,112 passed, 1
failed, 3 skipped. The sole failure was the stale Edit-menu expected list in
`tests/test_edit_menu.py`; it omitted the shared Options action that is required
in the Edit menu. Updated the expectation to include `Options…` before the
Toolbars separator. The original 11-marker interrupted run is not reproducible
from available artifacts; only this one failure was reproduced. Batch 11 was
rerun after the expectation update and passed all 25 tests, as recorded below.

| Batch rerun | Tests | Result | Duration | Artifacts |
|---:|---:|---|---:|---|
| 11 rerun | 25 | Passed | 13.50s | `desktop-batch-11-tests.txt`, `desktop-batch-11-rerun.xml` |

## v0.149.0 release validation batches (2026-11-21)

Release workflow smoke batch:

```text
.venv-test/Scripts/python.exe -m pytest -q tests/test_tools_menu.py tests/test_layout.py::test_portrait_layout_and_site_local_order tests/test_compare_menu.py::test_pane_filter_toolbar_can_expand tests/test_openssl_branches.py tests/test_openssl_updates.py tests/test_workspaces_tray.py::test_session_restart_names_count_selection_and_splitters tests/test_deployments.py tests/test_github_artifacts.py tests/test_gitlab_artifacts.py tests/test_cloud.py
78 passed, 2 skipped in 68.61s
```

New feature regression batch:

```text
.venv-test/Scripts/python.exe -m pytest -q tests/test_artifact_oauth.py tests/test_github_artifacts.py tests/test_gitlab_artifacts.py tests/test_deployment_compare.py tests/test_workspace_presets.py tests/test_auto_trading_config.py tests/test_trade_ledger.py tests/test_queue_engine.py tests/test_queue_storage_service.py tests/test_cli_api.py tests/test_deployments.py tests/test_tools_menu.py
87 passed in 53.30s
```

Both batches ran with Qt offscreen and rclone no-browser mode. The control API
tests continue using OS-assigned ephemeral loopback ports so they do not use
the live port 8765.

## v0.148.0 Windows package preflight (2026-11-20)

The maintained Windows package was built with local CPython 3.13.18, OpenSSL
3.5.9, SQLite 3.54.1, and the OpenSSL 4.1.0 adapter. The bundled rclone.exe
matches the executable SHA-256 recorded by the installed 0.147.5 package.
Packaged startup, credential-vault, and F6 shortcut smoke checks all exited 0.
The public ZIP passed the packaging script's archive integrity check:
`build/public-release/windows-0.148.0/FluxFTP-0.148.0-windows-x64.zip` (SHA-256
`fb334204fcaf3b8f31301cbc0a70dec3bc811d9861b3826c7944a65939ff2d28`).

The v0.148.0 tagged GitHub Actions release run `44875406604` completed
successfully for Windows, Ubuntu, Debian, and asset publishing. The published
Windows ZIP, Ubuntu portable archive, and Debian installer were downloaded;
each package SHA-256 matched its corresponding published checksum file. The
Windows package is installed at `D:\Program Files\FluxFTP`; its embedded
version is 0.148.0 and its executable hash matches the verified package. The
previous installation is preserved at `D:\Program Files\FluxFTP-backup-0.147.5`.

| Published artifact | SHA-256 |
|---|---|
| `FluxFTP-0.148.0-windows-x64.zip` | `fb334204fcaf3b8f31301cbc0a70dec3bc811d9861b3826c7944a65939ff2d28` |
| `FluxFTP-0.148.0-linux-x86_64-ubuntu24.04.tar.gz` | `61796dda96f2e696dd5623d8eed2ec4e6a6e3b287f8831e878b41f7d5a661c8a` |
| `fluxftp_0.148.0-1_amd64.deb` | `fe0cd35714bb67b8695fa96d2141f836e50d4346065a3ed928eeac95d241a67d` |

## v0.149.0 release package verification (2026-11-21)

The maintained Windows x64 package was built from source commit
`c6fb6bec78300b9f884e28f879f0b649bac24348`. Its startup, credential-vault and
F6 shortcut smoke checks passed. The Windows ZIP integrity check passed and
its embedded manifest reports version 0.149.0 and the expected source commit.
GitHub Actions validation run `44991042059` and published-release run
`44991853362` passed all Windows, Ubuntu, Debian and asset-publishing jobs.
All three downloaded public packages matched their corresponding published
SHA-256 files:

| Published artifact | SHA-256 |
|---|---|
| `FluxFTP-0.149.0-windows-x64.zip` | `516c678aa4d41e0a7714facb99b701083ed702fba9eb27cf790657a913f9ff57` |
| `FluxFTP-0.149.0-linux-x86_64-ubuntu24.04.tar.gz` | `31a7d735a3cef763504ba972be25cd4a994933e44b7f2cf02e76a3aec1a9f65a` |
| `fluxftp_0.149.0-1_amd64.deb` | `92716116fdefd0c0381f4676357e6f91501bbf1fa2d9b20955acb70d12e7dfea` |
