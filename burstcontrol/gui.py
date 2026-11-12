#!/usr/bin/env python3
"""Native GTK4 / Libadwaita desktop GUI for Burst Control on Linux."""

from __future__ import annotations

import sys
import threading
from typing import Optional

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
except (ImportError, ValueError) as exc:
    raise ImportError(
        "GTK4 and Libadwaita Python bindings (PyGObject) are required for the GUI.\n"
        "Install on Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
        "Install on Fedora: sudo dnf install python3-gobject gtk4 libadwaita\n"
        "Install on Arch: sudo pacman -S python-gobject gtk4 libadwaita"
    ) from exc

from burstcontrol.protocol import (
    LIFT_OFF_MODES,
    POLLING_RATES,
    DeviceTelemetry,
    HIDConnection,
    MockHIDConnection,
    MouseError,
    MouseService,
    Settings,
    last_backup_path,
    xdg_data_dir,
)


class BurstControlWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, mock: bool = False):
        super().__init__(application=app, title="Burst Control", default_width=820, default_height=640)
        self.mock = mock
        if mock:
            self._mock_conn = MockHIDConnection()
            self.service = MouseService(connection_factory=lambda **kw: self._mock_conn)
        else:
            self.service = MouseService(connection_factory=HIDConnection)

        self.saved: Optional[Settings] = None
        self.draft: Optional[Settings] = None
        self.telemetry: Optional[DeviceTelemetry] = None
        self.busy = False
        self._syncing_ui = False

        self._build_ui()
        self.refresh()
        GLib.timeout_add(1500, self._poll_active_stage)

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title="Burst II Air", subtitle="Connecting…")
        self.title_widget = title_widget
        header.set_title_widget(title_widget)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh from mouse")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        header.pack_start(refresh_btn)

        menu = Gio.Menu()
        menu.append("Load previous settings", "win.restore-backup")
        menu.append("Open backup folder", "win.open-backups")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Backups and options")
        header.pack_end(menu_btn)

        restore_action = Gio.SimpleAction.new("restore-backup", None)
        restore_action.connect("activate", lambda *_: self.restore_backup())
        self.add_action(restore_action)

        open_action = Gio.SimpleAction.new("open-backups", None)
        open_action.connect("activate", lambda *_: self.open_backup_folder())
        self.add_action(open_action)

        self.battery_label = Gtk.Label(label="")
        self.battery_label.add_css_class("dim-label")
        self.battery_label.set_margin_end(8)
        header.pack_end(self.battery_label)

        toolbar_view.add_top_bar(header)

        self.toast_overlay = Adw.ToastOverlay()
        toolbar_view.set_content(self.toast_overlay)

        scroll = Gtk.ScrolledWindow()
        self.toast_overlay.set_child(scroll)

        clamp = Adw.Clamp(maximum_size=760, margin_top=20, margin_bottom=24, margin_start=20, margin_end=20)
        scroll.set_child(clamp)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(main_box)

        # Error / Notice Banner
        self.banner = Adw.Banner(revealed=False)
        main_box.append(self.banner)

        # DPI Stages Group
        dpi_group = Adw.PreferencesGroup(
            title="DPI Speeds",
            description="50–26,000 DPI in steps of 50. Active stage is highlighted.",
        )
        main_box.append(dpi_group)

        dpi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        dpi_box.set_margin_top(6)
        dpi_box.set_margin_bottom(6)
        dpi_group.add(dpi_box)

        self.dpi_spins: list[Gtk.SpinButton] = []
        self.dpi_badges: list[Gtk.Label] = []
        for idx in range(5):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("card")
            card.set_margin_top(4)
            card.set_margin_bottom(4)

            stage_lbl = Gtk.Label(label=f"Stage {idx + 1}")
            stage_lbl.add_css_class("caption-heading")
            stage_lbl.set_margin_top(10)
            card.append(stage_lbl)

            adj = Gtk.Adjustment(value=800, lower=50, upper=26000, step_increment=50, page_increment=400)
            spin = Gtk.SpinButton(adjustment=adj, climb_rate=50, digits=0)
            spin.set_numeric(True)
            spin.set_margin_start(8)
            spin.set_margin_end(8)
            spin.connect("value-changed", self._on_dpi_changed, idx)
            self.dpi_spins.append(spin)
            card.append(spin)

            badge = Gtk.Label(label=" ")
            badge.add_css_class("caption")
            badge.set_margin_bottom(8)
            self.dpi_badges.append(badge)
            card.append(badge)

            dpi_box.append(card)

        # Response Group
        resp_group = Adw.PreferencesGroup(title="Response")
        main_box.append(resp_group)

        self.debounce_row = Adw.SpinRow.new_with_range(0, 10, 1)
        self.debounce_row.set_title("Debounce")
        self.debounce_row.set_subtitle("Click debounce delay in milliseconds (0–10 ms)")
        self.debounce_row.connect("notify::value", self._on_control_changed)
        resp_group.add(self.debounce_row)

        polling_model = Gtk.StringList.new([f"{hz} Hz" for hz in POLLING_RATES])
        self.polling_row = Adw.ComboRow(title="Polling Rate", subtitle="USB report rate", model=polling_model)
        self.polling_row.connect("notify::selected", self._on_control_changed)
        resp_group.add(self.polling_row)

        # Tracking Group
        track_group = Adw.PreferencesGroup(title="Tracking")
        main_box.append(track_group)

        lod_model = Gtk.StringList.new(["Very low", "Low"])
        self.lod_row = Adw.ComboRow(title="Lift-Off Distance", model=lod_model)
        self.lod_row.connect("notify::selected", self._on_control_changed)
        track_group.add(self.lod_row)

        self.angle_row = Adw.SwitchRow(
            title="Angle Snapping",
            subtitle="Straightens small deviations when moving in a line",
        )
        self.angle_row.connect("notify::active", self._on_control_changed)
        track_group.add(self.angle_row)

        self.motion_row = Adw.SwitchRow(
            title="Motion Sync",
            subtitle="Synchronizes sensor sampling with USB reports",
        )
        self.motion_row.connect("notify::active", self._on_control_changed)
        track_group.add(self.motion_row)

        # Action Bar
        self.action_bar = Gtk.ActionBar()
        self.action_bar.set_revealed(False)

        unsaved_lbl = Gtk.Label(label="Unsaved changes")
        unsaved_lbl.add_css_class("dim-label")
        self.action_bar.pack_start(unsaved_lbl)

        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda _: self.apply_changes())
        self.action_bar.pack_end(self.apply_btn)

        revert_btn = Gtk.Button(label="Revert")
        revert_btn.connect("clicked", lambda _: self.revert_changes())
        self.action_bar.pack_end(revert_btn)

        toolbar_view.add_bottom_bar(self.action_bar)

    def _update_ui_from_state(self) -> None:
        if self.draft is None or self.saved is None:
            return
        self._syncing_ui = True
        try:
            for idx in range(5):
                dpi_val = self.draft.dpi(idx)
                self.dpi_spins[idx].set_value(dpi_val)
                is_active = self.saved.active_stage == idx
                is_edited = self.saved.dpi(idx) != dpi_val
                if is_edited:
                    self.dpi_badges[idx].set_text("Edited")
                elif is_active:
                    self.dpi_badges[idx].set_text("Active")
                else:
                    self.dpi_badges[idx].set_text(" ")

            self.debounce_row.set_value(self.draft.debounce_ms)
            self.polling_row.set_selected(self.draft.polling_index)
            self.lod_row.set_selected(min(self.draft.lift_off_mode, 1))
            self.angle_row.set_active(self.draft.angle_snapping_enabled)
            self.motion_row.set_active(self.draft.motion_sync_enabled)

            if self.telemetry:
                if self.telemetry.battery_percent is not None:
                    bolt = " ⚡" if self.telemetry.charging else ""
                    self.battery_label.set_text(f"{self.telemetry.battery_percent}%{bolt} · {self.telemetry.transport}")
                else:
                    self.battery_label.set_text(self.telemetry.transport)

            dirty = self.draft != self.saved
            self.action_bar.set_revealed(dirty)
        finally:
            self._syncing_ui = False

    def _on_dpi_changed(self, spin: Gtk.SpinButton, idx: int) -> None:
        if self._syncing_ui or self.draft is None:
            return
        val = int(round(spin.get_value() / 50.0) * 50)
        val = max(50, min(26000, val))
        self.draft.set_dpi(idx, val)
        self._update_ui_from_state()

    def _on_control_changed(self, *_args) -> None:
        if self._syncing_ui or self.draft is None:
            return
        self.draft.debounce_ms = int(self.debounce_row.get_value())
        self.draft.polling_index = int(self.polling_row.get_selected())
        self.draft.lift_off_mode = int(self.lod_row.get_selected())
        self.draft.angle_snapping_enabled = self.angle_row.get_active()
        self.draft.motion_sync_enabled = self.motion_row.get_active()
        self._update_ui_from_state()

    def revert_changes(self) -> None:
        if self.saved is not None:
            self.draft = self.saved.clone()
            self._update_ui_from_state()

    def open_backup_folder(self) -> None:
        folder = xdg_data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder))).launch(self, None, None, None)

    def restore_backup(self) -> None:
        if self.saved is None:
            return
        try:
            backup = self.service.load_backup()
            prev = backup["settings_obj"]
            if prev.profile != self.saved.profile:
                raise MouseError("Backup belongs to a different profile.")
            restored = self.saved.clone()
            restored.debounce_ms = prev.debounce_ms
            restored.polling_index = prev.polling_index
            restored.motion_sync_enabled = prev.motion_sync_enabled
            restored.angle_snapping = prev.angle_snapping
            restored.lift_off_mode = prev.lift_off_mode
            for i in range(5):
                restored.set_dpi(i, prev.dpi(i))
            self.draft = restored
            self._update_ui_from_state()
            self.toast_overlay.add_toast(Adw.Toast(title="Previous settings loaded — review and click Apply"))
        except Exception as exc:
            self.banner.set_title(str(exc))
            self.banner.set_revealed(True)

    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.title_widget.set_subtitle("Connecting…")

        def worker() -> None:
            try:
                settings, telem = self.service.read_live()
                GLib.idle_add(self._on_refresh_ok, settings, telem)
            except Exception as exc:
                GLib.idle_add(self._on_refresh_err, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_ok(self, settings: Settings, telem: DeviceTelemetry) -> None:
        self.busy = False
        self.telemetry = telem
        if self.saved is not None and self.draft is not None and self.draft != self.saved:
            self.draft = settings.merging_edits(self.draft, self.saved)
        else:
            self.draft = settings.clone()
        self.saved = settings
        self.title_widget.set_subtitle(f"Connected · Profile {settings.profile_index + 1}")
        self.banner.set_revealed(False)
        self._update_ui_from_state()

    def _on_refresh_err(self, message: str) -> None:
        self.busy = False
        self.title_widget.set_subtitle("Not connected")
        self.banner.set_title(message)
        self.banner.set_revealed(True)

    def apply_changes(self) -> None:
        if self.busy or self.draft is None or self.saved is None:
            return
        self.busy = True
        self.apply_btn.set_sensitive(False)
        desired = self.draft.clone()
        expected = self.saved.clone()

        def worker() -> None:
            try:
                applied = self.service.apply(desired, expected)
                GLib.idle_add(self._on_apply_ok, applied)
            except Exception as exc:
                GLib.idle_add(self._on_apply_err, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_apply_ok(self, applied: Settings) -> None:
        self.busy = False
        self.apply_btn.set_sensitive(True)
        self.saved = applied
        self.draft = applied.clone()
        self.banner.set_revealed(False)
        self._update_ui_from_state()
        self.toast_overlay.add_toast(Adw.Toast(title="Settings saved to mouse"))

    def _on_apply_err(self, message: str) -> None:
        self.busy = False
        self.apply_btn.set_sensitive(True)
        self.banner.set_title(message)
        self.banner.set_revealed(True)

    def _poll_active_stage(self) -> bool:
        if self.busy or self.saved is None or self.draft is None or not self.is_active():
            return True

        def worker() -> None:
            try:
                profile, stage = self.service.active_stage()
                GLib.idle_add(self._on_stage_ok, profile, stage)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _on_stage_ok(self, profile: list[int], stage: int) -> None:
        if self.saved is None or self.draft is None:
            return
        if self.saved.profile != profile:
            self.refresh()
            return
        if self.saved.sensor[1] != stage:
            self.saved.sensor[1] = stage
            self.draft.sensor[1] = stage
            self._update_ui_from_state()


class BurstControlApp(Adw.Application):
    def __init__(self, mock: bool = False):
        super().__init__(application_id="io.github.burstcontrol.Linux")
        self.mock = mock

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = BurstControlWindow(self, mock=self.mock)
        win.present()


def run_gui(mock: bool = False) -> int:
    app = BurstControlApp(mock=mock)
    return app.run(None)


if __name__ == "__main__":
    raise SystemExit(run_gui(mock="--mock" in sys.argv))
