"""One-line delegates from MainWindow to the data/widget/module functions.

These keep every existing call site working while the implementation lives
in the layer packages. They can be removed once call sites are updated.
"""
from __future__ import annotations

from smage.modules.gate2col import page as _gate2col_page

from smage.data import columns as _data_columns
from smage.data import feature_outputs as _data_feature_outputs
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.data import pool as _data_pool
from smage.data import sampling as _data_sampling
from smage.data import stores as _data_stores
from smage.modules.area import page as _area_page
from smage.modules.batch_align import page as _batch_align_page
from smage.modules.cluster import page as _cluster_page
from smage.modules.compensation import page as _compensation_page
from smage.modules.data_clean import page as _data_clean_page
from smage.modules.dimred import page as _dimred_page
from smage.modules.export import page as _export_page
from smage.modules.feature import page as _feature_page
from smage.modules.feature_up import page as _feature_up_page
from smage.modules.figure import page as _figure_page
from smage.modules.gating import page as _gating_page
from smage.modules.heatmap import page as _heatmap_page
from smage.modules.illumination import page as _illumination_page
from smage.modules.import_ import page as _import_page
from smage.modules.rename import page as _rename_page
from smage.modules.scale import page as _scale_page
from smage.modules.segmentation import page as _segmentation_page
from smage.modules.subsample import page as _subsample_page
from smage.modules.subset import page as _subset_page
from smage.modules.unmix import page as _unmix_page
from smage.widgets import colormap as _widgets_colormap
from smage.widgets import image_level as _widgets_image_level
from smage.widgets import image_raster as _widgets_image_raster
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets import settings as _widgets_settings
from smage.widgets.gate_editor import editor as _widgets_gate_editor
from smage.widgets.plot import renderer as _widgets_plot


class ModuleDelegatesMixin:
    def _open_settings(self, *args, **kwargs):
        return _widgets_settings._open_settings(self._ctx, *args, **kwargs)

    def _apply_appearance_settings(self, *args, **kwargs):
        return _widgets_settings._apply_appearance_settings(self._ctx, *args, **kwargs)

    def _refresh_plots_for_settings(self, *args, **kwargs):
        return _widgets_settings._refresh_plots_for_settings(self._ctx, *args, **kwargs)

    def _populate_palette_combo(self, *args, **kwargs):
        return _widgets_colormap._populate_palette_combo(self._ctx, *args, **kwargs)

    def _add_more_colormaps(self, *args, **kwargs):
        return _widgets_colormap._add_more_colormaps(self._ctx, *args, **kwargs)

    def _palette_more_guard(self, *args, **kwargs):
        return _widgets_colormap._palette_more_guard(self._ctx, *args, **kwargs)

    def _palette_pick_more(self, *args, **kwargs):
        return _widgets_colormap._palette_pick_more(self._ctx, *args, **kwargs)

    def _pick_colormap(self, *args, **kwargs):
        return _widgets_colormap._pick_colormap(self._ctx, *args, **kwargs)

    def _import_task(self, *args, **kwargs):
        return _data_files._import_task(self._ctx, *args, **kwargs)

    def _import_locked(self, *args, **kwargs):
        return _import_page._import_locked(self._ctx, *args, **kwargs)

    def _load_import_files(self, *args, **kwargs):
        return _import_page._load_import_files(self._ctx, *args, **kwargs)

    def _persist_import_files(self, *args, **kwargs):
        return _import_page._persist_import_files(self._ctx, *args, **kwargs)

    def _module_dir(self, *args, **kwargs):
        return _data_files._module_dir(self._ctx, *args, **kwargs)

    def _module_rel(self, *args, **kwargs):
        return _data_files._module_rel(self._ctx, *args, **kwargs)

    def _module_size_path(self, *args, **kwargs):
        return _data_files._module_size_path(self._ctx, *args, **kwargs)

    def _attach_gating_stores(self, *args, **kwargs):
        return _data_stores._attach_gating_stores(self._ctx, *args, **kwargs)

    def _restore_gating_stores(self, *args, **kwargs):
        return _data_stores._restore_gating_stores(self._ctx, *args, **kwargs)

    def _rename_task(self, *args, **kwargs):
        return _rename_page._rename_task(self._ctx, *args, **kwargs)

    def _build_data_import_page(self, *args, **kwargs):
        return _import_page._build_data_import_page(self._ctx, *args, **kwargs)

    def _param_hint(self, *args, **kwargs):
        return _widgets_panels._param_hint(self._ctx, *args, **kwargs)

    def _build_panel_frame(self, *args, **kwargs):
        return _widgets_panels._build_panel_frame(self._ctx, *args, **kwargs)

    def _build_placeholder_panel(self, *args, **kwargs):
        return _widgets_panels._build_placeholder_panel(self._ctx, *args, **kwargs)

    def _build_middle_panel(self, *args, **kwargs):
        return _widgets_panels._build_middle_panel(self._ctx, *args, **kwargs)

    def _build_sample_panel(self, *args, **kwargs):
        return _import_page._build_sample_panel(self._ctx, *args, **kwargs)

    def _build_processed_panel(self, *args, **kwargs):
        return _import_page._build_processed_panel(self._ctx, *args, **kwargs)

    def _refresh_processed_panel(self, *args, **kwargs):
        return _import_page._refresh_processed_panel(self._ctx, *args, **kwargs)

    def _set_import_controls_enabled(self, *args, **kwargs):
        return _import_page._set_import_controls_enabled(self._ctx, *args, **kwargs)

    def _build_parameter_panel(self, *args, **kwargs):
        return _import_page._build_parameter_panel(self._ctx, *args, **kwargs)

    def _build_input_panel(self, *args, **kwargs):
        return _import_page._build_input_panel(self._ctx, *args, **kwargs)

    def _select_import_files(self, *args, **kwargs):
        return _import_page._select_import_files(self._ctx, *args, **kwargs)

    def _delete_selected_files(self, *args, **kwargs):
        return _import_page._delete_selected_files(self._ctx, *args, **kwargs)

    def _refresh_import_list(self, *args, **kwargs):
        return _import_page._refresh_import_list(self._ctx, *args, **kwargs)

    def _status_tooltip(self, *args, **kwargs):
        return _import_page._status_tooltip(self._ctx, *args, **kwargs)

    def _on_select_all_state_changed(self, *args, **kwargs):
        return _import_page._on_select_all_state_changed(self._ctx, *args, **kwargs)

    def _on_item_check_changed(self, *args, **kwargs):
        return _import_page._on_item_check_changed(self._ctx, *args, **kwargs)

    def _update_select_all_state(self, *args, **kwargs):
        return _import_page._update_select_all_state(self._ctx, *args, **kwargs)

    def _peek_files(self, *args, **kwargs):
        return _import_page._peek_files(self._ctx, *args, **kwargs)

    def _run_import(self, *args, **kwargs):
        return _import_page._run_import(self._ctx, *args, **kwargs)

    def _cancel_import(self, *args, **kwargs):
        return _import_page._cancel_import(self._ctx, *args, **kwargs)

    def _import_worker(self, *args, **kwargs):
        return _import_page._import_worker(self._ctx, *args, **kwargs)

    def _import_poll(self, *args, **kwargs):
        return _import_page._import_poll(self._ctx, *args, **kwargs)

    def _import_finish(self, *args, **kwargs):
        return _import_page._import_finish(self._ctx, *args, **kwargs)

    def _on_import_selection_changed(self, *args, **kwargs):
        return _import_page._on_import_selection_changed(self._ctx, *args, **kwargs)

    def _show_file_parameters(self, *args, **kwargs):
        return _import_page._show_file_parameters(self._ctx, *args, **kwargs)

    def _clear_parameter_rows(self, *args, **kwargs):
        return _import_page._clear_parameter_rows(self._ctx, *args, **kwargs)

    def _on_param_extract_toggled(self, *args, **kwargs):
        return _import_page._on_param_extract_toggled(self._ctx, *args, **kwargs)

    def _on_param_renamed(self, *args, **kwargs):
        return _import_page._on_param_renamed(self._ctx, *args, **kwargs)

    def _copy_parameters(self, *args, **kwargs):
        return _import_page._copy_parameters(self._ctx, *args, **kwargs)

    def _paste_parameters(self, *args, **kwargs):
        return _import_page._paste_parameters(self._ctx, *args, **kwargs)

    def _flash_button(self, *args, **kwargs):
        return _import_page._flash_button(self._ctx, *args, **kwargs)

    def _apply_to_files(self, *args, **kwargs):
        return _import_page._apply_to_files(self._ctx, *args, **kwargs)

    def _find_same_parameter_files(self, *args, **kwargs):
        return _import_page._find_same_parameter_files(self._ctx, *args, **kwargs)

    def _show_apply_dialog(self, *args, **kwargs):
        return _import_page._show_apply_dialog(self._ctx, *args, **kwargs)

    def _apply_parameter_settings(self, *args, **kwargs):
        return _import_page._apply_parameter_settings(self._ctx, *args, **kwargs)

    def _show_file_sampling(self, *args, **kwargs):
        return _import_page._show_file_sampling(self._ctx, *args, **kwargs)

    def _on_sampling_changed(self, *args, **kwargs):
        return _import_page._on_sampling_changed(self._ctx, *args, **kwargs)

    def _apply_sampling_to_files(self, *args, **kwargs):
        return _import_page._apply_sampling_to_files(self._ctx, *args, **kwargs)

    def _imported_files(self, *args, **kwargs):
        return _data_files._imported_files(self._ctx, *args, **kwargs)

    def _feature_signature(self, *args, **kwargs):
        return _data_feature_outputs._feature_signature(self._ctx, *args, **kwargs)

    def _feature_outputs(self, *args, **kwargs):
        return _data_feature_outputs._feature_outputs(self._ctx, *args, **kwargs)

    def _feature_output(self, *args, **kwargs):
        return _data_feature_outputs._feature_output(self._ctx, *args, **kwargs)

    def _feature_params_for(self, *args, **kwargs):
        return _data_feature_outputs._feature_params_for(self._ctx, *args, **kwargs)

    def _rename_file_name(self, *args, **kwargs):
        return _data_naming._rename_file_name(self._ctx, *args, **kwargs)

    def _available_files(self, *args, **kwargs):
        return _data_files._available_files(self._ctx, *args, **kwargs)

    def _file_record(self, *args, **kwargs):
        return _data_files._file_record(self._ctx, *args, **kwargs)

    def _rel_data_key(self, *args, **kwargs):
        return _data_files._rel_data_key(self._ctx, *args, **kwargs)

    def _subset_filters(self, *args, **kwargs):
        return _data_files._subset_filters(self._ctx, *args, **kwargs)

    def _subset_visible_files(self, *args, **kwargs):
        return _data_files._subset_visible_files(self._ctx, *args, **kwargs)

    def _column_rename_stages(self, *args, **kwargs):
        return _data_columns._column_rename_stages(self._ctx, *args, **kwargs)

    def _rename_columns_forward(self, *args, **kwargs):
        return _rename_page._rename_columns_forward(self._ctx, *args, **kwargs)

    def _columns_for(self, *args, **kwargs):
        return _data_columns._columns_for(self._ctx, *args, **kwargs)

    def _figure_record(self, *args, **kwargs):
        return _widgets_plot._figure_record(self._ctx, *args, **kwargs)

    def _task_chain(self, *args, **kwargs):
        return _data_files._task_chain(self._ctx, *args, **kwargs)

    def _parent_task_name(self, *args, **kwargs):
        return _data_files._parent_task_name(self._ctx, *args, **kwargs)

    def _module_display_title(self, *args, **kwargs):
        return _data_files._module_display_title(self._ctx, *args, **kwargs)

    def _upstream_scale_transforms(self, *args, **kwargs):
        return _data_columns._upstream_scale_transforms(self._ctx, *args, **kwargs)

    def _upstream_unmix_configs(self, *args, **kwargs):
        return _data_columns._upstream_unmix_configs(self._ctx, *args, **kwargs)

    def _unmix_output_names(self, *args, **kwargs):
        return _unmix_page._unmix_output_names(self._ctx, *args, **kwargs)

    def _unmix_flow_config(self, *args, **kwargs):
        return _unmix_page._unmix_flow_config(self._ctx, *args, **kwargs)

    def _unmix_params_vector(self, *args, **kwargs):
        return _unmix_page._unmix_params_vector(self._ctx, *args, **kwargs)

    def _map_stages(self, *args, **kwargs):
        return _data_columns._map_stages(self._ctx, *args, **kwargs)

    def _column_data(self, *args, **kwargs):
        return _data_columns._column_data(self._ctx, *args, **kwargs)

    def _read_columns(self, *args, **kwargs):
        return _data_columns._read_columns(self._ctx, *args, **kwargs)

    def _pool_tier(self, *args, **kwargs):
        return _data_pool._pool_tier(self._ctx, *args, **kwargs)

    def _pool_rows_cached(self, *args, **kwargs):
        return _data_pool._pool_rows_cached(self._ctx, *args, **kwargs)

    def _pool_rows(self, *args, **kwargs):
        return _data_pool._pool_rows(self._ctx, *args, **kwargs)

    def _pool_column(self, *args, **kwargs):
        return _data_pool._pool_column(self._ctx, *args, **kwargs)

    def _pool_register(self, *args, **kwargs):
        return _data_pool._pool_register(self._ctx, *args, **kwargs)

    def _pool_queue_job(self, *args, **kwargs):
        return _data_pool._pool_queue_job(self._ctx, *args, **kwargs)

    def _pool_start_worker(self, *args, **kwargs):
        return _data_pool._pool_start_worker(self._ctx, *args, **kwargs)

    def _pool_build_indices(self, *args, **kwargs):
        return _data_pool._pool_build_indices(self._ctx, *args, **kwargs)

    def _pool_build_column(self, *args, **kwargs):
        return _data_pool._pool_build_column(self._ctx, *args, **kwargs)

    def _pool_poll(self, *args, **kwargs):
        return _data_pool._pool_poll(self._ctx, *args, **kwargs)

    def _pool_store_column(self, *args, **kwargs):
        return _data_pool._pool_store_column(self._ctx, *args, **kwargs)

    def _pool_clear(self, *args, **kwargs):
        return _data_pool._pool_clear(self._ctx, *args, **kwargs)

    def _pool_ready(self, *args, **kwargs):
        return _data_pool._pool_ready(self._ctx, *args, **kwargs)

    def _pool_pending_keys(self, *args, **kwargs):
        return _data_pool._pool_pending_keys(self._ctx, *args, **kwargs)

    def _clear_layout(self, *args, **kwargs):
        return _widgets_panels._clear_layout(self._ctx, *args, **kwargs)

    def _settings_scroll(self, *args, **kwargs):
        return _widgets_panels._settings_scroll(self._ctx, *args, **kwargs)

    def _clear_settings_layout(self, *args, **kwargs):
        return _widgets_panels._clear_settings_layout(self._ctx, *args, **kwargs)

    def _build_figure_page(self, *args, **kwargs):
        return _figure_page._build_figure_page(self._ctx, *args, **kwargs)

    def _figure_make_plot_widget(self, *args, **kwargs):
        return _widgets_plot._figure_make_plot_widget(self._ctx, *args, **kwargs)

    def _figure_attach_legend(self, *args, **kwargs):
        return _widgets_plot._figure_attach_legend(self._ctx, *args, **kwargs)

    def _figure_new_card(self, *args, **kwargs):
        return _figure_page._figure_new_card(self._ctx, *args, **kwargs)

    def _figure_resize_card(self, *args, **kwargs):
        return _figure_page._figure_resize_card(self._ctx, *args, **kwargs)

    def _figure_mark_dirty(self, *args, **kwargs):
        return _figure_page._figure_mark_dirty(self._ctx, *args, **kwargs)

    def _figure_save_now(self, *args, **kwargs):
        return _figure_page._figure_save_now(self._ctx, *args, **kwargs)

    def _figure_close(self, *args, **kwargs):
        return _figure_page._figure_close(self._ctx, *args, **kwargs)

    def _figure_update_save_btn(self, *args, **kwargs):
        return _figure_page._figure_update_save_btn(self._ctx, *args, **kwargs)

    def _figure_reorder(self, *args, **kwargs):
        return _figure_page._figure_reorder(self._ctx, *args, **kwargs)

    def _figure_drop_marker_pos(self, *args, **kwargs):
        return _figure_page._figure_drop_marker_pos(self._ctx, *args, **kwargs)

    def _figure_drag_preview(self, *args, **kwargs):
        return _figure_page._figure_drag_preview(self._ctx, *args, **kwargs)

    def _figure_clear_drop_marker(self, *args, **kwargs):
        return _figure_page._figure_clear_drop_marker(self._ctx, *args, **kwargs)

    def _figure_flow_positions(self, *args, **kwargs):
        return _figure_page._figure_flow_positions(self._ctx, *args, **kwargs)

    def _figure_drop_index(self, *args, **kwargs):
        return _figure_page._figure_drop_index(self._ctx, *args, **kwargs)

    def _figure_toggle_break(self, *args, **kwargs):
        return _figure_page._figure_toggle_break(self._ctx, *args, **kwargs)

    def _figure_card_context_menu(self, *args, **kwargs):
        return _figure_page._figure_card_context_menu(self._ctx, *args, **kwargs)

    def _figure_scroll_to(self, *args, **kwargs):
        return _figure_page._figure_scroll_to(self._ctx, *args, **kwargs)

    def _figure_insert_plot(self, *args, **kwargs):
        return _figure_page._figure_insert_plot(self._ctx, *args, **kwargs)

    def _figure_duplicate_selected(self, *args, **kwargs):
        return _figure_page._figure_duplicate_selected(self._ctx, *args, **kwargs)

    def _figure_permute(self, *args, **kwargs):
        return _figure_page._figure_permute(self._ctx, *args, **kwargs)

    def _figure_delete_selected(self, *args, **kwargs):
        return _figure_page._figure_delete_selected(self._ctx, *args, **kwargs)

    def _figure_relayout(self, *args, **kwargs):
        return _figure_page._figure_relayout(self._ctx, *args, **kwargs)

    def _figure_update_scene_rect(self, *args, **kwargs):
        return _figure_page._figure_update_scene_rect(self._ctx, *args, **kwargs)

    def _figure_add_plot(self, *args, **kwargs):
        return _figure_page._figure_add_plot(self._ctx, *args, **kwargs)

    def _figure_add_nxn(self, *args, **kwargs):
        return _figure_page._figure_add_nxn(self._ctx, *args, **kwargs)

    def _figure_add_batch(self, *args, **kwargs):
        return _figure_page._figure_add_batch(self._ctx, *args, **kwargs)

    def _figure_render_queue(self, *args, **kwargs):
        return _figure_page._figure_render_queue(self._ctx, *args, **kwargs)

    def _figure_update_progress(self, *args, **kwargs):
        return _figure_page._figure_update_progress(self._ctx, *args, **kwargs)

    def _figure_cancel_rendering(self, *args, **kwargs):
        return _figure_page._figure_cancel_rendering(self._ctx, *args, **kwargs)

    def _figure_bump_render_token(self, *args, **kwargs):
        return _figure_page._figure_bump_render_token(self._ctx, *args, **kwargs)

    def _figure_render_histogram_when_ready(self, *args, **kwargs):
        return _figure_page._figure_render_histogram_when_ready(self._ctx, *args, **kwargs)

    def _figure_render_when_ready(self, *args, **kwargs):
        return _figure_page._figure_render_when_ready(self._ctx, *args, **kwargs)

    def _figure_filter_options(self, *args, **kwargs):
        return _figure_page._figure_filter_options(self._ctx, *args, **kwargs)

    def _figure_filter_mask(self, *args, **kwargs):
        return _figure_page._figure_filter_mask(self._ctx, *args, **kwargs)

    def _image_load_manifest(self, *args, **kwargs):
        return _data_image_index._image_load_manifest(self._ctx, *args, **kwargs)

    def _image_index_valid(self, *args, **kwargs):
        return _data_image_index._image_index_valid(self._ctx, *args, **kwargs)

    def _ensure_image_index(self, *args, **kwargs):
        return _data_image_index._ensure_image_index(self._ctx, *args, **kwargs)

    def _image_level_2d(self, *args, **kwargs):
        return _data_image_index._image_level_2d(self._ctx, *args, **kwargs)

    def _image_raster_raw(self, *args, **kwargs):
        return _widgets_image_raster._image_raster_raw(self._ctx, *args, **kwargs)

    def _image_raster(self, *args, **kwargs):
        return _widgets_image_raster._image_raster(self._ctx, *args, **kwargs)

    def _image_mask_crop(self, *args, **kwargs):
        return _widgets_image_raster._image_mask_crop(self._ctx, *args, **kwargs)

    def _image_filter_worker(self, *args, **kwargs):
        return _data_image_index._image_filter_worker(self._ctx, *args, **kwargs)

    def _figure_image_filter_error(self, *args, **kwargs):
        return _figure_page._figure_image_filter_error(self._ctx, *args, **kwargs)

    def _figure_render_image(self, *args, **kwargs):
        return _widgets_plot._figure_render_image(self._ctx, *args, **kwargs)

    def _figure_update_image_legend(self, *args, **kwargs):
        return _figure_page._figure_update_image_legend(self._ctx, *args, **kwargs)

    def _figure_image_refresh(self, *args, **kwargs):
        return _figure_page._figure_image_refresh(self._ctx, *args, **kwargs)

    def _figure_image_filter_on_timer(self, *args, **kwargs):
        return _figure_page._figure_image_filter_on_timer(self._ctx, *args, **kwargs)

    def _figure_render(self, *args, **kwargs):
        return _widgets_plot._figure_render(self._ctx, *args, **kwargs)

    def _figure_render_scatter(self, *args, **kwargs):
        return _widgets_plot._figure_render_scatter(self._ctx, *args, **kwargs)

    def _figure_draw_colored_scatter(self, *args, **kwargs):
        return _figure_page._figure_draw_colored_scatter(self._ctx, *args, **kwargs)

    def _figure_render_histogram(self, *args, **kwargs):
        return _widgets_plot._figure_render_histogram(self._ctx, *args, **kwargs)

    def _figure_apply_ranges(self, *args, **kwargs):
        return _widgets_plot._figure_apply_ranges(self._ctx, *args, **kwargs)

    def _figure_fit_all(self, *args, **kwargs):
        return _widgets_plot._figure_fit_all(self._ctx, *args, **kwargs)

    def _figure_apply_display_options(self, *args, **kwargs):
        return _widgets_plot._figure_apply_display_options(self._ctx, *args, **kwargs)

    def _figure_axis_scale_configs(self, *args, **kwargs):
        return _widgets_plot._figure_axis_scale_configs(self._ctx, *args, **kwargs)

    def _figure_original_tick_state(self, *args, **kwargs):
        return _figure_page._figure_original_tick_state(self._ctx, *args, **kwargs)

    def _figure_set_original_ticks(self, *args, **kwargs):
        return _figure_page._figure_set_original_ticks(self._ctx, *args, **kwargs)

    def _figure_refresh_original_ticks(self, *args, **kwargs):
        return _figure_page._figure_refresh_original_ticks(self._ctx, *args, **kwargs)

    def _figure_update_original_axis(self, *args, **kwargs):
        return _figure_page._figure_update_original_axis(self._ctx, *args, **kwargs)

    def _figure_apply_change(self, *args, **kwargs):
        return _figure_page._figure_apply_change(self._ctx, *args, **kwargs)

    def _figure_apply_display(self, *args, **kwargs):
        return _figure_page._figure_apply_display(self._ctx, *args, **kwargs)

    def _figure_apply_display_all(self, *args, **kwargs):
        return _figure_page._figure_apply_display_all(self._ctx, *args, **kwargs)

    def _figure_apply_change_multi(self, *args, **kwargs):
        return _figure_page._figure_apply_change_multi(self._ctx, *args, **kwargs)

    def _figure_refresh_all(self, *args, **kwargs):
        return _figure_page._figure_refresh_all(self._ctx, *args, **kwargs)

    def _figure_set_selection(self, *args, **kwargs):
        return _figure_page._figure_set_selection(self._ctx, *args, **kwargs)

    def _figure_select(self, *args, **kwargs):
        return _figure_page._figure_select(self._ctx, *args, **kwargs)

    def _figure_card_clicked(self, *args, **kwargs):
        return _figure_page._figure_card_clicked(self._ctx, *args, **kwargs)

    def _figure_box_select(self, *args, **kwargs):
        return _figure_page._figure_box_select(self._ctx, *args, **kwargs)

    def _figure_select_all(self, *args, **kwargs):
        return _figure_page._figure_select_all(self._ctx, *args, **kwargs)

    def _figure_clear_selection(self, *args, **kwargs):
        return _figure_page._figure_clear_selection(self._ctx, *args, **kwargs)

    def _figure_focus(self, *args, **kwargs):
        return _figure_page._figure_focus(self._ctx, *args, **kwargs)

    def _figure_unfocus(self, *args, **kwargs):
        return _figure_page._figure_unfocus(self._ctx, *args, **kwargs)

    def _figure_update_plot_mouse(self, *args, **kwargs):
        return _figure_page._figure_update_plot_mouse(self._ctx, *args, **kwargs)

    def _figure_plot_wheel(self, *args, **kwargs):
        return _figure_page._figure_plot_wheel(self._ctx, *args, **kwargs)

    def _figure_plot_pan_start(self, *args, **kwargs):
        return _figure_page._figure_plot_pan_start(self._ctx, *args, **kwargs)

    def _figure_plot_pan_move(self, *args, **kwargs):
        return _figure_page._figure_plot_pan_move(self._ctx, *args, **kwargs)

    def _figure_plot_pan_end(self, *args, **kwargs):
        return _figure_page._figure_plot_pan_end(self._ctx, *args, **kwargs)

    def _figure_rebuild_settings(self, *args, **kwargs):
        return _figure_page._figure_rebuild_settings(self._ctx, *args, **kwargs)

    def _figure_rebuild_settings_single(self, *args, **kwargs):
        return _figure_page._figure_rebuild_settings_single(self._ctx, *args, **kwargs)

    def _figure_rebuild_settings_multi(self, *args, **kwargs):
        return _figure_page._figure_rebuild_settings_multi(self._ctx, *args, **kwargs)

    def _file_nav_row(self, *args, **kwargs):
        return _widgets_panels._file_nav_row(self._ctx, *args, **kwargs)

    def _figure_param_combo(self, *args, **kwargs):
        return _widgets_plot._figure_param_combo(self._ctx, *args, **kwargs)

    def _figure_scatter_color_settings(self, *args, **kwargs):
        return _widgets_plot._figure_scatter_color_settings(self._ctx, *args, **kwargs)

    def _figure_point_size_settings(self, *args, **kwargs):
        return _figure_page._figure_point_size_settings(self._ctx, *args, **kwargs)

    def _figure_overlay_gate_options(self, *args, **kwargs):
        return _figure_page._figure_overlay_gate_options(self._ctx, *args, **kwargs)

    def _figure_overlay_pick(self, *args, **kwargs):
        return _figure_page._figure_overlay_pick(self._ctx, *args, **kwargs)

    def _figure_overlay_settings(self, *args, **kwargs):
        return _figure_page._figure_overlay_settings(self._ctx, *args, **kwargs)

    def _figure_overlay_masks(self, *args, **kwargs):
        return _figure_page._figure_overlay_masks(self._ctx, *args, **kwargs)

    def _figure_overlay_point_colors(self, *args, **kwargs):
        return _figure_page._figure_overlay_point_colors(self._ctx, *args, **kwargs)

    def _figure_channel_values(self, *args, **kwargs):
        return _figure_page._figure_channel_values(self._ctx, *args, **kwargs)

    def _channel_full_range(self, *args, **kwargs):
        return _data_image_index._channel_full_range(self._ctx, *args, **kwargs)

    def _image_level_controls(self, *args, **kwargs):
        return _widgets_image_level._image_level_controls(self._ctx, *args, **kwargs)

    def _figure_image_layer_settings(self, *args, **kwargs):
        return _widgets_plot._figure_image_layer_settings(self._ctx, *args, **kwargs)

    def _figure_display_toggles(self, *args, **kwargs):
        return _widgets_plot._figure_display_toggles(self._ctx, *args, **kwargs)

    def _figure_range_row(self, *args, **kwargs):
        return _widgets_plot._figure_range_row(self._ctx, *args, **kwargs)

    def _scaling_column_set(self, *args, **kwargs):
        return _scale_page._scaling_column_set(self._ctx, *args, **kwargs)

    def _scaling_load_config(self, *args, **kwargs):
        return _scale_page._scaling_load_config(self._ctx, *args, **kwargs)

    def _scaling_load_files(self, *args, **kwargs):
        return _scale_page._scaling_load_files(self._ctx, *args, **kwargs)

    def _scaling_union_columns(self, *args, **kwargs):
        return _scale_page._scaling_union_columns(self._ctx, *args, **kwargs)

    def _scaling_visible_configs(self, *args, **kwargs):
        return _scale_page._scaling_visible_configs(self._ctx, *args, **kwargs)

    def _scaling_refresh_files_box(self, *args, **kwargs):
        return _scale_page._scaling_refresh_files_box(self._ctx, *args, **kwargs)

    def _scaling_files_panel(self, *args, **kwargs):
        return _scale_page._scaling_files_panel(self._ctx, *args, **kwargs)

    def _scaling_select_files(self, *args, **kwargs):
        return _scale_page._scaling_select_files(self._ctx, *args, **kwargs)

    def _scaling_rebuild_visible(self, *args, **kwargs):
        return _scale_page._scaling_rebuild_visible(self._ctx, *args, **kwargs)

    def _scaling_apply_panel(self, *args, **kwargs):
        return _scale_page._scaling_apply_panel(self._ctx, *args, **kwargs)

    def _scaling_select_apply_vars(self, *args, **kwargs):
        return _scale_page._scaling_select_apply_vars(self._ctx, *args, **kwargs)

    def _scaling_rebuild_apply_list(self, *args, **kwargs):
        return _scale_page._scaling_rebuild_apply_list(self._ctx, *args, **kwargs)

    def _scaling_apply_batch(self, *args, **kwargs):
        return _scale_page._scaling_apply_batch(self._ctx, *args, **kwargs)

    def _build_scaling_page(self, *args, **kwargs):
        return _scale_page._build_scaling_page(self._ctx, *args, **kwargs)

    def _scaling_populate_table(self, *args, **kwargs):
        return _scale_page._scaling_populate_table(self._ctx, *args, **kwargs)

    def _scaling_update_param_cell(self, *args, **kwargs):
        return _scale_page._scaling_update_param_cell(self._ctx, *args, **kwargs)

    def _scaling_method_changed(self, *args, **kwargs):
        return _scale_page._scaling_method_changed(self._ctx, *args, **kwargs)

    def _scaling_field_edited(self, *args, **kwargs):
        return _scale_page._scaling_field_edited(self._ctx, *args, **kwargs)

    def _scaling_mark_dirty(self, *args, **kwargs):
        return _scale_page._scaling_mark_dirty(self._ctx, *args, **kwargs)

    def _scaling_save_now(self, *args, **kwargs):
        return _scale_page._scaling_save_now(self._ctx, *args, **kwargs)

    def _scaling_update_save_btn(self, *args, **kwargs):
        return _scale_page._scaling_update_save_btn(self._ctx, *args, **kwargs)

    def _scaling_close(self, *args, **kwargs):
        return _scale_page._scaling_close(self._ctx, *args, **kwargs)

    def _scaling_on_selection(self, *args, **kwargs):
        return _scale_page._scaling_on_selection(self._ctx, *args, **kwargs)

    def _scaling_config_for(self, *args, **kwargs):
        return _scale_page._scaling_config_for(self._ctx, *args, **kwargs)

    def _scaling_autofill_minmax(self, *args, **kwargs):
        return _scale_page._scaling_autofill_minmax(self._ctx, *args, **kwargs)

    def _scaling_render_preview(self, *args, **kwargs):
        return _scale_page._scaling_render_preview(self._ctx, *args, **kwargs)

    def _scaling_set_image_levels(self, *args, **kwargs):
        return _scale_page._scaling_set_image_levels(self._ctx, *args, **kwargs)

    def _scaling_rebuild_settings(self, *args, **kwargs):
        return _scale_page._scaling_rebuild_settings(self._ctx, *args, **kwargs)

    def _write_draft_params(self, *args, **kwargs):
        return _data_stores._write_draft_params(self._ctx, *args, **kwargs)

    def _discard_draft_folders(self, *args, **kwargs):
        return _data_stores._discard_draft_folders(self._ctx, *args, **kwargs)

    def _build_unmix_page(self, *args, **kwargs):
        return _unmix_page._build_unmix_page(self._ctx, *args, **kwargs)

    def _unmix_extraction_subpage(self, *args, **kwargs):
        return _unmix_page._unmix_extraction_subpage(self._ctx, *args, **kwargs)

    def _unmix_spectrum_area(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_area(self._ctx, *args, **kwargs)

    def _unmix_tree_placeholder(self, *args, **kwargs):
        return _unmix_page._unmix_tree_placeholder(self._ctx, *args, **kwargs)

    def _unmix_make_tree(self, *args, **kwargs):
        return _unmix_page._unmix_make_tree(self._ctx, *args, **kwargs)

    def _unmix_build_tree_widgets(self, *args, **kwargs):
        return _unmix_page._unmix_build_tree_widgets(self._ctx, *args, **kwargs)

    def _unmix_activate_tree(self, *args, **kwargs):
        return _unmix_page._unmix_activate_tree(self._ctx, *args, **kwargs)

    def _unmix_destroy_tree(self, *args, **kwargs):
        return _unmix_page._unmix_destroy_tree(self._ctx, *args, **kwargs)

    def _unmix_new_gate_tree(self, *args, **kwargs):
        return _unmix_page._unmix_new_gate_tree(self._ctx, *args, **kwargs)

    def _unmix_restore_trees(self, *args, **kwargs):
        return _unmix_page._unmix_restore_trees(self._ctx, *args, **kwargs)

    def _unmix_on_gating_dirty(self, *args, **kwargs):
        return _unmix_page._unmix_on_gating_dirty(self._ctx, *args, **kwargs)

    def _unmix_cleanup(self, *args, **kwargs):
        return _unmix_page._unmix_cleanup(self._ctx, *args, **kwargs)

    def _unmix_tree_by_id(self, *args, **kwargs):
        return _unmix_page._unmix_tree_by_id(self._ctx, *args, **kwargs)

    def _unmix_tree_for_holder(self, *args, **kwargs):
        return _unmix_page._unmix_tree_for_holder(self._ctx, *args, **kwargs)

    def _unmix_spectrum_for_tree(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_for_tree(self._ctx, *args, **kwargs)

    def _unmix_spectrum_parameters(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_parameters(self._ctx, *args, **kwargs)

    def _unmix_selected_parameters(self, *args, **kwargs):
        return _unmix_page._unmix_selected_parameters(self._ctx, *args, **kwargs)

    def _unmix_apply_parameter_order(self, *args, **kwargs):
        return _unmix_page._unmix_apply_parameter_order(self._ctx, *args, **kwargs)

    def _unmix_sync_spectrum_params(self, *args, **kwargs):
        return _unmix_page._unmix_sync_spectrum_params(self._ctx, *args, **kwargs)

    def _unmix_next_spectrum_name(self, *args, **kwargs):
        return _unmix_page._unmix_next_spectrum_name(self._ctx, *args, **kwargs)

    def _unmix_rebuild_spectrum_list(self, *args, **kwargs):
        return _unmix_page._unmix_rebuild_spectrum_list(self._ctx, *args, **kwargs)

    def _unmix_reorder_spectra(self, *args, **kwargs):
        return _unmix_page._unmix_reorder_spectra(self._ctx, *args, **kwargs)

    def _unmix_log(self, *args, **kwargs):
        return _unmix_page._unmix_log(self._ctx, *args, **kwargs)

    def _unmix_output_signature(self, *args, **kwargs):
        return _unmix_page._unmix_output_signature(self._ctx, *args, **kwargs)

    def _unmix_write_image_output(self, *args, **kwargs):
        return _unmix_page._unmix_write_image_output(self._ctx, *args, **kwargs)

    def _unmix_start_image(self, *args, **kwargs):
        return _unmix_page._unmix_start_image(self._ctx, *args, **kwargs)

    def _unmix_output_is_current(self, *args, **kwargs):
        return _unmix_page._unmix_output_is_current(self._ctx, *args, **kwargs)

    def _unmix_image_worker(self, *args, **kwargs):
        return _unmix_page._unmix_image_worker(self._ctx, *args, **kwargs)

    def _unmix_image_poll(self, *args, **kwargs):
        return _unmix_page._unmix_image_poll(self._ctx, *args, **kwargs)

    def _unmix_image_outputs(self, *args, **kwargs):
        return _data_outputs._unmix_image_outputs(self._ctx, *args, **kwargs)

    def _stored_pyramid_ok(self, *args, **kwargs):
        return _data_image_index._stored_pyramid_ok(self._ctx, *args, **kwargs)

    def _unmix_raster(self, *args, **kwargs):
        return _data_outputs._unmix_raster(self._ctx, *args, **kwargs)

    def _unmix_highlight_spectrum(self, *args, **kwargs):
        return _unmix_page._unmix_highlight_spectrum(self._ctx, *args, **kwargs)

    def _unmix_select_spectrum(self, *args, **kwargs):
        return _unmix_page._unmix_select_spectrum(self._ctx, *args, **kwargs)

    def _unmix_clear_spectrum_selection(self, *args, **kwargs):
        return _unmix_page._unmix_clear_spectrum_selection(self._ctx, *args, **kwargs)

    def _unmix_spectrum_context_menu(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_context_menu(self._ctx, *args, **kwargs)

    def _unmix_set_spectrum(self, *args, **kwargs):
        return _unmix_page._unmix_set_spectrum(self._ctx, *args, **kwargs)

    def _unmix_reset_spectrum_source(self, *args, **kwargs):
        return _unmix_page._unmix_reset_spectrum_source(self._ctx, *args, **kwargs)

    def _unmix_spectrum_menu(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_menu(self._ctx, *args, **kwargs)

    def _unmix_rename_spectrum(self, *args, **kwargs):
        return _unmix_page._unmix_rename_spectrum(self._ctx, *args, **kwargs)

    def _unmix_copy_spectrum(self, *args, **kwargs):
        return _unmix_page._unmix_copy_spectrum(self._ctx, *args, **kwargs)

    def _unmix_delete_spectrum(self, *args, **kwargs):
        return _unmix_page._unmix_delete_spectrum(self._ctx, *args, **kwargs)

    def _unmix_spectrum_arrays(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_arrays(self._ctx, *args, **kwargs)

    def _unmix_spectrum_arrays_for(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_arrays_for(self._ctx, *args, **kwargs)

    def _unmix_spectrum_values(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_values(self._ctx, *args, **kwargs)

    def _unmix_render_spectrum_plot(self, *args, **kwargs):
        return _unmix_page._unmix_render_spectrum_plot(self._ctx, *args, **kwargs)

    def _unmix_rebuild_spectrum_settings(self, *args, **kwargs):
        return _unmix_page._unmix_rebuild_spectrum_settings(self._ctx, *args, **kwargs)

    def _pick_items_dialog(self, *args, **kwargs):
        return _widgets_picker._pick_items_dialog(self._ctx, *args, **kwargs)

    def _unmix_edit_spectrum_parameters(self, *args, **kwargs):
        return _unmix_page._unmix_edit_spectrum_parameters(self._ctx, *args, **kwargs)

    def _unmix_matrix_subpage(self, *args, **kwargs):
        return _unmix_page._unmix_matrix_subpage(self._ctx, *args, **kwargs)

    def _unmix_matrix_panel(self, *args, **kwargs):
        return _unmix_page._unmix_matrix_panel(self._ctx, *args, **kwargs)

    def _unmix_matrix_tab_page(self, *args, **kwargs):
        return _unmix_page._unmix_matrix_tab_page(self._ctx, *args, **kwargs)

    def _unmix_curve_tab_page(self, *args, **kwargs):
        return _unmix_page._unmix_curve_tab_page(self._ctx, *args, **kwargs)

    def _unmix_similarity_tab_page(self, *args, **kwargs):
        return _unmix_page._unmix_similarity_tab_page(self._ctx, *args, **kwargs)

    def _unmix_on_matrix_tab(self, *args, **kwargs):
        return _unmix_page._unmix_on_matrix_tab(self._ctx, *args, **kwargs)

    def _unmix_matrix_settings_panel(self, *args, **kwargs):
        return _unmix_page._unmix_matrix_settings_panel(self._ctx, *args, **kwargs)

    def _unmix_rebuild_matrix_settings(self, *args, **kwargs):
        return _unmix_page._unmix_rebuild_matrix_settings(self._ctx, *args, **kwargs)

    def _unmix_grid_settings_content(self, *args, **kwargs):
        return _unmix_page._unmix_grid_settings_content(self._ctx, *args, **kwargs)

    def _unmix_curve_settings_content(self, *args, **kwargs):
        return _unmix_page._unmix_curve_settings_content(self._ctx, *args, **kwargs)

    def _unmix_matrix_cfg(self, *args, **kwargs):
        return _unmix_page._unmix_matrix_cfg(self._ctx, *args, **kwargs)

    def _unmix_curve_colors(self, *args, **kwargs):
        return _unmix_page._unmix_curve_colors(self._ctx, *args, **kwargs)

    def _unmix_similarity_values(self, *args, **kwargs):
        return _unmix_page._unmix_similarity_values(self._ctx, *args, **kwargs)

    def _unmix_copy_similarity(self, *args, **kwargs):
        return _unmix_page._unmix_copy_similarity(self._ctx, *args, **kwargs)

    def _unmix_copy_matrix(self, *args, **kwargs):
        return _unmix_page._unmix_copy_matrix(self._ctx, *args, **kwargs)

    def _unmix_signature_panel(self, *args, **kwargs):
        return _unmix_page._unmix_signature_panel(self._ctx, *args, **kwargs)

    def _unmix_sample_panel(self, *args, **kwargs):
        return _unmix_page._unmix_sample_panel(self._ctx, *args, **kwargs)

    def _unmix_image_panel(self, *args, **kwargs):
        return _unmix_page._unmix_image_panel(self._ctx, *args, **kwargs)

    def _unmix_rebuild_signature_table(self, *args, **kwargs):
        return _unmix_page._unmix_rebuild_signature_table(self._ctx, *args, **kwargs)

    def _unmix_signature_reordered(self, *args, **kwargs):
        return _unmix_page._unmix_signature_reordered(self._ctx, *args, **kwargs)

    def _unmix_signature_menu(self, *args, **kwargs):
        return _unmix_page._unmix_signature_menu(self._ctx, *args, **kwargs)

    def _unmix_signature_header_menu(self, *args, **kwargs):
        return _unmix_page._unmix_signature_header_menu(self._ctx, *args, **kwargs)

    def _unmix_signature_widget_menu(self, *args, **kwargs):
        return _unmix_page._unmix_signature_widget_menu(self._ctx, *args, **kwargs)

    def _unmix_add_signature(self, *args, **kwargs):
        return _unmix_page._unmix_add_signature(self._ctx, *args, **kwargs)

    def _unmix_remove_signature(self, *args, **kwargs):
        return _unmix_page._unmix_remove_signature(self._ctx, *args, **kwargs)

    def _unmix_unique_signature_name(self, *args, **kwargs):
        return _unmix_page._unmix_unique_signature_name(self._ctx, *args, **kwargs)

    def _unmix_on_signature_field(self, *args, **kwargs):
        return _unmix_page._unmix_on_signature_field(self._ctx, *args, **kwargs)

    def _unmix_set_signature_name(self, *args, **kwargs):
        return _unmix_page._unmix_set_signature_name(self._ctx, *args, **kwargs)

    def _unmix_rebuild_sample_list(self, *args, **kwargs):
        return _unmix_page._unmix_rebuild_sample_list(self._ctx, *args, **kwargs)

    def _unmix_on_samples_changed(self, *args, **kwargs):
        return _unmix_page._unmix_on_samples_changed(self._ctx, *args, **kwargs)

    def _unmix_set_all_samples(self, *args, **kwargs):
        return _unmix_page._unmix_set_all_samples(self._ctx, *args, **kwargs)

    def _unmix_on_method_changed(self, *args, **kwargs):
        return _unmix_page._unmix_on_method_changed(self._ctx, *args, **kwargs)

    def _unmix_matrix_table_values(self, *args, **kwargs):
        return _unmix_page._unmix_matrix_table_values(self._ctx, *args, **kwargs)

    def _unmix_paint_matrix_image(self, *args, **kwargs):
        return _unmix_page._unmix_paint_matrix_image(self._ctx, *args, **kwargs)

    def _unmix_render_matrix(self, *args, **kwargs):
        return _unmix_page._unmix_render_matrix(self._ctx, *args, **kwargs)

    def _unmix_render_curve(self, *args, **kwargs):
        return _unmix_page._unmix_render_curve(self._ctx, *args, **kwargs)

    def _unmix_render_similarity(self, *args, **kwargs):
        return _unmix_page._unmix_render_similarity(self._ctx, *args, **kwargs)

    def _unmix_zoom_matrix(self, *args, **kwargs):
        return _unmix_page._unmix_zoom_matrix(self._ctx, *args, **kwargs)

    def _unmix_refresh_matrix_views(self, *args, **kwargs):
        return _unmix_page._unmix_refresh_matrix_views(self._ctx, *args, **kwargs)

    def _unmix_refresh_outputs(self, *args, **kwargs):
        return _unmix_page._unmix_refresh_outputs(self._ctx, *args, **kwargs)

    def _unmix_spectrum_vector(self, *args, **kwargs):
        return _unmix_page._unmix_spectrum_vector(self._ctx, *args, **kwargs)

    def _unmix_mark_dirty(self, *args, **kwargs):
        return _unmix_page._unmix_mark_dirty(self._ctx, *args, **kwargs)

    def _unmix_save_now(self, *args, **kwargs):
        return _unmix_page._unmix_save_now(self._ctx, *args, **kwargs)

    def _unmix_update_save_btn(self, *args, **kwargs):
        return _unmix_page._unmix_update_save_btn(self._ctx, *args, **kwargs)

    def _unmix_close(self, *args, **kwargs):
        return _unmix_page._unmix_close(self._ctx, *args, **kwargs)

    def _build_compensation_page(self, *args, **kwargs):
        return _compensation_page._build_compensation_page(self._ctx, *args, **kwargs)

    def _comp_files_panel(self, *args, **kwargs):
        return _compensation_page._comp_files_panel(self._ctx, *args, **kwargs)

    def _comp_matrix_panel(self, *args, **kwargs):
        return _compensation_page._comp_matrix_panel(self._ctx, *args, **kwargs)

    def _comp_file_name(self, *args, **kwargs):
        return _data_naming._comp_file_name(self._ctx, *args, **kwargs)

    def _comp_columns_for(self, *args, **kwargs):
        return _compensation_page._comp_columns_for(self._ctx, *args, **kwargs)

    def _comp_all_columns(self, *args, **kwargs):
        return _compensation_page._comp_all_columns(self._ctx, *args, **kwargs)

    def _comp_refresh(self, *args, **kwargs):
        return _compensation_page._comp_refresh(self._ctx, *args, **kwargs)

    def _comp_rebuild_table(self, *args, **kwargs):
        return _compensation_page._comp_rebuild_table(self._ctx, *args, **kwargs)

    def _comp_refresh_colors(self, *args, **kwargs):
        return _compensation_page._comp_refresh_colors(self._ctx, *args, **kwargs)

    def _comp_item_changed(self, *args, **kwargs):
        return _compensation_page._comp_item_changed(self._ctx, *args, **kwargs)

    def _comp_set_files(self, *args, **kwargs):
        return _compensation_page._comp_set_files(self._ctx, *args, **kwargs)

    def _comp_set_params(self, *args, **kwargs):
        return _compensation_page._comp_set_params(self._ctx, *args, **kwargs)

    def _comp_show_missing(self, *args, **kwargs):
        return _compensation_page._comp_show_missing(self._ctx, *args, **kwargs)

    def _comp_apply_params(self, *args, **kwargs):
        return _compensation_page._comp_apply_params(self._ctx, *args, **kwargs)

    def _comp_reordered(self, *args, **kwargs):
        return _compensation_page._comp_reordered(self._ctx, *args, **kwargs)

    def _comp_copy_matrix(self, *args, **kwargs):
        return _compensation_page._comp_copy_matrix(self._ctx, *args, **kwargs)

    def _comp_paste_matrix(self, *args, **kwargs):
        return _compensation_page._comp_paste_matrix(self._ctx, *args, **kwargs)

    def _comp_mark_dirty(self, *args, **kwargs):
        return _compensation_page._comp_mark_dirty(self._ctx, *args, **kwargs)

    def _comp_save_now(self, *args, **kwargs):
        return _compensation_page._comp_save_now(self._ctx, *args, **kwargs)

    def _comp_update_save_btn(self, *args, **kwargs):
        return _compensation_page._comp_update_save_btn(self._ctx, *args, **kwargs)

    def _comp_close(self, *args, **kwargs):
        return _compensation_page._comp_close(self._ctx, *args, **kwargs)

    def _build_illumination_page(self, *args, **kwargs):
        return _illumination_page._build_illumination_page(self._ctx, *args, **kwargs)

    def _illum_list_panel(self, *args, **kwargs):
        return _illumination_page._illum_list_panel(self._ctx, *args, **kwargs)

    def _illum_refresh_list(self, *args, **kwargs):
        return _illumination_page._illum_refresh_list(self._ctx, *args, **kwargs)

    def _illum_selected_sub(self, *args, **kwargs):
        return _illumination_page._illum_selected_sub(self._ctx, *args, **kwargs)

    def _illum_next_name(self, *args, **kwargs):
        return _illumination_page._illum_next_name(self._ctx, *args, **kwargs)

    def _illum_unique_name(self, *args, **kwargs):
        return _illumination_page._illum_unique_name(self._ctx, *args, **kwargs)

    def _illum_default_axes(self, *args, **kwargs):
        return _illumination_page._illum_default_axes(self._ctx, *args, **kwargs)

    def _illum_add_submodule(self, *args, **kwargs):
        return _illumination_page._illum_add_submodule(self._ctx, *args, **kwargs)

    def _illum_list_menu(self, *args, **kwargs):
        return _illumination_page._illum_list_menu(self._ctx, *args, **kwargs)

    def _illum_rename_submodule(self, *args, **kwargs):
        return _illumination_page._illum_rename_submodule(self._ctx, *args, **kwargs)

    def _illum_duplicate_submodule(self, *args, **kwargs):
        return _illumination_page._illum_duplicate_submodule(self._ctx, *args, **kwargs)

    def _illum_drop_mask_file(self, *args, **kwargs):
        return _illumination_page._illum_drop_mask_file(self._ctx, *args, **kwargs)

    def _illum_delete_submodule(self, *args, **kwargs):
        return _illumination_page._illum_delete_submodule(self._ctx, *args, **kwargs)

    def _illum_select_submodule(self, *args, **kwargs):
        return _illumination_page._illum_select_submodule(self._ctx, *args, **kwargs)

    def _illum_reordered(self, *args, **kwargs):
        return _illumination_page._illum_reordered(self._ctx, *args, **kwargs)

    def _illum_rebuild_right(self, *args, **kwargs):
        return _illumination_page._illum_rebuild_right(self._ctx, *args, **kwargs)

    def _illum_channel_panel(self, *args, **kwargs):
        return _illumination_page._illum_channel_panel(self._ctx, *args, **kwargs)

    def _illum_all_columns(self, *args, **kwargs):
        return _illumination_page._illum_all_columns(self._ctx, *args, **kwargs)

    def _illum_set_files(self, *args, **kwargs):
        return _illumination_page._illum_set_files(self._ctx, *args, **kwargs)

    def _illum_params_panel(self, *args, **kwargs):
        return _illumination_page._illum_params_panel(self._ctx, *args, **kwargs)

    def _illum_divisibility_hint(self, *args, **kwargs):
        return _illumination_page._illum_divisibility_hint(self._ctx, *args, **kwargs)

    def _illum_mask_panel(self, *args, **kwargs):
        return _illumination_page._illum_mask_panel(self._ctx, *args, **kwargs)

    def _illum_render_mask(self, *args, **kwargs):
        return _illumination_page._illum_render_mask(self._ctx, *args, **kwargs)

    def _illum_plot_panel(self, *args, **kwargs):
        return _illumination_page._illum_plot_panel(self._ctx, *args, **kwargs)

    def _illum_plot_settings(self, *args, **kwargs):
        return _illumination_page._illum_plot_settings(self._ctx, *args, **kwargs)

    def _illum_sub_stage(self, *args, **kwargs):
        return _illumination_page._illum_sub_stage(self._ctx, *args, **kwargs)

    def _illum_preview_stages(self, *args, **kwargs):
        return _illumination_page._illum_preview_stages(self._ctx, *args, **kwargs)

    def _illum_render_preview(self, *args, **kwargs):
        return _illumination_page._illum_render_preview(self._ctx, *args, **kwargs)

    def _illum_clear_tile_grid(self, *args, **kwargs):
        return _illumination_page._illum_clear_tile_grid(self._ctx, *args, **kwargs)

    def _illum_draw_tile_grid(self, *args, **kwargs):
        return _illumination_page._illum_draw_tile_grid(self._ctx, *args, **kwargs)

    def _illum_update_tile_grid(self, *args, **kwargs):
        return _illumination_page._illum_update_tile_grid(self._ctx, *args, **kwargs)

    def _illum_reset_plot_defaults(self, *args, **kwargs):
        return _illumination_page._illum_reset_plot_defaults(self._ctx, *args, **kwargs)

    def _illum_calculate(self, *args, **kwargs):
        return _illumination_page._illum_calculate(self._ctx, *args, **kwargs)

    def _illum_on_poll(self, *args, **kwargs):
        return _illumination_page._illum_on_poll(self._ctx, *args, **kwargs)

    def _illum_mark_dirty(self, *args, **kwargs):
        return _illumination_page._illum_mark_dirty(self._ctx, *args, **kwargs)

    def _illum_save_now(self, *args, **kwargs):
        return _illumination_page._illum_save_now(self._ctx, *args, **kwargs)

    def _illum_update_save_btn(self, *args, **kwargs):
        return _illumination_page._illum_update_save_btn(self._ctx, *args, **kwargs)

    def _illum_close(self, *args, **kwargs):
        return _illumination_page._illum_close(self._ctx, *args, **kwargs)

    def _seg_params_for(self, *args, **kwargs):
        return _segmentation_page._seg_params_for(self._ctx, *args, **kwargs)

    def _seg_mask_record(self, *args, **kwargs):
        return _segmentation_page._seg_mask_record(self._ctx, *args, **kwargs)

    def _seg_mask_file(self, *args, **kwargs):
        return _segmentation_page._seg_mask_file(self._ctx, *args, **kwargs)

    def _seg_signature(self, *args, **kwargs):
        return _segmentation_page._seg_signature(self._ctx, *args, **kwargs)

    def _seg_applied_columns(self, *args, **kwargs):
        return _data_outputs._seg_applied_columns(self._ctx, *args, **kwargs)

    def _gate2col_params_for(self, *args, **kwargs):
        return _data_feature_outputs._gate2col_params_for(self._ctx, *args, **kwargs)

    def _gate2col_signature(self, *args, **kwargs):
        return _data_feature_outputs._gate2col_signature(self._ctx, *args, **kwargs)

    def _gate2col_applied_columns(self, *args, **kwargs):
        return _data_feature_outputs._gate2col_applied_columns(self._ctx, *args, **kwargs)

    def _gate2col_column_values(self, *args, **kwargs):
        return _data_feature_outputs._gate2col_column_values(self._ctx, *args, **kwargs)

    def _feature_up_signature(self, *args, **kwargs):
        return _data_feature_outputs._feature_up_signature(self._ctx, *args, **kwargs)

    def _fup_matched_from(self, *args, **kwargs):
        return _feature_up_page._fup_matched_from(self._ctx, *args, **kwargs)

    def _feature_up_params_for(self, *args, **kwargs):
        return _data_feature_outputs._feature_up_params_for(self._ctx, *args, **kwargs)

    def _feature_up_applied_columns(self, *args, **kwargs):
        return _data_feature_outputs._feature_up_applied_columns(self._ctx, *args, **kwargs)

    def _feature_up_column_values(self, *args, **kwargs):
        return _data_feature_outputs._feature_up_column_values(self._ctx, *args, **kwargs)

    def _feature_up_raster(self, *args, **kwargs):
        return _data_feature_outputs._feature_up_raster(self._ctx, *args, **kwargs)

    def _dimred_signature(self, *args, **kwargs):
        return _dimred_page._dimred_signature(self._ctx, *args, **kwargs)

    def _dimred_params_for(self, *args, **kwargs):
        return _dimred_page._dimred_params_for(self._ctx, *args, **kwargs)

    def _dimred_applied_columns(self, *args, **kwargs):
        return _data_outputs._dimred_applied_columns(self._ctx, *args, **kwargs)

    def _dimred_column_values(self, *args, **kwargs):
        return _dimred_page._dimred_column_values(self._ctx, *args, **kwargs)

    def _dimred_raster(self, *args, **kwargs):
        return _dimred_page._dimred_raster(self._ctx, *args, **kwargs)

    def _seg_load_mask(self, *args, **kwargs):
        return _segmentation_page._seg_load_mask(self._ctx, *args, **kwargs)

    def _seg_mask_values(self, *args, **kwargs):
        return _data_outputs._seg_mask_values(self._ctx, *args, **kwargs)

    def _seg_mask_raster(self, *args, **kwargs):
        return _data_outputs._seg_mask_raster(self._ctx, *args, **kwargs)

    def _g2c_raster(self, *args, **kwargs):
        return _data_outputs._g2c_raster(self._ctx, *args, **kwargs)

    def _seg_temp_dir(self, *args, **kwargs):
        return _segmentation_page._seg_temp_dir(self._ctx, *args, **kwargs)

    def _seg_clear_edge_cache(self, *args, **kwargs):
        return _segmentation_page._seg_clear_edge_cache(self._ctx, *args, **kwargs)

    def _seg_edge_map(self, *args, **kwargs):
        return _segmentation_page._seg_edge_map(self._ctx, *args, **kwargs)

    def _build_segmentation_page(self, *args, **kwargs):
        return _segmentation_page._build_segmentation_page(self._ctx, *args, **kwargs)

    def _seg_default_plot(self, *args, **kwargs):
        return _segmentation_page._seg_default_plot(self._ctx, *args, **kwargs)

    def _seg_file_rows(self, *args, **kwargs):
        return _segmentation_page._seg_file_rows(self._ctx, *args, **kwargs)

    def _seg_file_status(self, *args, **kwargs):
        return _segmentation_page._seg_file_status(self._ctx, *args, **kwargs)

    def _seg_any_mask(self, *args, **kwargs):
        return _segmentation_page._seg_any_mask(self._ctx, *args, **kwargs)

    def _seg_files_panel(self, *args, **kwargs):
        return _segmentation_page._seg_files_panel(self._ctx, *args, **kwargs)

    def _seg_refresh_files(self, *args, **kwargs):
        return _segmentation_page._seg_refresh_files(self._ctx, *args, **kwargs)

    def _seg_expose_changed(self, *args, **kwargs):
        return _segmentation_page._seg_expose_changed(self._ctx, *args, **kwargs)

    def _seg_refresh_status(self, *args, **kwargs):
        return _segmentation_page._seg_refresh_status(self._ctx, *args, **kwargs)

    def _seg_is_image_file(self, *args, **kwargs):
        return _data_outputs._seg_is_image_file(self._ctx, *args, **kwargs)

    def _seg_set_files(self, *args, **kwargs):
        return _segmentation_page._seg_set_files(self._ctx, *args, **kwargs)

    def _seg_column_edited(self, *args, **kwargs):
        return _segmentation_page._seg_column_edited(self._ctx, *args, **kwargs)

    def _seg_on_method_changed(self, *args, **kwargs):
        return _segmentation_page._seg_on_method_changed(self._ctx, *args, **kwargs)

    def _seg_rebuild_method_panel(self, *args, **kwargs):
        return _segmentation_page._seg_rebuild_method_panel(self._ctx, *args, **kwargs)

    def _seg_method_panel(self, *args, **kwargs):
        return _segmentation_page._seg_method_panel(self._ctx, *args, **kwargs)

    def _seg_map_params(self, *args, **kwargs):
        return _segmentation_page._seg_map_params(self._ctx, *args, **kwargs)

    def _seg_otsu_value(self, *args, **kwargs):
        return _segmentation_page._seg_otsu_value(self._ctx, *args, **kwargs)

    def _seg_method_params_panel(self, *args, **kwargs):
        return _segmentation_page._seg_method_params_panel(self._ctx, *args, **kwargs)

    def _seg_channel_columns(self, *args, **kwargs):
        return _segmentation_page._seg_channel_columns(self._ctx, *args, **kwargs)

    def _seg_external_method_panel(self, *args, **kwargs):
        return _segmentation_page._seg_external_method_panel(self._ctx, *args, **kwargs)

    def _seg_mask_input_panel(self, *args, **kwargs):
        return _segmentation_page._seg_mask_input_panel(self._ctx, *args, **kwargs)

    def _seg_mask_record_live(self, *args, **kwargs):
        return _segmentation_page._seg_mask_record_live(self._ctx, *args, **kwargs)

    def _seg_refresh_input_table(self, *args, **kwargs):
        return _segmentation_page._seg_refresh_input_table(self._ctx, *args, **kwargs)

    def _seg_pick_mask_file(self, *args, **kwargs):
        return _segmentation_page._seg_pick_mask_file(self._ctx, *args, **kwargs)

    def _seg_delete_mask(self, *args, **kwargs):
        return _segmentation_page._seg_delete_mask(self._ctx, *args, **kwargs)

    def _seg_import_masks(self, *args, **kwargs):
        return _segmentation_page._seg_import_masks(self._ctx, *args, **kwargs)

    def _seg_write_mask(self, *args, **kwargs):
        return _segmentation_page._seg_write_mask(self._ctx, *args, **kwargs)

    def _seg_after_masks_changed(self, *args, **kwargs):
        return _segmentation_page._seg_after_masks_changed(self._ctx, *args, **kwargs)

    def _seg_calculate(self, *args, **kwargs):
        return _segmentation_page._seg_calculate(self._ctx, *args, **kwargs)

    def _seg_calculate_nuclear(self, *args, **kwargs):
        return _segmentation_page._seg_calculate_nuclear(self._ctx, *args, **kwargs)

    def _seg_raster_stack(self, *args, **kwargs):
        return _segmentation_page._seg_raster_stack(self._ctx, *args, **kwargs)

    def _seg_calculate_external(self, *args, **kwargs):
        return _segmentation_page._seg_calculate_external(self._ctx, *args, **kwargs)

    def _seg_log(self, *args, **kwargs):
        return _segmentation_page._seg_log(self._ctx, *args, **kwargs)

    def _seg_log_tile(self, *args, **kwargs):
        return _segmentation_page._seg_log_tile(self._ctx, *args, **kwargs)

    def _seg_ext_poll(self, *args, **kwargs):
        return _segmentation_page._seg_ext_poll(self._ctx, *args, **kwargs)

    def _seg_raster_channel(self, *args, **kwargs):
        return _segmentation_page._seg_raster_channel(self._ctx, *args, **kwargs)

    def _seg_nuclear_expansion(self, *args, **kwargs):
        return _segmentation_page._seg_nuclear_expansion(self._ctx, *args, **kwargs)

    def _seg_mark_dirty(self, *args, **kwargs):
        return _segmentation_page._seg_mark_dirty(self._ctx, *args, **kwargs)

    def _seg_update_save_btn(self, *args, **kwargs):
        return _segmentation_page._seg_update_save_btn(self._ctx, *args, **kwargs)

    def _seg_save_now(self, *args, **kwargs):
        return _segmentation_page._seg_save_now(self._ctx, *args, **kwargs)

    def _seg_close(self, *args, **kwargs):
        return _segmentation_page._seg_close(self._ctx, *args, **kwargs)

    def _seg_preview_panel(self, *args, **kwargs):
        return _segmentation_page._seg_preview_panel(self._ctx, *args, **kwargs)

    def _seg_plot_settings_panel(self, *args, **kwargs):
        return _segmentation_page._seg_plot_settings_panel(self._ctx, *args, **kwargs)

    def _seg_plot_dict(self, *args, **kwargs):
        return _segmentation_page._seg_plot_dict(self._ctx, *args, **kwargs)

    def _seg_refresh_plot_settings(self, *args, **kwargs):
        return _segmentation_page._seg_refresh_plot_settings(self._ctx, *args, **kwargs)

    def _seg_update_preview_visibility(self, *args, **kwargs):
        return _segmentation_page._seg_update_preview_visibility(self._ctx, *args, **kwargs)

    def _seg_render_preview(self, *args, **kwargs):
        return _segmentation_page._seg_render_preview(self._ctx, *args, **kwargs)

    def _seg_render_overlay(self, *args, **kwargs):
        return _segmentation_page._seg_render_overlay(self._ctx, *args, **kwargs)

    def _build_feature_page(self, *args, **kwargs):
        return _feature_page._build_feature_page(self._ctx, *args, **kwargs)

    def _feat_log_panel(self, *args, **kwargs):
        return _feature_page._feat_log_panel(self._ctx, *args, **kwargs)

    def _feat_log(self, *args, **kwargs):
        return _feature_page._feat_log(self._ctx, *args, **kwargs)

    def _feat_all_columns(self, *args, **kwargs):
        return _feature_page._feat_all_columns(self._ctx, *args, **kwargs)

    def _feat_file_columns(self, *args, **kwargs):
        return _feature_page._feat_file_columns(self._ctx, *args, **kwargs)

    def _feat_common_columns(self, *args, **kwargs):
        return _feature_page._feat_common_columns(self._ctx, *args, **kwargs)

    def _feat_files_panel(self, *args, **kwargs):
        return _feature_page._feat_files_panel(self._ctx, *args, **kwargs)

    def _feat_ignore_section(self, *args, **kwargs):
        return _feature_page._feat_ignore_section(self._ctx, *args, **kwargs)

    def _feat_value_text(self, *args, **kwargs):
        return _feature_page._feat_value_text(self._ctx, *args, **kwargs)

    def _feat_value_json(self, *args, **kwargs):
        return _feature_page._feat_value_json(self._ctx, *args, **kwargs)

    def _feat_ignore_cache_path(self, *args, **kwargs):
        return _feature_page._feat_ignore_cache_path(self._ctx, *args, **kwargs)

    def _feat_ignore_cache_load(self, *args, **kwargs):
        return _feature_page._feat_ignore_cache_load(self._ctx, *args, **kwargs)

    def _feat_ignore_cache_save(self, *args, **kwargs):
        return _feature_page._feat_ignore_cache_save(self._ctx, *args, **kwargs)

    def _feat_file_stat(self, *args, **kwargs):
        return _feature_page._feat_file_stat(self._ctx, *args, **kwargs)

    def _feat_feature_source_sig(self, *args, **kwargs):
        return _feature_page._feat_feature_source_sig(self._ctx, *args, **kwargs)

    def _feat_ignore_cache_key(self, *args, **kwargs):
        return _feature_page._feat_ignore_cache_key(self._ctx, *args, **kwargs)

    def _feat_ignore_file_counts(self, *args, **kwargs):
        return _feature_page._feat_ignore_file_counts(self._ctx, *args, **kwargs)

    def _feat_ignore_combine(self, *args, **kwargs):
        return _feature_page._feat_ignore_combine(self._ctx, *args, **kwargs)

    def _feat_ignore_apply(self, *args, **kwargs):
        return _feature_page._feat_ignore_apply(self._ctx, *args, **kwargs)

    def _feat_ignore_start_worker(self, *args, **kwargs):
        return _feature_page._feat_ignore_start_worker(self._ctx, *args, **kwargs)

    def _feat_ignore_start_timer(self, *args, **kwargs):
        return _feature_page._feat_ignore_start_timer(self._ctx, *args, **kwargs)

    def _feat_ignore_worker(self, *args, **kwargs):
        return _feature_page._feat_ignore_worker(self._ctx, *args, **kwargs)

    def _feat_ignore_poll(self, *args, **kwargs):
        return _feature_page._feat_ignore_poll(self._ctx, *args, **kwargs)

    def _feat_ignore_cleanup(self, *args, **kwargs):
        return _feature_page._feat_ignore_cleanup(self._ctx, *args, **kwargs)

    def _feat_ignore_refresh(self, *args, **kwargs):
        return _feature_page._feat_ignore_refresh(self._ctx, *args, **kwargs)

    def _feat_ignore_fill(self, *args, **kwargs):
        return _feature_page._feat_ignore_fill(self._ctx, *args, **kwargs)

    def _feat_ignore_toggled(self, *args, **kwargs):
        return _feature_page._feat_ignore_toggled(self._ctx, *args, **kwargs)

    def _feat_ignore_prepare(self, *args, **kwargs):
        return _feature_page._feat_ignore_prepare(self._ctx, *args, **kwargs)

    def _feat_select_files(self, *args, **kwargs):
        return _feature_page._feat_select_files(self._ctx, *args, **kwargs)

    def _feat_file_status(self, *args, **kwargs):
        return _feature_page._feat_file_status(self._ctx, *args, **kwargs)

    def _feat_refresh_files(self, *args, **kwargs):
        return _feature_page._feat_refresh_files(self._ctx, *args, **kwargs)

    def _feat_rules_panel(self, *args, **kwargs):
        return _feature_page._feat_rules_panel(self._ctx, *args, **kwargs)

    def _feat_rebuild_rules(self, *args, **kwargs):
        return _feature_page._feat_rebuild_rules(self._ctx, *args, **kwargs)

    def _feat_refresh_rules(self, *args, **kwargs):
        return _feature_page._feat_refresh_rules(self._ctx, *args, **kwargs)

    def _feat_auto_name(self, *args, **kwargs):
        return _feature_page._feat_auto_name(self._ctx, *args, **kwargs)

    def _feat_refresh_statuses(self, *args, **kwargs):
        return _feature_page._feat_refresh_statuses(self._ctx, *args, **kwargs)

    def _feat_mark_dirty(self, *args, **kwargs):
        return _feature_page._feat_mark_dirty(self._ctx, *args, **kwargs)

    def _feat_update_save_btn(self, *args, **kwargs):
        return _feature_page._feat_update_save_btn(self._ctx, *args, **kwargs)

    def _feat_save_now(self, *args, **kwargs):
        return _feature_page._feat_save_now(self._ctx, *args, **kwargs)

    def _feat_validate(self, *args, **kwargs):
        return _feature_page._feat_validate(self._ctx, *args, **kwargs)

    def _feat_count_name(self, *args, **kwargs):
        return _feature_page._feat_count_name(self._ctx, *args, **kwargs)

    def _feat_output_stem(self, *args, **kwargs):
        return _feature_page._feat_output_stem(self._ctx, *args, **kwargs)

    def _feat_calculate(self, *args, **kwargs):
        return _feature_page._feat_calculate(self._ctx, *args, **kwargs)

    def _feat_compute_file(self, *args, **kwargs):
        return _feature_page._feat_compute_file(self._ctx, *args, **kwargs)

    def _feat_close(self, *args, **kwargs):
        return _feature_page._feat_close(self._ctx, *args, **kwargs)

    def _build_gate2col_page(self, *args, **kwargs):
        return _gate2col_page._build_gate2col_page(self._ctx, *args, **kwargs)

    def _g2c_log_panel(self, *args, **kwargs):
        return _gate2col_page._g2c_log_panel(self._ctx, *args, **kwargs)

    def _g2c_log(self, *args, **kwargs):
        return _gate2col_page._g2c_log(self._ctx, *args, **kwargs)

    def _g2c_gate_options(self, *args, **kwargs):
        return _gate2col_page._g2c_gate_options(self._ctx, *args, **kwargs)

    def _g2c_gate_mask(self, *args, **kwargs):
        return _gate2col_page._g2c_gate_mask(self._ctx, *args, **kwargs)

    def _g2c_files_panel(self, *args, **kwargs):
        return _gate2col_page._g2c_files_panel(self._ctx, *args, **kwargs)

    def _g2c_select_files(self, *args, **kwargs):
        return _gate2col_page._g2c_select_files(self._ctx, *args, **kwargs)

    def _g2c_file_status(self, *args, **kwargs):
        return _gate2col_page._g2c_file_status(self._ctx, *args, **kwargs)

    def _g2c_refresh_files(self, *args, **kwargs):
        return _gate2col_page._g2c_refresh_files(self._ctx, *args, **kwargs)

    def _g2c_rules_panel(self, *args, **kwargs):
        return _gate2col_page._g2c_rules_panel(self._ctx, *args, **kwargs)

    def _g2c_add_rule(self, *args, **kwargs):
        return _gate2col_page._g2c_add_rule(self._ctx, *args, **kwargs)

    def _g2c_delete_rule(self, *args, **kwargs):
        return _gate2col_page._g2c_delete_rule(self._ctx, *args, **kwargs)

    def _g2c_existing_columns(self, *args, **kwargs):
        return _gate2col_page._g2c_existing_columns(self._ctx, *args, **kwargs)

    def _g2c_duplicate_names(self, *args, **kwargs):
        return _gate2col_page._g2c_duplicate_names(self._ctx, *args, **kwargs)

    def _g2c_rebuild_rules(self, *args, **kwargs):
        return _gate2col_page._g2c_rebuild_rules(self._ctx, *args, **kwargs)

    def _g2c_refresh_rules(self, *args, **kwargs):
        return _gate2col_page._g2c_refresh_rules(self._ctx, *args, **kwargs)

    def _g2c_refresh_statuses(self, *args, **kwargs):
        return _gate2col_page._g2c_refresh_statuses(self._ctx, *args, **kwargs)

    def _g2c_mark_dirty(self, *args, **kwargs):
        return _gate2col_page._g2c_mark_dirty(self._ctx, *args, **kwargs)

    def _g2c_update_save_btn(self, *args, **kwargs):
        return _gate2col_page._g2c_update_save_btn(self._ctx, *args, **kwargs)

    def _g2c_save_now(self, *args, **kwargs):
        return _gate2col_page._g2c_save_now(self._ctx, *args, **kwargs)

    def _g2c_validate(self, *args, **kwargs):
        return _gate2col_page._g2c_validate(self._ctx, *args, **kwargs)

    def _g2c_calculate(self, *args, **kwargs):
        return _gate2col_page._g2c_calculate(self._ctx, *args, **kwargs)

    def _data_row_count(self, *args, **kwargs):
        return _data_columns._data_row_count(self._ctx, *args, **kwargs)

    def _g2c_row_count(self, *args, **kwargs):
        return _gate2col_page._g2c_row_count(self._ctx, *args, **kwargs)

    def _g2c_compute_file(self, *args, **kwargs):
        return _gate2col_page._g2c_compute_file(self._ctx, *args, **kwargs)

    def _g2c_close(self, *args, **kwargs):
        return _gate2col_page._g2c_close(self._ctx, *args, **kwargs)

    def _build_feature_up_page(self, *args, **kwargs):
        return _feature_up_page._build_feature_up_page(self._ctx, *args, **kwargs)

    def _fup_log_panel(self, *args, **kwargs):
        return _feature_up_page._fup_log_panel(self._ctx, *args, **kwargs)

    def _fup_log(self, *args, **kwargs):
        return _feature_up_page._fup_log(self._ctx, *args, **kwargs)

    def _fup_files_panel(self, *args, **kwargs):
        return _feature_up_page._fup_files_panel(self._ctx, *args, **kwargs)

    def _fup_match_panel(self, *args, **kwargs):
        return _feature_up_page._fup_match_panel(self._ctx, *args, **kwargs)

    def _fup_features_panel(self, *args, **kwargs):
        return _feature_up_page._fup_features_panel(self._ctx, *args, **kwargs)

    def _fup_refresh_files(self, *args, **kwargs):
        return _feature_up_page._fup_refresh_files(self._ctx, *args, **kwargs)

    def _fup_common_columns(self, *args, **kwargs):
        return _feature_up_page._fup_common_columns(self._ctx, *args, **kwargs)

    def _fup_refresh_key_combos(self, *args, **kwargs):
        return _feature_up_page._fup_refresh_key_combos(self._ctx, *args, **kwargs)

    def _fup_on_key(self, *args, **kwargs):
        return _feature_up_page._fup_on_key(self._ctx, *args, **kwargs)

    def _fup_select_files(self, *args, **kwargs):
        return _feature_up_page._fup_select_files(self._ctx, *args, **kwargs)

    def _fup_select_features(self, *args, **kwargs):
        return _feature_up_page._fup_select_features(self._ctx, *args, **kwargs)

    def _fup_delete_feature(self, *args, **kwargs):
        return _feature_up_page._fup_delete_feature(self._ctx, *args, **kwargs)

    def _fup_rebuild_features(self, *args, **kwargs):
        return _feature_up_page._fup_rebuild_features(self._ctx, *args, **kwargs)

    def _fup_existing_columns(self, *args, **kwargs):
        return _feature_up_page._fup_existing_columns(self._ctx, *args, **kwargs)

    def _fup_duplicate_names(self, *args, **kwargs):
        return _feature_up_page._fup_duplicate_names(self._ctx, *args, **kwargs)

    def _fup_default_to(self, *args, **kwargs):
        return _feature_up_page._fup_default_to(self._ctx, *args, **kwargs)

    def _fup_match(self, *args, **kwargs):
        return _feature_up_page._fup_match(self._ctx, *args, **kwargs)

    def _fup_key_values(self, *args, **kwargs):
        return _feature_up_page._fup_key_values(self._ctx, *args, **kwargs)

    def _fup_fill_match_stats(self, *args, **kwargs):
        return _feature_up_page._fup_fill_match_stats(self._ctx, *args, **kwargs)

    def _fup_rebuild_match_row(self, *args, **kwargs):
        return _feature_up_page._fup_rebuild_match_row(self._ctx, *args, **kwargs)

    def _fup_rebuild_matches(self, *args, **kwargs):
        return _feature_up_page._fup_rebuild_matches(self._ctx, *args, **kwargs)

    def _fup_validate(self, *args, **kwargs):
        return _feature_up_page._fup_validate(self._ctx, *args, **kwargs)

    def _fup_calculate(self, *args, **kwargs):
        return _feature_up_page._fup_calculate(self._ctx, *args, **kwargs)

    def _fup_compute_file(self, *args, **kwargs):
        return _feature_up_page._fup_compute_file(self._ctx, *args, **kwargs)

    def _fup_mark_dirty(self, *args, **kwargs):
        return _feature_up_page._fup_mark_dirty(self._ctx, *args, **kwargs)

    def _fup_update_save_btn(self, *args, **kwargs):
        return _feature_up_page._fup_update_save_btn(self._ctx, *args, **kwargs)

    def _fup_close(self, *args, **kwargs):
        return _feature_up_page._fup_close(self._ctx, *args, **kwargs)

    def _build_subset_page(self, *args, **kwargs):
        return _subset_page._build_subset_page(self._ctx, *args, **kwargs)

    def _subset_files_panel(self, *args, **kwargs):
        return _subset_page._subset_files_panel(self._ctx, *args, **kwargs)

    def _subset_columns_panel(self, *args, **kwargs):
        return _subset_page._subset_columns_panel(self._ctx, *args, **kwargs)

    def _subset_column_options(self, *args, **kwargs):
        return _subset_page._subset_column_options(self._ctx, *args, **kwargs)

    def _subset_select_files(self, *args, **kwargs):
        return _subset_page._subset_select_files(self._ctx, *args, **kwargs)

    def _subset_select_columns(self, *args, **kwargs):
        return _subset_page._subset_select_columns(self._ctx, *args, **kwargs)

    def _subset_refresh(self, *args, **kwargs):
        return _subset_page._subset_refresh(self._ctx, *args, **kwargs)

    def _subset_mark_dirty(self, *args, **kwargs):
        return _subset_page._subset_mark_dirty(self._ctx, *args, **kwargs)

    def _subset_update_save_btn(self, *args, **kwargs):
        return _subset_page._subset_update_save_btn(self._ctx, *args, **kwargs)

    def _subset_save_now(self, *args, **kwargs):
        return _subset_page._subset_save_now(self._ctx, *args, **kwargs)

    def _subset_close(self, *args, **kwargs):
        return _subset_page._subset_close(self._ctx, *args, **kwargs)

    def _subsample_params_for(self, *args, **kwargs):
        return _data_sampling._subsample_params_for(self._ctx, *args, **kwargs)

    def _subsample_tasks(self, *args, **kwargs):
        return _data_sampling._subsample_tasks(self._ctx, *args, **kwargs)

    def _subsample_sig(self, *args, **kwargs):
        return _data_sampling._subsample_sig(self._ctx, *args, **kwargs)

    def _subsample_seed(self, *args, **kwargs):
        return _data_sampling._subsample_seed(self._ctx, *args, **kwargs)

    def _subsample_visible_count(self, *args, **kwargs):
        return _data_sampling._subsample_visible_count(self._ctx, *args, **kwargs)

    def _subsample_row_index(self, *args, **kwargs):
        return _data_sampling._subsample_row_index(self._ctx, *args, **kwargs)

    def _subsample_coverage_dir(self, *args, **kwargs):
        return _data_sampling._subsample_coverage_dir(self._ctx, *args, **kwargs)

    def _subsample_coverage_paths(self, *args, **kwargs):
        return _data_sampling._subsample_coverage_paths(self._ctx, *args, **kwargs)

    def _subsample_mask_meta(self, *args, **kwargs):
        return _data_sampling._subsample_mask_meta(self._ctx, *args, **kwargs)

    def _subsample_own_mask(self, *args, **kwargs):
        return _data_sampling._subsample_own_mask(self._ctx, *args, **kwargs)

    def _subsample_saved_mask(self, *args, **kwargs):
        return _subsample_page._subsample_saved_mask(self._ctx, *args, **kwargs)

    def _subsample_write_masks(self, *args, **kwargs):
        return _subsample_page._subsample_write_masks(self._ctx, *args, **kwargs)

    def _subsample_module_mask(self, *args, **kwargs):
        return _subsample_page._subsample_module_mask(self._ctx, *args, **kwargs)

    def _subsample_pixel_mask(self, *args, **kwargs):
        return _subsample_page._subsample_pixel_mask(self._ctx, *args, **kwargs)

    def _subsample_cover_ent(self, *args, **kwargs):
        return _subsample_page._subsample_cover_ent(self._ctx, *args, **kwargs)

    def _subsample_cover_crop(self, *args, **kwargs):
        return _data_sampling._subsample_cover_crop(self._ctx, *args, **kwargs)

    def _build_subsample_page(self, *args, **kwargs):
        return _subsample_page._build_subsample_page(self._ctx, *args, **kwargs)

    def _subs_settings_panel(self, *args, **kwargs):
        return _subsample_page._subs_settings_panel(self._ctx, *args, **kwargs)

    def _subs_table_panel(self, *args, **kwargs):
        return _subsample_page._subs_table_panel(self._ctx, *args, **kwargs)

    def _subs_visible_sizes(self, *args, **kwargs):
        return _subsample_page._subs_visible_sizes(self._ctx, *args, **kwargs)

    def _subs_computed(self, *args, **kwargs):
        return _subsample_page._subs_computed(self._ctx, *args, **kwargs)

    def _subs_select_files(self, *args, **kwargs):
        return _subsample_page._subs_select_files(self._ctx, *args, **kwargs)

    def _subs_param_changed(self, *args, **kwargs):
        return _subsample_page._subs_param_changed(self._ctx, *args, **kwargs)

    def _subs_percent_mode(self, *args, **kwargs):
        return _subsample_page._subs_percent_mode(self._ctx, *args, **kwargs)

    def _subs_on_basis(self, *args, **kwargs):
        return _subsample_page._subs_on_basis(self._ctx, *args, **kwargs)

    def _subs_on_unit(self, *args, **kwargs):
        return _subsample_page._subs_on_unit(self._ctx, *args, **kwargs)

    def _subs_on_value(self, *args, **kwargs):
        return _subsample_page._subs_on_value(self._ctx, *args, **kwargs)

    def _subs_on_alloc(self, *args, **kwargs):
        return _subsample_page._subs_on_alloc(self._ctx, *args, **kwargs)

    def _subs_on_cell(self, *args, **kwargs):
        return _subsample_page._subs_on_cell(self._ctx, *args, **kwargs)

    def _subs_refresh(self, *args, **kwargs):
        return _subsample_page._subs_refresh(self._ctx, *args, **kwargs)

    def _subs_mark_dirty(self, *args, **kwargs):
        return _subsample_page._subs_mark_dirty(self._ctx, *args, **kwargs)

    def _subs_update_save_btn(self, *args, **kwargs):
        return _subsample_page._subs_update_save_btn(self._ctx, *args, **kwargs)

    def _subs_save_now(self, *args, **kwargs):
        return _subsample_page._subs_save_now(self._ctx, *args, **kwargs)

    def _subs_close(self, *args, **kwargs):
        return _subsample_page._subs_close(self._ctx, *args, **kwargs)

    def _build_rename_page(self, *args, **kwargs):
        return _rename_page._build_rename_page(self._ctx, *args, **kwargs)

    def _rename_file_panel(self, *args, **kwargs):
        return _rename_page._rename_file_panel(self._ctx, *args, **kwargs)

    def _rename_column_panel(self, *args, **kwargs):
        return _rename_page._rename_column_panel(self._ctx, *args, **kwargs)

    def _rename_table(self, *args, **kwargs):
        return _rename_page._rename_table(self._ctx, *args, **kwargs)

    def _rename_copy_paste(self, *args, **kwargs):
        return _rename_page._rename_copy_paste(self._ctx, *args, **kwargs)

    def _rename_on_mode(self, *args, **kwargs):
        return _rename_page._rename_on_mode(self._ctx, *args, **kwargs)

    def _rename_cfg(self, *args, **kwargs):
        return _rename_page._rename_cfg(self._ctx, *args, **kwargs)

    def _rename_select_files(self, *args, **kwargs):
        return _rename_page._rename_select_files(self._ctx, *args, **kwargs)

    def _rename_column_union(self, *args, **kwargs):
        return _rename_page._rename_column_union(self._ctx, *args, **kwargs)

    def _rename_select_vars(self, *args, **kwargs):
        return _rename_page._rename_select_vars(self._ctx, *args, **kwargs)

    def _rename_file_status(self, *args, **kwargs):
        return _rename_page._rename_file_status(self._ctx, *args, **kwargs)

    def _rename_column_status(self, *args, **kwargs):
        return _rename_page._rename_column_status(self._ctx, *args, **kwargs)

    def _rename_refresh(self, *args, **kwargs):
        return _rename_page._rename_refresh(self._ctx, *args, **kwargs)

    def _rename_refresh_file(self, *args, **kwargs):
        return _rename_page._rename_refresh_file(self._ctx, *args, **kwargs)

    def _rename_statuses_file(self, *args, **kwargs):
        return _rename_page._rename_statuses_file(self._ctx, *args, **kwargs)

    def _rename_refresh_column(self, *args, **kwargs):
        return _rename_page._rename_refresh_column(self._ctx, *args, **kwargs)

    def _rename_statuses_column(self, *args, **kwargs):
        return _rename_page._rename_statuses_column(self._ctx, *args, **kwargs)

    def _rename_status_item(self, *args, **kwargs):
        return _rename_page._rename_status_item(self._ctx, *args, **kwargs)

    def _rename_on_cell(self, *args, **kwargs):
        return _rename_page._rename_on_cell(self._ctx, *args, **kwargs)

    def _rename_table_rows(self, *args, **kwargs):
        return _rename_page._rename_table_rows(self._ctx, *args, **kwargs)

    def _rename_copy(self, *args, **kwargs):
        return _rename_page._rename_copy(self._ctx, *args, **kwargs)

    def _rename_paste(self, *args, **kwargs):
        return _rename_page._rename_paste(self._ctx, *args, **kwargs)

    def _rename_apply(self, *args, **kwargs):
        return _rename_page._rename_apply(self._ctx, *args, **kwargs)

    def _rename_mark_dirty(self, *args, **kwargs):
        return _rename_page._rename_mark_dirty(self._ctx, *args, **kwargs)

    def _rename_update_save_btn(self, *args, **kwargs):
        return _rename_page._rename_update_save_btn(self._ctx, *args, **kwargs)

    def _rename_save_now(self, *args, **kwargs):
        return _rename_page._rename_save_now(self._ctx, *args, **kwargs)

    def _rename_close(self, *args, **kwargs):
        return _rename_page._rename_close(self._ctx, *args, **kwargs)

    def _build_dimred_page(self, *args, **kwargs):
        return _dimred_page._build_dimred_page(self._ctx, *args, **kwargs)

    def _dimred_warm_umap(self, *args, **kwargs):
        return _dimred_page._dimred_warm_umap(self._ctx, *args, **kwargs)

    def _dimred_warm_opentsne(self, *args, **kwargs):
        return _dimred_page._dimred_warm_opentsne(self._ctx, *args, **kwargs)

    def _dimred_log_panel(self, *args, **kwargs):
        return _dimred_page._dimred_log_panel(self._ctx, *args, **kwargs)

    def _dimred_log(self, *args, **kwargs):
        return _dimred_page._dimred_log(self._ctx, *args, **kwargs)

    def _dimred_left_panel(self, *args, **kwargs):
        return _dimred_page._dimred_left_panel(self._ctx, *args, **kwargs)

    def _dimred_center_panel(self, *args, **kwargs):
        return _dimred_page._dimred_center_panel(self._ctx, *args, **kwargs)

    def _dimred_right_panel(self, *args, **kwargs):
        return _dimred_page._dimred_right_panel(self._ctx, *args, **kwargs)

    def _dimred_is_tsne(self, *args, **kwargs):
        return _dimred_page._dimred_is_tsne(self._ctx, *args, **kwargs)

    def _dimred_sample_seed(self, *args, **kwargs):
        return _dimred_page._dimred_sample_seed(self._ctx, *args, **kwargs)

    def _dimred_on_method(self, *args, **kwargs):
        return _dimred_page._dimred_on_method(self._ctx, *args, **kwargs)

    def _dimred_on_param(self, *args, **kwargs):
        return _dimred_page._dimred_on_param(self._ctx, *args, **kwargs)

    def _dimred_on_normalize(self, *args, **kwargs):
        return _dimred_page._dimred_on_normalize(self._ctx, *args, **kwargs)

    def _dimred_on_pca(self, *args, **kwargs):
        return _dimred_page._dimred_on_pca(self._ctx, *args, **kwargs)

    def _dimred_on_parallel(self, *args, **kwargs):
        return _dimred_page._dimred_on_parallel(self._ctx, *args, **kwargs)

    def _dimred_on_max_events(self, *args, **kwargs):
        return _dimred_page._dimred_on_max_events(self._ctx, *args, **kwargs)

    def _dimred_on_backend(self, *args, **kwargs):
        return _dimred_page._dimred_on_backend(self._ctx, *args, **kwargs)

    def _dimred_on_names(self, *args, **kwargs):
        return _dimred_page._dimred_on_names(self._ctx, *args, **kwargs)

    def _dimred_common_columns(self, *args, **kwargs):
        return _dimred_page._dimred_common_columns(self._ctx, *args, **kwargs)

    def _dimred_select_files(self, *args, **kwargs):
        return _dimred_page._dimred_select_files(self._ctx, *args, **kwargs)

    def _dimred_select_vars(self, *args, **kwargs):
        return _dimred_page._dimred_select_vars(self._ctx, *args, **kwargs)

    def _dimred_existing_columns(self, *args, **kwargs):
        return _dimred_page._dimred_existing_columns(self._ctx, *args, **kwargs)

    def _dimred_name_conflicts(self, *args, **kwargs):
        return _dimred_page._dimred_name_conflicts(self._ctx, *args, **kwargs)

    def _dimred_update_names(self, *args, **kwargs):
        return _dimred_page._dimred_update_names(self._ctx, *args, **kwargs)

    def _dimred_update_calc_btn(self, *args, **kwargs):
        return _dimred_page._dimred_update_calc_btn(self._ctx, *args, **kwargs)

    def _dimred_estimate(self, *args, **kwargs):
        return _dimred_page._dimred_estimate(self._ctx, *args, **kwargs)

    def _dimred_estimate_text(self, *args, **kwargs):
        return _dimred_page._dimred_estimate_text(self._ctx, *args, **kwargs)

    def _dimred_refresh(self, *args, **kwargs):
        return _dimred_page._dimred_refresh(self._ctx, *args, **kwargs)

    def _dimred_validate(self, *args, **kwargs):
        return _dimred_page._dimred_validate(self._ctx, *args, **kwargs)

    def _dimred_run(self, *args, **kwargs):
        return _dimred_page._dimred_run(self._ctx, *args, **kwargs)

    def _dimred_fit(self, *args, **kwargs):
        return _dimred_page._dimred_fit(self._ctx, *args, **kwargs)

    def _dimred_prepare(self, *args, **kwargs):
        return _dimred_page._dimred_prepare(self._ctx, *args, **kwargs)

    def _dimred_write_file(self, *args, **kwargs):
        return _dimred_page._dimred_write_file(self._ctx, *args, **kwargs)

    def _dimred_calculate(self, *args, **kwargs):
        return _dimred_page._dimred_calculate(self._ctx, *args, **kwargs)

    def _dimred_poll(self, *args, **kwargs):
        return _dimred_page._dimred_poll(self._ctx, *args, **kwargs)

    def _dimred_mark_dirty(self, *args, **kwargs):
        return _dimred_page._dimred_mark_dirty(self._ctx, *args, **kwargs)

    def _dimred_update_save_btn(self, *args, **kwargs):
        return _dimred_page._dimred_update_save_btn(self._ctx, *args, **kwargs)

    def _dimred_save_now(self, *args, **kwargs):
        return _dimred_page._dimred_save_now(self._ctx, *args, **kwargs)

    def _dimred_close(self, *args, **kwargs):
        return _dimred_page._dimred_close(self._ctx, *args, **kwargs)

    def _cluster_signature(self, *args, **kwargs):
        return _cluster_page._cluster_signature(self._ctx, *args, **kwargs)

    def _cluster_params_for(self, *args, **kwargs):
        return _cluster_page._cluster_params_for(self._ctx, *args, **kwargs)

    def _cluster_applied_columns(self, *args, **kwargs):
        return _data_outputs._cluster_applied_columns(self._ctx, *args, **kwargs)

    def _cluster_column_values(self, *args, **kwargs):
        return _data_outputs._cluster_column_values(self._ctx, *args, **kwargs)

    def _cluster_raster(self, *args, **kwargs):
        return _data_outputs._cluster_raster(self._ctx, *args, **kwargs)

    def _build_cluster_page(self, *args, **kwargs):
        return _cluster_page._build_cluster_page(self._ctx, *args, **kwargs)

    def _cluster_log_panel(self, *args, **kwargs):
        return _cluster_page._cluster_log_panel(self._ctx, *args, **kwargs)

    def _cluster_log(self, *args, **kwargs):
        return _cluster_page._cluster_log(self._ctx, *args, **kwargs)

    def _cluster_left_panel(self, *args, **kwargs):
        return _cluster_page._cluster_left_panel(self._ctx, *args, **kwargs)

    def _cluster_center_panel(self, *args, **kwargs):
        return _cluster_page._cluster_center_panel(self._ctx, *args, **kwargs)

    def _cluster_right_panel(self, *args, **kwargs):
        return _cluster_page._cluster_right_panel(self._ctx, *args, **kwargs)

    def _cluster_on_method(self, *args, **kwargs):
        return _cluster_page._cluster_on_method(self._ctx, *args, **kwargs)

    def _cluster_on_param(self, *args, **kwargs):
        return _cluster_page._cluster_on_param(self._ctx, *args, **kwargs)

    def _cluster_update_ext_state(self, *args, **kwargs):
        return _cluster_page._cluster_update_ext_state(self._ctx, *args, **kwargs)

    def _cluster_on_normalize(self, *args, **kwargs):
        return _cluster_page._cluster_on_normalize(self._ctx, *args, **kwargs)

    def _cluster_on_max_events(self, *args, **kwargs):
        return _cluster_page._cluster_on_max_events(self._ctx, *args, **kwargs)

    def _cluster_on_name(self, *args, **kwargs):
        return _cluster_page._cluster_on_name(self._ctx, *args, **kwargs)

    def _cluster_common_columns(self, *args, **kwargs):
        return _cluster_page._cluster_common_columns(self._ctx, *args, **kwargs)

    def _cluster_select_files(self, *args, **kwargs):
        return _cluster_page._cluster_select_files(self._ctx, *args, **kwargs)

    def _cluster_select_vars(self, *args, **kwargs):
        return _cluster_page._cluster_select_vars(self._ctx, *args, **kwargs)

    def _cluster_existing_columns(self, *args, **kwargs):
        return _cluster_page._cluster_existing_columns(self._ctx, *args, **kwargs)

    def _cluster_name_conflict(self, *args, **kwargs):
        return _cluster_page._cluster_name_conflict(self._ctx, *args, **kwargs)

    def _cluster_update_name(self, *args, **kwargs):
        return _cluster_page._cluster_update_name(self._ctx, *args, **kwargs)

    def _cluster_update_calc_btn(self, *args, **kwargs):
        return _cluster_page._cluster_update_calc_btn(self._ctx, *args, **kwargs)

    def _cluster_refresh(self, *args, **kwargs):
        return _cluster_page._cluster_refresh(self._ctx, *args, **kwargs)

    def _cluster_validate(self, *args, **kwargs):
        return _cluster_page._cluster_validate(self._ctx, *args, **kwargs)

    def _cluster_sample_seed(self, *args, **kwargs):
        return _cluster_page._cluster_sample_seed(self._ctx, *args, **kwargs)

    def _cluster_prepare(self, *args, **kwargs):
        return _cluster_page._cluster_prepare(self._ctx, *args, **kwargs)

    def _cluster_fit(self, *args, **kwargs):
        return _cluster_page._cluster_fit(self._ctx, *args, **kwargs)

    def _cluster_fit_parc(self, *args, **kwargs):
        return _cluster_page._cluster_fit_parc(self._ctx, *args, **kwargs)

    def _cluster_fit_flowsom(self, *args, **kwargs):
        return _cluster_page._cluster_fit_flowsom(self._ctx, *args, **kwargs)

    def _cluster_write_file(self, *args, **kwargs):
        return _cluster_page._cluster_write_file(self._ctx, *args, **kwargs)

    def _cluster_run(self, *args, **kwargs):
        return _cluster_page._cluster_run(self._ctx, *args, **kwargs)

    def _cluster_calculate(self, *args, **kwargs):
        return _cluster_page._cluster_calculate(self._ctx, *args, **kwargs)

    def _cluster_poll(self, *args, **kwargs):
        return _cluster_page._cluster_poll(self._ctx, *args, **kwargs)

    def _cluster_mark_dirty(self, *args, **kwargs):
        return _cluster_page._cluster_mark_dirty(self._ctx, *args, **kwargs)

    def _cluster_update_save_btn(self, *args, **kwargs):
        return _cluster_page._cluster_update_save_btn(self._ctx, *args, **kwargs)

    def _cluster_save_now(self, *args, **kwargs):
        return _cluster_page._cluster_save_now(self._ctx, *args, **kwargs)

    def _cluster_close(self, *args, **kwargs):
        return _cluster_page._cluster_close(self._ctx, *args, **kwargs)

    def _heatmap_store_path(self, *args, **kwargs):
        return _heatmap_page._heatmap_store_path(self._ctx, *args, **kwargs)

    def _heatmap_store_load(self, *args, **kwargs):
        return _heatmap_page._heatmap_store_load(self._ctx, *args, **kwargs)

    def _heatmap_store_save(self, *args, **kwargs):
        return _heatmap_page._heatmap_store_save(self._ctx, *args, **kwargs)

    def _heatmap_signature(self, *args, **kwargs):
        return _heatmap_page._heatmap_signature(self._ctx, *args, **kwargs)

    def _heatmap_gate_options(self, *args, **kwargs):
        return _heatmap_page._heatmap_gate_options(self._ctx, *args, **kwargs)

    def _heatmap_common_columns(self, *args, **kwargs):
        return _heatmap_page._heatmap_common_columns(self._ctx, *args, **kwargs)

    def _heatmap_file_sizes(self, *args, **kwargs):
        return _heatmap_page._heatmap_file_sizes(self._ctx, *args, **kwargs)

    def _heatmap_sampled_counts(self, *args, **kwargs):
        return _heatmap_page._heatmap_sampled_counts(self._ctx, *args, **kwargs)

    def _build_heatmap_page(self, *args, **kwargs):
        return _heatmap_page._build_heatmap_page(self._ctx, *args, **kwargs)

    def _heatmap_left_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_left_panel(self._ctx, *args, **kwargs)

    def _heatmap_files_section(self, *args, **kwargs):
        return _heatmap_page._heatmap_files_section(self._ctx, *args, **kwargs)

    def _heatmap_sampling_section(self, *args, **kwargs):
        return _heatmap_page._heatmap_sampling_section(self._ctx, *args, **kwargs)

    def _heatmap_gates_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_gates_panel(self._ctx, *args, **kwargs)

    def _heatmap_variables_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_variables_panel(self._ctx, *args, **kwargs)

    def _heatmap_middle_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_middle_panel(self._ctx, *args, **kwargs)

    def _heatmap_right_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_right_panel(self._ctx, *args, **kwargs)

    def _heatmap_plot_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_plot_panel(self._ctx, *args, **kwargs)

    def _heatmap_log_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_log_panel(self._ctx, *args, **kwargs)

    def _heatmap_params_panel(self, *args, **kwargs):
        return _heatmap_page._heatmap_params_panel(self._ctx, *args, **kwargs)

    def _heatmap_fill_params(self, *args, **kwargs):
        return _heatmap_page._heatmap_fill_params(self._ctx, *args, **kwargs)

    def _heatmap_select_files(self, *args, **kwargs):
        return _heatmap_page._heatmap_select_files(self._ctx, *args, **kwargs)

    def _heatmap_select_gates(self, *args, **kwargs):
        return _heatmap_page._heatmap_select_gates(self._ctx, *args, **kwargs)

    def _heatmap_select_variables(self, *args, **kwargs):
        return _heatmap_page._heatmap_select_variables(self._ctx, *args, **kwargs)

    def _heatmap_on_order(self, *args, **kwargs):
        return _heatmap_page._heatmap_on_order(self._ctx, *args, **kwargs)

    def _heatmap_fill_list(self, *args, **kwargs):
        return _heatmap_page._heatmap_fill_list(self._ctx, *args, **kwargs)

    def _heatmap_refresh(self, *args, **kwargs):
        return _heatmap_page._heatmap_refresh(self._ctx, *args, **kwargs)

    def _heatmap_sync_sampling_controls(self, *args, **kwargs):
        return _heatmap_page._heatmap_sync_sampling_controls(self._ctx, *args, **kwargs)

    def _heatmap_on_sampling(self, *args, **kwargs):
        return _heatmap_page._heatmap_on_sampling(self._ctx, *args, **kwargs)

    def _heatmap_on_sampling_value(self, *args, **kwargs):
        return _heatmap_page._heatmap_on_sampling_value(self._ctx, *args, **kwargs)

    def _heatmap_refresh_sampling(self, *args, **kwargs):
        return _heatmap_page._heatmap_refresh_sampling(self._ctx, *args, **kwargs)

    def _heatmap_update_buttons(self, *args, **kwargs):
        return _heatmap_page._heatmap_update_buttons(self._ctx, *args, **kwargs)

    def _heatmap_refresh_stale(self, *args, **kwargs):
        return _heatmap_page._heatmap_refresh_stale(self._ctx, *args, **kwargs)

    def _heatmap_update_save_btn(self, *args, **kwargs):
        return _heatmap_page._heatmap_update_save_btn(self._ctx, *args, **kwargs)

    def _heatmap_mark_dirty(self, *args, **kwargs):
        return _heatmap_page._heatmap_mark_dirty(self._ctx, *args, **kwargs)

    def _heatmap_log(self, *args, **kwargs):
        return _heatmap_page._heatmap_log(self._ctx, *args, **kwargs)

    def _heatmap_calculate(self, *args, **kwargs):
        return _heatmap_page._heatmap_calculate(self._ctx, *args, **kwargs)

    def _heatmap_cancel(self, *args, **kwargs):
        return _heatmap_page._heatmap_cancel(self._ctx, *args, **kwargs)

    def _heatmap_run(self, *args, **kwargs):
        return _heatmap_page._heatmap_run(self._ctx, *args, **kwargs)

    def _heatmap_poll(self, *args, **kwargs):
        return _heatmap_page._heatmap_poll(self._ctx, *args, **kwargs)

    def _heatmap_apply_result(self, *args, **kwargs):
        return _heatmap_page._heatmap_apply_result(self._ctx, *args, **kwargs)

    def _heatmap_display(self, *args, **kwargs):
        return _heatmap_page._heatmap_display(self._ctx, *args, **kwargs)

    def _heatmap_dendro(self, *args, **kwargs):
        return _heatmap_page._heatmap_dendro(self._ctx, *args, **kwargs)

    def _heatmap_refresh_plot(self, *args, **kwargs):
        return _heatmap_page._heatmap_refresh_plot(self._ctx, *args, **kwargs)

    def _heatmap_copy_matrix(self, *args, **kwargs):
        return _heatmap_page._heatmap_copy_matrix(self._ctx, *args, **kwargs)

    def _heatmap_save_now(self, *args, **kwargs):
        return _heatmap_page._heatmap_save_now(self._ctx, *args, **kwargs)

    def _heatmap_close(self, *args, **kwargs):
        return _heatmap_page._heatmap_close(self._ctx, *args, **kwargs)

    def _heatmap_cleanup(self, *args, **kwargs):
        return _heatmap_page._heatmap_cleanup(self._ctx, *args, **kwargs)

    def _export_params_for(self, *args, **kwargs):
        return _export_page._export_params_for(self._ctx, *args, **kwargs)

    def _export_pane(self, *args, **kwargs):
        return _export_page._export_pane(self._ctx, *args, **kwargs)

    def _export_default_columns(self, *args, **kwargs):
        return _export_page._export_default_columns(self._ctx, *args, **kwargs)

    def _build_export_page(self, *args, **kwargs):
        return _export_page._build_export_page(self._ctx, *args, **kwargs)

    def _export_subpage(self, *args, **kwargs):
        return _export_page._export_subpage(self._ctx, *args, **kwargs)

    def _export_files_panel(self, *args, **kwargs):
        return _export_page._export_files_panel(self._ctx, *args, **kwargs)

    def _export_columns_panel(self, *args, **kwargs):
        return _export_page._export_columns_panel(self._ctx, *args, **kwargs)

    def _export_settings_panel(self, *args, **kwargs):
        return _export_page._export_settings_panel(self._ctx, *args, **kwargs)

    def _export_records(self, *args, **kwargs):
        return _export_page._export_records(self._ctx, *args, **kwargs)

    def _export_schema_types(self, *args, **kwargs):
        return _export_page._export_schema_types(self._ctx, *args, **kwargs)

    def _export_source_column(self, *args, **kwargs):
        return _export_page._export_source_column(self._ctx, *args, **kwargs)

    def _export_common_columns(self, *args, **kwargs):
        return _export_page._export_common_columns(self._ctx, *args, **kwargs)

    def _export_select_files(self, *args, **kwargs):
        return _export_page._export_select_files(self._ctx, *args, **kwargs)

    def _export_select_columns(self, *args, **kwargs):
        return _export_page._export_select_columns(self._ctx, *args, **kwargs)

    def _export_on_order(self, *args, **kwargs):
        return _export_page._export_on_order(self._ctx, *args, **kwargs)

    def _export_browse(self, *args, **kwargs):
        return _export_page._export_browse(self._ctx, *args, **kwargs)

    def _export_on_format(self, *args, **kwargs):
        return _export_page._export_on_format(self._ctx, *args, **kwargs)

    def _export_on_tab(self, *args, **kwargs):
        return _export_page._export_on_tab(self._ctx, *args, **kwargs)

    def _export_refresh(self, *args, **kwargs):
        return _export_page._export_refresh(self._ctx, *args, **kwargs)

    def _export_refresh_pane(self, *args, **kwargs):
        return _export_page._export_refresh_pane(self._ctx, *args, **kwargs)

    def _export_update_buttons(self, *args, **kwargs):
        return _export_page._export_update_buttons(self._ctx, *args, **kwargs)

    def _export_mark_dirty(self, *args, **kwargs):
        return _export_page._export_mark_dirty(self._ctx, *args, **kwargs)

    def _export_update_save_btn(self, *args, **kwargs):
        return _export_page._export_update_save_btn(self._ctx, *args, **kwargs)

    def _export_log(self, *args, **kwargs):
        return _export_page._export_log(self._ctx, *args, **kwargs)

    def _export_file_name(self, *args, **kwargs):
        return _export_page._export_file_name(self._ctx, *args, **kwargs)

    def _export_targets(self, *args, **kwargs):
        return _export_page._export_targets(self._ctx, *args, **kwargs)

    def _export_conflicts(self, *args, **kwargs):
        return _export_page._export_conflicts(self._ctx, *args, **kwargs)

    def _export_start(self, *args, **kwargs):
        return _export_page._export_start(self._ctx, *args, **kwargs)

    def _export_confirm(self, *args, **kwargs):
        return _export_page._export_confirm(self._ctx, *args, **kwargs)

    def _export_cancel(self, *args, **kwargs):
        return _export_page._export_cancel(self._ctx, *args, **kwargs)

    def _export_cancelled(self, *args, **kwargs):
        return _export_page._export_cancelled(self._ctx, *args, **kwargs)

    def _export_job(self, *args, **kwargs):
        return _export_page._export_job(self._ctx, *args, **kwargs)

    def _export_job_table(self, *args, **kwargs):
        return _export_page._export_job_table(self._ctx, *args, **kwargs)

    def _export_frame(self, *args, **kwargs):
        return _export_page._export_frame(self._ctx, *args, **kwargs)

    def _export_write_fcs(self, *args, **kwargs):
        return _export_page._export_write_fcs(self._ctx, *args, **kwargs)

    def _export_image_geometry(self, *args, **kwargs):
        return _export_page._export_image_geometry(self._ctx, *args, **kwargs)

    def _export_job_image(self, *args, **kwargs):
        return _export_page._export_job_image(self._ctx, *args, **kwargs)

    def _export_image_data(self, *args, **kwargs):
        return _export_page._export_image_data(self._ctx, *args, **kwargs)

    def _export_poll(self, *args, **kwargs):
        return _export_page._export_poll(self._ctx, *args, **kwargs)

    def _export_finish(self, *args, **kwargs):
        return _export_page._export_finish(self._ctx, *args, **kwargs)

    def _export_save_now(self, *args, **kwargs):
        return _export_page._export_save_now(self._ctx, *args, **kwargs)

    def _export_close(self, *args, **kwargs):
        return _export_page._export_close(self._ctx, *args, **kwargs)

    def _export_cleanup(self, *args, **kwargs):
        return _export_page._export_cleanup(self._ctx, *args, **kwargs)

    def _build_area_page(self, *args, **kwargs):
        return _area_page._build_area_page(self._ctx, *args, **kwargs)

    def _area_left_panel(self, *args, **kwargs):
        return _area_page._area_left_panel(self._ctx, *args, **kwargs)

    def _area_center_panel(self, *args, **kwargs):
        return _area_page._area_center_panel(self._ctx, *args, **kwargs)

    def _area_right_panel(self, *args, **kwargs):
        return _area_page._area_right_panel(self._ctx, *args, **kwargs)

    def _area_select_files(self, *args, **kwargs):
        return _area_page._area_select_files(self._ctx, *args, **kwargs)

    def _area_refresh(self, *args, **kwargs):
        return _area_page._area_refresh(self._ctx, *args, **kwargs)

    def _area_update_summary(self, *args, **kwargs):
        return _area_page._area_update_summary(self._ctx, *args, **kwargs)

    def _area_on_file_toggle(self, *args, **kwargs):
        return _area_page._area_on_file_toggle(self._ctx, *args, **kwargs)

    def _area_batch_common_columns(self, *args, **kwargs):
        return _area_page._area_batch_common_columns(self._ctx, *args, **kwargs)

    def _area_rebuild_batch_combos(self, *args, **kwargs):
        return _area_page._area_rebuild_batch_combos(self._ctx, *args, **kwargs)

    def _area_apply_batch(self, *args, **kwargs):
        return _area_page._area_apply_batch(self._ctx, *args, **kwargs)

    def _area_default_cfg(self, *args, **kwargs):
        return _area_page._area_default_cfg(self._ctx, *args, **kwargs)

    def _area_row_cfg(self, *args, **kwargs):
        return _area_page._area_row_cfg(self._ctx, *args, **kwargs)

    def _area_row_index(self, *args, **kwargs):
        return _area_page._area_row_index(self._ctx, *args, **kwargs)

    def _area_rebuild_table(self, *args, **kwargs):
        return _area_page._area_rebuild_table(self._ctx, *args, **kwargs)

    def _area_sync_row(self, *args, **kwargs):
        return _area_page._area_sync_row(self._ctx, *args, **kwargs)

    def _area_on_row_param(self, *args, **kwargs):
        return _area_page._area_on_row_param(self._ctx, *args, **kwargs)

    def _area_on_row_selected(self, *args, **kwargs):
        return _area_page._area_on_row_selected(self._ctx, *args, **kwargs)

    def _area_on_grid(self, *args, **kwargs):
        return _area_page._area_on_grid(self._ctx, *args, **kwargs)

    def _area_format(self, *args, **kwargs):
        return _area_page._area_format(self._ctx, *args, **kwargs)

    def _area_update_area_cell(self, *args, **kwargs):
        return _area_page._area_update_area_cell(self._ctx, *args, **kwargs)

    def _area_set_status(self, *args, **kwargs):
        return _area_page._area_set_status(self._ctx, *args, **kwargs)

    def _area_update_progress(self, *args, **kwargs):
        return _area_page._area_update_progress(self._ctx, *args, **kwargs)

    def _map_stage_token(self, *args, **kwargs):
        return _data_columns._map_stage_token(self._ctx, *args, **kwargs)

    def _area_signature(self, *args, **kwargs):
        return _area_page._area_signature(self._ctx, *args, **kwargs)

    def _area_start_worker(self, *args, **kwargs):
        return _area_page._area_start_worker(self._ctx, *args, **kwargs)

    def _area_enqueue(self, *args, **kwargs):
        return _area_page._area_enqueue(self._ctx, *args, **kwargs)

    def _area_run(self, *args, **kwargs):
        return _area_page._area_run(self._ctx, *args, **kwargs)

    def _area_compute(self, *args, **kwargs):
        return _area_page._area_compute(self._ctx, *args, **kwargs)

    def _area_histogram(self, *args, **kwargs):
        return _area_page._area_histogram(self._ctx, *args, **kwargs)

    def _area_histogram_loaded(self, *args, **kwargs):
        return _area_page._area_histogram_loaded(self._ctx, *args, **kwargs)

    def _area_bounds(self, *args, **kwargs):
        return _area_page._area_bounds(self._ctx, *args, **kwargs)

    def _area_poll(self, *args, **kwargs):
        return _area_page._area_poll(self._ctx, *args, **kwargs)

    def _area_sync_plot_settings(self, *args, **kwargs):
        return _area_page._area_sync_plot_settings(self._ctx, *args, **kwargs)

    def _area_on_plot_type(self, *args, **kwargs):
        return _area_page._area_on_plot_type(self._ctx, *args, **kwargs)

    def _area_on_plot_param(self, *args, **kwargs):
        return _area_page._area_on_plot_param(self._ctx, *args, **kwargs)

    def _area_resolved_plot_type(self, *args, **kwargs):
        return _area_page._area_resolved_plot_type(self._ctx, *args, **kwargs)

    def _area_set_image_levels(self, *args, **kwargs):
        return _area_page._area_set_image_levels(self._ctx, *args, **kwargs)

    def _area_rebuild_levels(self, *args, **kwargs):
        return _area_page._area_rebuild_levels(self._ctx, *args, **kwargs)

    def _area_render_plot(self, *args, **kwargs):
        return _area_page._area_render_plot(self._ctx, *args, **kwargs)

    def _area_draw_overlay(self, *args, **kwargs):
        return _area_page._area_draw_overlay(self._ctx, *args, **kwargs)

    def _area_copy(self, *args, **kwargs):
        return _area_page._area_copy(self._ctx, *args, **kwargs)

    def _area_mark_dirty(self, *args, **kwargs):
        return _area_page._area_mark_dirty(self._ctx, *args, **kwargs)

    def _area_update_save_btn(self, *args, **kwargs):
        return _area_page._area_update_save_btn(self._ctx, *args, **kwargs)

    def _area_save_now(self, *args, **kwargs):
        return _area_page._area_save_now(self._ctx, *args, **kwargs)

    def _area_close(self, *args, **kwargs):
        return _area_page._area_close(self._ctx, *args, **kwargs)

    def _dc_signature(self, *args, **kwargs):
        return _data_clean_page._dc_signature(self._ctx, *args, **kwargs)

    def _dc_params_for(self, *args, **kwargs):
        return _data_clean_page._dc_params_for(self._ctx, *args, **kwargs)

    def _dc_applied_columns(self, *args, **kwargs):
        return _data_outputs._dc_applied_columns(self._ctx, *args, **kwargs)

    def _dc_column_values(self, *args, **kwargs):
        return _data_clean_page._dc_column_values(self._ctx, *args, **kwargs)

    def _dc_raster(self, *args, **kwargs):
        return _data_outputs._dc_raster(self._ctx, *args, **kwargs)

    def _build_dc_page(self, *args, **kwargs):
        return _data_clean_page._build_dc_page(self._ctx, *args, **kwargs)

    def _dc_log_panel(self, *args, **kwargs):
        return _data_clean_page._dc_log_panel(self._ctx, *args, **kwargs)

    def _dc_log(self, *args, **kwargs):
        return _data_clean_page._dc_log(self._ctx, *args, **kwargs)

    def _dc_left_panel(self, *args, **kwargs):
        return _data_clean_page._dc_left_panel(self._ctx, *args, **kwargs)

    def _dc_center_panel(self, *args, **kwargs):
        return _data_clean_page._dc_center_panel(self._ctx, *args, **kwargs)

    def _dc_right_panel(self, *args, **kwargs):
        return _data_clean_page._dc_right_panel(self._ctx, *args, **kwargs)

    def _dc_common_columns(self, *args, **kwargs):
        return _data_clean_page._dc_common_columns(self._ctx, *args, **kwargs)

    def _dc_select_files(self, *args, **kwargs):
        return _data_clean_page._dc_select_files(self._ctx, *args, **kwargs)

    def _dc_select_vars(self, *args, **kwargs):
        return _data_clean_page._dc_select_vars(self._ctx, *args, **kwargs)

    def _dc_on_time(self, *args, **kwargs):
        return _data_clean_page._dc_on_time(self._ctx, *args, **kwargs)

    def _dc_on_mode(self, *args, **kwargs):
        return _data_clean_page._dc_on_mode(self._ctx, *args, **kwargs)

    def _dc_on_param(self, *args, **kwargs):
        return _data_clean_page._dc_on_param(self._ctx, *args, **kwargs)

    def _dc_on_name(self, *args, **kwargs):
        return _data_clean_page._dc_on_name(self._ctx, *args, **kwargs)

    def _dc_existing_columns(self, *args, **kwargs):
        return _data_clean_page._dc_existing_columns(self._ctx, *args, **kwargs)

    def _dc_name_conflict(self, *args, **kwargs):
        return _data_clean_page._dc_name_conflict(self._ctx, *args, **kwargs)

    def _dc_update_name(self, *args, **kwargs):
        return _data_clean_page._dc_update_name(self._ctx, *args, **kwargs)

    def _dc_update_calc_btn(self, *args, **kwargs):
        return _data_clean_page._dc_update_calc_btn(self._ctx, *args, **kwargs)

    def _dc_refresh(self, *args, **kwargs):
        return _data_clean_page._dc_refresh(self._ctx, *args, **kwargs)

    def _dc_validate(self, *args, **kwargs):
        return _data_clean_page._dc_validate(self._ctx, *args, **kwargs)

    def _dc_analyze_file(self, *args, **kwargs):
        return _data_clean_page._dc_analyze_file(self._ctx, *args, **kwargs)

    def _dc_write_file(self, *args, **kwargs):
        return _data_clean_page._dc_write_file(self._ctx, *args, **kwargs)

    def _dc_run(self, *args, **kwargs):
        return _data_clean_page._dc_run(self._ctx, *args, **kwargs)

    def _dc_calculate(self, *args, **kwargs):
        return _data_clean_page._dc_calculate(self._ctx, *args, **kwargs)

    def _dc_poll(self, *args, **kwargs):
        return _data_clean_page._dc_poll(self._ctx, *args, **kwargs)

    def _dc_mark_dirty(self, *args, **kwargs):
        return _data_clean_page._dc_mark_dirty(self._ctx, *args, **kwargs)

    def _dc_update_save_btn(self, *args, **kwargs):
        return _data_clean_page._dc_update_save_btn(self._ctx, *args, **kwargs)

    def _dc_save_now(self, *args, **kwargs):
        return _data_clean_page._dc_save_now(self._ctx, *args, **kwargs)

    def _dc_close(self, *args, **kwargs):
        return _data_clean_page._dc_close(self._ctx, *args, **kwargs)

    def _ba_config_sig(self, *args, **kwargs):
        return _batch_align_page._ba_config_sig(self._ctx, *args, **kwargs)

    def _ba_load_model(self, *args, **kwargs):
        return _batch_align_page._ba_load_model(self._ctx, *args, **kwargs)

    def _ba_stage(self, *args, **kwargs):
        return _batch_align_page._ba_stage(self._ctx, *args, **kwargs)

    def _build_ba_page(self, *args, **kwargs):
        return _batch_align_page._build_ba_page(self._ctx, *args, **kwargs)

    def _ba_log_panel(self, *args, **kwargs):
        return _batch_align_page._ba_log_panel(self._ctx, *args, **kwargs)

    def _ba_log(self, *args, **kwargs):
        return _batch_align_page._ba_log(self._ctx, *args, **kwargs)

    def _ba_left_panel(self, *args, **kwargs):
        return _batch_align_page._ba_left_panel(self._ctx, *args, **kwargs)

    def _ba_center_panel(self, *args, **kwargs):
        return _batch_align_page._ba_center_panel(self._ctx, *args, **kwargs)

    def _ba_right_panel(self, *args, **kwargs):
        return _batch_align_page._ba_right_panel(self._ctx, *args, **kwargs)

    def _ba_common_columns(self, *args, **kwargs):
        return _batch_align_page._ba_common_columns(self._ctx, *args, **kwargs)

    def _ba_select_files(self, *args, **kwargs):
        return _batch_align_page._ba_select_files(self._ctx, *args, **kwargs)

    def _ba_select_vars(self, *args, **kwargs):
        return _batch_align_page._ba_select_vars(self._ctx, *args, **kwargs)

    def _ba_on_method(self, *args, **kwargs):
        return _batch_align_page._ba_on_method(self._ctx, *args, **kwargs)

    def _ba_update_flowsom_ext(self, *args, **kwargs):
        return _batch_align_page._ba_update_flowsom_ext(self._ctx, *args, **kwargs)

    def _ba_on_param(self, *args, **kwargs):
        return _batch_align_page._ba_on_param(self._ctx, *args, **kwargs)

    def _ba_on_table_item(self, *args, **kwargs):
        return _batch_align_page._ba_on_table_item(self._ctx, *args, **kwargs)

    def _ba_paint_table(self, *args, **kwargs):
        return _batch_align_page._ba_paint_table(self._ctx, *args, **kwargs)

    def _ba_copy(self, *args, **kwargs):
        return _batch_align_page._ba_copy(self._ctx, *args, **kwargs)

    def _ba_paste(self, *args, **kwargs):
        return _batch_align_page._ba_paste(self._ctx, *args, **kwargs)

    def _ba_existing_columns(self, *args, **kwargs):
        return _batch_align_page._ba_existing_columns(self._ctx, *args, **kwargs)

    def _ba_model_current(self, *args, **kwargs):
        return _batch_align_page._ba_model_current(self._ctx, *args, **kwargs)

    def _ba_update_calc_btn(self, *args, **kwargs):
        return _batch_align_page._ba_update_calc_btn(self._ctx, *args, **kwargs)

    def _ba_refresh(self, *args, **kwargs):
        return _batch_align_page._ba_refresh(self._ctx, *args, **kwargs)

    def _ba_validate(self, *args, **kwargs):
        return _batch_align_page._ba_validate(self._ctx, *args, **kwargs)

    def _ba_calculate(self, *args, **kwargs):
        return _batch_align_page._ba_calculate(self._ctx, *args, **kwargs)

    def _ba_run(self, *args, **kwargs):
        return _batch_align_page._ba_run(self._ctx, *args, **kwargs)

    def _ba_read_matrix(self, *args, **kwargs):
        return _batch_align_page._ba_read_matrix(self._ctx, *args, **kwargs)

    def _ba_fit_cytonorm(self, *args, **kwargs):
        return _batch_align_page._ba_fit_cytonorm(self._ctx, *args, **kwargs)

    def _ba_fit_combat(self, *args, **kwargs):
        return _batch_align_page._ba_fit_combat(self._ctx, *args, **kwargs)

    def _ba_write_model(self, *args, **kwargs):
        return _batch_align_page._ba_write_model(self._ctx, *args, **kwargs)

    def _ba_poll(self, *args, **kwargs):
        return _batch_align_page._ba_poll(self._ctx, *args, **kwargs)

    def _ba_mark_dirty(self, *args, **kwargs):
        return _batch_align_page._ba_mark_dirty(self._ctx, *args, **kwargs)

    def _ba_update_save_btn(self, *args, **kwargs):
        return _batch_align_page._ba_update_save_btn(self._ctx, *args, **kwargs)

    def _ba_save_now(self, *args, **kwargs):
        return _batch_align_page._ba_save_now(self._ctx, *args, **kwargs)

    def _ba_close(self, *args, **kwargs):
        return _batch_align_page._ba_close(self._ctx, *args, **kwargs)

    def _ba_report(self, *args, **kwargs):
        return _batch_align_page._ba_report(self._ctx, *args, **kwargs)

    def _ba_report_run(self, *args, **kwargs):
        return _batch_align_page._ba_report_run(self._ctx, *args, **kwargs)

    def _ba_report_poll(self, *args, **kwargs):
        return _batch_align_page._ba_report_poll(self._ctx, *args, **kwargs)

    def _ba_density(self, *args, **kwargs):
        return _batch_align_page._ba_density(self._ctx, *args, **kwargs)

    def _ba_write_report(self, *args, **kwargs):
        return _batch_align_page._ba_write_report(self._ctx, *args, **kwargs)

    def _gating_store_path(self, *args, **kwargs):
        return _gating_page._gating_store_path(self._ctx, *args, **kwargs)

    def _gating_store_load(self, *args, **kwargs):
        return _gating_page._gating_store_load(self._ctx, *args, **kwargs)

    def _gating_store_save(self, *args, **kwargs):
        return _gating_page._gating_store_save(self._ctx, *args, **kwargs)

    def _gating_gates_for(self, *args, **kwargs):
        return _data_stores._gating_gates_for(self._ctx, *args, **kwargs)

    def _build_gating_page(self, *args, **kwargs):
        return _gating_page._build_gating_page(self._ctx, *args, **kwargs)

    def _gating_state_init(self, *args, **kwargs):
        return _widgets_gate_editor._gating_state_init(self._ctx, *args, **kwargs)

    def _gating_file_records(self, *args, **kwargs):
        return _gating_page._gating_file_records(self._ctx, *args, **kwargs)

    def _gating_refresh_files(self, *args, **kwargs):
        return _gating_page._gating_refresh_files(self._ctx, *args, **kwargs)

    def _gating_select_files(self, *args, **kwargs):
        return _gating_page._gating_select_files(self._ctx, *args, **kwargs)

    def _gating_build_content(self, *args, **kwargs):
        return _widgets_gate_editor._gating_build_content(self._ctx, *args, **kwargs)

    def _gating_make_plot(self, *args, **kwargs):
        return _gating_page._gating_make_plot(self._ctx, *args, **kwargs)

    def _gating_set_tool(self, *args, **kwargs):
        return _gating_page._gating_set_tool(self._ctx, *args, **kwargs)

    def _gating_update_tools(self, *args, **kwargs):
        return _gating_page._gating_update_tools(self._ctx, *args, **kwargs)

    def _gating_default_axes(self, *args, **kwargs):
        return _gating_page._gating_default_axes(self._ctx, *args, **kwargs)

    def _gating_set_plot_type(self, *args, **kwargs):
        return _gating_page._gating_set_plot_type(self._ctx, *args, **kwargs)

    def _gating_restore_gate_display(self, *args, **kwargs):
        return _gating_page._gating_restore_gate_display(self._ctx, *args, **kwargs)

    def _gating_file_key(self, *args, **kwargs):
        return _gating_page._gating_file_key(self._ctx, *args, **kwargs)

    def _gating_sync_gate(self, *args, **kwargs):
        return _gating_page._gating_sync_gate(self._ctx, *args, **kwargs)

    def _gating_apply_geometry(self, *args, **kwargs):
        return _gating_page._gating_apply_geometry(self._ctx, *args, **kwargs)

    def _gating_apply_group_geometry(self, *args, **kwargs):
        return _gating_page._gating_apply_group_geometry(self._ctx, *args, **kwargs)

    def _gating_on_roi_drag(self, *args, **kwargs):
        return _gating_page._gating_on_roi_drag(self._ctx, *args, **kwargs)

    def _gating_quadrant_sync(self, *args, **kwargs):
        return _gating_page._gating_quadrant_sync(self._ctx, *args, **kwargs)

    def _gating_split_sync(self, *args, **kwargs):
        return _gating_page._gating_split_sync(self._ctx, *args, **kwargs)

    def _gating_range_sync(self, *args, **kwargs):
        return _gating_page._gating_range_sync(self._ctx, *args, **kwargs)

    def _gating_skewed_sync(self, *args, **kwargs):
        return _gating_page._gating_skewed_sync(self._ctx, *args, **kwargs)

    def _gating_default_name(self, *args, **kwargs):
        return _gating_page._gating_default_name(self._ctx, *args, **kwargs)

    def _gating_col2gate_parquets(self, *args, **kwargs):
        return _gating_page._gating_col2gate_parquets(self._ctx, *args, **kwargs)

    def _gating_col2gate_columns(self, *args, **kwargs):
        return _gating_page._gating_col2gate_columns(self._ctx, *args, **kwargs)

    def _gating_col2gate_counts(self, *args, **kwargs):
        return _gating_page._gating_col2gate_counts(self._ctx, *args, **kwargs)

    def _gating_open_col2gate(self, *args, **kwargs):
        return _gating_page._gating_open_col2gate(self._ctx, *args, **kwargs)

    def _gating_col2gate_generate(self, *args, **kwargs):
        return _gating_page._gating_col2gate_generate(self._ctx, *args, **kwargs)

    def _gating_add_gate(self, *args, **kwargs):
        return _gating_page._gating_add_gate(self._ctx, *args, **kwargs)

    def _gating_log_gates(self, *args, **kwargs):
        return _gating_page._gating_log_gates(self._ctx, *args, **kwargs)

    def _gating_add_gate_impl(self, *args, **kwargs):
        return _gating_page._gating_add_gate_impl(self._ctx, *args, **kwargs)

    def _gating_style_handles(self, *args, **kwargs):
        return _gating_page._gating_style_handles(self._ctx, *args, **kwargs)

    def _gating_set_items_handles_visible(self, *args, **kwargs):
        return _gating_page._gating_set_items_handles_visible(self._ctx, *args, **kwargs)

    def _gating_show_handles(self, *args, **kwargs):
        return _gating_page._gating_show_handles(self._ctx, *args, **kwargs)

    def _gating_setup_line(self, *args, **kwargs):
        return _gating_page._gating_setup_line(self._ctx, *args, **kwargs)

    def _gating_select_gate_by_id(self, *args, **kwargs):
        return _gating_page._gating_select_gate_by_id(self._ctx, *args, **kwargs)

    def _gating_deselect(self, *args, **kwargs):
        return _gating_page._gating_deselect(self._ctx, *args, **kwargs)

    def _gating_open_gate_subset(self, *args, **kwargs):
        return _gating_page._gating_open_gate_subset(self._ctx, *args, **kwargs)

    def _gating_gate_clicked(self, *args, **kwargs):
        return _gating_page._gating_gate_clicked(self._ctx, *args, **kwargs)

    def _gating_descendant_ids(self, *args, **kwargs):
        return _gating_page._gating_descendant_ids(self._ctx, *args, **kwargs)

    def _gating_visible_ids(self, *args, **kwargs):
        return _gating_page._gating_visible_ids(self._ctx, *args, **kwargs)

    def _gating_update_roi_visibility(self, *args, **kwargs):
        return _gating_page._gating_update_roi_visibility(self._ctx, *args, **kwargs)

    def _gating_set_label_style(self, *args, **kwargs):
        return _gating_page._gating_set_label_style(self._ctx, *args, **kwargs)

    def _gating_apply_label_style(self, *args, **kwargs):
        return _gating_page._gating_apply_label_style(self._ctx, *args, **kwargs)

    def _gating_new_label(self, *args, **kwargs):
        return _gating_page._gating_new_label(self._ctx, *args, **kwargs)

    def _gating_on_label_dragged(self, *args, **kwargs):
        return _gating_page._gating_on_label_dragged(self._ctx, *args, **kwargs)

    def _gating_update_labels(self, *args, **kwargs):
        return _gating_page._gating_update_labels(self._ctx, *args, **kwargs)

    def _gating_load_rois(self, *args, **kwargs):
        return _gating_page._gating_load_rois(self._ctx, *args, **kwargs)

    def _gating_remove_rois(self, *args, **kwargs):
        return _gating_page._gating_remove_rois(self._ctx, *args, **kwargs)

    def _gating_reload_rois(self, *args, **kwargs):
        return _gating_page._gating_reload_rois(self._ctx, *args, **kwargs)

    def _gating_delete_selected(self, *args, **kwargs):
        return _gating_page._gating_delete_selected(self._ctx, *args, **kwargs)

    def _gating_delete_gate(self, *args, **kwargs):
        return _gating_page._gating_delete_gate(self._ctx, *args, **kwargs)

    def _gating_prune_crosshairs(self, *args, **kwargs):
        return _gating_page._gating_prune_crosshairs(self._ctx, *args, **kwargs)

    def _gating_gate_chain(self, *args, **kwargs):
        return _gating_page._gating_gate_chain(self._ctx, *args, **kwargs)

    def _gating_compute_mask(self, *args, **kwargs):
        return _gating_page._gating_compute_mask(self._ctx, *args, **kwargs)

    def _gating_context_mask(self, *args, **kwargs):
        return _gating_page._gating_context_mask(self._ctx, *args, **kwargs)

    def _gating_on_tree_double_clicked(self, *args, **kwargs):
        return _gating_page._gating_on_tree_double_clicked(self._ctx, *args, **kwargs)

    def _gating_tree_context_menu(self, *args, **kwargs):
        return _gating_page._gating_tree_context_menu(self._ctx, *args, **kwargs)

    def _gating_toggle_per_file(self, *args, **kwargs):
        return _gating_page._gating_toggle_per_file(self._ctx, *args, **kwargs)

    def _gating_unify_gate(self, *args, **kwargs):
        return _gating_page._gating_unify_gate(self._ctx, *args, **kwargs)

    def _gating_copy_nodes(self, *args, **kwargs):
        return _gating_page._gating_copy_nodes(self._ctx, *args, **kwargs)

    def _gating_collect_single(self, *args, **kwargs):
        return _gating_page._gating_collect_single(self._ctx, *args, **kwargs)

    def _gating_collect_branch(self, *args, **kwargs):
        return _gating_page._gating_collect_branch(self._ctx, *args, **kwargs)

    def _gating_copy_gate(self, *args, **kwargs):
        return _gating_page._gating_copy_gate(self._ctx, *args, **kwargs)

    def _gating_copy_branch(self, *args, **kwargs):
        return _gating_page._gating_copy_branch(self._ctx, *args, **kwargs)

    def _gating_paste_nodes(self, *args, **kwargs):
        return _gating_page._gating_paste_nodes(self._ctx, *args, **kwargs)

    def _gating_paste_gate(self, *args, **kwargs):
        return _gating_page._gating_paste_gate(self._ctx, *args, **kwargs)

    def _gating_paste_branch(self, *args, **kwargs):
        return _gating_page._gating_paste_branch(self._ctx, *args, **kwargs)

    def _gating_apply_selection(self, *args, **kwargs):
        return _gating_page._gating_apply_selection(self._ctx, *args, **kwargs)

    def _gating_tree_item_for_index(self, *args, **kwargs):
        return _gating_page._gating_tree_item_for_index(self._ctx, *args, **kwargs)

    def _gating_on_tree_selection(self, *args, **kwargs):
        return _gating_page._gating_on_tree_selection(self._ctx, *args, **kwargs)

    def _gating_cancel_pending_render(self, *args, **kwargs):
        return _gating_page._gating_cancel_pending_render(self._ctx, *args, **kwargs)

    def _gating_render_and_settings(self, *args, **kwargs):
        return _gating_page._gating_render_and_settings(self._ctx, *args, **kwargs)

    def _gating_image_refresh(self, *args, **kwargs):
        return _gating_page._gating_image_refresh(self._ctx, *args, **kwargs)

    def _gating_rename_gate(self, *args, **kwargs):
        return _gating_page._gating_rename_gate(self._ctx, *args, **kwargs)

    def _gating_add_not_gate(self, *args, **kwargs):
        return _gating_page._gating_add_not_gate(self._ctx, *args, **kwargs)

    def _gating_add_boolean_gate(self, *args, **kwargs):
        return _gating_page._gating_add_boolean_gate(self._ctx, *args, **kwargs)

    def _gating_request_stats(self, *args, **kwargs):
        return _gating_page._gating_request_stats(self._ctx, *args, **kwargs)

    def _gating_apply_stats(self, *args, **kwargs):
        return _gating_page._gating_apply_stats(self._ctx, *args, **kwargs)

    def _gating_on_stats_timer(self, *args, **kwargs):
        return _gating_page._gating_on_stats_timer(self._ctx, *args, **kwargs)

    def _gating_gate_display_name(self, *args, **kwargs):
        return _gating_page._gating_gate_display_name(self._ctx, *args, **kwargs)

    def _gating_refresh_tree_stats(self, *args, **kwargs):
        return _gating_page._gating_refresh_tree_stats(self._ctx, *args, **kwargs)

    def _gating_on_collapse(self, *args, **kwargs):
        return _gating_page._gating_on_collapse(self._ctx, *args, **kwargs)

    def _gating_on_expand(self, *args, **kwargs):
        return _gating_page._gating_on_expand(self._ctx, *args, **kwargs)

    def _gating_rebuild_tree(self, *args, **kwargs):
        return _gating_page._gating_rebuild_tree(self._ctx, *args, **kwargs)

    def _gating_render(self, *args, **kwargs):
        return _widgets_gate_editor._gating_render(self._ctx, *args, **kwargs)

    def _gating_param_combo(self, *args, **kwargs):
        return _widgets_gate_editor._gating_param_combo(self._ctx, *args, **kwargs)

    def _gating_rebuild_settings(self, *args, **kwargs):
        return _widgets_gate_editor._gating_rebuild_settings(self._ctx, *args, **kwargs)

    def _gating_set_image_levels(self, *args, **kwargs):
        return _gating_page._gating_set_image_levels(self._ctx, *args, **kwargs)

    def _gating_on_preview_change(self, *args, **kwargs):
        return _gating_page._gating_on_preview_change(self._ctx, *args, **kwargs)

    def _gating_pick_gate_color(self, *args, **kwargs):
        return _gating_page._gating_pick_gate_color(self._ctx, *args, **kwargs)

    def _gating_apply_gate_color(self, *args, **kwargs):
        return _gating_page._gating_apply_gate_color(self._ctx, *args, **kwargs)

    def _gating_switch_file(self, *args, **kwargs):
        return _gating_page._gating_switch_file(self._ctx, *args, **kwargs)

    def _gating_mark_dirty(self, *args, **kwargs):
        return _gating_page._gating_mark_dirty(self._ctx, *args, **kwargs)

    def _gating_save_now(self, *args, **kwargs):
        return _gating_page._gating_save_now(self._ctx, *args, **kwargs)

    def _gating_update_save_btn(self, *args, **kwargs):
        return _gating_page._gating_update_save_btn(self._ctx, *args, **kwargs)

    def _gating_cleanup(self, *args, **kwargs):
        return _widgets_gate_editor._gating_cleanup(self._ctx, *args, **kwargs)

    def _gating_close(self, *args, **kwargs):
        return _gating_page._gating_close(self._ctx, *args, **kwargs)
