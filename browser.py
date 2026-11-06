import os
import shutil
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

try:
    import vlc
except ImportError:
    vlc = None

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None


VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".mpeg", ".mpg", ".m4v", ".3gp", ".ts", ".mts", ".m2ts",
    ".avf", ".vob"
}


class VideoBrowser:

    def __init__(self, root):
        self.root = root

        self.root.title("Video Browser and Player")
        self.root.geometry("1200x760")
        self.root.minsize(900, 600)

        self.current_path = Path.home()

        self.folder_history = []
        self.folder_history_index = -1

        self.folder_view_states = {}
        self.tree_root_path = None

        self.clipboard_paths = []
        self.clipboard_mode = None
        self.folder_clipboard_path = None
        self.folder_clipboard_mode = None

        self.video_paths = []
        self.current_video_index = -1

        self.vlc_instance = None
        self.player = None
        self._seeking = False
        self._progress_after_id = None

        self.path_var = tk.StringVar(value=str(self.current_path))
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._init_vlc()
        self._schedule_progress_update()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.load_folder(self.current_path, add_history=True)

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------

    def _build_ui(self):

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Folder path:").pack(side="left")

        self.path_entry = ttk.Entry(
            top,
            textvariable=self.path_var
        )

        self.path_entry.pack(
            side="left",
            fill="x",
            expand=True,
            padx=6
        )

        self.path_entry.bind(
            "<Return>",
            lambda _event: self.load_folder(
                self.path_var.get(),
                add_history=True
            )
        )

        ttk.Button(
            top,
            text="Browse",
            command=self.browse_folder
        ).pack(side="left", padx=3)

        ttk.Button(
            top,
            text="Load Folder",
            command=lambda: self.load_folder(
                self.path_var.get(),
                add_history=True
            )
        ).pack(side="left", padx=3)

        ttk.Button(
            top,
            text="← Back",
            command=self.previous_folder
        ).pack(side="left", padx=3)

        ttk.Button(
            top,
            text="Next →",
            command=self.next_folder
        ).pack(side="left", padx=3)

        # Main split
        main = ttk.PanedWindow(
            self.root,
            orient="horizontal"
        )

        main.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 8)
        )

        left = ttk.Frame(main, padding=4)
        right = ttk.Frame(main, padding=4)

        main.add(left, weight=1)
        main.add(right, weight=4)

        # -----------------------------------------------------
        # Folder list
        # -----------------------------------------------------

        ttk.Label(
            left,
            text="Folders (double-click to open)"
        ).pack(anchor="w")

        folder_frame = ttk.Frame(left)
        folder_frame.pack(
            fill="both",
            expand=True,
            pady=4
        )

        self.folder_tree = ttk.Treeview(
            folder_frame,
            show="tree",
            selectmode="browse"
        )

        folder_scroll = ttk.Scrollbar(
            folder_frame,
            orient="vertical",
            command=self.folder_tree.yview
        )

        self.folder_tree.configure(yscrollcommand=folder_scroll.set)
        self.folder_tree.pack(side="left", fill="both", expand=True)
        folder_scroll.pack(side="right", fill="y")

        self.folder_tree.bind("<<TreeviewOpen>>", self.on_tree_expand)
        self.folder_tree.bind("<Double-Button-1>", self.open_selected_folder)
        self.folder_tree.bind("<Button-1>", self.on_folder_tree_click)
        self.folder_tree.bind("<Delete>", self.on_delete_key)
        self.folder_tree.bind("<Button-3>", self.folder_context_menu)

        # -----------------------------------------------------
        # Video / file list
        # -----------------------------------------------------

        ttk.Label(
            left,
            text="Videos / Files"
        ).pack(
            anchor="w",
            pady=(8, 0)
        )

        file_frame = ttk.Frame(left)

        file_frame.pack(
            fill="both",
            expand=True,
            pady=4
        )

        self.video_list = tk.Listbox(
            file_frame,
            selectmode=tk.EXTENDED,
            exportselection=False
        )

        file_scroll = ttk.Scrollbar(
            file_frame,
            orient="vertical",
            command=self.video_list.yview
        )

        self.video_list.configure(
            yscrollcommand=file_scroll.set
        )

        self.video_list.pack(
            side="left",
            fill="both",
            expand=True
        )

        file_scroll.pack(
            side="right",
            fill="y"
        )

        self.video_list.bind(
            "<<ListboxSelect>>",
            self.on_video_select
        )

        self.video_list.bind(
            "<Double-Button-1>",
            self.on_video_double_click
        )

        self.video_list.bind(
            "<Delete>",
            self.on_delete_key
        )

        self.video_list.bind(
            "<Button-3>",
            self.file_context_menu
        )

        # Keyboard shortcuts
        self.root.bind(
            "<Delete>",
            self.on_delete_key
        )

        self.root.bind(
            "<Control-x>",
            self.cut_selected
        )

        self.root.bind(
            "<Control-c>",
            self.copy_selected
        )

        self.root.bind(
            "<Control-v>",
            self.paste_clipboard
        )

        # -----------------------------------------------------
        # Video player
        # -----------------------------------------------------

        video_area = ttk.Frame(right)
        video_area.pack(
            fill="both",
            expand=True
        )

        self.video_panel = tk.Frame(
            video_area,
            bg="black",
            width=800,
            height=480
        )

        self.video_panel.pack(
            fill="both",
            expand=True
        )

        self.video_panel.pack_propagate(False)

        # Playback time and seek bar
        progress_frame = ttk.Frame(right)
        progress_frame.pack(fill="x", pady=(5, 0))

        self.time_var = tk.StringVar(value="00:00:00 / 00:00:00")
        self.time_label = ttk.Label(
            progress_frame,
            textvariable=self.time_var,
            width=19,
            anchor="w"
        )
        self.time_label.pack(side="left", padx=(2, 8))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_scale = ttk.Scale(
            progress_frame,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.progress_var
        )
        self.progress_scale.pack(side="left", fill="x", expand=True)
        self.progress_scale.bind("<ButtonPress-1>", self._start_seek)
        self.progress_scale.bind("<ButtonRelease-1>", self._finish_seek)

        # -----------------------------------------------------
        # Playback controls
        # -----------------------------------------------------

        controls = ttk.Frame(
            right,
            padding=(0, 8)
        )

        controls.pack(fill="x")

        ttk.Button(
            controls,
            text="⏮ Previous Video",
            command=self.previous_video
        ).pack(side="left", padx=3)

        ttk.Button(
            controls,
            text="▶ Play",
            command=self.play_video
        ).pack(side="left", padx=3)

        ttk.Button(
            controls,
            text="⏸ Pause",
            command=self.pause_video
        ).pack(side="left", padx=3)

        ttk.Button(
            controls,
            text="⏹ Stop",
            command=self.stop_video
        ).pack(side="left", padx=3)

        ttk.Button(
            controls,
            text="Next Video ⏭",
            command=self.next_video
        ).pack(side="left", padx=3)

        ttk.Button(
            controls,
            text="Delete Video/File",
            command=self.delete_selected_files
        ).pack(side="right", padx=3)

        ttk.Button(
            controls,
            text="Delete Folder",
            command=self.delete_selected_folder
        ).pack(side="right", padx=3)

        # -----------------------------------------------------
        # Status bar
        # -----------------------------------------------------

        status = ttk.Label(
            self.root,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=4
        )

        status.pack(
            fill="x",
            side="bottom"
        )

        # -----------------------------------------------------
        # Context menus
        # -----------------------------------------------------

        self.folder_menu = tk.Menu(
            self.root,
            tearoff=0
        )

        self.folder_menu.add_command(
            label="Open Folder",
            command=self.open_selected_folder
        )

        self.folder_menu.add_command(
            label="Delete Folder",
            command=self.delete_selected_folder
        )
        self.folder_menu.add_separator()
        self.folder_menu.add_command(
            label="Cut Folder",
            command=self.cut_selected_folder
        )
        self.folder_menu.add_command(
            label="Copy Folder",
            command=self.copy_selected_folder
        )
        self.folder_menu.add_command(
            label="Paste",
            command=self.paste_into_selected_folder
        )

        self.file_menu = tk.Menu(
            self.root,
            tearoff=0
        )

        self.file_menu.add_command(
            label="Play",
            command=self.play_video
        )

        self.file_menu.add_command(
            label="Cut",
            command=self.cut_selected
        )

        self.file_menu.add_command(
            label="Copy",
            command=self.copy_selected
        )

        self.file_menu.add_command(
            label="Paste",
            command=self.paste_clipboard
        )

        self.file_menu.add_command(
            label="Delete",
            command=self.delete_selected_files
        )

    # ---------------------------------------------------------
    # VLC
    # ---------------------------------------------------------

    def _init_vlc(self):

        if vlc is None:
            self.status_var.set(
                "python-vlc is not installed. "
                "Install it and VLC to enable playback."
            )
            return

        try:
            self.vlc_instance = vlc.Instance(
                "--no-video-title-show",
                "--avcodec-hw=none",
                "--avcodec-threads=1"
            )

            self.player = self.vlc_instance.media_player_new()

        except Exception as exc:
            self.status_var.set(
                f"VLC initialization failed: {exc}"
            )

            self.player = None

    # ---------------------------------------------------------
    # Folder navigation
    # ---------------------------------------------------------

    def browse_folder(self):

        chosen = filedialog.askdirectory(
            initialdir=str(self.current_path)
        )

        if chosen:
            self.path_var.set(chosen)

            self.load_folder(
                chosen,
                add_history=True
            )

    def _folder_children(self, path):

        try:
            return sorted(
                [
                    p for p in Path(path).iterdir()
                    if p.is_dir()
                ],
                key=lambda p: p.name.lower()
            )

        except (OSError, PermissionError):
            return []

    def _files_children(self, path):

        try:
            return sorted(
                [
                    p for p in Path(path).iterdir()
                    if p.is_file()
                ],
                key=lambda p: p.name.lower()
            )

        except (OSError, PermissionError):
            return []

    # ---------------------------------------------------------
    # Save and restore folder list view
    # ---------------------------------------------------------

    def save_folder_list_position(self):
        """Keep the currently selected folder path for navigation history."""
        try:
            selected = self.folder_tree.selection()
            selected_path = self.folder_tree.item(selected[0], "values")[0] if selected else None
            self.folder_view_states[str(self.current_path)] = {
                "selected_path": selected_path
            }
        except (tk.TclError, IndexError, KeyError):
            pass

    def restore_folder_list_position(self):
        state = self.folder_view_states.get(str(self.current_path), {})
        selected_path = state.get("selected_path")
        if selected_path:
            item = self._tree_item_for_path(Path(selected_path))
            if item:
                self.folder_tree.selection_set(item)
                self.folder_tree.focus(item)
                self.folder_tree.see(item)

    def _tree_item_for_path(self, path):
        target = str(Path(path).resolve())
        for item in self.folder_tree.get_children(""):
            found = self._find_tree_item(item, target)
            if found:
                return found
        return ""

    def _find_tree_item(self, item, target):
        values = self.folder_tree.item(item, "values")
        if values and values[0] == target:
            return item
        for child in self.folder_tree.get_children(item):
            found = self._find_tree_item(child, target)
            if found:
                return found
        return ""

    def _insert_tree_folder(self, parent_item, folder_path):
        folder_path = Path(folder_path)
        item = self.folder_tree.insert(
            parent_item, "end", text=folder_path.name,
            values=(str(folder_path.resolve()),), open=False
        )
        # A dummy child makes the folder expandable; children load on expansion.
        if self._folder_children(folder_path):
            self.folder_tree.insert(item, "end", text="Loading...")
        return item

    def _populate_tree_children(self, item):
        children = self.folder_tree.get_children(item)
        if len(children) == 1 and self.folder_tree.item(children[0], "text") == "Loading...":
            self.folder_tree.delete(children[0])
            values = self.folder_tree.item(item, "values")
            if not values:
                return
            for folder in self._folder_children(values[0]):
                self._insert_tree_folder(item, folder)

    def on_tree_expand(self, _event=None):
        item = self.folder_tree.focus()
        if item:
            self._populate_tree_children(item)

    def on_folder_tree_click(self, event):
        item = self.folder_tree.identify_row(event.y)
        if not item:
            return
        self.folder_tree.selection_set(item)
        self.folder_tree.focus(item)
        # A single click on a folder selects it and displays its files.
        values = self.folder_tree.item(item, "values")
        if values:
            self.load_folder(values[0], add_history=True)

    def _selected_folder_path(self):
        selected = self.folder_tree.selection()
        if not selected:
            return self.current_path
        values = self.folder_tree.item(selected[0], "values")
        return Path(values[0]) if values else self.current_path

    @staticmethod
    def _format_duration(seconds):
        """Format video duration as HH:MM:SS."""
        try:
            total_seconds = max(0, int(float(seconds)))
        except (TypeError, ValueError):
            return "--:--:--"

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _get_video_duration(self, file_path):
        """Get video duration using ffprobe; return a placeholder if unavailable."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(file_path)
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )
            return self._format_duration(result.stdout.strip())
        except (FileNotFoundError, subprocess.SubprocessError, ValueError):
            return "--:--:--"

    def _refresh_folder_tree(self, current_path):
        current_path = Path(current_path).resolve()

        # Keep one dataset root visible while browsing its descendants.
        if self.tree_root_path is None:
            self.tree_root_path = current_path
        else:
            try:
                current_path.relative_to(self.tree_root_path)
            except ValueError:
                self.tree_root_path = current_path
                self.folder_tree.delete(*self.folder_tree.get_children(""))

        existing = self.folder_tree.get_children("")
        if not existing:
            root_path = self.tree_root_path
            root_item = self.folder_tree.insert(
                "", "end", text=root_path.name or str(root_path),
                values=(str(root_path),), open=True
            )
            for folder in self._folder_children(root_path):
                self._insert_tree_folder(root_item, folder)
        else:
            root_item = existing[0]

        selected_item = self._tree_item_for_path(current_path)
        if selected_item:
            self.folder_tree.selection_set(selected_item)
            self.folder_tree.focus(selected_item)
            self.folder_tree.see(selected_item)

    def load_folder(self, path, add_history=False):

        try:
            new_path = Path(path).expanduser().resolve()

            if not new_path.is_dir():

                messagebox.showerror(
                    "Invalid folder",
                    f"Folder does not exist:\n{new_path}"
                )

                return

        except (OSError, RuntimeError) as exc:

            messagebox.showerror(
                "Folder error",
                str(exc)
            )

            return

        if str(self.current_path) != str(new_path):
            self.save_folder_list_position()

        self.current_path = new_path

        self.path_var.set(
            str(new_path)
        )

        # Keep the folder tree visible; refresh it only when its root changes.
        self._refresh_folder_tree(new_path)

        self.video_list.delete(
            0,
            tk.END
        )

        folders = self._folder_children(
            new_path
        )

        files = self._files_children(
            new_path
        )

        self.video_paths = [
            p for p in files
            if p.suffix.lower() in VIDEO_EXTENSIONS
        ]

        for file_path in files:
            display_name = file_path.name

            if file_path.suffix.lower() in VIDEO_EXTENSIONS:
                duration = self._get_video_duration(file_path)
                display_name = f"{file_path.name}    [{duration}]"

            self.video_list.insert(
                tk.END,
                display_name
            )

        if add_history:

            if (
                self.folder_history_index
                < len(self.folder_history) - 1
            ):

                self.folder_history = (
                    self.folder_history[
                        :self.folder_history_index + 1
                    ]
                )

            if (
                not self.folder_history
                or self.folder_history[-1] != new_path
            ):

                self.folder_history.append(
                    new_path
                )

            self.folder_history_index = (
                len(self.folder_history) - 1
            )

        self.restore_folder_list_position()

        self.status_var.set(
            f"Folder: {new_path} | "
            f"{len(folders)} folders | "
            f"{len(files)} files | "
            f"{len(self.video_paths)} videos"
        )

    def open_selected_folder(self, _event=None):
        folder_path = self._selected_folder_path()
        if folder_path.is_dir():
            self.load_folder(folder_path, add_history=True)

    def previous_folder(self):

        if self.folder_history_index <= 0:
            return

        self.save_folder_list_position()

        self.folder_history_index -= 1

        self.load_folder(
            self.folder_history[
                self.folder_history_index
            ],
            add_history=False
        )

    def next_folder(self):

        if (
            self.folder_history_index
            >= len(self.folder_history) - 1
        ):
            return

        self.save_folder_list_position()

        self.folder_history_index += 1

        self.load_folder(
            self.folder_history[
                self.folder_history_index
            ],
            add_history=False
        )

    # ---------------------------------------------------------
    # Video playback
    # ---------------------------------------------------------

    def on_video_select(self, _event=None):

        selected = self.video_list.curselection()

        if not selected:
            return

        file_path = (
            self.current_path
            / self.video_list.get(selected[0]).rsplit("    [", 1)[0]
        )

        if file_path.suffix.lower() in VIDEO_EXTENSIONS:
            self.play_path(file_path)

    def on_video_double_click(self, _event=None):
        self.play_video()

    def _start_seek(self, _event=None):
        self._seeking = True

    def _finish_seek(self, _event=None):
        if self.player:
            try:
                target_ms = int(float(self.progress_var.get()) * 1000)
                self.player.set_time(target_ms)
            except Exception:
                pass
        self._seeking = False

    def _schedule_progress_update(self):
        self._update_playback_progress()
        self._progress_after_id = self.root.after(
            250, self._schedule_progress_update
        )

    def _update_playback_progress(self):
        if not self.player:
            return

        try:
            length_ms = self.player.get_length()
            current_ms = self.player.get_time()

            if length_ms and length_ms > 0:
                total_seconds = length_ms / 1000.0
                current_seconds = max(0, current_ms) / 1000.0
                current_seconds = min(current_seconds, total_seconds)

                self.time_var.set(
                    f"{self._format_duration(current_seconds)} / "
                    f"{self._format_duration(total_seconds)}"
                )

                self.progress_scale.configure(to=total_seconds)
                if not self._seeking:
                    self.progress_var.set(current_seconds)
            elif current_ms < 0:
                self.time_var.set("00:00:00 / 00:00:00")
        except Exception:
            pass

    def play_path(self, path):

        if not self.player:

            messagebox.showwarning(
                "VLC unavailable",
                "Install VLC and python-vlc to play videos."
            )

            return

        try:

            media = self.vlc_instance.media_new(
                str(path)
            )

            self.player.set_media(media)
            self.time_var.set("00:00:00 / 00:00:00")
            self.progress_var.set(0)
            self.progress_scale.configure(to=100)

            self.root.update_idletasks()

            self.player.set_xwindow(
                self.video_panel.winfo_id()
            )

            self.player.play()

            self.status_var.set(
                f"Playing: {path.name}"
            )

        except Exception as exc:

            messagebox.showerror(
                "Playback error",
                str(exc)
            )

    def play_video(self):

        selected = self.video_list.curselection()

        if selected:

            self.play_path(
                self.current_path
                / self.video_list.get(selected[0]).rsplit("    [", 1)[0]
            )

        elif self.player:
            self.player.play()

    def pause_video(self):

        if self.player:
            self.player.pause()

    def stop_video(self):

        if self.player:
            self.player.stop()
            self.progress_var.set(0)
            self.time_var.set("00:00:00 / 00:00:00")

    def previous_video(self):

        if not self.video_paths:
            return

        self.current_video_index = max(
            0,
            self.current_video_index - 1
        )

        self._select_video_index(
            self.current_video_index
        )

    def next_video(self):

        if not self.video_paths:
            return

        self.current_video_index = min(
            len(self.video_paths) - 1,
            self.current_video_index + 1
        )

        self._select_video_index(
            self.current_video_index
        )

    def _select_video_index(self, index):

        path = self.video_paths[index]

        try:

            list_index = next(
                i for i, item in enumerate(self.video_list.get(0, tk.END))
                if item.rsplit("    [", 1)[0] == path.name
            )

        except ValueError:
            return

        self.video_list.selection_clear(
            0,
            tk.END
        )

        self.video_list.selection_set(
            list_index
        )

        self.video_list.activate(
            list_index
        )

        self.video_list.see(
            list_index
        )

        self.play_path(path)

    # ---------------------------------------------------------
    # Delete shortcut
    # ---------------------------------------------------------

    def _selected_files(self):

        return [
            self.current_path
            / self.video_list.get(i).rsplit("    [", 1)[0]
            for i in self.video_list.curselection()
        ]

    def on_delete_key(self, event=None):
        """
        Delete key behavior:
        - Folder list has focus: delete selected folder.
        - Video/file list has focus: delete selected files.
        - Otherwise, use the list with a current selection.
        """

        focused = self.root.focus_get()

        if focused == self.folder_tree:

            self.delete_selected_folder()

        elif focused == self.video_list:

            self.delete_selected_files()

        else:

            if self.folder_tree.selection():

                self.delete_selected_folder()

            elif self.video_list.curselection():

                self.delete_selected_files()

        return "break"

    def _move_to_trash(self, paths, item_type):

        existing = [
            Path(p)
            for p in paths
            if Path(p).exists()
        ]

        if not existing:

            messagebox.showinfo(
                "Delete",
                f"No {item_type} selected."
            )

            return

        names = "\n".join(
            p.name for p in existing[:10]
        )

        if len(existing) > 10:

            names += (
                f"\n... and {len(existing) - 10} more"
            )

        confirmed = messagebox.askyesno(
            "Confirm delete",
            f"Move {len(existing)} {item_type}(s) to Trash?\n\n"
            f"{names}"
        )

        if not confirmed:
            return

        try:

            if not send2trash:

                raise RuntimeError(
                    "send2trash is not installed.\n"
                    "Install it using:\n"
                    "python3 -m pip install --user send2trash"
                )

            for path in existing:
                send2trash(str(path))

            self.status_var.set(
                f"Moved {len(existing)} {item_type}(s) to Trash"
            )

            self.load_folder(
                self.current_path,
                add_history=False
            )
            self._refresh_folder_tree(self.current_path)

        except Exception as exc:

            messagebox.showerror(
                "Delete error",
                str(exc)
            )

    def delete_selected_folder(self):
        folder_path = self._selected_folder_path()
        if folder_path == self.current_path:
            messagebox.showinfo("Delete folder", "Select a child folder, not the current root.")
            return
        self._move_to_trash([folder_path], "folder")

    def cut_selected_folder(self):
        folder_path = self._selected_folder_path()
        if folder_path == self.current_path or not folder_path.is_dir():
            messagebox.showinfo("Cut folder", "Select a child folder first.")
            return
        self.folder_clipboard_path = folder_path
        self.folder_clipboard_mode = "cut"
        self.status_var.set(f"Cut folder: {folder_path.name}. Select destination and choose Paste.")

    def copy_selected_folder(self):
        folder_path = self._selected_folder_path()
        if folder_path == self.current_path or not folder_path.is_dir():
            messagebox.showinfo("Copy folder", "Select a child folder first.")
            return
        self.folder_clipboard_path = folder_path
        self.folder_clipboard_mode = "copy"
        self.status_var.set(f"Copied folder: {folder_path.name}. Select destination and choose Paste.")

    def paste_into_selected_folder(self):
        destination = self._selected_folder_path()
        if not destination.is_dir():
            messagebox.showerror("Paste", "Destination folder does not exist.")
            return

        # Folder clipboard takes priority; otherwise paste selected/copied files.
        if self.folder_clipboard_path:
            source = self.folder_clipboard_path
            target = destination / source.name
            try:
                if source.resolve() == destination.resolve() or source.resolve() in destination.resolve().parents:
                    raise ValueError("Cannot paste a folder into itself or one of its subfolders.")
                if target.exists():
                    raise FileExistsError(f"Destination already exists: {target}")
                if self.folder_clipboard_mode == "cut":
                    shutil.move(str(source), str(target))
                    self.folder_clipboard_path = None
                    self.folder_clipboard_mode = None
                else:
                    shutil.copytree(str(source), str(target))
                self.status_var.set(f"Folder pasted: {target}")
                self._refresh_folder_tree(self.current_path)
                self.load_folder(self.current_path, add_history=False)
            except Exception as exc:
                messagebox.showerror("Paste folder error", str(exc))
            return

        self.paste_clipboard(destination_override=destination)

    def delete_selected_files(self):

        paths = self._selected_files()

        if not paths:

            messagebox.showinfo(
                "Delete video/file",
                "Select one or more files first."
            )

            return

        self._move_to_trash(
            paths,
            "file"
        )

    # ---------------------------------------------------------
    # Cut / Copy / Paste
    # ---------------------------------------------------------

    def cut_selected(self, _event=None):

        paths = self._selected_files()

        if not paths:
            return "break"

        self.clipboard_paths = paths

        self.clipboard_mode = "cut"

        self.status_var.set(
            f"Cut {len(paths)} file(s). "
            "Navigate to destination and press Ctrl+V."
        )

        return "break"

    def copy_selected(self, _event=None):

        paths = self._selected_files()

        if not paths:
            return "break"

        self.clipboard_paths = paths

        self.clipboard_mode = "copy"

        self.status_var.set(
            f"Copied {len(paths)} file(s). "
            "Navigate to destination and press Ctrl+V."
        )

        return "break"

    def paste_clipboard(self, _event=None, destination_override=None):

        if not self.clipboard_paths:
            return "break"

        count = 0
        errors = []

        for src in self.clipboard_paths:

            destination = Path(destination_override) if destination_override else self.current_path
            dest = destination / src.name

            if dest.exists():

                errors.append(
                    f"Already exists: {dest.name}"
                )

                continue

            try:

                if self.clipboard_mode == "cut":

                    shutil.move(
                        str(src),
                        str(dest)
                    )

                else:

                    shutil.copy2(
                        str(src),
                        str(dest)
                    )

                count += 1

            except Exception as exc:

                errors.append(
                    f"{src.name}: {exc}"
                )

        if self.clipboard_mode == "cut":

            self.clipboard_paths = []
            self.clipboard_mode = None
        self.folder_clipboard_path = None
        self.folder_clipboard_mode = None

        self.load_folder(
            self.current_path,
            add_history=False
        )
        self._refresh_folder_tree(self.current_path)

        message = f"Pasted {count} file(s)."

        if errors:

            message += (
                "\n\n"
                + "\n".join(errors[:8])
            )

        messagebox.showinfo(
            "Paste",
            message
        )

        return "break"

    # ---------------------------------------------------------
    # Context menus
    # ---------------------------------------------------------

    def folder_context_menu(self, event):
        item = self.folder_tree.identify_row(event.y)
        if item:
            self.folder_tree.selection_set(item)
            self.folder_tree.focus(item)
            values = self.folder_tree.item(item, "values")
            if values:
                self.folder_menu.tk_popup(event.x_root, event.y_root)

    def file_context_menu(self, event):

        index = self.video_list.nearest(
            event.y
        )

        if index >= 0:

            self.video_list.selection_set(
                index
            )

            self.video_list.activate(
                index
            )

        self.video_list.focus_set()

        self.file_menu.tk_popup(
            event.x_root,
            event.y_root
        )

    # ---------------------------------------------------------
    # Close application
    # ---------------------------------------------------------

    def on_close(self):

        if self._progress_after_id is not None:
            try:
                self.root.after_cancel(self._progress_after_id)
            except tk.TclError:
                pass

        try:

            if self.player:

                self.player.stop()
                self.player.release()

            if self.vlc_instance:
                self.vlc_instance.release()

        except Exception:
            pass

        self.root.destroy()


# -------------------------------------------------------------
# Main
# -------------------------------------------------------------

def main():

    root = tk.Tk()

    VideoBrowser(root)

    root.mainloop()


if __name__ == "__main__":
    main()
