import os
# ==================== FIX LOG RÁC SYSTEM ====================
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GLOG_minloglevel'] = '2'
# ==========================================================

import customtkinter
import threading
import time
import requests
import json
import random
import pyautogui
import ssl
import warnings
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageTk, ImageDraw, ImageFont
from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen
import google.generativeai as genai

# ==================== CẤU HÌNH NGƯỜI DÙNG (ĐIỀN VÀO ĐÂY) ====================

# 1. API GAME SMMO (Lấy tại: https://web.simple-mmo.com/api-key)
SMMO_GAME_API_KEY = "YOUR_SMMO_GAME_KEY_HERE"

# 2. DANH SÁCH GEMINI API KEYS (Càng nhiều càng tốt để xoay vòng)
GEMINI_API_KEYS = [
    'your_gemini_api_key_1_here', 

]

# ============================================================================

ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings("ignore")
pyautogui.FAILSAFE = False

# Theme Colors
THEME_BG = "#0F0F0F"
THEME_CARD = "#1A1A1A"
THEME_ACCENT = "#00D9FF"
THEME_TEXT_SEC = "#888888"

# ==================== API KEY MANAGER (ROTATION ENGINE) ====================
class APIKeyManager:
    def __init__(self, api_keys):
        self.api_keys = api_keys
        self.current_index = 0
        self.key_stats = {key: {'used': 0, 'errors': 0, 'quota_exhausted': False} for key in api_keys}
        self.lock = threading.Lock()

    def get_next_key(self):
        with self.lock:
            available = [k for k in self.api_keys if not self.key_stats[k]['quota_exhausted']]
            if not available:
                # Nếu hết sạch key thì reset lại tất cả để thử vận may
                for k in self.api_keys: self.key_stats[k]['quota_exhausted'] = False
                return self.api_keys[random.randint(0, len(self.api_keys)-1)]
            
            # Round-robin
            for _ in range(len(self.api_keys)):
                key = self.api_keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                if not self.key_stats[key]['quota_exhausted']:
                    return key
            return available[0]

    def mark_key_error(self, key, error_msg):
        with self.lock:
            self.key_stats[key]['errors'] += 1
            if '429' in str(error_msg) or 'quota' in str(error_msg).lower() or '403' in str(error_msg):
                self.key_stats[key]['quota_exhausted'] = True

    def try_all_keys(self, operation_func):
        attempts = 0
        last_error = ""
        max_attempts = len(self.api_keys) + 2
        
        while attempts < max_attempts:
            key = self.get_next_key()
            try:
                result = operation_func(key)
                with self.lock: self.key_stats[key]['used'] += 1
                return result
            except Exception as e:
                last_error = str(e)
                self.mark_key_error(key, last_error)
                attempts += 1
                time.sleep(0.5)
        
        raise Exception(f"All Keys Failed. Last: {last_error[:50]}")

api_key_manager = APIKeyManager(GEMINI_API_KEYS)

# ==================== HELPER ====================
safe = [{"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]

def add_text_to_image(image_path, text, output_path):
    try:
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        try: font = ImageFont.truetype("arial.ttf", 20)
        except: font = ImageFont.load_default()
        
        x, y = 10, 10
        # Outline text effect
        for adj in [(x-1, y-1), (x+1, y-1), (x-1, y+1), (x+1, y+1)]:
            draw.text(adj, text, font=font, fill="black")
        draw.text((x, y), text, fill="white", font=font)
        image.save(output_path)
    except: pass

def remove_non_numbers(text):
    return ''.join(char for char in text if char.isdigit())

# ==================== BOT WORKER (CORE LOGIC) ====================
class BotWorker:
    def __init__(self, ui_app):
        self.ui = ui_app
        self.running = False
        self.paused = False
        self.browser_instance = None
        
        # Watchdog variables
        self.last_heartbeat = time.time()
        self.last_action_time = time.time()
        self.restart_requested = False
        
        self.counts = {'steps': 0, 'captcha': 0, 'captcha_skipped': 0, 'attack': 0, 'gather': 0, 'event': 0}

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.ui.log(f"[{timestamp}] {msg}")

    def update_heartbeat(self, action_done=False):
        """Cập nhật trạng thái sống để không bị Watchdog giết"""
        self.last_heartbeat = time.time()
        if action_done:
            self.last_action_time = time.time()

    def smart_sleep(self, seconds):
        """Ngủ nhưng vẫn báo cáo heartbeat"""
        for _ in range(int(seconds)):
            if not self.running or self.restart_requested: break
            time.sleep(1)
            self.update_heartbeat()
        # Ngủ thêm phần lẻ nếu có (float)
        remaining = seconds - int(seconds)
        if remaining > 0: time.sleep(remaining)

    def start(self):
        if self.running: return
        self.running = True
        self.restart_requested = False
        self.ui.set_status("STARTING", "#2ECC71")
        
        # Start Threads
        threading.Thread(target=self.run_logic_wrapper, daemon=True).start()
        threading.Thread(target=self.api_monitor_loop, daemon=True).start()
        threading.Thread(target=self.watchdog_loop, daemon=True).start()

    def watchdog_loop(self):
        """Giám sát bot, nếu treo quá lâu sẽ restart trình duyệt"""
        while self.running:
            time.sleep(10)
            if self.paused: continue
            
            # Nếu 3 phút không có heartbeat -> Force Restart
            if time.time() - self.last_heartbeat > 180:
                self.log("🚨 WATCHDOG: Bot frozen! Force restarting browser...")
                self.force_restart_browser()

    def force_restart_browser(self):
        self.restart_requested = True
        self.last_heartbeat = time.time() + 60 # Gia hạn thời gian sống
        try:
            if self.browser_instance: self.browser_instance.close()
        except: pass

    def api_monitor_loop(self):
        """Cập nhật thông tin nhân vật liên tục"""
        url = "https://api.simple-mmo.com/v1/player/me"
        while self.running:
            try:
                res = requests.post(url, data={"api_key": SMMO_GAME_API_KEY}, timeout=5)
                if res.status_code == 200:
                    self.ui.update_player_data(res.json())
            except: pass
            time.sleep(15)

    def run_logic_wrapper(self):
        while self.running:
            self.restart_requested = False
            self.last_action_time = time.time()
            self.last_heartbeat = time.time()
            
            try:
                self.run_browser_session()
            except Exception as e:
                self.log(f"⚠️ Browser Crash: {str(e)[:50]}")
            
            if self.running:
                self.log("♻️ Restarting session in 5s...")
                time.sleep(5)
        
        self.ui.set_status("STOPPED", "#E74C3C")

    def run_browser_session(self):
        if not os.path.exists('session.json'):
            self.log("❌ No session file! Please Login first.")
            self.running = False
            return

        is_headless = self.ui.chk_headless.get() == 1
        mode_str = "GHOST MODE" if is_headless else "VISIBLE MODE"
        self.log(f"🚀 Launching {mode_str}...")
        self.ui.set_status("RUNNING", "#00D9FF")

        with open('session.json', 'r') as f: cookies = json.load(f)
        constrains = Screen(min_width=1280, min_height=720)
        
        with Camoufox(humanize=True, screen=constrains, headless=is_headless) as browser:
            self.browser_instance = browser
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            context.add_cookies(cookies)
            page = context.new_page()
            
            try: page.goto("https://web.simple-mmo.com/travel", timeout=60000)
            except: page.reload()

            while self.running and not self.restart_requested:
                # Pause Handling
                while self.paused:
                    self.ui.set_status("PAUSED", "#F39C12")
                    self.update_heartbeat()
                    time.sleep(1)
                self.ui.set_status("RUNNING", "#00D9FF")
                self.update_heartbeat()

                try:
                    # 1. CHECK CAPTCHA
                    if page.is_visible("text=I'm a person! Promise!", timeout=2000):
                        if self.solve_captcha(page, context):
                            self.counts['captcha'] += 1
                            self.ui.update_stat('captcha', self.counts['captcha'])
                            self.log("✅ CAPTCHA SOLVED")
                            page.click("button:has-text('Take a step')", force=True)
                            self.update_heartbeat(action_done=True)
                            self.smart_sleep(5)
                        else:
                            self.counts['captcha_skipped'] += 1
                            self.log("❌ Captcha Error. Smart Sleep 60s...")
                            self.smart_sleep(60) # Ngủ an toàn
                            page.reload()
                            continue

                    # 2. TAKE STEP
                    btn = page.locator("button:has-text('Take a step')").nth(2)
                    if btn.is_visible():
                        btn.click(force=True)
                        self.counts['steps'] += 1
                        self.ui.update_stat('steps', self.counts['steps'])
                        self.update_heartbeat(action_done=True)
                    elif "travel" not in page.url:
                        page.goto("https://web.simple-mmo.com/travel")
                    
                    time.sleep(random.uniform(2.5, 3.5))

                    # 3. GATHER / EVENT
                    if self.ui.chk_gather.get() == 1:
                        # Ưu tiên Event
                        if page.is_visible('button:has-text("Grab")'):
                             if not page.is_visible('text=Your skill level isn\'t high enough'):
                                self.log("🎁 EVENT ITEM FOUND!")
                                page.click('button:has-text("Grab")', force=True)
                                self.perform_action(page)
                                self.counts['event'] += 1
                                self.ui.update_stat('event', self.counts['event'])
                                self.update_heartbeat(action_done=True)

                        # Gather thường
                        for act in ['Salvage', 'Mine', 'Chop', 'Catch']:
                            if page.is_visible(f'button:has-text("{act}")'):
                                self.log(f"⛏️ Gather: {act}")
                                page.click(f'button:has-text("{act}")', force=True)
                                self.perform_action(page)
                                self.counts['gather'] += 1
                                self.ui.update_stat('gather', self.counts['gather'])
                                self.update_heartbeat(action_done=True)
                                break

                    # 4. ATTACK
                    if self.ui.chk_attack.get() == 1 and page.is_visible('a:has-text("Attack")'):
                        self.log("⚔️ Engaging Enemy!")
                        page.click('a:has-text("Attack")', force=True)
                        self.counts['attack'] += 1
                        self.ui.update_stat('attack', self.counts['attack'])
                        self.handle_combat(page)
                        self.update_heartbeat(action_done=True)

                except Exception as e:
                    if "Target closed" in str(e): raise e
                    self.update_heartbeat() # Vẫn báo sống nếu chỉ là lỗi nhỏ
                    time.sleep(2)

            browser.close()

    def perform_action(self, page):
        self.smart_sleep(random.uniform(8, 10))
        if page.is_visible('text=Press here to gather'): page.click('text=Press here to gather', force=True)
        self.smart_sleep(random.uniform(8, 10))
        if page.is_visible('text=Press here to close'): page.click('text=Press here to close', force=True)

    def handle_combat(self, page):
        try:
            page.wait_for_selector('button:has-text("Attack")', timeout=5000)
            while page.is_visible('button:has-text("Attack")') and not page.is_visible('text=won') and not page.is_visible('text=defeated'):
                page.click('button:has-text("Attack")', force=True)
                time.sleep(random.uniform(1.5, 2.0))
            if page.is_visible('text=Return'): page.click('text=Return', force=True)
            else: page.goto("https://web.simple-mmo.com/travel")
        except: page.goto("https://web.simple-mmo.com/travel")

    def solve_captcha(self, page, context):
        p_in = f'cap_{time.time()}.png'
        p_out = f'sol_{time.time()}.png'
        new_page = None
        try:
            self.log("⚠️ Solving Captcha...")
            with context.expect_page(timeout=15000) as new_page_info:
                page.click("text=I'm a person! Promise!", force=True)
            new_page = new_page_info.value
            new_page.wait_for_load_state()
            self.smart_sleep(5) # Đợi render
            
            try: q_text = new_page.locator('div.text-2xl').inner_text(timeout=5000)
            except: q_text = "Unknown"

            div = new_page.locator('div.grid.grid-cols-4.gap-2.items-center.justify-center.max-w-sm.mx-auto')
            div.screenshot(path=p_in)
            add_text_to_image(p_in, f"Find: {q_text} (1-4)", p_out)

            with Image.open(p_out) as img:
                prompt = f"Look at this image and tell me which numbered option (1-4) best resembles '{q_text}'. Respond only with the number."
                
                def call_ai(k):
                    genai.configure(api_key=k)
                    # SỬ DỤNG GEMINI 2.5
                    try: return genai.GenerativeModel('gemini-2.5-flash').generate_content([prompt, img], safety_settings=safe).text
                    except: return genai.GenerativeModel('gemini-2.5-flash-lite').generate_content([prompt, img], safety_settings=safe).text
                
                res = api_key_manager.try_all_keys(call_ai)

            ans = int(remove_non_numbers(res))
            self.log(f"🤖 AI Answer: {ans}")
            btns = new_page.query_selector_all('.grid.grid-cols-4 button')
            if 1 <= ans <= len(btns):
                btns[ans-1].click()
                time.sleep(3)
                new_page.close()
                try: os.remove(p_in); os.remove(p_out)
                except: pass
                return True
        except Exception as e:
            self.log(f"❌ Cap Error: {str(e)[:40]}")
            try: os.remove(p_in); os.remove(p_out)
            except: pass
            if new_page: 
                try: new_page.close()
                except: pass
        return False

# ==================== MAIN GUI APP ====================
class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.title("SMMO SINGLE BOT V11 (ULTRA EDITION)")
        self.geometry("1100x750")
        customtkinter.set_appearance_mode("Dark")
        self.configure(fg_color=THEME_BG)
        
        self.setup_ui()
        self.bot = BotWorker(self)

    def setup_ui(self):
        # 1. SIDEBAR
        sidebar = customtkinter.CTkFrame(self, width=280, fg_color=THEME_CARD, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Title
        customtkinter.CTkLabel(sidebar, text="V11 ULTIMATE", font=("Impact", 32), text_color=THEME_ACCENT).pack(pady=(30, 5))
        customtkinter.CTkLabel(sidebar, text="SINGLE ACCOUNT", font=("Arial", 12), text_color=THEME_TEXT_SEC).pack(pady=(0, 20))

        # Status Badge
        self.status_badge = customtkinter.CTkButton(sidebar, text="READY", fg_color="#555", state="disabled", font=("Arial", 12, "bold"))
        self.status_badge.pack(pady=10, padx=20, fill="x")

        # Controls
        self.btn_login = customtkinter.CTkButton(sidebar, text="🔐 LOGIN", fg_color="#333", hover_color="#444", command=self.login_flow)
        self.btn_login.pack(pady=5, padx=20, fill="x")

        self.btn_start = customtkinter.CTkButton(sidebar, text="▶ START", fg_color="#00C853", hover_color="#009624", font=("Arial", 14, "bold"), command=self.start_bot)
        self.btn_start.pack(pady=5, padx=20, fill="x")

        self.btn_pause = customtkinter.CTkButton(sidebar, text="⏸ PAUSE", fg_color="#FFAB00", hover_color="#FF6F00", font=("Arial", 14, "bold"), command=self.pause_bot)
        self.btn_pause.pack(pady=5, padx=20, fill="x")

        # Settings
        st_frame = customtkinter.CTkFrame(sidebar, fg_color="transparent")
        st_frame.pack(pady=30, padx=20, fill="x")
        
        self.chk_headless = customtkinter.CTkSwitch(st_frame, text="Ghost Mode", progress_color=THEME_ACCENT, font=("Arial", 12, "bold"))
        self.chk_headless.select()
        self.chk_headless.pack(pady=5, anchor="w")
        
        self.chk_attack = customtkinter.CTkCheckBox(st_frame, text="Auto Attack", text_color="#DDD", fg_color="#FF9100", hover_color="#FF9100")
        self.chk_attack.select()
        self.chk_attack.pack(pady=5, anchor="w")
        
        self.chk_gather = customtkinter.CTkCheckBox(st_frame, text="Auto Gather", text_color="#DDD", fg_color="#B0BEC5", hover_color="#B0BEC5")
        self.chk_gather.select()
        self.chk_gather.pack(pady=5, anchor="w")

        # 2. MAIN PANEL
        main = customtkinter.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        
        # Player Info Card
        p_card = customtkinter.CTkFrame(main, fg_color=THEME_CARD, corner_radius=15)
        p_card.pack(fill="x", pady=(0, 15))
        
        self.lbl_name = customtkinter.CTkLabel(p_card, text="CONNECTING API...", font=("Segoe UI Black", 24), text_color="white")
        self.lbl_name.pack(pady=(15, 5))
        
        stat_row = customtkinter.CTkFrame(p_card, fg_color="transparent")
        stat_row.pack(pady=(0, 15))
        self.lbl_gold = customtkinter.CTkLabel(stat_row, text="💰 --", font=("Consolas", 16), text_color="#FFD700")
        self.lbl_gold.pack(side="left", padx=20)
        self.lbl_level = customtkinter.CTkLabel(stat_row, text="⭐ --", font=("Consolas", 16), text_color="#E040FB")
        self.lbl_level.pack(side="left", padx=20)

        # Bars
        bars = customtkinter.CTkFrame(p_card, fg_color="transparent")
        bars.pack(fill="x", padx=30, pady=(0, 20))
        
        def mk_bar(t, c, kv, kb):
            f = customtkinter.CTkFrame(bars, fg_color="transparent")
            f.pack(fill="x", pady=2)
            customtkinter.CTkLabel(f, text=t, width=30, font=("Arial", 10, "bold"), text_color="#888").pack(side="left")
            self.__dict__[kv] = customtkinter.CTkLabel(f, text="0/0", width=50, anchor="e", font=("Arial", 10), text_color="#888")
            self.__dict__[kv].pack(side="right")
            self.__dict__[kb] = customtkinter.CTkProgressBar(f, height=8, progress_color=c, fg_color="#333")
            self.__dict__[kb].pack(side="left", fill="x", expand=True, padx=10)
            self.__dict__[kb].set(0)

        mk_bar("HP", "#FF1744", 'lbl_hp_val', 'bar_hp')
        mk_bar("EN", "#00E676", 'lbl_en_val', 'bar_en')
        mk_bar("QP", "#2979FF", 'lbl_qp_val', 'bar_qp')

        # Stats Grid
        grid = customtkinter.CTkFrame(main, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 15))
        grid.columnconfigure((0,1,2,3), weight=1)

        def mk_stat(idx, tit, icon, col, key):
            f = customtkinter.CTkFrame(grid, fg_color=THEME_CARD, corner_radius=10)
            f.grid(row=0, column=idx, padx=5, sticky="ew")
            customtkinter.CTkLabel(f, text=f"{icon} {tit}", font=("Arial", 10), text_color="#AAA").pack(pady=(10,0))
            self.__dict__[key] = customtkinter.CTkLabel(f, text="0", font=("Arial", 22, "bold"), text_color=col)
            self.__dict__[key].pack(pady=(0, 10))

        mk_stat(0, "STEPS", "👣", "#00E5FF", 'stat_steps')
        mk_stat(1, "EVENTS", "🎁", "#FF4081", 'stat_event')
        mk_stat(2, "ATTACKS", "⚔️", "#FF9100", 'stat_attack')
        mk_stat(3, "CAPTCHA", "🤖", "#76FF03", 'stat_captcha')

        # Log
        self.log_box = customtkinter.CTkTextbox(main, font=("Consolas", 11), fg_color="#000", text_color="#00FF00")
        self.log_box.pack(fill="both", expand=True)
        self.log_box.configure(state="disabled")

    # === GUI METHODS ===
    def set_status(self, text, color):
        self.status_badge.configure(text=text, fg_color=color)

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def update_player_data(self, d):
        try:
            self.lbl_name.configure(text=d.get('name', 'Unknown').upper())
            self.lbl_gold.configure(text=f"💰 {d.get('gold',0):,}")
            self.lbl_level.configure(text=f"⭐ Lv.{d.get('level',0)}")
            
            # Bars
            cur_h, max_h = d.get('hp', 0), d.get('maximum_hp', 1)
            self.bar_hp.set(cur_h/max_h if max_h else 0)
            self.lbl_hp_val.configure(text=f"{cur_h}/{max_h}")

            cur_e, max_e = d.get('energy', 0), d.get('maximum_energy', 1)
            self.bar_en.set(cur_e/max_e if max_e else 0)
            self.lbl_en_val.configure(text=f"{cur_e}/{max_e}")

            cur_q, max_q = d.get('quest_points', 0), d.get('maximum_quest_points', 1)
            self.bar_qp.set(cur_q/max_q if max_q else 0)
            self.lbl_qp_val.configure(text=f"{cur_q}/{max_q}")
        except: pass

    def update_stat(self, key, val):
        k_map = {'steps': 'stat_steps', 'event': 'stat_event', 'attack': 'stat_attack', 'captcha': 'stat_captcha', 'gather': 'stat_steps'} # Gather dùng chung logic hiển thị nếu cần
        if key in k_map: self.__dict__[k_map[key]].configure(text=str(val))

    def login_flow(self):
        threading.Thread(target=self._perform_login, daemon=True).start()

    def _perform_login(self):
        self.btn_login.configure(state="disabled", text="LOGGING IN...")
        try:
            constrains = Screen(max_width=1920, max_height=1080)
            with Camoufox(humanize=True, screen=constrains, headless=False) as browser:
                context = browser.new_context()
                page = context.new_page()
                page.goto('https://web.simple-mmo.com/login')
                while True:
                    if page.is_visible('text=Travel') or page.is_visible('text=The start of your journey'):
                        break
                    time.sleep(1)
                with open("session.json", "w") as f:
                    json.dump(context.cookies(), f, indent=2)
                self.log("✅ Login Success! Session Saved.")
        except Exception as e: self.log(f"❌ Login Fail: {e}")
        self.btn_login.configure(state="normal", text="🔐 LOGIN")

    def start_bot(self):
        self.bot.start()

    def pause_bot(self):
        self.bot.paused = not self.bot.paused
        txt = "▶ RESUME" if self.bot.paused else "⏸ PAUSE"
        self.btn_pause.configure(text=txt)

if __name__ == "__main__":
    app = App()
    app.mainloop()