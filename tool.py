import customtkinter
import threading
import time
import requests
import json
import os
import random
import pyautogui
import ssl
import warnings
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageTk, ImageDraw, ImageFont
from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen
import google.generativeai as genai

# --- CONFIGURATION ---
SMMO_GAME_API_KEY = "your_game_api_key_here"

ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings("ignore")
pyautogui.FAILSAFE = False

# ==================== API KEY MANAGER (11 KEYS) ====================
class APIKeyManager:
    def __init__(self, api_keys):
        self.api_keys = api_keys
        self.current_index = 0
        self.key_stats = {
            key: {
                'used': 0, 
                'errors': 0, 
                'last_error': None,
                'quota_exhausted': False,
                'quota_reset_time': None
            } for key in api_keys
        }
        self.lock = threading.Lock()
        self.all_keys_exhausted = False
    
    def get_next_key(self):
        with self.lock:
            available_keys = [
                key for key in self.api_keys 
                if not self.key_stats[key]['quota_exhausted']
            ]
            
            if not available_keys:
                self.all_keys_exhausted = True
                return None
            
            attempts = 0
            while attempts < len(self.api_keys):
                key = self.api_keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                
                if not self.key_stats[key]['quota_exhausted']:
                    return key
                
                attempts += 1
            
            return None
    
    def mark_key_used(self, key):
        with self.lock:
            if key in self.key_stats:
                self.key_stats[key]['used'] += 1
    
    def mark_key_error(self, key, error_msg):
        with self.lock:
            if key in self.key_stats:
                self.key_stats[key]['errors'] += 1
                self.key_stats[key]['last_error'] = error_msg
                
                if '429' in str(error_msg) and 'quota' in str(error_msg).lower():
                    self.key_stats[key]['quota_exhausted'] = True
                    now = datetime.now(timezone.utc)
                    tomorrow = now + timedelta(days=1)
                    reset_time = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
                    self.key_stats[key]['quota_reset_time'] = reset_time
    
    def get_stats(self):
        with self.lock:
            return dict(self.key_stats)
    
    def check_and_reset_quotas(self):
        with self.lock:
            now = datetime.now(timezone.utc)
            for key in self.api_keys:
                if self.key_stats[key]['quota_exhausted']:
                    reset_time = self.key_stats[key]['quota_reset_time']
                    if reset_time and now >= reset_time:
                        self.key_stats[key]['quota_exhausted'] = False
                        self.key_stats[key]['quota_reset_time'] = None
            
            available_keys = [
                key for key in self.api_keys 
                if not self.key_stats[key]['quota_exhausted']
            ]
            self.all_keys_exhausted = len(available_keys) == 0
    
    def get_time_until_reset(self):
        with self.lock:
            reset_times = [
                stat['quota_reset_time'] 
                for stat in self.key_stats.values() 
                if stat['quota_reset_time']
            ]
            
            if not reset_times:
                return None
            
            nearest_reset = min(reset_times)
            now = datetime.now(timezone.utc)
            
            if nearest_reset > now:
                return nearest_reset - now
            return None
    
    def try_all_keys(self, operation_func, max_retries=None):
        self.check_and_reset_quotas()
        
        available_keys = [
            key for key in self.api_keys 
            if not self.key_stats[key]['quota_exhausted']
        ]
        
        if not available_keys:
            time_until_reset = self.get_time_until_reset()
            if time_until_reset:
                hours = int(time_until_reset.total_seconds() // 3600)
                minutes = int((time_until_reset.total_seconds() % 3600) // 60)
                raise QuotaExhaustedException(
                    f"All API keys exhausted. Quota resets in {hours}h {minutes}m"
                )
            else:
                raise QuotaExhaustedException("All API keys exhausted")
        
        if max_retries is None:
            max_retries = len(available_keys)
        
        attempts = 0
        errors = []
        
        while attempts < max_retries:
            current_key = self.get_next_key()
            
            if current_key is None:
                raise QuotaExhaustedException("No available keys")
            
            try:
                result = operation_func(current_key)
                self.mark_key_used(current_key)
                return result
            except Exception as e:
                error_msg = str(e)
                self.mark_key_error(current_key, error_msg)
                errors.append(f"Key {attempts + 1}: {error_msg[:100]}...")
                attempts += 1
                
                if "429" in error_msg or "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
                    continue
                
                if "not found" in error_msg.lower() or "does not exist" in error_msg.lower():
                    continue
                
                if "invalid" in error_msg.lower() or "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
                    continue
                
                if attempts >= max_retries:
                    break
                
                time.sleep(2)
        
        raise Exception(f"All API keys failed after {attempts} attempts")

class QuotaExhaustedException(Exception):
    pass

API_KEYS = [
    'your_api_key_1',
]

api_key_manager = APIKeyManager(API_KEYS)

# ==================== HELPER FUNCTIONS ====================
safe = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

def add_text_to_image(image_path, text, position=(10, 10), font_size=15, color="black", output_path="output_image.jpg"):
    try:
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except IOError:
            font = ImageFont.load_default()
        draw.text(position, text, fill=color, font=font)
        image.save(output_path)
    except Exception as e:
        print(f"Error adding text to image: {e}")

def remove_non_numbers(text):
    if not isinstance(text, str):
        return ""
    return ''.join(char for char in text if char.isdigit())

# ==================== MAIN APPLICATION ====================
class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.title("SimpleMMO ULTIMATE BOT - Headless Optimized")
        self.geometry("1200x800")
        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")

        # Variables
        self.bot_running = False
        self.paused = False
        self.pause_condition = threading.Condition()
        self.counts = {
            'steps': 0, 'mine': 0, 'attack': 0, 'salvage': 0, 
            'chop': 0, 'catch': 0, 'captcha': 0, 'grab': 0, 'captcha_skipped': 0
        }

        # Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.create_sidebar()
        self.create_main_panel()
        
        self.log("✅ System Initialized - 11 API Keys Loaded")
        self.log("💡 Headless Mode Optimized for Background Run")

    def create_sidebar(self):
        self.sidebar = customtkinter.CTkFrame(self, width=250, corner_radius=0, fg_color="#1a1a1a")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(12, weight=1)

        # Title
        title = customtkinter.CTkLabel(
            self.sidebar, 
            text="SMMO\nULTIMATE", 
            font=("Arial Black", 24, "bold"),
            text_color="#00D9FF"
        )
        title.grid(row=0, column=0, padx=20, pady=20)

        # Login Button
        self.btn_login = customtkinter.CTkButton(
            self.sidebar,
            text="🔐 Login & Save Session",
            command=self.login_thread,
            fg_color="#E67E22",
            hover_color="#D35400",
            height=40,
            font=("Arial", 13, "bold")
        )
        self.btn_login.grid(row=1, column=0, padx=20, pady=10, sticky="ew")

        # Start Button
        self.btn_start = customtkinter.CTkButton(
            self.sidebar,
            text="▶️ START BOT",
            command=self.start_bot,
            fg_color="#27AE60",
            hover_color="#229954",
            height=45,
            font=("Arial", 14, "bold")
        )
        self.btn_start.grid(row=2, column=0, padx=20, pady=10, sticky="ew")

        # Pause Button
        self.btn_pause = customtkinter.CTkButton(
            self.sidebar,
            text="⏸️ PAUSE",
            command=self.toggle_pause,
            state="disabled",
            fg_color="#F39C12",
            hover_color="#E67E22",
            height=40,
            font=("Arial", 13, "bold")
        )
        self.btn_pause.grid(row=3, column=0, padx=20, pady=10, sticky="ew")

        # Settings Label
        settings_label = customtkinter.CTkLabel(
            self.sidebar,
            text="⚙️ Bot Settings",
            font=("Arial", 14, "bold"),
            anchor="w"
        )
        settings_label.grid(row=4, column=0, padx=20, pady=(20, 5), sticky="w")

        # Headless Mode Checkbox
        self.chk_headless = customtkinter.CTkCheckBox(
            self.sidebar, 
            text="🔲 Headless (Background)",
            font=("Arial", 12, "bold"),
            text_color="#00D9FF"
        )
        self.chk_headless.grid(row=5, column=0, padx=20, pady=8, sticky="w")
        self.chk_headless.select()  # Enable by default

        # Attack Checkbox
        self.chk_attack = customtkinter.CTkCheckBox(
            self.sidebar, 
            text="⚔️ Attack Mobs/NPCs",
            font=("Arial", 12)
        )
        self.chk_attack.grid(row=6, column=0, padx=20, pady=5, sticky="w")

        # Gather Checkbox
        self.chk_gather = customtkinter.CTkCheckBox(
            self.sidebar, 
            text="⛏️ Gather Resources",
            font=("Arial", 12)
        )
        self.chk_gather.grid(row=7, column=0, padx=20, pady=5, sticky="w")

        # Event Checkbox
        self.chk_event = customtkinter.CTkCheckBox(
            self.sidebar, 
            text="🎁 Grab Event Items",
            font=("Arial", 12)
        )
        self.chk_event.grid(row=8, column=0, padx=20, pady=5, sticky="w")

        # API Status Frame
        api_frame = customtkinter.CTkFrame(self.sidebar, fg_color="#2a2a2a")
        api_frame.grid(row=9, column=0, padx=20, pady=20, sticky="ew")

        customtkinter.CTkLabel(
            api_frame,
            text="🔑 API Status",
            font=("Arial", 12, "bold")
        ).pack(pady=5)

        self.lbl_api_status = customtkinter.CTkLabel(
            api_frame,
            text="Available: 11/11",
            font=("Arial", 11),
            text_color="#2ECC71"
        )
        self.lbl_api_status.pack(pady=2)

        self.lbl_api_calls = customtkinter.CTkLabel(
            api_frame,
            text="Total Calls: 0",
            font=("Arial", 10),
            text_color="gray"
        )
        self.lbl_api_calls.pack(pady=2)

        # Footer
        footer = customtkinter.CTkLabel(
            self.sidebar,
            text="👨‍💻 Dev: LuNu\n🚀 Ver: Headless Pro\n📦 11-Key System",
            font=("Arial", 9),
            text_color="gray",
            justify="center"
        )
        footer.grid(row=13, column=0, pady=20)

    def create_main_panel(self):
        self.main = customtkinter.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main.grid_rowconfigure(3, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        # Player Info Card
        self.create_player_info()
        
        # Stats Dashboard
        self.create_stats_dashboard()

        # Progress Bars
        self.create_progress_bars()

        # Log Console
        log_label = customtkinter.CTkLabel(
            self.main,
            text="📋 Activity Log",
            font=("Arial", 14, "bold"),
            anchor="w"
        )
        log_label.grid(row=3, column=0, sticky="w", pady=(10, 5))

        self.log_box = customtkinter.CTkTextbox(
            self.main,
            font=("Consolas", 11),
            fg_color="#1a1a1a",
            border_width=2,
            border_color="#00D9FF"
        )
        self.log_box.grid(row=4, column=0, sticky="nsew", pady=(0, 0))
        self.log_box.configure(state="disabled")

    def create_player_info(self):
        info_frame = customtkinter.CTkFrame(self.main, fg_color="#1E1E1E", corner_radius=15)
        info_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        info_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.lbl_player_name = customtkinter.CTkLabel(
            info_frame,
            text="👤 Player: Loading...",
            font=("Arial", 16, "bold"),
            text_color="#00D9FF"
        )
        self.lbl_player_name.grid(row=0, column=0, padx=20, pady=15, sticky="w")

        self.lbl_gold = customtkinter.CTkLabel(
            info_frame,
            text="💰 Gold: 0",
            font=("Arial", 14),
            text_color="#F1C40F"
        )
        self.lbl_gold.grid(row=0, column=1, padx=10, pady=15)

        self.lbl_level = customtkinter.CTkLabel(
            info_frame,
            text="⭐ Level: 0",
            font=("Arial", 14),
            text_color="#9B59B6"
        )
        self.lbl_level.grid(row=0, column=2, padx=20, pady=15, sticky="e")

    def create_stats_dashboard(self):
        stats_frame = customtkinter.CTkFrame(self.main, fg_color="transparent")
        stats_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.create_stat_card(stats_frame, "👣 Steps", "steps", 0, "#3498DB")
        self.create_stat_card(stats_frame, "🤖 CAPTCHA", "captcha", 1, "#E74C3C")
        self.create_stat_card(stats_frame, "⚔️ Attacks", "attack", 2, "#E67E22")
        self.create_stat_card(stats_frame, "⛏️ Gathered", "gathered", 3, "#27AE60")

    def create_stat_card(self, parent, title, key, column, color):
        card = customtkinter.CTkFrame(parent, fg_color="#1E1E1E", corner_radius=10)
        card.grid(row=0, column=column, padx=5, sticky="ew")

        customtkinter.CTkLabel(
            card,
            text=title,
            font=("Arial", 11),
            text_color="gray"
        ).pack(pady=(10, 2))

        label = customtkinter.CTkLabel(
            card,
            text="0",
            font=("Arial", 24, "bold"),
            text_color=color
        )
        label.pack(pady=(0, 10))

        setattr(self, f"lbl_{key}", label)

    def create_progress_bars(self):
        bars_frame = customtkinter.CTkFrame(self.main, fg_color="#1E1E1E", corner_radius=15)
        bars_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        bars_frame.grid_columnconfigure(1, weight=1)

        # Energy Bar
        customtkinter.CTkLabel(
            bars_frame,
            text="⚡ Energy:",
            font=("Arial", 12),
            width=80,
            anchor="w"
        ).grid(row=0, column=0, padx=(15, 5), pady=10)

        self.bar_energy = customtkinter.CTkProgressBar(
            bars_frame,
            height=20,
            progress_color="#2ECC71",
            fg_color="#2a2a2a"
        )
        self.bar_energy.grid(row=0, column=1, sticky="ew", padx=10, pady=10)
        self.bar_energy.set(0)

        self.lbl_energy = customtkinter.CTkLabel(
            bars_frame,
            text="0/0",
            font=("Arial", 11),
            width=60
        )
        self.lbl_energy.grid(row=0, column=2, padx=(5, 15), pady=10)

        # Quest Points Bar
        customtkinter.CTkLabel(
            bars_frame,
            text="🎯 QP:",
            font=("Arial", 12),
            width=80,
            anchor="w"
        ).grid(row=1, column=0, padx=(15, 5), pady=10)

        self.bar_qp = customtkinter.CTkProgressBar(
            bars_frame,
            height=20,
            progress_color="#9B59B6",
            fg_color="#2a2a2a"
        )
        self.bar_qp.grid(row=1, column=1, sticky="ew", padx=10, pady=10)
        self.bar_qp.set(0)

        self.lbl_qp = customtkinter.CTkLabel(
            bars_frame,
            text="0/0",
            font=("Arial", 11),
            width=60
        )
        self.lbl_qp.grid(row=1, column=2, padx=(5, 15), pady=10)

    # ==================== LOGGING ====================
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.after(0, self._update_log, f"[{timestamp}] {msg}\n")

    def _update_log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ==================== API MONITORING ====================
    def start_api_monitor(self):
        threading.Thread(target=self.run_api_monitor, daemon=True).start()
        threading.Thread(target=self.update_api_status, daemon=True).start()

    def run_api_monitor(self):
        url = "https://api.simple-mmo.com/v1/player/me"
        while self.bot_running:
            try:
                res = requests.post(
                    url, 
                    data={"api_key": SMMO_GAME_API_KEY}, 
                    timeout=5
                )
                if res.status_code == 200 and "error" not in res.json():
                    data = res.json()
                    self.after(0, self.update_player_ui, data)
            except:
                pass
            time.sleep(10)

    def update_player_ui(self, data):
        name = data.get('name', 'Unknown')
        level = data.get('level', 0)
        gold = data.get('gold', 0)
        
        self.lbl_player_name.configure(text=f"👤 {name}")
        self.lbl_level.configure(text=f"⭐ Lv.{level}")
        self.lbl_gold.configure(text=f"💰 {gold:,}")

        # Energy
        cur_energy = data.get('energy', 0)
        max_energy = data.get('maximum_energy', 1)
        self.bar_energy.set(cur_energy / max_energy if max_energy > 0 else 0)
        self.lbl_energy.configure(text=f"{cur_energy}/{max_energy}")

        # Quest Points
        cur_qp = data.get('quest_points', 0)
        max_qp = data.get('maximum_quest_points', 1)
        self.bar_qp.set(cur_qp / max_qp if max_qp > 0 else 0)
        self.lbl_qp.configure(text=f"{cur_qp}/{max_qp}")

    def update_api_status(self):
        while self.bot_running:
            key_stats = api_key_manager.get_stats()
            total_used = sum(stat['used'] for stat in key_stats.values())
            exhausted = sum(1 for stat in key_stats.values() if stat['quota_exhausted'])
            available = 11 - exhausted

            self.after(0, self._update_api_labels, available, total_used)
            time.sleep(5)

    def _update_api_labels(self, available, total_used):
        color = "#2ECC71" if available > 7 else "#F39C12" if available > 3 else "#E74C3C"
        self.lbl_api_status.configure(
            text=f"Available: {available}/11",
            text_color=color
        )
        self.lbl_api_calls.configure(text=f"Total Calls: {total_used}")

    # ==================== BOT CONTROLS ====================
    def toggle_pause(self):
        with self.pause_condition:
            if self.paused:
                self.paused = False
                self.btn_pause.configure(text="⏸️ PAUSE", fg_color="#F39C12")
                self.pause_condition.notify_all()
                self.log("▶️ Bot Resumed")
            else:
                self.paused = True
                self.btn_pause.configure(text="▶️ RESUME", fg_color="#27AE60")
                self.log("⏸️ Bot Paused")

    def check_pause(self):
        with self.pause_condition:
            while self.paused:
                self.pause_condition.wait()

    def login_thread(self):
        self.btn_login.configure(state="disabled", text="⏳ Logging in...")
        threading.Thread(target=self.perform_login, daemon=True).start()

    def perform_login(self):
        self.log("🔐 Opening browser for authentication...")
        try:
            constrains = Screen(max_width=1920, max_height=1080)
            with Camoufox(humanize=True, screen=constrains, headless=False) as browser:
                context = browser.new_context()
                page = context.new_page()
                page.goto('https://web.simple-mmo.com/login')
                
                while True:
                    if page.is_visible('text=The start of your journey'):
                        time.sleep(2)
                        break
                
                cookies = context.cookies()
                with open("session.json", "w") as f:
                    json.dump(cookies, f, indent=2)
                
                browser.close()
                self.log("✅ Login successful! Session saved.")
                pyautogui.alert("Login Saved Successfully!\n\nYou can now start the bot.")
                
        except Exception as e:
            self.log(f"❌ Login Error: {str(e)}")
        finally:
            self.after(0, lambda: self.btn_login.configure(
                state="normal",
                text="🔐 Login & Save Session"
            ))

    def start_bot(self):
        if self.bot_running:
            return
        
        self.bot_running = True
        self.btn_start.configure(state="disabled", text="🟢 Running...")
        self.btn_pause.configure(state="normal")
        
        self.start_api_monitor()
        threading.Thread(target=self.run_bot_logic, daemon=True).start()

    # ==================== CORE BOT LOGIC ====================
    def run_bot_logic(self):
        self.log("🚀 Starting bot automation...")
        
        is_headless = self.chk_headless.get() == 1
        mode_text = "HEADLESS (Background - Optimized)" if is_headless else "VISIBLE"
        self.log(f"🔧 Browser Mode: {mode_text}")
        
        try:
            with open('session.json', 'r') as f:
                cookies = json.load(f)
        except FileNotFoundError:
            self.log("❌ session.json not found! Please login first.")
            self.bot_running = False
            self.after(0, lambda: self.btn_start.configure(state="normal", text="▶️ START BOT"))
            return

        constrains = Screen(min_width=1920, min_height=1080, max_width=1920, max_height=1080)
        
        with Camoufox(
            humanize=True, 
            screen=constrains, 
            headless=is_headless,
            geoip=False, 
        ) as browser:
            
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            context.add_cookies(cookies)
            page = context.new_page()
            
            try:
                page.goto("https://web.simple-mmo.com/travel", timeout=60000)
            except Exception as e:
                self.log(f"⚠️ Load timeout, retrying...")
                page.reload()

            if is_headless:
                self.log("✅ Headless mode active: Running silently")
            else:
                self.log("✅ Browser visible")

            while self.bot_running:
                self.check_pause()
                
                try:
                    # --- CAPTCHA Detection ---
                    if page.is_visible("text=I'm a person! Promise!", timeout=2000):
                        solved = self.solve_captcha(page, context)
                        
                        if solved:
                            self.counts['captcha'] += 1
                            self.log(f"✅ AI Solved! Total: {self.counts['captcha']}")
                            self.update_stat('captcha', self.counts['captcha'])
                            page.click("button:has-text('Take a step')", timeout=20000, force=True)
                            time.sleep(5)
                        else:
                            self.counts['captcha_skipped'] += 1
                            self.log(f"⏭️ AI Failed/Quota - Cooling down 60s...")
                            time.sleep(60)
                            api_key_manager.check_and_reset_quotas()
                            page.reload()
                            continue

                    # --- TAKE STEP ---
                    step_btn = page.locator("button:has-text('Take a step')").nth(2)
                    if step_btn.is_visible(timeout=5000):
                        step_btn.click(force=True)
                        self.counts['steps'] += 1
                        self.update_stat('steps', self.counts['steps'])
                    else:
                        if "travel" not in page.url:
                            page.goto("https://web.simple-mmo.com/travel")
                        
                    time.sleep(random.uniform(2.5, 3.5))
                    
                    # --- GATHER RESOURCES ---
                    if self.chk_gather.get() == 1:
                        content = page.content()
                        for action in ['Salvage', 'Mine', 'Chop', 'Catch']:
                            if action in content:
                                if page.is_visible(f'button:has-text("{action}")'):
                                    self.log(f"⛏️ Action: {action}")
                                    page.click(f'button:has-text("{action}")', force=True)
                                    self.perform_action(page)
                                    self.counts[action.lower()] += 1
                                    total = sum([self.counts['mine'], self.counts['salvage'], self.counts['chop'], self.counts['catch']])
                                    self.update_stat('gathered', total)
                                    break
                    
                    # --- GRAB EVENTS ---
                    if self.chk_event.get() == 1:
                        if page.is_visible('button:has-text("Grab")'):
                            self.log("🎁 Event Item Found!")
                            page.click('button:has-text("Grab")', force=True)
                            self.perform_action(page)
                            self.counts['grab'] += 1

                    # --- ATTACK MOBS ---
                    if self.chk_attack.get() == 1:
                        if page.is_visible('a:has-text("Attack")'):
                            self.log("⚔️ Enemy spotted! Engaging...")
                            page.click('a:has-text("Attack")', force=True)
                            self.counts['attack'] += 1
                            self.update_stat('attack', self.counts['attack'])
                            
                            try:
                                page.wait_for_selector('button:has-text("Attack")', timeout=5000)
                                while True:
                                    if page.is_visible('text=You won'):
                                        break
                                    if page.is_visible('text=You have been defeated'):
                                        self.log("💀 Defeated!")
                                        break
                                        
                                    if page.is_visible('button:has-text("Attack")'):
                                        page.click('button:has-text("Attack")', force=True)
                                        time.sleep(random.uniform(1.5, 2.5))
                                    else:
                                        break
                                
                                if page.is_visible('text=Return'):
                                     page.click('text=Return', force=True)
                                elif "travel" not in page.url:
                                     page.goto("https://web.simple-mmo.com/travel")

                            except:
                                page.goto("https://web.simple-mmo.com/travel")

                except QuotaExhaustedException:
                    continue
                except Exception as e:
                    err_msg = str(e)
                    if "Target closed" in err_msg:
                        self.log("❌ Browser closed unexpectedly.")
                        break
                    time.sleep(2)

            browser.close()

    def perform_action(self, page):
        time.sleep(random.uniform(10, 12))
        if page.is_visible('text=Gather'):
            page.click('text=Gather', force=True)
        
        time.sleep(random.uniform(10, 12))
        
        while page.is_visible('text=Press here to gather', timeout=5000):
            page.click('text=Press here to gather', force=True)
            time.sleep(random.uniform(10, 12))
        
        if page.is_visible('text=Press here to close'):
            page.click('text=Press here to close', force=True)
        time.sleep(random.uniform(4, 5))

    # ==================== IMPROVED CAPTCHA SOLVER ====================
    def solve_captcha(self, page, context):
        screenshot_path = f'div_screenshot_{time.time()}.png'
        output_path = f'image_with_text_{time.time()}.png'
        new_page = None
        
        try:
            self.log("🤖 CAPTCHA detected - Initiating sequence...")
            
            # 1. Click mở popup (Thêm delay giống người)
            time.sleep(random.uniform(1.5, 2.5))
            
            with context.expect_page(timeout=15000) as new_page_info:
                btn = page.locator("text=I'm a person! Promise!")
                if btn.is_visible():
                    btn.click(force=True)
                else:
                    page.click("button:has-text('Promise')", force=True)
            
            new_page = new_page_info.value
            
            # 2. QUAN TRỌNG: Đợi load + Đợi Render (Delay 5s)
            new_page.wait_for_load_state("domcontentloaded")
            new_page.wait_for_load_state("networkidle")
            
            self.log("⏳ Waiting for visuals to render (5s)...")
            time.sleep(5)  # Delay cho render ảnh trong headless mode

            # 3. Lấy text câu hỏi
            try:
                new_page.wait_for_selector('div.text-2xl', state='visible', timeout=10000)
                time.sleep(1) # Đợi font render
                div_text = new_page.inner_text('div.text-2xl', timeout=5000)
            except Exception:
                try:
                    div_text = new_page.locator("div:has-text('resembles')").inner_text()
                except:
                    div_text = "Unknown"

            self.log(f"❓ Question: {div_text}")

            # 4. Chụp ảnh CAPTCHA
            div_selector = 'div.grid.grid-cols-4.gap-2.items-center.justify-center.max-w-sm.mx-auto'
            new_page.wait_for_selector(div_selector, state='visible', timeout=10000)
            
            # [FIXED] Xóa dòng wait_for_element_state gây lỗi
            # Chỉ dùng sleep nhẹ để đảm bảo element đã sẵn sàng
            time.sleep(1) 
            div = new_page.locator(div_selector)
            
            div.screenshot(path=screenshot_path)
            
            add_text_to_image(
                screenshot_path, 
                f"Which of these resembles {div_text}, give number (numeric)", 
                output_path=output_path
            )
            
            try:
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
            except:
                pass
            
            # 5. Gửi cho AI
            with Image.open(output_path) as img:
                prompt = f"Look at this image and tell me which numbered option (1-4) best resembles '{div_text}'. Respond only with the number."
                
                def gemini_operation(api_key):
                    genai.configure(api_key=api_key)
                    try:
                        # [FIXED] Dùng 'gemini-flash-latest' vì test log xác nhận nó chạy tốt nhất
                        model = genai.GenerativeModel('gemini-flash-latest')
                        response = model.generate_content([prompt, img], safety_settings=safe)
                        return response.text
                    except Exception as e:
                        # Fallback
                        if "not found" in str(e).lower() or "not available" in str(e).lower():
                            model = genai.GenerativeModel('gemini-2.0-flash-exp')
                            response = model.generate_content([prompt, img], safety_settings=safe)
                            return response.text
                        raise e
                
                try:
                    self.log("🧠 Sending to Gemini AI...")
                    response_text = api_key_manager.try_all_keys(gemini_operation)
                except QuotaExhaustedException as e:
                    self.log(f"⚠️ {str(e)}")
                    try:
                        if os.path.exists(output_path):
                            os.remove(output_path)
                    except:
                        pass
                    if new_page:
                        new_page.close()
                    return False
            
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except:
                pass
            
            # 6. Xử lý kết quả
            result_text = remove_non_numbers(response_text)
            if not result_text:
                raise ValueError(f"AI invalid text: {response_text}")
                
            result = int(result_text)
            self.log(f"🤖 AI Answer: Option {result}")
            
            buttons = new_page.query_selector_all('.grid.grid-cols-4.gap-2.items-center.justify-center.max-w-sm.mx-auto button')
            if 1 <= result <= len(buttons):
                time.sleep(random.uniform(1, 2)) # Delay trước khi click
                buttons[result - 1].click()
                time.sleep(3) # Delay sau khi click để gửi request
            else:
                raise ValueError(f"Invalid result number: {result}")
            
            try:
                new_page.close()
            except:
                pass
            return True
            
        except Exception as e:
            self.log(f"❌ CAPTCHA Error: {str(e)[:100]}")
            for file in [screenshot_path, output_path]:
                try:
                    if os.path.exists(file):
                        os.remove(file)
                except:
                    pass
            if new_page:
                try:
                    new_page.close()
                except:
                    pass
            return False

    def update_stat(self, stat_name, value):
        label_map = {
            'steps': self.lbl_steps,
            'captcha': self.lbl_captcha,
            'attack': self.lbl_attack,
            'gathered': self.lbl_gathered
        }
        if stat_name in label_map:
            self.after(0, lambda: label_map[stat_name].configure(text=str(value)))

# ==================== MAIN ====================
if __name__ == "__main__":
    app = App()
    app.mainloop()