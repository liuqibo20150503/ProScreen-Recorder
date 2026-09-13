# -*- coding: utf-8 -*-
"""
ProScreen Recorder - Professional Screen Recording Software
"""

import sys
import os
import traceback
import datetime

def hide_console():
    """Hide Windows console window"""
    if sys.platform.startswith('win'):
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0
            )
        except:
            pass

def setup_logging():
    """Setup logging for debugging"""
    log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ProScreenRecorder")
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except:
            log_dir = os.path.dirname(os.path.abspath(__file__))
    
    log_file = os.path.join(log_dir, "error.log")
    
    def log_exception(exc_type, exc_value, exc_traceback):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\n")
                f.write(f"Time: {timestamp}\n")
                f.write(f"Error: {error_msg}\n")
        except:
            pass
        
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Program Error", 
                f"An error occurred. Check log:\n{log_file}\n\n{str(exc_value)}")
            root.destroy()
        except:
            pass
    
    sys.excepthook = log_exception

hide_console()
setup_logging()

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont
import subprocess
import json
from datetime import datetime
import threading
import time
import queue
import shutil
import re

class ScreenRecorderApp:
    def __init__(self):
        try:
            self.root = tk.Tk()
            self.root.title("ProScreen Recorder")
            self.root.geometry("1000x800")
            self.root.minsize(800, 600)
            
            self.settings = self.load_settings()
            
            self.colors = {
                'bg': '#0f1117',
                'fg': '#e1e4e8',
                'accent': '#58a6ff',
                'accent_hover': '#79c0ff',
                'success': '#3fb950',
                'warning': '#d29922',
                'danger': '#f85149',
                'card': '#161b22',
                'card_hover': '#1c2128',
                'border': '#30363d',
                'button': '#21262d',
                'button_hover': '#30363d',
                'button_text': '#e1e4e8',
                'input_bg': '#0d1117',
                'input_fg': '#e1e4e8',
                'muted': '#8b949e',
            }
            
            self.fonts = {
                'title': tkfont.Font(family='Segoe UI', size=18, weight='bold'),
                'heading': tkfont.Font(family='Segoe UI', size=12, weight='bold'),
                'body': tkfont.Font(family='Segoe UI', size=10),
                'small': tkfont.Font(family='Segoe UI', size=9),
                'button': tkfont.Font(family='Segoe UI', size=10, weight='bold'),
                'large_button': tkfont.Font(family='Segoe UI', size=11, weight='bold'),
                'timer': tkfont.Font(family='Segoe UI', size=28, weight='bold'),
            }
            
            self.root.configure(bg=self.colors['bg'])
            
            self.is_windows = sys.platform.startswith('win')
            
            self.recording = False
            self.paused = False
            self.recording_thread = None
            self.frame_queue = queue.Queue(maxsize=50)
            self.recording_lock = threading.Lock()
            self.capture_error_count = 0
            self.max_capture_errors = 10
            
            self.fps = self.settings.get('fps', 20)
            self.codec = 'avc1'
            self.output_path = ""
            self.recording_start_time = None
            self.recording_duration = 0
            
            self.screen_region = None
            self.selecting_region = False
            self.selection_start = None
            self.selection_rect = None
            
            self.video_path = None
            self.video_capture = None
            self.current_frame = 0
            self.total_frames = 0
            self.clip_start = 0
            self.clip_end = 0
            self.playing = False
            self.play_thread = None
            self.video_fps = 0
            self.video_width = 0
            self.video_height = 0
            self.preview_update_lock = threading.Lock()
            self.preview_update_pending = False
            self.video_capture_lock = threading.Lock()
            self.is_exporting = False
            
            self.screen_width = self.root.winfo_screenwidth()
            self.screen_height = self.root.winfo_screenheight()
            
            self.setup_ui()
            
        except Exception as e:
            print(f"Init error: {e}")
            traceback.print_exc()
            raise

    def load_settings(self):
        default_settings = {
            'fps': 20, 'output_path': '', 'mode': 'full', 'codec': 'mp4'
        }
        try:
            settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
            if os.path.exists(settings_path):
                with open(settings_path, "r", encoding='utf-8') as f:
                    default_settings.update(json.load(f))
        except:
            pass
        return default_settings

    def save_settings(self):
        try:
            settings = {
                'fps': self.fps_var.get(),
                'output_path': self.path_var.get(),
                'mode': self.mode_var.get(),
                'codec': self.codec_var.get(),
            }
            settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
            with open(settings_path, "w", encoding='utf-8') as f:
                json.dump(settings, f, indent=2)
        except:
            pass

    def setup_ui(self):
        # Main container - use pack for outer
        self.main_container = tk.Frame(self.root, bg=self.colors['bg'])
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        # Title bar - use pack
        title_frame = tk.Frame(self.main_container, bg=self.colors['bg'])
        title_frame.pack(fill=tk.X, pady=(0, 10))
        
        title_container = tk.Frame(title_frame, bg=self.colors['bg'])
        title_container.pack(side=tk.LEFT)
        
        tk.Label(title_container, text="ProScreen Recorder",
                font=self.fonts['title'], bg=self.colors['bg'],
                fg=self.colors['accent']).pack(anchor='w')
        
        tk.Label(title_container, text="Professional Screen Recording Studio",
                font=self.fonts['small'], bg=self.colors['bg'],
                fg=self.colors['muted']).pack(anchor='w')
        
        tk.Button(title_frame, text="⚙ Settings", command=self.open_settings,
                 bg=self.colors['button'], fg=self.colors['button_text'],
                 relief=tk.FLAT, padx=12, pady=8, cursor='hand2',
                 font=self.fonts['body']).pack(side=tk.RIGHT)
        
        # Tab frame - use pack
        self.tab_frame = tk.Frame(self.main_container, bg=self.colors['bg'])
        self.tab_frame.pack(fill=tk.BOTH, expand=True)
        
        # Tab buttons - use pack
        self.tab_buttons_frame = tk.Frame(self.tab_frame, bg=self.colors['bg'])
        self.tab_buttons_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.tabs = {}
        self.create_tab_button("recorder", "🎥 Record")
        self.create_tab_button("editor", "✂️ Editor")
        
        # Content frame - use pack
        self.content_frame = tk.Frame(self.tab_frame, bg=self.colors['card'],
                                      highlightbackground=self.colors['border'],
                                      highlightthickness=1)
        self.content_frame.pack(fill=tk.BOTH, expand=True)
        
        self.create_recorder_page()
        self.create_editor_page()
        self.show_tab("recorder")

    def create_tab_button(self, tab_name, text):
        btn = tk.Button(self.tab_buttons_frame, text=text, font=self.fonts['body'],
                       bg=self.colors['button'], fg=self.colors['button_text'],
                       relief=tk.FLAT, padx=24, pady=10, cursor='hand2',
                       command=lambda: self.show_tab(tab_name))
        btn.pack(side=tk.LEFT, padx=(0, 8))
        self.tabs[tab_name] = btn

    def show_tab(self, tab_name):
        self.current_tab = tab_name
        for name, btn in self.tabs.items():
            if name == tab_name:
                btn.config(bg=self.colors['accent'])
            else:
                btn.config(bg=self.colors['button'])
        
        if tab_name == "recorder":
            self.recorder_page.pack(fill=tk.BOTH, expand=True)
            self.editor_page.pack_forget()
        else:
            self.editor_page.pack(fill=tk.BOTH, expand=True)
            self.recorder_page.pack_forget()

    def create_card(self, parent, title):
        """Create card - outer uses pack, inner content can use grid"""
        card = tk.Frame(parent, bg=self.colors['card'],
                       highlightbackground=self.colors['border'],
                       highlightthickness=1)
        
        title_label = tk.Label(card, text=title, font=self.fonts['heading'],
                              bg=self.colors['card'], fg=self.colors['accent'])
        title_label.pack(anchor='w', padx=15, pady=(10, 5))
        
        separator = tk.Frame(card, bg=self.colors['border'], height=1)
        separator.pack(fill='x', padx=10)
        
        # Inner content frame - this is where you can use grid
        inner = tk.Frame(card, bg=self.colors['card'])
        inner.pack(fill='both', expand=True, padx=10, pady=10)
        
        return card, inner

    def create_recorder_page(self):
        self.recorder_page = tk.Frame(self.content_frame, bg=self.colors['card'])
        
        # Mode card - 套一层子Frame解决grid/pack冲突
        mode_card, mode_inner = self.create_card(self.recorder_page, "Recording Mode")
        mode_card.pack(fill='x', padx=15, pady=(15, 8))
        
        # 在inner里用grid
        mode_inner.grid_columnconfigure(0, weight=1)
        mode_inner.grid_columnconfigure(1, weight=1)
        
        self.mode_var = tk.StringVar(value=self.settings.get('mode', 'full'))
        
        tk.Radiobutton(mode_inner, text="🖥 Full Screen", variable=self.mode_var,
                      value='full', bg=self.colors['card'], fg=self.colors['fg'],
                      selectcolor=self.colors['input_bg'], font=self.fonts['body'],
                      activebackground=self.colors['card'], cursor='hand2'
                      ).grid(row=0, column=0, padx=10, pady=10, sticky='w')
        
        tk.Radiobutton(mode_inner, text="🎯 Custom Region", variable=self.mode_var,
                      value='region', bg=self.colors['card'], fg=self.colors['fg'],
                      selectcolor=self.colors['input_bg'], font=self.fonts['body'],
                      activebackground=self.colors['card'], cursor='hand2'
                      ).grid(row=0, column=1, padx=10, pady=10, sticky='w')
        
        self.select_region_btn = tk.Button(mode_inner, text="Select Region",
                                           command=self.select_region,
                                           bg=self.colors['accent'],
                                           fg=self.colors['button_text'],
                                           relief=tk.FLAT, padx=15, pady=5,
                                           cursor='hand2', font=self.fonts['button'])
        self.select_region_btn.grid(row=1, column=0, columnspan=2, pady=(0, 10))
        
        # Settings card
        set_card, set_inner = self.create_card(self.recorder_page, "Recording Settings")
        set_card.pack(fill='x', padx=15, pady=8)
        
        set_inner.grid_columnconfigure(1, weight=1)
        
        tk.Label(set_inner, text="Frame Rate (FPS):", bg=self.colors['card'],
                fg=self.colors['fg'], font=self.fonts['body']
                ).grid(row=0, column=0, sticky='w', padx=5, pady=8)
        
        self.fps_var = tk.IntVar(value=self.settings.get('fps', 20))
        tk.Spinbox(set_inner, from_=5, to=60, textvariable=self.fps_var, width=8,
                  bg=self.colors['input_bg'], fg=self.colors['input_fg'],
                  font=self.fonts['body']
                  ).grid(row=0, column=1, sticky='ew', padx=5, pady=8)
        
        tk.Label(set_inner, text="Format:", bg=self.colors['card'],
                fg=self.colors['fg'], font=self.fonts['body']
                ).grid(row=1, column=0, sticky='w', padx=5, pady=8)
        
        self.codec_var = tk.StringVar(value=self.settings.get('codec', 'mp4'))
        ttk.Combobox(set_inner, textvariable=self.codec_var, values=["mp4", "avi"],
                    state="readonly").grid(row=1, column=1, sticky='ew', padx=5, pady=8)
        
        tk.Label(set_inner, text="Output Path:", bg=self.colors['card'],
                fg=self.colors['fg'], font=self.fonts['body']
                ).grid(row=2, column=0, sticky='w', padx=5, pady=8)
        
        path_frame = tk.Frame(set_inner, bg=self.colors['card'])
        path_frame.grid(row=2, column=1, sticky='ew', padx=5, pady=8)
        path_frame.grid_columnconfigure(0, weight=1)
        
        self.path_var = tk.StringVar(value=self.settings.get('output_path', ''))
        tk.Entry(path_frame, textvariable=self.path_var, bg=self.colors['input_bg'],
                fg=self.colors['input_fg'], font=self.fonts['body'], relief=tk.FLAT
                ).grid(row=0, column=0, sticky='ew', padx=(0, 8))
        
        tk.Button(path_frame, text="Browse", command=self.browse_output,
                 bg=self.colors['button'], fg=self.colors['button_text'],
                 relief=tk.FLAT, padx=12, pady=4, cursor='hand2',
                 font=self.fonts['button']).grid(row=0, column=1)
        
        # Control buttons
        control_frame = tk.Frame(self.recorder_page, bg=self.colors['card'])
        control_frame.pack(pady=15)
        
        self.start_btn = tk.Button(control_frame, text="⏺ Start Recording",
                                   command=self.start_recording, bg=self.colors['success'],
                                   fg=self.colors['button_text'], relief=tk.FLAT,
                                   padx=20, pady=12, cursor='hand2', font=self.fonts['large_button'])
        self.start_btn.pack(side=tk.LEFT, padx=8)
        
        self.pause_btn = tk.Button(control_frame, text="⏸ Pause",
                                   command=self.pause_recording, bg=self.colors['warning'],
                                   fg=self.colors['button_text'], relief=tk.FLAT,
                                   padx=20, pady=12, cursor='hand2', font=self.fonts['large_button'],
                                   state='disabled')
        self.pause_btn.pack(side=tk.LEFT, padx=8)
        
        self.stop_btn = tk.Button(control_frame, text="⏹ Stop",
                                  command=self.stop_recording, bg=self.colors['danger'],
                                  fg=self.colors['button_text'], relief=tk.FLAT,
                                  padx=20, pady=12, cursor='hand2', font=self.fonts['large_button'],
                                  state='disabled')
        self.stop_btn.pack(side=tk.LEFT, padx=8)
        
        # Status card
        status_card, status_inner = self.create_card(self.recorder_page, "Status")
        status_card.pack(fill='both', expand=True, padx=15, pady=(8, 15))
        
        self.status_label = tk.Label(status_inner, text="Ready", font=self.fonts['heading'],
                                     bg=self.colors['card'], fg=self.colors['success'])
        self.status_label.pack(pady=(10, 5))
        
        self.info_label = tk.Label(status_inner, text="", font=self.fonts['small'],
                                   bg=self.colors['card'], fg=self.colors['muted'])
        self.info_label.pack(pady=5)
        
        self.timer_label = tk.Label(status_inner, text="00:00", font=self.fonts['timer'],
                                    bg=self.colors['card'], fg=self.colors['accent'])
        self.timer_label.pack(pady=(10, 20), expand=True)
        
        self.mode_var.trace('w', self.on_mode_change)

    def create_editor_page(self):
        self.editor_page = tk.Frame(self.content_frame, bg=self.colors['card'])
        
        # File card
        file_card, file_inner = self.create_card(self.editor_page, "Video File")
        file_card.pack(fill='x', padx=15, pady=(15, 8))
        
        file_inner.grid_columnconfigure(0, weight=1)
        
        self.video_path_var = tk.StringVar()
        tk.Entry(file_inner, textvariable=self.video_path_var, bg=self.colors['input_bg'],
                fg=self.colors['input_fg'], font=self.fonts['body'], relief=tk.FLAT
                ).grid(row=0, column=0, sticky='ew', padx=(0, 8))
        
        tk.Button(file_inner, text="Load Video", command=self.load_video,
                 bg=self.colors['accent'], fg=self.colors['button_text'],
                 relief=tk.FLAT, padx=12, pady=4, cursor='hand2',
                 font=self.fonts['button']).grid(row=0, column=1)
        
        # Preview card
        prev_card, prev_inner = self.create_card(self.editor_page, "Preview")
        prev_card.pack(fill='both', expand=True, padx=15, pady=8)
        
        self.preview_label = tk.Label(prev_inner, text="Load a video to preview",
                                      bg=self.colors['input_bg'], fg=self.colors['muted'],
                                      font=self.fonts['body'])
        self.preview_label.pack(fill='both', expand=True)
        
        # Timeline card
        time_card, time_inner = self.create_card(self.editor_page, "Timeline")
        time_card.pack(fill='x', padx=15, pady=8)
        
        time_display = tk.Frame(time_inner, bg=self.colors['card'])
        time_display.pack(fill='x', pady=(5, 0))
        
        self.current_time_label = tk.Label(time_display, text="00:00", font=self.fonts['small'],
                                           bg=self.colors['card'], fg=self.colors['accent'])
        self.current_time_label.pack(side='left')
        
        self.total_time_label = tk.Label(time_display, text="00:00", font=self.fonts['small'],
                                         bg=self.colors['card'], fg=self.colors['muted'])
        self.total_time_label.pack(side='right')
        
        self.timeline_scale = tk.Scale(time_inner, from_=0, to=100, orient='horizontal',
                                       bg=self.colors['card'], fg=self.colors['fg'],
                                       highlightthickness=0, troughcolor=self.colors['button'],
                                       command=self.on_timeline_change)
        self.timeline_scale.pack(fill='x', pady=5)
        
        play_controls = tk.Frame(time_inner, bg=self.colors['card'])
        play_controls.pack(pady=(5, 10))
        
        self.play_btn = tk.Button(play_controls, text="▶ Play", command=self.play_video,
                                  bg=self.colors['success'], fg=self.colors['button_text'],
                                  relief=tk.FLAT, padx=15, pady=5, cursor='hand2',
                                  font=self.fonts['button'])
        self.play_btn.pack(side='left', padx=5)
        
        self.pause_video_btn = tk.Button(play_controls, text="⏸ Pause", command=self.pause_video,
                                         bg=self.colors['warning'], fg=self.colors['button_text'],
                                         relief=tk.FLAT, padx=15, pady=5, cursor='hand2',
                                         font=self.fonts['button'])
        self.pause_video_btn.pack(side='left', padx=5)
        
        # Clip card
        clip_card, clip_inner = self.create_card(self.editor_page, "Clip Controls")
        clip_card.pack(fill='x', padx=15, pady=(8, 15))
        
        clip_controls = tk.Frame(clip_inner, bg=self.colors['card'])
        clip_controls.pack(pady=10)
        
        self.set_start_btn = tk.Button(clip_controls, text="Set Start", command=self.set_clip_start,
                                       bg=self.colors['accent'], fg=self.colors['button_text'],
                                       relief=tk.FLAT, padx=15, pady=5, cursor='hand2',
                                       font=self.fonts['button'])
        self.set_start_btn.pack(side='left', padx=5)
        
        self.set_end_btn = tk.Button(clip_controls, text="Set End", command=self.set_clip_end,
                                     bg=self.colors['accent'], fg=self.colors['button_text'],
                                     relief=tk.FLAT, padx=15, pady=5, cursor='hand2',
                                     font=self.fonts['button'])
        self.set_end_btn.pack(side='left', padx=5)
        
        self.export_clip_btn = tk.Button(clip_controls, text="✂ Export Clip", command=self.export_clip,
                                         bg=self.colors['danger'], fg=self.colors['button_text'],
                                         relief=tk.FLAT, padx=15, pady=5, cursor='hand2',
                                         font=self.fonts['button'])
        self.export_clip_btn.pack(side='left', padx=5)
        
        self.clip_info_label = tk.Label(clip_inner, text="", font=self.fonts['small'],
                                        bg=self.colors['card'], fg=self.colors['muted'])
        self.clip_info_label.pack(pady=(0, 10))

    def on_mode_change(self, *args):
        if self.mode_var.get() == "region":
            self.select_region_btn.config(state="normal")
        else:
            self.select_region_btn.config(state="disabled")
            self.screen_region = None

    def browse_output(self):
        filetypes = [("MP4 files", "*.mp4"), ("All files", "*.*")]
        if self.codec_var.get() == "avi":
            filetypes = [("AVI files", "*.avi"), ("All files", "*.*")]
        filename = filedialog.asksaveasfilename(
            defaultextension=f".{self.codec_var.get()}",
            filetypes=filetypes,
            initialfile=f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{self.codec_var.get()}"
        )
        if filename:
            self.path_var.set(filename)

    def select_region(self):
        self.selecting_region = True
        self.root.withdraw()
        time.sleep(0.3)
        
        self.selection_window = tk.Toplevel()
        self.selection_window.attributes('-fullscreen', True)
        self.selection_window.attributes('-alpha', 0.5)
        self.selection_window.attributes('-topmost', True)
        self.selection_window.configure(bg='black')
        
        self.selection_canvas = tk.Canvas(self.selection_window, cursor="crosshair",
                                          highlightthickness=0)
        self.selection_canvas.pack(fill='both', expand=True)
        
        self.selection_canvas.create_text(
            self.screen_width//2, 50,
            text="Drag to select region\nESC to cancel, Enter to confirm",
            fill="white", font=("Arial", 16, "bold"), justify='center'
        )
        
        self.selection_canvas.bind("<ButtonPress-1>", self.on_selection_start)
        self.selection_canvas.bind("<B1-Motion>", self.on_selection_drag)
        self.selection_canvas.bind("<ButtonRelease-1>", self.on_selection_end)
        self.selection_window.bind("<Escape>", self.cancel_selection)
        self.selection_window.bind("<Return>", self.confirm_selection)
        self.selection_window.focus_force()

    def on_selection_start(self, event):
        self.selection_start = (event.x, event.y)
        if self.selection_rect:
            self.selection_canvas.delete(self.selection_rect)

    def on_selection_drag(self, event):
        if self.selection_start:
            if self.selection_rect:
                self.selection_canvas.delete(self.selection_rect)
            self.selection_rect = self.selection_canvas.create_rectangle(
                self.selection_start[0], self.selection_start[1],
                event.x, event.y, outline='#58a6ff', width=3,
                fill='#58a6ff', stipple='gray50'
            )

    def on_selection_end(self, event):
        if self.selection_start:
            x1, y1 = self.selection_start
            x2, y2 = event.x, event.y
            self.screen_region = (min(x1,x2), min(y1,y2), abs(x2-x1), abs(y2-y1))
            self.selection_canvas.create_text(
                self.screen_width//2, self.screen_height - 80,
                text=f"Region: {self.screen_region[2]}x{self.screen_region[3]}\nPress Enter",
                fill="#3fb950", font=("Arial", 14, "bold"), justify='center'
            )

    def cancel_selection(self, event):
        self.selection_window.destroy()
        self.root.deiconify()
        self.selecting_region = False

    def confirm_selection(self, event):
        if self.screen_region:
            self.selection_window.destroy()
            self.root.deiconify()
            self.selecting_region = False
            self.status_label.config(
                text=f"Region: {self.screen_region[2]}x{self.screen_region[3]}",
                fg=self.colors['success']
            )
        else:
            messagebox.showwarning("Warning", "Please select a region first")

    def start_recording(self):
        if self.recording:
            messagebox.showwarning("Warning", "Already recording")
            return
        if not self.path_var.get():
            ext = self.codec_var.get()
            self.path_var.set(os.path.join(
                os.path.expanduser("~"), "Desktop",
                f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
            ))
        self.output_path = self.path_var.get()
        if self.mode_var.get() == "region" and not self.screen_region:
            messagebox.showwarning("Warning", "Please select a region first")
            return
        
        self.fps = self.fps_var.get()
        self.recording_start_time = time.time()
        self.capture_error_count = 0
        
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break
        
        self.recording = True
        self.paused = False
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="Recording...", fg=self.colors['success'])
        
        self.update_timer()
        
        self.recording_thread = threading.Thread(target=self.record_frames, daemon=True)
        self.recording_thread.start()
        
        self.writer_thread = threading.Thread(target=self.write_frames, daemon=True)
        self.writer_thread.start()

    def update_timer(self):
        if self.recording:
            if not self.paused:
                elapsed = time.time() - self.recording_start_time
                m, s = int(elapsed // 60), int(elapsed % 60)
                self.timer_label.config(text=f"{m:02d}:{s:02d}")
                self.root.after(1000, self.update_timer)
            else:
                self.root.after(100, self.update_timer)

    def record_frames(self):
        import mss
        import numpy as np
        import cv2
        
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if not monitors or len(monitors) < 1:
                    self.root.after(0, lambda: self.status_label.config(
                        text="Cannot get screen", fg=self.colors['danger']))
                    return
                
                monitor = monitors[1] if len(monitors) > 1 else monitors[0]
                
                while self.recording:
                    if not self.paused:
                        try:
                            with self.recording_lock:
                                if self.mode_var.get() == "full":
                                    cap = monitor
                                else:
                                    if not self.screen_region:
                                        break
                                    cap = {
                                        'left': int(self.screen_region[0]),
                                        'top': int(self.screen_region[1]),
                                        'width': int(self.screen_region[2]),
                                        'height': int(self.screen_region[3])
                                    }
                                
                                shot = sct.grab(cap)
                                if shot is None:
                                    raise Exception("Screenshot failed")
                                
                                frame = np.array(shot)
                                if frame is None or frame.size == 0:
                                    raise Exception("Invalid frame")
                                
                                if len(frame.shape) == 3 and frame.shape[2] == 4:
                                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                                else:
                                    frame_bgr = frame
                                
                                if self.frame_queue.qsize() < 30:
                                    self.frame_queue.put(frame_bgr)
                                    self.capture_error_count = 0
                                else:
                                    try:
                                        self.frame_queue.get_nowait()
                                        self.frame_queue.put(frame_bgr)
                                    except queue.Empty:
                                        pass
                        except Exception:
                            self.capture_error_count += 1
                            if self.capture_error_count >= self.max_capture_errors:
                                self.root.after(0, self.stop_recording)
                                break
                            time.sleep(0.1)
                    
                    time.sleep(1 / self.fps)
        except Exception:
            pass

    def write_frames(self):
        import cv2
        
        if not self.output_path:
            return
        
        first_frame = None
        while first_frame is None and self.recording:
            try:
                first_frame = self.frame_queue.get(timeout=1)
            except queue.Empty:
                if not self.recording:
                    return
                continue
        
        if first_frame is None:
            return
        
        h, w = first_frame.shape[:2]
        
        if self.output_path.lower().endswith('.mp4'):
            out = None
            for codec in ['avc1', 'mp4v', 'x264', 'h264']:
                try:
                    fourcc = cv2.VideoWriter_fourcc(*codec)
                    out = cv2.VideoWriter(self.output_path, fourcc, self.fps, (w, h))
                    if out.isOpened():
                        break
                    out.release()
                    out = None
                except:
                    continue
            if out is None:
                self.output_path = self.output_path.rsplit('.', 1)[0] + '.avi'
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                out = cv2.VideoWriter(self.output_path, fourcc, self.fps, (w, h))
        else:
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(self.output_path, fourcc, self.fps, (w, h))
        
        if not out.isOpened():
            self.root.after(0, lambda: messagebox.showerror("Error", "Cannot create video file"))
            return
        
        out.write(first_frame)
        
        while self.recording or not self.frame_queue.empty():
            try:
                frame = self.frame_queue.get(timeout=1)
                out.write(frame)
            except queue.Empty:
                continue
        
        out.release()
        cv2.destroyAllWindows()
        
        self.root.after(0, self.on_recording_finished)

    def on_recording_finished(self):
        self.recording = False
        self.paused = False
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Recording complete", fg=self.colors['fg'])
        self.info_label.config(text=f"Saved: {self.output_path}")
        messagebox.showinfo("Complete", f"Recording finished!\n{self.output_path}")

    def pause_recording(self):
        if self.recording:
            self.paused = not self.paused
            if self.paused:
                self.pause_btn.config(text="▶ Resume")
                self.status_label.config(text="Paused", fg=self.colors['warning'])
            else:
                self.pause_btn.config(text="⏸ Pause")
                self.status_label.config(text="Recording...", fg=self.colors['success'])

    def stop_recording(self):
        if self.recording:
            self.recording = False
            self.status_label.config(text="Stopping...", fg=self.colors['warning'])

    def load_video(self):
        def load_thread():
            import cv2
            filename = filedialog.askopenfilename(
                filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv"), ("All files", "*.*")]
            )
            if filename:
                self.video_path = filename
                self.video_path_var.set(filename)
                try:
                    with self.video_capture_lock:
                        if self.video_capture:
                            self.video_capture.release()
                        self.video_capture = cv2.VideoCapture(filename)
                        if not self.video_capture.isOpened():
                            self.root.after(0, lambda: messagebox.showerror("Error", "Cannot open video"))
                            return
                        self.total_frames = int(self.video_capture.get(cv2.CAP_PROP_FRAME_COUNT))
                        self.video_fps = self.video_capture.get(cv2.CAP_PROP_FPS)
                        self.video_width = int(self.video_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                        self.video_height = int(self.video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    
                    self.root.after(0, self.update_video_info)
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
        
        threading.Thread(target=load_thread, daemon=True).start()

    def update_video_info(self):
        self.timeline_scale.config(to=self.total_frames)
        total_s = self.total_frames / self.video_fps
        m, s = int(total_s // 60), int(total_s % 60)
        self.total_time_label.config(text=f"{m:02d}:{s:02d}")
        self.show_frame_async(0)
        self.clip_start = 0
        self.clip_end = self.total_frames
        self.update_clip_info()
        messagebox.showinfo("Success", f"Video loaded!\n{self.video_width}x{self.video_height}\n{self.video_fps:.2f} FPS\n{self.total_frames} frames")

    def show_frame_async(self, frame_number):
        if self.preview_update_pending:
            return
        self.preview_update_pending = True
        
        def load_frame():
            import cv2
            from PIL import Image, ImageTk
            try:
                with self.video_capture_lock:
                    if not self.video_capture:
                        return
                    self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                    ret, frame = self.video_capture.read()
                    if ret:
                        self.current_frame = frame_number
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        pw = max(320, self.preview_label.winfo_width() - 30)
                        ph = max(240, self.preview_label.winfo_height() - 30)
                        h, w = frame_rgb.shape[:2]
                        scale = min(pw/w, ph/h)
                        if scale < 1:
                            frame_rgb = cv2.resize(frame_rgb, (int(w*scale), int(h*scale)))
                        photo = ImageTk.PhotoImage(Image.fromarray(frame_rgb))
                        self.root.after(0, lambda: self.update_preview_label(photo, frame_number))
            except:
                pass
            finally:
                self.preview_update_pending = False
        
        threading.Thread(target=load_frame, daemon=True).start()

    def update_preview_label(self, photo, frame_number):
        self.preview_label.config(image=photo)
        self.preview_label.image = photo
        s = frame_number / self.video_fps
        m, sec = int(s // 60), int(s % 60)
        self.current_time_label.config(text=f"{m:02d}:{sec:02d}")

    def show_frame(self, n):
        self.show_frame_async(n)

    def on_timeline_change(self, value):
        if self.video_capture:
            self.show_frame_async(int(float(value)))

    def play_video(self):
        if not self.video_capture:
            messagebox.showwarning("Warning", "Load a video first")
            return
        if self.playing:
            return
        self.playing = True
        self.play_btn.config(state="disabled")
        self.pause_video_btn.config(state="normal")
        self.play_thread = threading.Thread(target=self.play_frames, daemon=True)
        self.play_thread.start()

    def play_frames(self):
        while self.playing and self.current_frame < self.total_frames:
            self.current_frame += 1
            self.show_frame_async(self.current_frame)
            self.root.after(0, lambda f=self.current_frame: self.timeline_scale.set(f))
            time.sleep(1 / self.video_fps)
        self.playing = False
        self.root.after(0, self.on_play_finished)

    def on_play_finished(self):
        self.play_btn.config(state="normal")
        self.pause_video_btn.config(state="disabled")

    def pause_video(self):
        self.playing = False
        self.play_btn.config(state="normal")
        self.pause_video_btn.config(state="disabled")

    def set_clip_start(self):
        if not self.video_capture:
            messagebox.showwarning("Warning", "Load a video first")
            return
        self.clip_start = self.current_frame
        self.update_clip_info()

    def set_clip_end(self):
        if not self.video_capture:
            messagebox.showwarning("Warning", "Load a video first")
            return
        self.clip_end = self.current_frame
        self.update_clip_info()

    def update_clip_info(self):
        if self.video_capture:
            ss, es = self.clip_start / self.video_fps, self.clip_end / self.video_fps
            dur = es - ss
            self.clip_info_label.config(
                text=f"Start: {int(ss//60):02d}:{int(ss%60):02d} | "
                     f"End: {int(es//60):02d}:{int(es%60):02d} | "
                     f"Duration: {int(dur//60):02d}:{int(dur%60):02d}"
            )

    def export_clip(self):
        if self.is_exporting:
            messagebox.showwarning("Warning", "Export in progress")
            return
        if not self.video_capture:
            messagebox.showwarning("Warning", "Load a video first")
            return
        if self.clip_start >= self.clip_end:
            messagebox.showwarning("Warning", "Start must be before end")
            return
        if self.recording:
            messagebox.showwarning("Warning", "Stop recording first")
            return
        
        output_path = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4 files", "*.mp4"), ("AVI files", "*.avi"), ("All files", "*.*")],
            initialfile=f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        )
        if not output_path:
            return
        
        self.playing = False
        self.is_exporting = True
        self.export_clip_btn.config(state="disabled")
        
        progress_win = tk.Toplevel(self.root)
        progress_win.title("Exporting...")
        progress_win.geometry("300x120")
        progress_win.configure(bg=self.colors['bg'])
        progress_win.transient(self.root)
        progress_win.grab_set()
        
        tk.Label(progress_win, text="Exporting...", bg=self.colors['bg'],
                fg=self.colors['fg'], font=self.fonts['body']).pack(pady=15)
        
        bar = ttk.Progressbar(progress_win, mode='determinate', length=250)
        bar.pack(pady=10)
        
        def export_thread():
            import cv2
            try:
                if shutil.which('ffmpeg'):
                    self.export_ffmpeg(output_path, bar, progress_win)
                else:
                    self.export_opencv(output_path, bar, progress_win)
            except Exception as e:
                self.root.after(0, lambda: self.on_export_error(progress_win, str(e)))
            finally:
                self.is_exporting = False
                self.root.after(0, lambda: self.export_clip_btn.config(state="normal"))
        
        threading.Thread(target=export_thread, daemon=True).start()

    def export_ffmpeg(self, output_path, bar, win):
        start_time = self.clip_start / self.video_fps
        duration = (self.clip_end - self.clip_start) / self.video_fps
        cmd = ['ffmpeg', '-i', self.video_path, '-ss', str(start_time),
               '-t', str(duration), '-c:v', 'libx264', '-preset', 'medium',
               '-crf', '23', '-c:a', 'aac', '-b:a', '128k',
               '-movflags', '+faststart', '-y', output_path]
        flags = subprocess.CREATE_NO_WINDOW if self.is_windows else 0
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True, creationflags=flags)
        for line in process.stderr:
            if 'time=' in line:
                m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if m:
                    t = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                    self.root.after(0, lambda p=min(100, t/duration*100): bar.config(value=p))
        process.wait()
        if process.returncode == 0:
            self.root.after(0, lambda: self.on_export_finished(win, output_path))
        else:
            self.export_opencv(output_path, bar, win)

    def export_opencv(self, output_path, bar, win):
        import cv2
        temp = output_path + ".tmp"
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, self.clip_start)
        
        out = None
        for codec in ['avc1', 'mp4v', 'x264']:
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                out = cv2.VideoWriter(temp, fourcc, self.video_fps,
                                      (self.video_width, self.video_height))
                if out.isOpened():
                    break
                out.release()
                out = None
            except:
                continue
        
        if out is None:
            raise Exception("Cannot create video writer")
        
        total = self.clip_end - self.clip_start
        for i in range(total):
            ret, frame = cap.read()
            if ret:
                out.write(frame)
                self.root.after(0, lambda p=(i+1)/total*100: bar.config(value=p))
            else:
                break
        
        cap.release()
        out.release()
        cv2.destroyAllWindows()
        
        if os.path.exists(temp):
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(temp, output_path)
        
        self.root.after(0, lambda: self.on_export_finished(win, output_path))

    def on_export_finished(self, win, path):
        win.destroy()
        messagebox.showinfo("Complete", f"Clip exported!\n{path}")

    def on_export_error(self, win, error):
        win.destroy()
        messagebox.showerror("Error", f"Export failed: {error}")

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("400x250")
        win.configure(bg=self.colors['bg'])
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        
        card, inner = self.create_card(win, "Font Settings")
        card.pack(fill='x', padx=15, pady=15)
        
        inner.grid_columnconfigure(1, weight=1)
        
        tk.Label(inner, text="Font Family:", bg=self.colors['card'],
                fg=self.colors['fg'], font=self.fonts['body']
                ).grid(row=0, column=0, sticky='w', padx=5, pady=5)
        
        family_var = tk.StringVar(value='Segoe UI')
        ttk.Combobox(inner, textvariable=family_var,
                    values=['Segoe UI', 'Arial', 'Helvetica', 'Consolas'],
                    state='readonly').grid(row=0, column=1, sticky='ew', padx=5, pady=5)
        
        tk.Label(inner, text="Title Size:", bg=self.colors['card'],
                fg=self.colors['fg'], font=self.fonts['body']
                ).grid(row=1, column=0, sticky='w', padx=5, pady=5)
        
        title_size = tk.IntVar(value=18)
        tk.Spinbox(inner, from_=10, to=30, textvariable=title_size, width=8,
                  bg=self.colors['input_bg'], fg=self.colors['input_fg']
                  ).grid(row=1, column=1, sticky='w', padx=5, pady=5)
        
        tk.Label(inner, text="Body Size:", bg=self.colors['card'],
                fg=self.colors['fg'], font=self.fonts['body']
                ).grid(row=2, column=0, sticky='w', padx=5, pady=5)
        
        body_size = tk.IntVar(value=10)
        tk.Spinbox(inner, from_=8, to=20, textvariable=body_size, width=8,
                  bg=self.colors['input_bg'], fg=self.colors['input_fg']
                  ).grid(row=2, column=1, sticky='w', padx=5, pady=5)
        
        def save():
            self.fonts['title'].config(family=family_var.get(), size=title_size.get())
            self.fonts['body'].config(family=family_var.get(), size=body_size.get())
            self.fonts['heading'].config(family=family_var.get(), size=body_size.get()+2)
            self.fonts['small'].config(family=family_var.get(), size=body_size.get()-1)
            self.fonts['button'].config(family=family_var.get(), size=body_size.get())
            self.fonts['large_button'].config(family=family_var.get(), size=body_size.get()+1)
            self.fonts['timer'].config(family=family_var.get(), size=body_size.get()+18)
            win.destroy()
            messagebox.showinfo("Success", "Settings saved!")
        
        tk.Button(win, text="Save", command=save, bg=self.colors['success'],
                 fg=self.colors['button_text'], relief=tk.FLAT, padx=20, pady=8,
                 cursor='hand2', font=self.fonts['button']).pack(pady=15)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()

    def on_closing(self):
        self.save_settings()
        if self.recording:
            self.recording = False
        if self.video_capture:
            self.video_capture.release()
        self.root.destroy()


def main():
    try:
        app = ScreenRecorderApp()
        app.run()
    except Exception as e:
        print(f"Startup error: {e}")
        traceback.print_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Startup Error",
                f"Failed to start:\n{str(e)}\n\nInstall dependencies:\n"
                "pip install opencv-python mss pillow numpy")
            root.destroy()
        except:
            pass


if __name__ == "__main__":
    main()