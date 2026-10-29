#!/usr/bin/env python3
"""
ArgentOS Extrepo Manager v1.2.1
Gestor visual de repositorios externos para ArgentOS Platinum Edition
GTK4 + libadwaita | GPLv3

Integración con OpenDash:
    from argentos_extrepo_manager import build_tab_widget
    tab = build_tab_widget(toast_overlay=parent_toast)

Cambios v1.2.1 respecto a v1.2.0:
  - Parser de `extrepo search` RESTAURADO al de v1.1.0 (compatible con
    formato "Found <name>:" + YAML separado por "---").
  - Se ignora la línea separadora "---" al construir el dict.
  - Se mantienen todos los parches ortogonales (handlers, timers, _alive,
    diálogo único, timeouts, idempotencia, spinner, filas instaladas).
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio
import subprocess
import threading
import signal
import sys
import os
import glob
import re

APP_ID  = "org.openargentos.ExtrepoManager"
VERSION = "1.2.1"


# ══════════════════════════════════════════════════════════════════════════════
#  Capa de datos: extrepo
# ══════════════════════════════════════════════════════════════════════════════

def get_enabled_repos() -> set:
    """Lee /etc/apt/sources.list.d/extrepo_*.sources y devuelve nombres activos."""
    enabled = set()
    for path in glob.glob("/etc/apt/sources.list.d/extrepo_*.sources"):
        basename = os.path.basename(path)
        name = basename.removeprefix("extrepo_").removesuffix(".sources")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            is_disabled = False
            for line in content.splitlines():
                key, sep, val = line.partition(":")
                if sep and key.strip().lower() == "enabled":
                    if val.strip().lower() in ("no", "false", "0"):
                        is_disabled = True
                        break
            if not is_disabled:
                enabled.add(name)
        except (PermissionError, OSError):
            enabled.add(name)
    return enabled


def run_extrepo_search(keyword: str = "") -> tuple[list, str | None]:
    cmd = ["extrepo", "search"]
    if keyword.strip():
        cmd.append(keyword.strip())
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        repos = _parse_search_output(result.stdout)
        if not repos and result.stderr.strip():
            return [], result.stderr.strip()
        return repos, None
    except FileNotFoundError:
        return [], "extrepo no está instalado.\nInstálalo con:  sudo apt install extrepo"
    except subprocess.TimeoutExpired:
        return [], "Tiempo de espera agotado (45 s)"
    except Exception as exc:
        return [], str(exc)


def _parse_search_output(output: str) -> list:
    """
    Parser compatible con el formato real de extrepo search:
        Found <nombre>:
        ---
        description: ...
        source:
          URIs: ...
        <línea en blanco>
        Found <nombre>:
        ...
    """
    repos = []
    current_name = None
    current_lines: list[str] = []
    for line in output.splitlines():
        if line.startswith("Found ") and line.rstrip().endswith(":"):
            if current_name is not None:
                repos.append(_build_repo_dict(current_name, current_lines))
            current_name = line[6:].rstrip().rstrip(":")
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        repos.append(_build_repo_dict(current_name, current_lines))
    return repos


def _build_repo_dict(name: str, lines: list[str]) -> dict:
    data = {"name": name, "description": "", "url": "", "suite": "",
            "components": "", "arch": ""}
    for line in lines:
        s = line.strip()
        if not s or s == "---":
            continue
        key, _, val = s.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "uris":
            data["url"] = val
        elif key == "description":
            data["description"] = val
        elif key in ("suites", "suite"):
            data["suite"] = val
        elif key == "components":
            data["components"] = val
        elif key == "architectures":
            data["arch"] = val
    if not data["description"]:
        data["description"] = data["url"]
    return data


# ══════════════════════════════════════════════════════════════════════════════
#  Capa de datos: APT / instalación
# ══════════════════════════════════════════════════════════════════════════════

def run_privileged(args: list, timeout: int = 300) -> tuple[bool, str]:
    """Ejecuta con pkexec. start_new_session=True permite matar el grupo
    completo (pkexec + apt-get nieto) en timeout, liberando el lock de dpkg."""
    try:
        proc = subprocess.Popen(
            ["pkexec"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False, "pkexec no está disponible"
    except OSError as exc:
        return False, str(exc)

    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        return False, f"Tiempo de espera agotado ({timeout} s)"

    rc = proc.returncode
    if rc == 0:
        return True, (out or "").strip() or "Operación completada"
    if rc == 126:
        return False, "Autenticación cancelada"
    if rc == 127:
        return False, "No se pudo autenticar con polkit"

    err = (err or "").strip()
    low = err.lower()
    if "already" in low and ("enabled" in low or "disabled" in low):
        return True, "Sin cambios (ya estaba en ese estado)"
    return False, err or f"Error (código {rc})"


def apt_update() -> tuple[bool, str]:
    return run_privileged(["apt-get", "update", "-q"])


def _apt_search(pattern: str, names_only: bool) -> tuple[list[dict], int, str]:
    cmd = ["apt-cache", "search"]
    if names_only:
        cmd.append("--names-only")
    cmd.append(pattern)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return _parse_apt_search(r.stdout), r.returncode, r.stderr.strip()


def search_apt_packages(query: str) -> tuple[list[dict], str | None]:
    if not query.strip():
        return [], None
    q = query.strip().replace("_", "-")
    try:
        pkgs, rc, err = _apt_search(q, names_only=True)
        if rc != 0:
            pkgs, rc, err = _apt_search(re.escape(q), names_only=True)
        if rc == 0 and not pkgs:
            pkgs, rc, err = _apt_search(q, names_only=False)
            if rc != 0:
                pkgs, rc, err = _apt_search(re.escape(q), names_only=False)
        if rc != 0:
            return [], err or f"apt-cache falló (código {rc})"
        return pkgs, None
    except FileNotFoundError:
        return [], "apt-cache no disponible"
    except subprocess.TimeoutExpired:
        return [], "Tiempo de espera agotado"
    except Exception as exc:
        return [], str(exc)


def _parse_apt_search(output: str) -> list[dict]:
    pkgs = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, desc = line.partition(" - ")
        pkgs.append({"name": name.strip(), "description": desc.strip()})
    return pkgs


def get_installed_packages() -> set:
    try:
        r = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\n"],
            capture_output=True, text=True, timeout=10,
        )
        return set(r.stdout.splitlines())
    except Exception:
        return set()


def install_apt_packages(packages: list[str],
                         no_recommends: bool = False) -> tuple[bool, str]:
    cmd = ["apt-get", "install", "-y", "-q",
           "-o", "Dpkg::Options::=--force-confdef",
           "-o", "Dpkg::Options::=--force-confold"]
    if no_recommends:
        cmd.append("--no-install-recommends")
    cmd.extend(packages)
    return run_privileged(cmd, timeout=1800)


# ══════════════════════════════════════════════════════════════════════════════
#  PackageRow
# ══════════════════════════════════════════════════════════════════════════════

class PackageRow(Adw.ActionRow):
    def __init__(self, pkg: dict, installed: bool):
        super().__init__()
        self.pkg_name   = pkg["name"]
        self._installed = installed

        self.set_title(pkg["name"])
        subtitle = pkg["description"]
        if len(subtitle) > 85:
            subtitle = subtitle[:82] + "…"
        self.set_subtitle(subtitle or "Sin descripción")

        self._check = Gtk.CheckButton()
        self._check.set_valign(Gtk.Align.CENTER)
        self._check.set_active(False)
        self._check.set_sensitive(not installed)
        self.add_prefix(self._check)
        self.set_activatable_widget(self._check)

        self._badge = Gtk.Label(label="Instalado")
        self._badge.add_css_class("caption")
        self._badge.add_css_class("success")
        self._badge.set_valign(Gtk.Align.CENTER)
        self._badge.set_visible(installed)
        self.add_suffix(self._badge)

    def is_selected(self) -> bool:
        return self._check.get_active() and not self._installed

    def is_installed(self) -> bool:
        return self._installed

    def set_check_active(self, active: bool):
        self._check.set_active(active)

    def connect_toggled(self, cb):
        self._check.connect("toggled", cb)

    def mark_installed(self):
        self._installed = True
        self._check.set_active(False)
        self._check.set_sensitive(False)
        self._badge.set_visible(True)


# ══════════════════════════════════════════════════════════════════════════════
#  InstallDialog
# ══════════════════════════════════════════════════════════════════════════════

class InstallDialog(Adw.Window):
    def __init__(self, parent, repo_name: str, parent_toast: Adw.ToastOverlay | None):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(640, 580)
        self.set_size_request(400, 440)
        self.set_title(f"Instalar software — {repo_name}")

        self._repo_name     = repo_name
        self._parent_toast  = parent_toast
        self._pkg_rows: list[PackageRow] = []
        self._search_timer  = None
        self._bulk_updating = False
        self._search_seq    = 0
        self._alive         = True
        self._pkg_entry_hid = None

        self._build_ui()
        self._start_update()
        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, *_):
        self._alive = False
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = None
        return False

    def _build_ui(self):
        self._local_toast = Adw.ToastOverlay()
        self.set_content(self._local_toast)

        toolbar_view = Adw.ToolbarView()
        self._local_toast.set_child(toolbar_view)

        header = Adw.HeaderBar()
        tw = Adw.WindowTitle()
        tw.set_title("Instalar software")
        tw.set_subtitle(self._repo_name)
        header.set_title_widget(tw)
        toolbar_view.add_top_bar(header)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(200)
        toolbar_view.set_content(self._stack)

        self._build_page_updating()
        self._build_page_guide()
        self._build_page_search()
        self._build_page_installing()
        self._build_page_success()
        self._build_page_error()

    def _build_page_updating(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(48)
        box.set_margin_bottom(48)

        self._update_spinner = Gtk.Spinner()
        self._update_spinner.set_size_request(48, 48)
        box.append(self._update_spinner)

        self._update_lbl = Gtk.Label(label="¿Actualizar lista de paquetes?")
        self._update_lbl.add_css_class("title-3")
        box.append(self._update_lbl)

        self._update_sub = Gtk.Label(
            label="Se recomienda antes de instalar.\nRequiere autenticación (polkit)."
        )
        self._update_sub.add_css_class("dim-label")
        self._update_sub.set_justify(Gtk.Justification.CENTER)
        box.append(self._update_sub)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)

        skip_btn = Gtk.Button(label="Omitir")
        skip_btn.add_css_class("pill")
        skip_btn.connect("clicked", self._on_skip_update)
        btn_box.append(skip_btn)

        update_btn = Gtk.Button(label="Actualizar lista")
        update_btn.add_css_class("pill")
        update_btn.add_css_class("suggested-action")
        update_btn.connect("clicked", self._on_confirm_update)
        btn_box.append(update_btn)

        box.append(btn_box)
        self._stack.add_named(box, "updating")

    def _build_page_guide(self):
        page = Adw.StatusPage()
        page.set_icon_name("system-search-symbolic")
        page.set_title("Buscar paquetes")
        page.set_description(
            "Escribe un nombre de paquete o software para buscarlo\n"
            f"en el índice APT (repositorio «{self._repo_name}»)."
        )
        btn = Gtk.Button(label="Ir a la búsqueda")
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _: self._go_to_search())
        page.set_child(btn)
        self._stack.add_named(page, "guide")

    def _build_page_search(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_margin_start(12)
        top.set_margin_end(12)
        top.set_margin_top(10)
        top.set_margin_bottom(8)

        self._pkg_entry = Gtk.SearchEntry()
        self._pkg_entry.set_hexpand(True)
        self._pkg_entry.set_placeholder_text("Buscar paquete…")
        self._pkg_entry_hid = self._pkg_entry.connect(
            "search-changed", self._on_pkg_search_changed
        )
        top.append(self._pkg_entry)

        self._search_spin = Gtk.Spinner()
        self._search_spin.set_visible(False)
        top.append(self._search_spin)

        root.append(top)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        action = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action.set_margin_start(12)
        action.set_margin_end(12)
        action.set_margin_top(6)
        action.set_margin_bottom(6)

        self._select_all_check = Gtk.CheckButton()
        self._select_all_check.set_tooltip_text("Seleccionar / deseleccionar todo")
        self._select_all_hid = self._select_all_check.connect(
            "toggled", self._on_select_all_toggled
        )
        action.append(self._select_all_check)

        self._sel_label = Gtk.Label(label="Ningún paquete seleccionado")
        self._sel_label.add_css_class("dim-label")
        self._sel_label.add_css_class("caption")
        self._sel_label.set_hexpand(True)
        self._sel_label.set_halign(Gtk.Align.START)
        action.append(self._sel_label)

        self._install_btn = Gtk.Button(label="Instalar seleccionados")
        self._install_btn.add_css_class("suggested-action")
        self._install_btn.add_css_class("pill")
        self._install_btn.set_sensitive(False)
        self._install_btn.connect("clicked", self._on_install_clicked)
        action.append(self._install_btn)

        root.append(action)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._list_stack = Gtk.Stack()
        self._list_stack.set_vexpand(True)
        self._list_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._list_stack.set_transition_duration(150)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._pkg_list = Gtk.ListBox()
        self._pkg_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._pkg_list.add_css_class("boxed-list")
        self._pkg_list.set_margin_start(12)
        self._pkg_list.set_margin_end(12)
        self._pkg_list.set_margin_top(8)
        self._pkg_list.set_margin_bottom(12)

        scroll.set_child(self._pkg_list)
        self._list_stack.add_named(scroll, "list")

        self._empty_page = Adw.StatusPage()
        self._empty_page.set_icon_name("package-x-generic-symbolic")
        self._empty_page.set_title("No se encontraron paquetes")
        self._empty_page.set_description(
            "El índice APT puede no tener aún los paquetes\n"
            "de este repositorio. Actualiza la lista e intenta de nuevo,\n"
            "o busca con otro término."
        )
        empty_btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty_btn_box.set_halign(Gtk.Align.CENTER)

        self._empty_update_btn = Gtk.Button(label="Actualizar lista de paquetes")
        self._empty_update_btn.add_css_class("pill")
        self._empty_update_btn.add_css_class("suggested-action")
        self._empty_update_btn.connect("clicked", self._on_confirm_update)
        empty_btn_box.append(self._empty_update_btn)

        self._empty_page.set_child(empty_btn_box)
        self._list_stack.add_named(self._empty_page, "empty")

        root.append(self._list_stack)
        self._stack.add_named(root, "search")

    def _build_page_installing(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(48)
        box.set_margin_bottom(48)

        self._installing_spin = Gtk.Spinner()
        self._installing_spin.set_size_request(48, 48)
        box.append(self._installing_spin)

        self._installing_lbl = Gtk.Label()
        self._installing_lbl.add_css_class("title-3")
        box.append(self._installing_lbl)

        sub = Gtk.Label(label="Esto puede tardar unos minutos")
        sub.add_css_class("dim-label")
        box.append(sub)

        self._stack.add_named(box, "installing")

    def _build_page_success(self):
        page = Adw.StatusPage()
        page.set_icon_name("emblem-ok-symbolic")
        page.set_title("¡Instalación completada!")
        page.set_description("Los paquetes se instalaron correctamente")

        btn = Gtk.Button(label="Cerrar")
        btn.set_halign(Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _: self.close())
        page.set_child(btn)

        self._stack.add_named(page, "success")

    def _build_page_error(self):
        self._error_page = Adw.StatusPage()
        self._error_page.set_icon_name("dialog-error-symbolic")
        self._error_page.set_title("Ocurrió un error")

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)

        retry_btn = Gtk.Button(label="Reintentar")
        retry_btn.add_css_class("pill")
        retry_btn.add_css_class("suggested-action")
        retry_btn.connect("clicked", lambda _: self._start_update())
        btn_box.append(retry_btn)

        back_btn = Gtk.Button(label="Volver a la búsqueda")
        back_btn.add_css_class("pill")
        back_btn.connect("clicked", lambda _: self._go_to_search())
        btn_box.append(back_btn)

        self._error_page.set_child(btn_box)
        self._stack.add_named(self._error_page, "error")

    def _start_update(self):
        self._update_spinner.stop()
        self._update_lbl.set_label("¿Actualizar lista de paquetes?")
        self._update_sub.set_label(
            "Se recomienda antes de instalar.\nRequiere autenticación (polkit)."
        )
        self._stack.set_visible_child_name("updating")

    def _on_skip_update(self, _btn):
        self._go_to_search()

    def _on_confirm_update(self, _btn):
        self._update_spinner.start()
        self._update_lbl.set_label("Actualizando lista de paquetes…")
        self._update_sub.set_label("Se requiere autenticación (polkit)")
        self._empty_update_btn.set_sensitive(False)
        self._stack.set_visible_child_name("updating")

        def worker():
            ok, msg = apt_update()
            if not self._alive:
                return
            GLib.idle_add(self._on_update_done, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _go_to_search(self):
        if not self._alive:
            return
        self._stack.set_visible_child_name("search")
        if not self._pkg_entry.get_text().strip():
            self._clear_pkg_list()
        self._pkg_entry.grab_focus()

    def _on_update_done(self, ok: bool, msg: str):
        if not self._alive:
            return
        self._update_spinner.stop()
        self._empty_update_btn.set_sensitive(True)
        if ok:
            self._go_to_search()
            self._show_local_toast("Índice APT actualizado")
        else:
            self._error_page.set_description(msg)
            self._stack.set_visible_child_name("error")

    def _on_pkg_search_changed(self, entry):
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = None
        q = entry.get_text().strip()
        if q:
            self._search_timer = GLib.timeout_add(400, self._fire_search, q)
        else:
            self._clear_pkg_list()

    def _fire_search(self, query: str):
        self._search_timer = None
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._do_search(query)
        return GLib.SOURCE_REMOVE

    def _do_search(self, query: str):
        self._search_spin.set_visible(True)
        self._search_spin.start()
        self._install_btn.set_sensitive(False)

        self._search_seq += 1
        my_seq = self._search_seq

        def worker():
            pkgs, err = search_apt_packages(query)
            installed = get_installed_packages()
            if not self._alive:
                return
            GLib.idle_add(self._on_search_done, pkgs, installed, err, my_seq)

        threading.Thread(target=worker, daemon=True).start()

    def _on_search_done(self, pkgs: list, installed: set, err: str | None, seq: int):
        if not self._alive or seq != self._search_seq:
            return

        self._search_spin.stop()
        self._search_spin.set_visible(False)

        if err:
            self._show_local_toast(f"Error: {err}")
            return

        self._clear_pkg_list()

        for pkg in pkgs:
            is_inst = pkg["name"] in installed
            row = PackageRow(pkg, is_inst)
            row.connect_toggled(lambda *_: self._update_selection())
            self._pkg_rows.append(row)
            self._pkg_list.append(row)

        self._select_all_check.handler_block(self._select_all_hid)
        self._select_all_check.set_active(False)
        self._select_all_check.handler_unblock(self._select_all_hid)

        if pkgs:
            self._list_stack.set_visible_child_name("list")
            self._select_all_check.set_visible(True)
        else:
            q = self._pkg_entry.get_text().strip()
            self._empty_page.set_description(
                f"No se encontraron paquetes para «{q}».\n\n"
                "El índice APT puede no incluir aún los paquetes\n"
                "de este repositorio. Actualiza la lista e intenta de nuevo,\n"
                "o escribe otro término de búsqueda."
            )
            self._list_stack.set_visible_child_name("empty")
            self._select_all_check.set_visible(False)

        self._update_selection()

    def _clear_pkg_list(self):
        self._pkg_rows = []
        child = self._pkg_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._pkg_list.remove(child)
            child = nxt
        self._select_all_check.set_visible(True)
        self._list_stack.set_visible_child_name("list")
        self._update_selection()

    def _on_select_all_toggled(self, check):
        active = check.get_active()
        self._bulk_updating = True
        for row in self._pkg_rows:
            if not row.is_installed():
                row.set_check_active(active)
        self._bulk_updating = False
        self._update_selection()

    def _update_selection(self):
        if self._bulk_updating:
            return
        n = sum(1 for r in self._pkg_rows if r.is_selected())
        if n == 0:
            self._sel_label.set_label("Ningún paquete seleccionado")
        elif n == 1:
            self._sel_label.set_label("1 paquete seleccionado")
        else:
            self._sel_label.set_label(f"{n} paquetes seleccionados")
        self._install_btn.set_sensitive(n > 0)

    def _on_install_clicked(self, _btn):
        selected = [r.pkg_name for r in self._pkg_rows if r.is_selected()]
        if not selected:
            return

        label = selected[0] if len(selected) == 1 else f"{len(selected)} paquetes"
        self._installing_lbl.set_label(f"Instalando {label}…")
        self._installing_spin.start()
        self._stack.set_visible_child_name("installing")

        def worker():
            ok, msg = install_apt_packages(selected)
            if not self._alive:
                return
            GLib.idle_add(self._on_install_done, ok, msg, selected)

        threading.Thread(target=worker, daemon=True).start()

    def _on_install_done(self, ok: bool, msg: str, installed_pkgs: list):
        if not self._alive:
            return
        self._installing_spin.stop()

        if ok:
            sel_set = set(installed_pkgs)
            for row in self._pkg_rows:
                if row.pkg_name in sel_set:
                    row.mark_installed()
            self._update_selection()

            self._stack.set_visible_child_name("success")
            if self._parent_toast:
                n = len(installed_pkgs)
                label = installed_pkgs[0] if n == 1 else f"{n} paquetes"
                t = Adw.Toast.new(f"{label} instalado correctamente")
                t.set_timeout(5)
                self._parent_toast.add_toast(t)
        else:
            self._error_page.set_description(msg)
            self._stack.set_visible_child_name("error")

    def _show_local_toast(self, msg: str, timeout: int = 4):
        if not self._alive:
            return
        t = Adw.Toast.new(msg)
        t.set_timeout(timeout)
        self._local_toast.add_toast(t)


# ══════════════════════════════════════════════════════════════════════════════
#  RepoRow
# ══════════════════════════════════════════════════════════════════════════════

class RepoRow(Adw.ActionRow):
    def __init__(self, repo_data: dict, enabled: bool, on_toggle_cb, on_install_cb):
        super().__init__()
        self.repo_data   = repo_data
        self._enabled    = enabled
        self._on_toggle  = on_toggle_cb
        self._on_install = on_install_cb

        self.set_title(repo_data["name"])
        subtitle = repo_data["description"]
        if len(subtitle) > 90:
            subtitle = subtitle[:87] + "…"
        self.set_subtitle(subtitle or "Sin descripción")
        self.set_activatable(False)

        # Badge de estado
        self._badge = Gtk.Label()
        self._badge.set_valign(Gtk.Align.CENTER)
        self._badge.add_css_class("caption")
        self.add_suffix(self._badge)

        # Botón "Instalar software" (solo visible si está activo)
        self._install_btn = Gtk.Button()
        self._install_btn.set_icon_name("system-software-install-symbolic")
        self._install_btn.set_tooltip_text("Instalar software de este repositorio")
        self._install_btn.set_valign(Gtk.Align.CENTER)
        self._install_btn.add_css_class("flat")
        self._install_btn.connect("clicked", lambda _: self._on_install(self))
        self.add_suffix(self._install_btn)

        # Botón habilitar / deshabilitar
        self._btn = Gtk.Button()
        self._btn.set_valign(Gtk.Align.CENTER)
        self._btn.connect("clicked", lambda _: self._on_toggle(self))
        self.add_suffix(self._btn)

        # Spinner operación en curso
        self._spinner = Gtk.Spinner()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_visible(False)
        self.add_suffix(self._spinner)

        self._refresh_ui()

    def _refresh_ui(self):
        self._install_btn.set_visible(self._enabled)
        if self._enabled:
            self._badge.set_label("● Activo")
            self._badge.remove_css_class("dim-label")
            self._badge.add_css_class("success")
            self._btn.set_label("Deshabilitar")
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("destructive-action")
        else:
            self._badge.set_label("○ Inactivo")
            self._badge.remove_css_class("success")
            self._badge.add_css_class("dim-label")
            self._btn.set_label("Habilitar")
            self._btn.remove_css_class("destructive-action")
            self._btn.add_css_class("suggested-action")

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._refresh_ui()

    def set_busy(self, busy: bool):
        self._btn.set_sensitive(not busy)
        self._install_btn.set_sensitive(not busy)
        self._spinner.set_visible(busy)
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()

    def is_enabled(self) -> bool:
        return self._enabled


# ══════════════════════════════════════════════════════════════════════════════
#  ExtrepoWidget
# ══════════════════════════════════════════════════════════════════════════════

class ExtrepoWidget(Gtk.Box):
    """
    Widget autocontenido embebible en OpenDash o como app independiente.

    Uso en OpenDash:
        from argentos_extrepo_manager import ExtrepoWidget
        tab = ExtrepoWidget(toast_overlay=self._toast)
    """

    def __init__(self, toast_overlay: Adw.ToastOverlay | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._toast_overlay = toast_overlay
        self._all_repos: list  = []
        self._enabled_set: set = set()
        self._rows: dict       = {}
        self._filter_mode: str = "all"
        self._search_text: str = ""
        self._alive: bool      = True
        self._active_dialog    = None

        self._build_toolbar()
        self._build_list_area()
        self._load_repos()

        self.connect("unrealize", self._on_unrealize)

    def _on_unrealize(self, *_):
        self._alive = False

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(12)
        bar.set_margin_end(12)
        bar.set_margin_top(10)
        bar.set_margin_bottom(6)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Buscar repositorio…")
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        bar.append(self._search_entry)

        self._btn_all = Gtk.ToggleButton(label="Todos")
        self._btn_all.set_active(True)
        self._btn_all.add_css_class("pill")
        self._btn_all.connect(
            "toggled", lambda b: self._set_filter("all") if b.get_active() else None
        )

        self._btn_active = Gtk.ToggleButton(label="Activos")
        self._btn_active.set_group(self._btn_all)
        self._btn_active.add_css_class("pill")
        self._btn_active.connect(
            "toggled", lambda b: self._set_filter("active") if b.get_active() else None
        )

        pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pill_box.append(self._btn_all)
        pill_box.append(self._btn_active)
        bar.append(pill_box)

        self._update_btn = Gtk.Button()
        self._update_btn.set_icon_name("view-refresh-symbolic")
        self._update_btn.set_tooltip_text("Actualizar índice de extrepo")
        self._update_btn.add_css_class("flat")
        self._update_btn.connect("clicked", self._on_update_index)
        bar.append(self._update_btn)

        self._global_spinner = Gtk.Spinner()
        self._global_spinner.set_visible(False)
        bar.append(self._global_spinner)

        self.append(bar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._count_label = Gtk.Label()
        self._count_label.add_css_class("dim-label")
        self._count_label.add_css_class("caption")
        self._count_label.set_halign(Gtk.Align.END)
        self._count_label.set_margin_end(14)
        self._count_label.set_margin_top(4)
        self.append(self._count_label)

    # ── Lista ─────────────────────────────────────────────────────────────────

    def _build_list_area(self):
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self._stack)

        loading = Adw.StatusPage()
        loading.set_icon_name("folder-remote-symbolic")
        loading.set_title("Cargando repositorios…")
        spin = Gtk.Spinner()
        spin.set_size_request(48, 48)
        spin.start()
        loading.set_child(spin)
        self._stack.add_named(loading, "loading")

        self._error_page = Adw.StatusPage()
        self._error_page.set_icon_name("dialog-error-symbolic")
        self._error_page.set_title("No se pudieron cargar los repositorios")
        retry_btn = Gtk.Button(label="Reintentar")
        retry_btn.set_halign(Gtk.Align.CENTER)
        retry_btn.add_css_class("pill")
        retry_btn.add_css_class("suggested-action")
        retry_btn.connect("clicked", lambda _: self._load_repos())
        self._error_page.set_child(retry_btn)
        self._stack.add_named(self._error_page, "error")

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_margin_start(12)
        self._list_box.set_margin_end(12)
        self._list_box.set_margin_top(8)
        self._list_box.set_margin_bottom(12)
        self._list_box.set_filter_func(self._filter_func)

        ph = Adw.StatusPage()
        ph.set_icon_name("system-search-symbolic")
        ph.set_title("Sin resultados")
        ph.set_description("Prueba con otra búsqueda o cambia el filtro")
        self._list_box.set_placeholder(ph)

        scroll.set_child(self._list_box)
        self._stack.add_named(scroll, "list")

    # ── Carga ─────────────────────────────────────────────────────────────────

    def _load_repos(self, keyword: str = ""):
        self._stack.set_visible_child_name("loading")
        self._rows.clear()
        child = self._list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        def worker():
            repos, error = run_extrepo_search(keyword)
            enabled = get_enabled_repos()
            if not self._alive:
                return
            GLib.idle_add(self._on_repos_loaded, repos, enabled, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_repos_loaded(self, repos, enabled, error):
        if not self._alive:
            return
        if error:
            self._error_page.set_description(error)
            self._stack.set_visible_child_name("error")
            return

        self._all_repos   = repos
        self._enabled_set = enabled

        for repo in repos:
            is_en = repo["name"] in enabled
            row = RepoRow(repo, is_en, self._on_toggle_repo, self._on_open_install)
            self._rows[repo["name"]] = row
            self._list_box.append(row)

        self._stack.set_visible_child_name("list")
        self._update_count()

    # ── Filtrado ──────────────────────────────────────────────────────────────

    def _row_matches(self, row: RepoRow) -> bool:
        if self._filter_mode == "active" and not row.is_enabled():
            return False
        if self._search_text:
            q = self._search_text.lower()
            return (
                q in row.repo_data["name"].lower()
                or q in row.repo_data["description"].lower()
                or q in row.repo_data["url"].lower()
            )
        return True

    def _filter_func(self, row) -> bool:
        if not isinstance(row, RepoRow):
            return True
        return self._row_matches(row)

    def _set_filter(self, mode: str):
        self._filter_mode = mode
        self._list_box.invalidate_filter()
        self._update_count()

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text()
        self._list_box.invalidate_filter()
        self._update_count()

    def _update_count(self):
        visible = sum(1 for r in self._rows.values() if self._row_matches(r))
        total   = len(self._rows)
        def plural(n, sing, plur):
            return f"{n} {sing}" if n == 1 else f"{n} {plur}"
        if self._search_text or self._filter_mode == "active":
            self._count_label.set_label(
                f"{visible} de {plural(total, 'repositorio', 'repositorios')}"
            )
        else:
            self._count_label.set_label(
                f"{plural(total, 'repositorio disponible', 'repositorios disponibles')}"
            )

    # ── Toggle repo ───────────────────────────────────────────────────────────

    def _on_toggle_repo(self, row: RepoRow):
        name     = row.repo_data["name"]
        enabling = not row.is_enabled()
        action   = "enable" if enabling else "disable"
        verb     = "Habilitando" if enabling else "Deshabilitando"

        row.set_busy(True)
        self._toast(f"{verb} {name}…")

        def worker():
            ok, msg = run_privileged(["extrepo", action, name])
            if not self._alive:
                return
            GLib.idle_add(self._on_toggle_done, row, name, enabling, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_toggle_done(self, row: RepoRow, name: str, enabling: bool,
                        ok: bool, msg: str):
        if not self._alive:
            return
        row.set_busy(False)
        if ok:
            row.set_enabled(enabling)
            if enabling:
                self._enabled_set.add(name)
                if self._toast_overlay:
                    t = Adw.Toast.new(f"Repositorio {name} habilitado")
                    t.set_button_label("Instalar software")
                    t.set_timeout(10)
                    t.connect("button-clicked", lambda _: self._on_open_install(row))
                    self._toast_overlay.add_toast(t)
            else:
                self._enabled_set.discard(name)
                self._toast(f"Repositorio {name} deshabilitado")
            if self._filter_mode == "active":
                self._list_box.invalidate_filter()
            self._update_count()
        else:
            self._toast(f"Error: {msg}", timeout=6)

    # ── Abrir diálogo de instalación ──────────────────────────────────────────

    def _on_open_install(self, row: RepoRow):
        if self._active_dialog is not None:
            self._active_dialog.present()
            return
        parent = self.get_root()
        dlg = InstallDialog(parent, row.repo_data["name"], self._toast_overlay)

        def _on_close(*_):
            self._active_dialog = None
            return False

        dlg.connect("close-request", _on_close)
        self._active_dialog = dlg
        dlg.present()

    # ── Actualizar índice extrepo ─────────────────────────────────────────────

    def _on_update_index(self, _btn=None):
        self._update_btn.set_sensitive(False)
        self._global_spinner.set_visible(True)
        self._global_spinner.start()
        self._toast("Actualizando índice de extrepo…")

        def worker():
            ok, msg = run_privileged(["extrepo", "update"])
            if not self._alive:
                return
            GLib.idle_add(self._on_update_done, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_done(self, ok: bool, msg: str):
        if not self._alive:
            return
        self._global_spinner.stop()
        self._global_spinner.set_visible(False)
        self._update_btn.set_sensitive(True)
        if ok:
            self._toast("Índice actualizado — recargando lista…")
            self._load_repos("")
        else:
            self._toast(f"Error al actualizar: {msg}", timeout=6)

    # ── Toasts ────────────────────────────────────────────────────────────────

    def _toast(self, msg: str, timeout: int = 3):
        if not self._alive or not self._toast_overlay:
            return
        t = Adw.Toast.new(msg)
        t.set_timeout(timeout)
        self._toast_overlay.add_toast(t)


# ══════════════════════════════════════════════════════════════════════════════
#  MainWindow
# ══════════════════════════════════════════════════════════════════════════════

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Extrepo Manager")
        self.set_default_size(760, 620)
        self.set_size_request(440, 480)

        self._toast_ov = Adw.ToastOverlay()
        self.set_content(self._toast_ov)

        toolbar_view = Adw.ToolbarView()
        self._toast_ov.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)

        tw = Adw.WindowTitle()
        tw.set_title("Extrepo Manager")
        tw.set_subtitle("Repositorios externos · ArgentOS")
        header.set_title_widget(tw)

        menu = Gio.Menu()
        menu.append("Acerca de", "win.about")
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        toolbar_view.add_top_bar(header)
        self._widget = ExtrepoWidget(toast_overlay=self._toast_ov)
        toolbar_view.set_content(self._widget)

        act = Gio.SimpleAction.new("about", None)
        act.connect("activate", self._on_about)
        self.add_action(act)

    def _on_about(self, _action, _param):
        dlg = Adw.AboutDialog()
        dlg.set_application_name("ArgentOS Extrepo Manager")
        dlg.set_version(VERSION)
        dlg.set_developer_name("ArgentOS Team")
        dlg.set_application_icon(APP_ID)
        dlg.set_license_type(Gtk.License.GPL_3_0)
        dlg.set_comments(
            "Gestor visual de repositorios externos para ArgentOS Platinum Edition.\n"
            "Basado en extrepo · Debian."
        )
        dlg.set_website("https://github.com/Tavo78ok")
        dlg.present(self)


# ══════════════════════════════════════════════════════════════════════════════
#  App
# ══════════════════════════════════════════════════════════════════════════════

class ExtrepoApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.connect("activate", lambda app: MainWindow(app).present())


def build_tab_widget(toast_overlay: Adw.ToastOverlay | None = None) -> ExtrepoWidget:
    """
    API pública para OpenDash.
        page = build_tab_widget(toast_overlay=self._toast)
        self._view_stack.add_titled(page, "extrepo", "Repositorios")
    """
    return ExtrepoWidget(toast_overlay=toast_overlay)


def main():
    return ExtrepoApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())