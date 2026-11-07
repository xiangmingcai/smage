# Smage — `main.py` split: Step 0 dependency inventory

> Status: **All batches (1–6) complete.**
> Baseline: commit `10181d7`, `src/smage/main.py` = **42,355 lines**.
> Current: `main.py` = **348 lines**; `core/`, `data/`, `widgets/`,
> `app/`, all 21 `modules/<name>/` packages, and `runners/` added.
> This file is the working checklist for the following steps; tick items as
> they land. Every line count below was measured with an AST pass, not
> estimated by eye.

## 0. How this inventory was produced

The file was parsed with `ast`; each top-level function, class and
`MainWindow` method was measured and classified on three axes:

| Axis | Test | Meaning |
|---|---|---|
| **Qt dependency** | source contains `QWidget`, `QDialog`, `pg.`, `Signal`, `QTimer`, `Qt`, … | needs a GUI/event loop; cannot live in a Qt-free module |
| **Window-state dependency** | body reads/writes `self._xxx` on `MainWindow` | cannot move unless that state is passed explicitly (`AppContext`) |
| **page-first** | first argument after `self` is named `page` | the method already operates on a module page object, so it can become a method of that page almost mechanically |

Definitions used below:

- **module logic** — methods whose name starts with a module prefix (`_gating_*`,
  `_unmix_*`, …), excluding the `_build_*_page` builders.
- **builder** — `_build_<module>_page`, 40–200 lines each, creates the page widgets.
- **shared** — everything else on `MainWindow`: data access, toolbar/lock/draft,
  project I/O, extensions, settings, workflow tree.
- **ctx API surface** — the shared `MainWindow` methods a module calls. This is
  the interface the future `AppContext` / `ModuleHost` must expose.

## 1. Baseline metrics

| Metric | Value |
|---|---|
| `main.py` total lines | 42,355 |
| Top-level functions | 118 (94 Qt-free, 24 Qt) |
| Top-level classes | 43 (incl. `MainWindow`) |
| Class methods | 1,334 |
| `MainWindow` span | L7,271–42,333 (**35,063 lines**) |
| `MainWindow` methods | 1,100 |
| Module logic methods | **896 methods / 27,056 lines** |
| Module page builders | 22 / 1,816 lines |
| Other shared `MainWindow` methods | 182 / ~4,939 lines |
| Class-level constants / docstrings in `MainWindow` | ~1,252 lines |
| Module methods that are **page-first** | **723 / 896 (81%)** |
| Module methods *not* page-first | 173 (manual review needed) |

**Key finding:** 81% of module code already takes the page object as its first
argument, and each page already owns 7–47 `page._<module>_*` state attributes.
The refactor is therefore mostly mechanical: `self._gating_x(page, …)` becomes
`page._gating_x(…)`, and the remaining `self._shared_y(…)` calls become
`self.ctx.shared_y(…)`.

## 2. Module weights and coupling

`logic` excludes the builder. `out` = other modules this module calls;
`in` = modules that call into this module. `state` = distinct `page._*`
attributes the module touches.

| Module | Builder | Methods | Logic lines | page-first | state | out → | in ← | Risk |
|---|---:|---:|---:|---:|---:|---|---|---|
| figure | 169 | 85 | 4,109 | 51 | 20 | cluster, data_clean, dimred, gate2col, gating, segmentation, subsample, unmix | area, gating, illumination, scale, segmentation | highest |
| gating | 42 | 100 | 3,114 | 90 | 45 | compensation, figure, segmentation, subsample | figure, gate2col, heatmap, illumination, scale, unmix | highest |
| unmix | 97 | 94 | 2,561 | 84 | 46 | compensation, gating | figure | high |
| segmentation | 87 | 58 | 1,939 | 41 | 39 | compensation, figure | export, feature, figure, gating | high |
| batch_align | 82 | 39 | 1,718 | 35 | 40 | compensation | — | medium-high |
| heatmap | 107 | 46 | 1,494 | 42 | 41 | compensation, gating, subsample | — | medium |
| feature | 84 | 44 | 1,284 | 38 | 29 | compensation, segmentation, subsample | — | medium |
| area | 86 | 44 | 1,217 | 42 | 38 | compensation, figure, subsample | — | high |
| dimred | 83 | 43 | 1,163 | 33 | 40 | compensation, subsample | figure | medium-high |
| illumination | 85 | 37 | 1,122 | 30 | 29 | compensation, figure, gating | — | medium |
| cluster | 84 | 38 | 1,075 | 31 | 47 | compensation, subsample | data_clean, figure | medium-high |
| export | 65 | 42 | 1,004 | 36 | 7 | compensation, segmentation | — | low |
| data_clean | 83 | 32 | 834 | 27 | 34 | cluster, compensation, subsample | figure | medium |
| feature_up | 77 | 29 | 813 | 27 | 19 | compensation, subsample | — | medium |
| gate2col | 71 | 29 | 716 | 22 | 15 | compensation, gating, subsample | figure | medium |
| subsample | 69 | 33 | 712 | 17 | 17 | compensation | 10 modules | medium |
| scale | 198 | 27 | 711 | 24 | 30 | compensation, figure, gating | — | medium |
| rename | 72 | 29 | 496 | 25 | 12 | compensation | compensation | low |
| compensation | 76 | 20 | 414 | 18 | 19 | rename | 18 modules | low |
| subset | 71 | 13 | 188 | 10 | 9 | compensation | — | low |
| import | 28 | 14 | 372 | 0 | 1 | — | — | special (state on window) |

### The five coupling hubs

These are the only helpers with wide fan-out. They must move **before** the
modules that use them.

| Hub | Used by | Target |
|---|---|---|
| `_comp_file_name` / `_rename_file_name` (display names) | 20 modules | `data/naming.py` |
| `_subsample_row_index`, `_subsample_sig`, `_subsample_visible_count`, `_subsample_cover_crop` | 10 modules | `data/sampling.py` |
| `_figure_record`, `_figure_render*`, `_figure_range_row`, `_figure_*_settings` | gating, scale, illumination, segmentation, area | `widgets/plot/` |
| `_gating_gates_for`, `_gating_state_init`, `_gating_build_content`, `_gating_render`, `_gating_rebuild_settings` | figure, gate2col, heatmap, unmix, scale, illumination | `data/gates_store.py` + `widgets/gate_editor/` |
| `_seg_applied_columns/_seg_mask_*`, `_cluster_*`, `_dimred_*`, `_dc_*`, `_g2c_*`, `_unmix_*` output adapters | figure (and data_clean→cluster) | `data/outputs.py` |

## 3. Target package layout

Line counts are the contents listed in this inventory, not the final file size.

```
src/smage/
  main.py                     348     entry + MainWindow assembly
  app/                        ~6,780
    workflow_tree.py                  tree, cards, +/Actions menus, lock actions
    module_host.py                    open/close, registry, lock/draft/session
    extensions.py                     ext config, manager dialog, process calls
    project_io.py                     menus, project open/save, window shell
    module_sizes.py                   storage-usage worker/labels
    logging_setup.py                  Qt message handler + excepthook
    delegates.py                      1,013 one-line module delegates
  core/                       ~1,380   Qt-free, no window state
    formatting.py
    scale_math.py
    pipeline.py
    gates.py
    feature.py
    heatmap.py
    sampling.py
    image_ops.py
    settings.py                       settings.json I/O (no dialog)
  data/                       ~2,890   Qt-free, takes AppContext
    context.py                        AppContext / Session
    files.py
    naming.py
    columns.py
    pool.py
    image_index.py                    index/manifest/load (Qt-free part)
    stores.py
    sampling.py
    outputs.py
    feature_outputs.py
    workers.py                        background threads (Qt-free)
  widgets/                    ~6,150   Qt, ctx passed explicitly
    resources.py
    panels.py
    picker.py
    colormap.py
    plot_axes.py
    plot/                             shared renderers (figure's engine)
    gate_editor/                      embedded gate tree (gating/unmix)
    image_level.py
    image_raster.py                   QRectF crops (moved in Batch 4)
    settings.py                       settings dialog
    ui_logger.py
  modules/                    ~29,630  one package per module (landed)
    import_/  export/  compensation/  rename/  subset/  subsample/
    feature_up/  gate2col/  feature/  heatmap/  scale/  illumination/
    data_clean/  batch_align/  dimred/  cluster/  area/  segmentation/
    unmix/  gating/  figure/
  runners/                             cellpose, stardist, flowsom, parc
```

**Import rule:** `app → modules → widgets → data → core`, never upward, never
sideways between modules. A module may only reach another module through
`data/` (stores, output adapters) or `widgets/` (shared widgets).

## 4. Inventory A — `core/` (Qt-free, no window state)

Everything here is a pure function taking arrays/dicts and returning values.
Two transitional exceptions are noted.

| Package | Symbols | Lines |
|---|---|---:|
| `core/formatting.py` | `_format_bytes`, `_format_sci`, `_format_number`, `_format_level`, `_round_level`, `_category_value_label`, `_json_stable`, `_fcs_safe_channel_names`, `_heatmap_export_text`, `_heatmap_cell_text` | 145 |
| `core/scale_math.py` | `_apply_scale_transform`, `_apply_scale_transforms`, `_forward_scale_value`, `_inverse_scale_value`, `_round_value_candidates`, `_nice_ticks` | 108 |
| `core/pipeline.py` | `_rename_applies`, `_rename_stage`, `_pipeline_touches`, `_pipeline_dependencies`, `_linear_combine`, `_evaluate_stages`, `_assign_batch_clusters`, `_apply_batch_align` | 300 |
| `core/gates.py` | `_point_in_polygon`, `_gate_mask`, `_gate_source_ids`, `_gate_group_id`, `_gate_geometry`, `_merge_gate_geometry`, `_resolve_gates_for_file`, `_gate_needed_columns`, `_gate_own_mask`, `_compute_gate_filter_mask`, `_gate_chain` | 288 |
| `core/feature.py` | `_feature_auto_name`, `_feature_safe_stem`, `_feature_count_name`, `_feature_aggregate_frame` | 98 |
| `core/heatmap.py` | `_heatmap_sample_counts`, `_heatmap_normalize`, `_heatmap_fill_nan`, `_heatmap_linkage` | 112 |
| `core/sampling.py` | `_subsample_allocate`, `_row_priorities`, `_stable_sample_selection` | 101 |
| `core/image_ops.py` | `_otsu_threshold`, `_relabel_sequential`, `_row_mode`, `_mode_pool`, `_pool_mask` | 115 |

Notes:

- `_evaluate_stages` calls `_apply_batch_align` → `_assign_batch_clusters`;
  keep these three together in `core/pipeline.py`.
- `_gate_own_mask` calls `_apply_scale_transforms`; `core/gates.py` may import
  `core/scale_math.py`. No other cross-imports.
- `_heatmap_linkage` uses `scipy`; `core/image_ops` uses `numpy`/`scipy` only.
- `_nice_ticks` is used by `_figure_range_row` in `widgets/plot/`; import it
  from `core`.

**Acceptance for Batch 1:** new unit tests for each package run without
`QApplication`; `main.py` imports them by name; all offscreen tests unchanged.

## 5. Inventory B — `data/` (Qt-free, needs `AppContext`)

All of these were verified to contain **no Qt symbols**; they depend only on
`project`, caches and file paths.

| Package | Symbols | Lines |
|---|---|---:|
| `data/columns.py` | `_column_rename_stages`, `_columns_for`, `_column_data`, `_read_columns`, `_data_row_count`, `_map_stages`, `_map_stage_token`, `_upstream_scale_transforms`, `_upstream_unmix_configs`, `_task_chain`, `_parent_task_name` | 390 |
| `data/files.py` | `_available_files`, `_file_record`, `_rel_data_key`, `_module_display_title`, `_module_dir`, `_module_rel`, `_module_size_path`, `_imported_files`, `_subset_filters`, `_subset_visible_files` | 79 |
| `data/naming.py` | `_comp_file_name`, `_rename_file_name` | 21 |
| `data/feature_outputs.py` | `_feature_signature`, `_feature_outputs`, `_feature_output`, `_feature_params_for`, `_feature_up_signature`, `_feature_up_params_for`, `_feature_up_applied_columns`, `_feature_up_column_values`, `_feature_up_raster`, `_gate2col_params_for`, `_gate2col_signature`, `_gate2col_applied_columns`, `_gate2col_column_values` | 325 |
| `data/outputs.py` | `_seg_applied_columns`, `_seg_mask_values`, `_seg_mask_raster`, `_seg_is_image_file`, `_dimred_applied_columns`, `_cluster_applied_columns`, `_cluster_column_values`, `_cluster_raster`, `_dc_applied_columns`, `_dc_raster`, `_g2c_raster`, `_unmix_image_outputs`, `_unmix_raster` | 280 |
| `data/pool.py` | `_pool_tier`, `_pool_rows_cached`, `_pool_rows`, `_pool_column`, `_pool_register`, `_pool_queue_job`, `_pool_start_worker`, `_pool_build_indices`, `_pool_build_column`, `_pool_poll`, `_pool_store_column`, `_pool_clear`, `_pool_ready`, `_pool_pending_keys` | 197 |
| `data/image_index.py` | `_image_index_dir`, `_image_geometry`, `_image_write_level`, `_build_image_index_files`, `_build_label_index`, `_image_load_level`, `_ensure_image_index`, `_image_index_valid`, `_stored_pyramid_ok`, `_image_load_manifest`, `_image_level_2d`, `_image_raster_raw`, `_image_raster`, `_image_mask_crop`, `_channel_full_range` | 542 |
| `data/sampling.py` | `_subsample_row_index`, `_subsample_sig`, `_subsample_visible_count`, `_subsample_cover_crop`, `_subsample_own_mask`, `_subsample_coverage_dir`, `_subsample_coverage_paths`, `_subsample_mask_meta`, `_subsample_seed` | 198 |
| `data/stores.py` | `_attach_gating_stores`, `_restore_gating_stores`, `_gating_gates_for`, `_write_draft_params`, `_discard_draft_folders` | 96 |
| `data/workers.py` | `_ProcessWorker`, `_GatingStatsWorker`, `_IlluminationWorker`, `_ImageFilterWorker` | 505 |

### `AppContext` vs `Session`

`AppContext` (app lifetime): settings dict, extension config/paths,
`runlog`, host callbacks (`refresh_workflow_tree`, `module_saved`,
`close_page`), background worker pool.

`Session` (recreated per project): `project`, `column_index`,
all frame caches, pool state, module registry, draft/session-unlock sets.

State to move, taken from the measured `MainWindow` attributes:

```
project
column_index
stage_cache
subsample_cache, subsample_mask_cache
seg_mask_cache, seg_value_cache
unmix_value_cache, unmix_flow_cache
ba_cache, image_filter_cache, channel_range_cache
pool_rows_cache, pool_col_cache, pool_col_order, pool_col_bytes,
pool_results, pool_waiters, pool_failed, pool_queued, pool_jobs,
pool_generation, pool_timer, pool_worker
import_files, processed_files
module_registry
draft_task_ids, session_unlocked
```

The **module API surface** these packages must expose is ~50 methods, measured
as the union of the per-module call lists:

```
files:      available_files, file_record, rel_data_key, imported_files,
            subset_visible_files, module_dir, module_rel, module_display_title
columns:    columns_for, column_data, read_columns, data_row_count,
            map_stages, map_stage_token, column_rename_stages,
            upstream_scale_transforms, upstream_unmix_configs
outputs:    feature_output, feature_outputs, feature_signature,
            feature_params_for, depred/cluster/dc/g2c/seg/unmix adapters
image:      ensure_image_index, image_index_valid, image_load_manifest,
            image_raster, image_mask_crop, channel_full_range,
            stored_pyramid_ok
sampling:   subsample_sig, subsample_row_index, subsample_visible_count,
            subsample_cover_crop
gates:      gating_gates_for, gating_store_load, gating_store_path
host:       module_saved, module_set_state, close_module_page,
            refresh_workflow_tree, pick_items_dialog, build_panel_frame
extensions: ext_ready, ext_entry, ext_python, ext_runner_path, ext_worker
```

## 6. Inventory C — `widgets/` (Qt, ctx passed explicitly)

| Package | Contents | Lines |
|---|---|---:|
| `widgets/resources.py` | `_dot_pixmap`, `_module_icon_*`, `_drag_handle_pixmap`, `_lock_pixmap`, `_format_bytes`, `_folder_size`, `_process_memory_bytes`, `_system_memory_bytes` | 213 |
| `widgets/panels.py` | `_build_panel_frame`, `_build_placeholder_panel`, `_build_middle_panel`, `_build_sample_panel`, `_build_processed_panel`, `_build_parameter_panel`, `_build_input_panel`, `_clear_layout`, `_clear_settings_layout`, `_settings_scroll`, `_param_hint`, `_file_nav_row`, `_refresh_processed_panel`, `_refresh_import_list` | 280 |
| `widgets/picker.py` | `_pick_items_dialog`, `_SelectAllFilter` | 177 |
| `widgets/colormap.py` | `_normalize_stops`, `_colormap_from_stops`, `_colormap_groups`, `_gradient_pixmap`, `_colormap_gradient_pixmap`, `_colormap_name_taken`, `_palette_names`, `_figure_colormap`, `_CustomColormapDialog`, `_ColormapPicker`, `_populate_palette_combo`, `_add_more_colormaps`, `_palette_more_guard`, `_palette_pick_more`, `_pick_colormap` | 517 |
| `widgets/plot_axes.py` | `_make_original_axis`, `_make_sci_axis`, `_make_fixed_axis`, `_make_rotated_axis`, `_make_fit_plot_item`, `_letter_pixmap`, `_triangle_icon`, `_gate_tool_icon`, `_gate_color`, `_gate_hover_color`, `_gate_roi_pens`, `_detach_gate_item`, `_scatter_brush`, `_figure_image_layers`, `_is_scatter_plot`, `_figure_default_size` | 324 |
| `widgets/plot/` | `_figure_record`, `_figure_axis_scale_configs`, `_figure_render`, `_figure_range_row`, `_figure_render_image`, `_figure_display_toggles`, `_figure_apply_display_options`, `_figure_scatter_color_settings`, `_figure_render_scatter`, `_figure_attach_legend`, `_figure_image_layer_settings`, `_figure_make_plot_widget`, `_figure_fit_all`, `_figure_apply_ranges`, `_figure_render_histogram`, `_figure_param_combo` | 1,615 |
| `widgets/gate_editor/` | `_gating_state_init`, `_gating_build_content`, `_gating_render`, `_gating_rebuild_settings`, `_gating_param_combo`, `_gating_cleanup` | 764 |
| `widgets/image_level.py` | `_image_level_controls` | 166 |
| `widgets/settings.py` | `_GlobalSettingsDialog`, settings path/load/save, palette/stylesheet, `_debug_log`, `_log_exception`, `_install_*` | 395 |
| `widgets/ui_logger.py` | `_UIActionLogger` | 268 |

Module-private Qt classes stay with their module:

| Class | Module |
|---|---|
| `_GatingState`, `_GateLabel`, `_GateViewBox`, `_GatePolygonROI`, `_SkewedQuadrantROI`, `_GatingCanvasView`, `_ReleaseFilter`, `_ClickFilter`, `_DoubleClickFilter`, `_NoClickScatter` | gating |
| `_FigureCard`, `_FigurePermuteDialog`, `_FigureLegendWidget`, `_OverlayLayerTable`, `_ZoomableGraphicsView`, `_ZoomPixmapLabel`, `_ImageFilterWorker` | figure |
| `_HeatmapCanvas` | heatmap |
| `_ExportPane`, `_ExportCancelled` | export |
| `_AreaCancelled` | area |
| `_SizeField`, `_LogStepSpinBox` | widgets (shared) |
| `_ModuleChip`, `_AddModuleOverlay`, `_CardProxy`, `_FlowArrowBand`, `_PinnedFirstTabBar` | app/workflow_tree |
| `_DraftSaveGuard` | app/module_host |
| `_ExtManagerDialog` | app/extensions |
| `_HoverLabel`, `_NoWheelComboBox/SpinBox/DoubleSpinBox` | widgets |

## 7. Inventory D — app shell

| Package | Contents | Lines |
|---|---|---:|
| `app/workflow_tree.py` | `_build_workflow_tab`, `_task_item_widget`, `_show_actions_menu`, `_build_actions_menu`, copy/paste/remap, `_delete_task`, `_add_task*`, `_refresh_workflow_tree`, `_lock_task`, `_task_locked`, `_refresh_locked_pages`, `_ModuleChip`, `_AddModuleOverlay`, `_CardProxy`, `_FlowArrowBand`, `_PinnedFirstTabBar` | 991 |
| `app/module_host.py` | `_build_module_toolbar`, `_build_module_page`, `_build_generate_page`, `_open_module`, `_close_module_page`, `_cleanup_module_page`, `_module_register/set_state/descendant_ids`, draft + unlock helpers, `_module_saved`, `_module_refresh_from_upstream`, `_DraftSaveGuard`, `_has_unsaved_module_changes` | 627 |
| `app/extensions.py` | all `_ext_*`, `_detect_python_interpreters`, `_open_extension_manager`, `_ext_detail_panel` (530), `_ext_status_text`, `_run_extension_process`, `_flowsom_external`, `_parc_external`, `_ExtManagerDialog`, `_extension_def`, CUDA helpers | 958 |
| `app/project_io.py` | `_new_project`, `_open_project`, `_save_project_as`, `_reset_module_tabs`, window geometry, appearance, `_open_settings`, `_build_menu_bar`, `_build_main_area`, resource indicator, `closeEvent` | 277 |
| `app/module_sizes.py` | `_module_size_path`, size worker/poll/store/apply, labels | 127 |

## 8. Inventory E — runners

Already standalone and imported only through the extension manager:

```
src/smage/cellpose_runner.py    131
src/smage/stardist_runner.py    187
src/smage/flowsom_runner.py      86
src/smage/parc_runner.py         67
```

Move to `src/smage/runners/` as a pure rename in Batch 2; update
`_ext_runner_path` and the packaging spec. No logic change.

## 9. Cross-module edges to break

| Edge | Direction | Resolution |
|---|---|---|
| figure ↔ gating | figure calls `_gating_gates_for` (data), gating calls `_figure_*` (renderers) | split into `data/stores.py` (gates) and `widgets/plot/` (renderers); both modules import those |
| unmix → gating | embeds the gate tree editor | extract `widgets/gate_editor/` and let unmix construct it directly |
| figure → 8 modules | `_figure_render_image` calls each module's `_*_applied_columns` / `_*_raster` | move adapters into `data/outputs.py` with one registry entry per module; figure asks the registry |
| 20 modules → compensation | `_comp_file_name` for display names | `data/naming.py` in Batch 2 |
| 10 modules → subsample | row index / signature / cover crop | `data/sampling.py` in Batch 2 |
| gating/feature/export/figure → segmentation | mask values / applied columns | `data/outputs.py` (segmentation adapters) in Batch 2 |
| scale/illumination → gating | `_gating_param_combo` only | move that 7-line combo builder to `widgets/gate_editor/` |
| data_clean → cluster | shares `_cluster_column_values` / `_cluster_raster` | move to `data/outputs.py` |

After these moves, the only remaining module-to-module imports are through
`data/outputs.py`, which is a plain dictionary keyed by task type.

## 10. Extraction checklist

### Batch 1 — `core/` (no prerequisites) — **DONE**

- [x] 1.1 `core/formatting.py`
- [x] 1.2 `core/scale_math.py`
- [x] 1.3 `core/pipeline.py`
- [x] 1.4 `core/gates.py`
- [x] 1.5 `core/feature.py`
- [x] 1.6 `core/heatmap.py`
- [x] 1.7 `core/sampling.py`
- [x] 1.8 `core/image_ops.py`
- [x] Add unit tests that import `smage.core.*` without `QApplication`
      (`E:\smage\data\test_core_units.py`, per the offscreen-test convention)
- [x] `main.py` imports from `smage.core`; delete moved definitions

Result: 51 functions + 2 module constants moved (1,310 net lines removed);
`main.py` 42,355 → 41,045. The AST of every moved function was compared
against the original and is identical. Regression runs passed:
`test_core_units`, `test_pool_mask`, `test_scale_page`, `test_heatmap_ui`,
`test_gating_canvas`, `test_boolean_gate_axes`, `test_col2gate`,
`test_feature_ignore`, `test_export`, plus an offscreen `MainWindow` smoke.

### Batch 2 — `data/` + `AppContext` — **DONE (with one split)**

- [x] 2.1 `data/context.py` (`AppContext`, `Session`, transparent proxy)
- [x] 2.2 `data/naming.py` (2 functions)
- [x] 2.3 `data/files.py` (13 functions)
- [x] 2.4 `data/columns.py` (9 methods + `_illumination_stage_item`)
- [x] 2.5 `data/pool.py` (14 methods)
- [~] 2.6 `data/image_index.py` — only the **Qt-free** part moved (6 functions
      + 7 methods). `_image_raster_raw`, `_image_raster`, `_image_mask_crop`
      return `QRectF`, so they move to `widgets/image_raster.py` in Batch 4.
- [x] 2.7 `data/stores.py` (5 methods)
- [x] 2.8 `data/sampling.py` (11 methods)
- [x] 2.9 `data/feature_outputs.py` (13) + `data/outputs.py` (13)
- [x] 2.10 `data/workers.py` (4 worker classes)
- [x] Move runners to `runners/` (all four + `_ext_runner_path`)
- [x] Transitional: 87 MainWindow methods became `self._ctx` delegates; the
      data functions call the remaining methods through the proxy, so no
      call site changed
- [x] `core/settings.py` added (settings I/O was needed by `data/pool.py`)
- [x] Re-ran `test_pool_mask`, `test_original_values`, `test_heatmap_ui`,
      `test_scale_page`, `test_export`, `test_export_ui`, `test_col2gate`,
      `test_feature_ignore`, `test_gating_canvas`, `test_gating_delete`,
      `test_figure_overlay`, `test_module_lock`, `test_draft_locked`,
      `test_import_unlock`, `test_flowsom_extension`, `test_global_settings`

Result: **87 MainWindow methods** replaced by `_ctx` delegates, plus 4 worker
classes, 7 image top-level functions, `_illumination_stage_item`, and the
settings I/O moved out. 1,937 net lines removed: `main.py` 41,045 → **39,108**.
`data/` and `core/` import without PySide6/pyqtgraph (checked in a fresh
process).

Known stale test (pre-existing, not a regression): `test_unmix_image.py`
references `figure-f2aee738`, which no longer exists in `evos_test.smage`,
so it appends `None` to the tree and fails before opening the module.

### Batch 3 — shared widgets — **DONE**

- [x] 3.1 `widgets/resources.py` (icons, pixmaps, folder/memory helpers)
- [~] 3.2 `widgets/panels.py` — moved the generic helpers
      (`_build_panel_frame`, `_build_placeholder_panel`, `_build_middle_panel`,
      `_clear_layout`, `_clear_settings_layout`, `_settings_scroll`,
      `_param_hint`, `_file_nav_row`). The Data Import-specific builders
      (`_build_input_panel`, `_build_sample_panel`, `_build_processed_panel`,
      `_refresh_processed_panel`, `_refresh_import_list`) stay with the import
      module (Batch 5.21).
- [x] 3.3 `widgets/picker.py` (dialog + `_SelectAllFilter`)
- [x] 3.4 `widgets/colormap.py` (palettes, gradients, picker, editor)
- [x] 3.5 `widgets/plot_axes.py` (axes, tick labels, gate colours, brushes)
- [x] 3.6 `widgets/image_level.py`
- [x] 3.7 `widgets/settings.py` (dialog; settings.json I/O already in
      `core/settings.py`)
- [x] 3.8 `widgets/ui_logger.py`
- [x] `widgets/controls.py` added for `_NoWheelComboBox/SpinBox/DoubleSpinBox`
- [x] `core/settings.py` extended with `APP_STYLE`, `_default_palette`,
      `_plot_background`, `_app_stylesheet` (needed by colormap/settings)

Result: 18 MainWindow methods replaced by delegates; 2,074 net lines removed:
`main.py` 39,030 → **36,956**. Regression runs passed: `test_global_settings`,
`test_heatmap_ui`, `test_scale_page`, `test_illum_image_levels`,
`test_add_module_overlay`, `test_module_lock`, `test_gating_canvas`,
`test_figure_card`, `test_figure_overlay`, `test_figure_menu`,
`test_export_ui`, `test_col2gate`, `test_gating_delete`, `test_core_units`,
plus the offscreen smoke.

Still in `main.py` for a later batch: `_SizeField`, `_LogStepSpinBox`,
`_HoverLabel` (small controls) and `_image_raster_raw` / `_image_raster` /
`_image_mask_crop` (QRectF, Batch 4).

### Batch 4 — shared renderers and gate editor — **DONE**

- [x] 4.1 `widgets/plot/renderer.py` (1,728 lines): the figure render engine
      (16 functions + `_FigureLegendWidget` + `FIGURE_COLOR_MODES` /
      `IMAGE_LAYER_COLORS`)
- [x] 4.2 `widgets/gate_editor/`:
      - `canvas.py` (856): `_GatingCanvasView` + gate ROIs + view box + filters
      - `editor.py` (842): state init, tree content, rendering, settings,
        `_default_gating_preview`, `_gating_plot_size`
      Moving the canvas classes too was required: the editor constructs
      `_GatingCanvasView` directly, and leaving it in `main.py` would have
      forced a circular import.
- [x] `widgets/image_raster.py` (224): `_image_raster_raw`, `_image_raster`,
      `_image_mask_crop` (the QRectF-producing part deferred from Batch 2)
- [x] `widgets/controls.py` extended with `_SizeField`, `_LogStepSpinBox`
- [x] Verify figure and gating call the new widgets identically

Result: 25 MainWindow methods replaced by delegates; 3,629 net lines removed:
`main.py` 36,956 → **33,327**. Fixes needed during the move: `self` was
sometimes a Qt parent, so `QTimer/QDialog/_GlobalSettingsDialog/
QColorDialog` now receive `ctx.host` instead of `ctx`. Regression runs passed:
`test_gating_canvas`, `test_gate_click_drag`, `test_gating_delete`,
`test_boolean_gate_axes`, `test_figure_card`, `test_figure_menu`,
`test_figure_overlay`, `test_figure_nxn`, `test_heatmap_ui`,
`test_scale_page`, `test_export_ui`, `test_illum_image_levels`,
`test_image_rect_subset`, `test_feature_ignore`, `test_module_lock`,
`test_unmix_names`, `test_core_units`, plus the offscreen smoke.

The figure ↔ gating cycle is now broken at the code level: `widgets/plot`
and `widgets/gate_editor` depend only on `core`/`widgets`, never on the
Figure or Gating modules.

### Batch 5 — modules — **DONE**

Every module now lives in `modules/<name>/` with a `page.py` holding the
builder and the logic that used to be `MainWindow` methods. The window keeps
one-line `self._ctx` delegates so no call site changed.

- [x] 5.1 export — `modules/export/{state,page}.py`
- [x] 5.2 compensation — `modules/compensation/page.py`
- [x] 5.3 rename — `modules/rename/page.py`
- [x] 5.4 subset — `modules/subset/page.py`
- [x] 5.5 subsample — `modules/subsample/page.py`
- [x] 5.6 feature_up — `modules/feature_up/page.py`
- [x] 5.7 gate2col — `modules/gate2col/page.py`
- [x] 5.8 feature — `modules/feature/page.py` (+ ignore cache constants)
- [x] 5.9 heatmap — `modules/heatmap/page.py` (+ `_HeatmapCanvas`)
- [x] 5.10 scale — `modules/scale/page.py`
- [x] 5.11 illumination — `modules/illumination/page.py`
- [x] 5.12 data_clean — `modules/data_clean/page.py`
- [x] 5.13 batch_align — `modules/batch_align/page.py`
- [x] 5.14 dimred — `modules/dimred/page.py`
- [x] 5.15 cluster — `modules/cluster/page.py`
- [x] 5.16 area — `modules/area/page.py`
- [x] 5.17 segmentation — `modules/segmentation/page.py`
- [x] 5.18 unmix — `modules/unmix/page.py`
- [x] 5.19 gating — `modules/gating/page.py`
- [x] 5.20 figure — `modules/figure/page.py` (+ card / dialog / overlay classes)
- [x] 5.21 import — `modules/import_/page.py` (state still on the host; R3)

Result: `main.py` **33,327 → 7,456**; 21 module packages. Shared pieces that
had to move out along the way:

- `core/constants.py`: `_FEATURE_LOG_LIMIT`, `MODULE_STATE_*`
- `extensions_registry.py`: `EXTENSION_DEFS`, `_extension_def`
- `data/files.py`: `_subset_visible_columns` (used by `_columns_for`)
- `widgets/controls.py`: `_FlowArrowBand`, `_ZoomPixmapLabel`, `_CardProxy`
- duplicate copies of the four worker classes removed from `main.py`
  (they had been left behind when `data/workers.py` was created)

Three compatibility shims worth knowing:

1. `_figure_permute_dialog` on `MainWindow` builds the dialog through the
   module global, so `test_figure_menu` can still monkeypatch
   `main._FigurePermuteDialog`.
2. `_figure_page` is the alias tests should patch for `QMenu` (the page owns
   its own import now).
3. `import_` keeps `_import_files`, `_import_queue`, `_import_jobs`, etc. on
   the window (risk R3); only the methods moved.

Regression sweep: all module-level tests print `ALL CHECKS DONE`; the
extension tests (`flowsom`, `parc`, `ba_cytonorm`) pass end to end.
`test_all_pages_smoke` opens **all 21 module pages** on one temp project and
closes them, which is the broadest runtime check.

Two cleanup notes from this batch:

- Class constants that page code accessed as `ctx._X` were switched to the
  page-local `_X` (`_DC_MODES`, `_FEAT_IGNORE_*`, `_HEATMAP_STORE_NAME`,
  `_AREA_GRID_DEFAULT`, `_BA_*`); a few unused copies still sit on
  `MainWindow` and can be removed in Batch 6.
- The four worker classes had duplicate definitions left in `main.py` when
  `data/workers.py` was created; the main copies were identical and are now
  removed, so the imported versions are authoritative.

Some offscreen scripts segfault **during interpreter teardown** after their
checks complete (pre-existing Qt/pyqtgraph teardown issue; `test_exit_crash`
passes with `SHUTDOWN OK`).

### Batch 6 — app shell and entry — **DONE**

- [x] 6.1 `app/workflow_tree.py` — `WorkflowTreeMixin`, cards,
      `_AddModuleOverlay`, `_PinnedFirstTabBar`, add-module picker, copy/paste,
      lock actions, `TASK_TYPES` / `MODULE_KINDS` / task helpers
- [x] 6.2 `app/module_host.py` — `ModuleHostMixin`, `_DraftSaveGuard`,
      toolbar/open/close/registry/draft/unlock/save
- [x] 6.3 `app/extensions.py` — `ExtensionsMixin`, `_ExtManagerDialog`,
      process runner, FlowSOM/PARC calls, CUDA helpers
- [x] 6.4 `app/project_io.py` — `ProjectIOMixin` (menu, project open/save,
      window geometry, tabs, resource indicator); `app/module_sizes.py` —
      `ModuleSizesMixin`
- [x] `app/logging_setup.py` — Qt message handler + excepthook
- [x] `app/delegates.py` — `ModuleDelegatesMixin`: all 1,013 one-line
      delegates from the earlier batches, kept so call sites stay unchanged
- [x] 6.5 `main.py` = docstring + imports + `MainWindow` + `main()`

Result: `main.py` **348 lines**. `MainWindow` is now:

```python
class MainWindow(QMainWindow, WorkflowTreeMixin, ModuleHostMixin,
                  ExtensionsMixin, ProjectIOMixin, ModuleSizesMixin,
                  ModuleDelegatesMixin):
    def __init__(self): ...
    def _figure_permute_dialog(self, *args, **kwargs): ...
```

The app shell uses **mixins** (not `ctx`-taking functions) because these
methods are inherently window code; moving them verbatim kept every `self`
reference working. The 1,013 module delegates live in one generated mixin
and are the remaining cleanup target: once call sites use the layer/module
functions directly, `app/delegates.py` can disappear.

Final verification: `test_all_pages_smoke` opens and closes all 21 module
pages; `test_module_lock`, `test_draft_locked`, `test_import_unlock`,
`test_add_module_overlay`, `test_add_module_reopen`, `test_export`,
`test_export_ui`, `test_figure_menu`, `test_figure_card`,
`test_figure_overlay`, `test_gating_canvas`, `test_gating_delete`,
`test_flowsom_extension`, `test_global_settings`, `test_runlog`,
`test_exit_crash`, and `test_core_units` all pass (`ALL CHECKS DONE`).
Static undefined-name check on `main.py`: 0.

## 11. `ModulePage` contract (needed from Batch 5)

Observed naming is already consistent (`_<mod>_task_id`, `_task_def`,
`_params`, `_dirty`, `_locked`, `_snapshot`, `_log`). The protocol:

```python
class ModulePage(QWidget):
    task_id: str | None
    task_def: dict
    ctx: AppContext

    def is_dirty(self) -> bool: ...
    def save(self) -> None: ...
    def close_cleanup(self) -> None: ...
    def on_upstream_saved(self) -> None: ...
```

`_open_module`'s 21-branch `elif` chain becomes:

```python
BUILDERS = {
    "import": ImportPage,
    "gating": GatingPage,
    ...
}
page = BUILDERS[task_type](ctx=self.ctx, task_def=task_def,
                           locked=locked, title=name)
```

## 12. Risk register

| ID | Risk | Evidence | Mitigation |
|---|---|---|---|
| R1 | 173 module methods are **not** page-first | measured: 896−723 | review them per module; most are small helpers taking explicit args; move as free functions in the module package |
| R2 | `_build_*_page` builders are counted in “shared”, not module lines | 22 builders / 1,816 lines | move each builder with its module in Batch 5 |
| R3 | Import module stores all state on `MainWindow` (`_import_files`, `_import_jobs`, `_import_thread`, `_import_queue`, `_import_timer`, UI widgets) | measured: 0 page-first, 1 page attr | treat as its own mini-refactor: introduce `ImportPage` state first |
| R4 | Some state attributes are accidentally module-local (`self._buf`, `self.total_iter`, `self.method`, `self.q`, `self.start` in dimred) | measured in state scan | move into dimred page state; do not put in `Session` |
| R5 | Data caches are keyed by parquet path and mutated from workers | pool/gating/image workers | `Session` owns them; document thread ownership; keep generation counters |
| R6 | Draft/lock/session logic is cross-cutting | `_open_module`, `_close_module_page`, `_module_unlock` | Batch 6.2 after all pages implement `is_dirty/save/close_cleanup` |
| R7 | Figure renderers are 444/373/243-line methods | measured | move verbatim first, refactor internals later; behavior must not change |
| R8 | Gating store has a legacy inline fallback | `_gating_gates_for` branch on `params.gates` | keep fallback in `data/stores.py`; add migration test |
| R9 | Image index code reads/writes parquet and is used by 10 modules | measured | move to `data/image_index.py` in Batch 2; keep `_ensure_image_index` signature |
| R10 | Tests live outside the repo and write real project folders | `E:\smage\data\*.py` | every batch: run offscreen suite; tests must keep `project.save` disabled |

## 13. Test mapping (existing offscreen suite)

| Area | Tests |
|---|---|
| gating / gates | `test_gating_canvas.py`, `test_gating_card.py`, `test_gating_delete.py`, `test_gating_subset_type*.py`, `test_gate_*.py`, `test_boolean_gate_axes.py`, `test_col2gate*.py`, `test_paste_gating*.py` |
| figure | `test_figure_card.py`, `test_figure_menu.py`, `test_figure_nxn.py`, `test_figure_overlay.py` |
| heatmap | `test_heatmap.py`, `test_heatmap_canvas.py`, `test_heatmap_ui.py` |
| export | `test_export.py`, `test_export_ui.py` |
| feature | `test_feature_ignore*.py`, `test_fup_*.py` |
| scaling / unmix / illum | `test_scale_*.py`, `test_unmix_*.py`, `test_illum_image_levels.py` |
| pools / data | `test_pool_mask*.py`, `test_original_values.py`, `test_evos_*.py`, `test_image_rect_subset.py` |
| lock / draft / extensions | `test_module_lock.py`, `test_draft_locked.py`, `test_import_unlock.py`, `test_*_extension.py` |
| runlog / settings | `test_runlog.py`, `test_global_settings.py` |

New tests to add with Batch 1 (no Qt): gate masks, stage evaluation, scale
transforms, heatmap normalization/linkage, subsample allocation, FCS name
sanitizing. These become the first `pytest` files runnable in CI.

## 14. Step-0 exit criteria

- [x] Every `MainWindow` method assigned to a target package (table above)
- [x] Every top-level function/class assigned (tables above)
- [x] Shared hubs and cycles identified with resolutions
- [x] `AppContext`/`Session` fields enumerated from measured usage
- [x] Batch order fixed, each batch has an acceptance test set
- [ ] User sign-off on Batch 1 start
