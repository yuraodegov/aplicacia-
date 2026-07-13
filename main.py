"""
HMI / FIZZZ WiFi OTA Uploader  -  Android (Kivy)
================================================
Android port of wifi_ota_gui.py. The whole networking/file backend is reused
verbatim; only the UI is rebuilt in Kivy and two Android-specific problems are
handled:

  1. AP has no internet -> Android would route traffic over mobile data, so we
     bind this process's sockets to the Wi-Fi network (bind_to_wifi()).
  2. Non-media .bin files -> need All-files access on Android 11+ to read them
     by path (requested at startup).

The bar's AP web server lives at 192.168.4.1. Flow (proven on element-p-hmi):
  POST http://192.168.4.1/ota/upload
       ?version=<ver>&sha256=<sha>&component=<fizzz|hmi>&transactionComplete=true

Build (on Linux/WSL):  buildozer android debug
Entry point MUST be named main.py for buildozer.
"""

import re
import time
import hashlib
import threading
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.utils import platform
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton

# ── CONFIG (identical to the desktop tool) ───────────────────────────────────
DEFAULT_HOST = "192.168.4.1"
INFO_PATH = "/ap?tk=tk&command=get_info"        # reachability ping only
UPLOAD_PATH = "/ota/upload"
PREPARE_PATH = "/ap?tk=tk&command=fota_prepare"  # official pre-upload step
SUCCESS_TEXT = "uploaded successfully"
CONNECT_TIMEOUT = 5
UPLOAD_TIMEOUT = 120
RETRY_ON_500 = 4
RETRY_WAIT_SEC = 8
REBOOT_WAIT_SEC = 120

HMI_PATTERN = "*hmi*enc*.bin"
HMI_PATTERN_FALLBACK = "*hmi*.bin"
ADDON_PATTERN = "addon-fizz*.bin"
RC_PATTERN = "RC*.bin"

# platform-aware paths (no Desktop on Android)
if platform == "android":
    DEFAULT_FOLDER = "/sdcard/Download/fota"
    LOG_FILE = Path("/sdcard/Download/wifi_ota_log.txt")
else:
    DEFAULT_FOLDER = str(Path.home() / "Desktop")
    LOG_FILE = Path.home() / "Desktop" / "wifi_ota_log.txt"

# colors
BG = (0.05, 0.07, 0.09, 1)
SURFACE = (0.09, 0.11, 0.13, 1)
ACCENT = (0, 0.86, 0.67, 1)
ACCENT2 = (0.38, 1, 0.84, 1)
WARN = (1, 0.82, 0.4, 1)
RED = (1, 0.42, 0.42, 1)
DIM = (0.55, 0.58, 0.62, 1)
WHITE = (0.94, 0.96, 0.99, 1)

TAG_COLOR = {"ok": ACCENT, "warn": WARN, "err": RED, "dim": WHITE}


def log_file(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ============================================================================
#  ANDROID GLUE
# ============================================================================

def bind_to_wifi(log=lambda *_: None) -> bool:
    """Force this process's sockets through the Wi-Fi network.

    The bar AP has no internet, so without this Android routes traffic over
    mobile data and requests to 192.168.4.1 fail. No-op off Android.
    """
    if platform != "android":
        return True
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        NetworkCapabilities = autoclass("android.net.NetworkCapabilities")
        activity = PythonActivity.mActivity
        cm = activity.getSystemService(Context.CONNECTIVITY_SERVICE)
        for net in cm.getAllNetworks():
            caps = cm.getNetworkCapabilities(net)
            if caps and caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI):
                cm.bindProcessToNetwork(net)
                log("Bound to Wi-Fi network (AP route forced)")
                return True
        log("No Wi-Fi network found - connect to the bar AP first")
        return False
    except Exception as e:
        log(f"bind_to_wifi error: {e}")
        return False


def request_android_permissions(log=lambda *_: None):
    """Runtime permissions + All-files access (Android 11+) for reading .bin."""
    if platform != "android":
        return
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([
            Permission.INTERNET, Permission.ACCESS_NETWORK_STATE,
            Permission.ACCESS_WIFI_STATE, Permission.CHANGE_WIFI_STATE,
            Permission.CHANGE_NETWORK_STATE, Permission.ACCESS_FINE_LOCATION,
            Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE,
        ])
    except Exception as e:
        log(f"permission request failed: {e}")
    try:
        from jnius import autoclass
        Build = autoclass("android.os.Build$VERSION")
        Environment = autoclass("android.os.Environment")
        if Build.SDK_INT >= 30 and not Environment.isExternalStorageManager():
            Intent = autoclass("android.content.Intent")
            Settings = autoclass("android.provider.Settings")
            Uri = autoclass("android.net.Uri")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            act = PythonActivity.mActivity
            intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
            intent.setData(Uri.parse("package:" + act.getPackageName()))
            act.startActivity(intent)
            log("Grant 'All files access' in the opened settings, then return.")
    except Exception as e:
        log(f"all-files-access request failed: {e}")


# ============================================================================
#  BACKEND  (reused verbatim from the desktop tool)
# ============================================================================

def sha256_of_file(path: Path) -> str:
    """Return the lowercase hex sha256 of a file (streamed, low memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def version_from_name(name: str, component: str) -> str:
    """Pull the version out of a firmware filename."""
    groups = re.findall(r"\d{1,2}\.\d{2,3}\.\d{2,3}", name)
    if not groups:
        return ""
    if component == "hmi":
        for g in groups:
            if g.startswith(("0.0", "0.1", "1.0")) and not g.startswith("00."):
                return g
        for g in groups:
            if not g.startswith("00."):
                return g
        return groups[0]
    if component == "rc":
        return groups[0]
    for g in groups:
        if g.startswith("00."):
            return g
    return groups[-1]


def find_firmware(folder: Path) -> dict:
    """Locate HMI / addon / RC .bin files in a folder (recursive)."""
    result = {"hmi": None, "addon": None, "rc": None}
    addon = sorted(folder.rglob(ADDON_PATTERN), key=lambda p: len(p.parts))
    if addon:
        result["addon"] = addon[0]
    rc = sorted(folder.rglob(RC_PATTERN), key=lambda p: len(p.parts))
    if rc:
        result["rc"] = rc[0]
    hmi = sorted(folder.rglob(HMI_PATTERN), key=lambda p: len(p.parts))
    if not hmi:
        hmi = sorted((p for p in folder.rglob(HMI_PATTERN_FALLBACK)
                      if "addon" not in p.name.lower()),
                     key=lambda p: len(p.parts))
    if hmi:
        result["hmi"] = hmi[0]
    return result


def ping_bar(host: str) -> tuple:
    """Reachability check. Any HTTP reply means the bar is up."""
    url = f"http://{host}{INFO_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=CONNECT_TIMEOUT) as resp:
            body = resp.read().decode(errors="ignore").strip()
            return True, f"HTTP {resp.status}: {body or '(empty)'}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def prepare_fota(host: str) -> tuple:
    """Send the official pre-upload step: command=fota_prepare (best-effort)."""
    url = f"http://{host}{PREPARE_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=CONNECT_TIMEOUT) as resp:
            body = resp.read().decode(errors="ignore").strip()
            return True, f"HTTP {resp.status}: {body or '(empty)'}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def build_upload_url(host, version, sha, component, transaction_complete) -> str:
    """Assemble the /ota/upload URL with query params in the proven order."""
    url = f"http://{host}{UPLOAD_PATH}?version={version}&sha256={sha}"
    if component:
        url += f"&component={component}"
    if transaction_complete:
        url += "&transactionComplete=true"
    return url


def upload_once(host, file_path, version, sha, component, transaction_complete):
    """Single POST of the binary. Returns (http_code, body, ok)."""
    url = build_upload_url(host, version, sha, component, transaction_complete)
    data = Path(file_path).read_bytes()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
            body = resp.read().decode(errors="ignore").strip()
            ok = SUCCESS_TEXT in body.lower()
            return resp.status, body, ok
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="ignore").strip()
        except Exception:
            pass
        return e.code, body or e.reason, False
    except Exception as e:
        return None, str(e), False


# ============================================================================
#  UI HELPERS
# ============================================================================

def _mk_label(text, color=DIM, bold=False, size=15, halign="left"):
    lbl = Label(text=text, color=color, font_size=f"{size}sp",
                bold=bold, halign=halign, valign="middle",
                size_hint_y=None, height="26dp")
    lbl.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
    return lbl


def _mk_input(text):
    return TextInput(text=text, multiline=False, size_hint_y=None, height="42dp",
                     background_color=SURFACE, foreground_color=WHITE,
                     cursor_color=WHITE, font_size="15sp", padding=[8, 10])


def _mk_button(text, on_press, bg=ACCENT, fg=(0, 0, 0, 1), size=16):
    b = Button(text=text, size_hint_y=None, height="52dp",
               background_normal="", background_color=bg, color=fg,
               font_size=f"{size}sp", bold=True)
    b.bind(on_press=on_press)
    return b


# ============================================================================
#  APP
# ============================================================================

class OtaApp(App):
    def build(self):
        self.title = "HMI / FIZZZ WiFi OTA"
        Window.clearcolor = BG

        self.hmi_path = None
        self.addon_path = None
        self.rc_path = None
        self._busy = False

        root = BoxLayout(orientation="vertical", padding="12dp", spacing="6dp")

        root.add_widget(_mk_label("HMI / FIZZZ WiFi OTA  ->  192.168.4.1",
                                  color=ACCENT, bold=True, size=19))

        # host + folder inputs
        form = GridLayout(cols=1, size_hint_y=None, spacing="4dp")
        form.bind(minimum_height=form.setter("height"))
        form.add_widget(_mk_label("Bar IP:"))
        self.host_in = _mk_input(DEFAULT_HOST)
        form.add_widget(self.host_in)
        form.add_widget(_mk_label("Firmware folder:"))
        self.folder_in = _mk_input(DEFAULT_FOLDER)
        form.add_widget(self.folder_in)
        root.add_widget(form)

        # detected files
        self.detected_lbl = _mk_label("Detected: (rescan)", color=DIM, size=13)
        self.detected_lbl.height = "60dp"
        root.add_widget(self.detected_lbl)

        # action buttons row 1
        row1 = BoxLayout(size_hint_y=None, height="52dp", spacing="6dp")
        row1.add_widget(_mk_button("HMI", lambda *_: self.start_single("hmi")))
        row1.add_widget(_mk_button("ADDON", lambda *_: self.start_single("fizzz")))
        row1.add_widget(_mk_button("HMI+ADDON", lambda *_: self.start_combo(),
                                   bg=ACCENT2))
        root.add_widget(row1)

        # action buttons row 2
        row2 = BoxLayout(size_hint_y=None, height="48dp", spacing="6dp")
        row2.add_widget(_mk_button("RC", lambda *_: self.start_single("rc"),
                                   bg=SURFACE, fg=WHITE, size=14))
        row2.add_widget(_mk_button("CHECK LINK", lambda *_: self.do_ping(),
                                   bg=SURFACE, fg=WHITE, size=14))
        row2.add_widget(_mk_button("RESCAN", lambda *_: self.scan_folder(),
                                   bg=SURFACE, fg=WHITE, size=14))
        row2.add_widget(_mk_button("BIND WIFI",
                                   lambda *_: bind_to_wifi(self.log),
                                   bg=SURFACE, fg=WHITE, size=14))
        root.add_widget(row2)

        # options
        opt = BoxLayout(size_hint_y=None, height="40dp", spacing="6dp")
        self.tc_btn = ToggleButton(text="transactionComplete", state="down",
                                   background_normal="", background_color=SURFACE,
                                   color=WHITE, font_size="12sp")
        self.retry_btn = ToggleButton(text="auto-retry 500", state="down",
                                      background_normal="", background_color=SURFACE,
                                      color=WHITE, font_size="12sp")
        opt.add_widget(self.tc_btn)
        opt.add_widget(self.retry_btn)
        root.add_widget(opt)

        # log
        root.add_widget(_mk_label("// LOG", color=DIM, size=12))
        self.log_scroll = ScrollView()
        self.log_label = Label(text="", color=WHITE, font_size="12sp",
                               halign="left", valign="top", markup=False,
                               size_hint_y=None, padding=[6, 6])
        self.log_label.bind(
            width=lambda w, *_: setattr(w, "text_size", (w.width, None)),
            texture_size=lambda w, *_: setattr(w, "height", w.texture_size[1]))
        self.log_scroll.add_widget(self.log_label)
        root.add_widget(self.log_scroll)

        return root

    def on_start(self):
        request_android_permissions(self.log)
        self.log("Ready. Connect this phone to the bar's Wi-Fi AP.", "dim")
        self.log("Then tap BIND WIFI, CHECK LINK, and flash.", "dim")
        self.scan_folder()

    # ── LOG (thread-safe via Clock) ──────────────────────────────────────────
    def log(self, msg, tag="dim"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_file(msg)

        def _append(_dt):
            self.log_label.text += line + "\n"
            Clock.schedule_once(lambda *_: setattr(self.log_scroll, "scroll_y", 0), 0)
        Clock.schedule_once(_append, 0)

    def _lock(self, locked):
        self._busy = locked

    # ── FOLDER ───────────────────────────────────────────────────────────────
    def scan_folder(self):
        folder = Path(self.folder_in.text.strip())
        if not folder.is_dir():
            self.hmi_path = self.addon_path = self.rc_path = None
            self.detected_lbl.text = "Detected: folder not found"
            self.detected_lbl.color = RED
            return
        found = find_firmware(folder)
        self.hmi_path = found["hmi"]
        self.addon_path = found["addon"]
        self.rc_path = found["rc"]
        parts = []
        parts.append("HMI: " + (self.hmi_path.name if self.hmi_path else "(none)"))
        parts.append("ADDON: " + (self.addon_path.name if self.addon_path else "(none)"))
        parts.append("RC: " + (self.rc_path.name if self.rc_path else "(none)"))
        self.detected_lbl.text = "\n".join(parts)
        self.detected_lbl.color = ACCENT if (self.hmi_path or self.addon_path) else DIM

    # ── PING ─────────────────────────────────────────────────────────────────
    def do_ping(self):
        if self._busy:
            return
        self._lock(True)
        threading.Thread(target=self._ping_worker, daemon=True).start()

    def _ping_worker(self):
        host = self.host_in.text.strip()
        bind_to_wifi(self.log)
        self.log(f"Pinging {host}...", "dim")
        ok, detail = ping_bar(host)
        if ok:
            self.log(f"Bar reachable: {detail}", "ok")
        else:
            self.log(f"No link: {detail}. Connect to the bar's Wi-Fi AP.", "err")
        self._lock(False)

    # ── SINGLE (HMI / ADDON / RC) ────────────────────────────────────────────
    def start_single(self, component):
        if self._busy:
            return
        self.scan_folder()
        path_map = {"hmi": self.hmi_path, "fizzz": self.addon_path,
                    "rc": self.rc_path}
        label_map = {"hmi": "HMI", "fizzz": "ADDON", "rc": "RC"}
        path = path_map.get(component)
        label = label_map.get(component, component.upper())
        if not path or not path.exists():
            self.log(f"[ERROR] No {label} .bin found in the folder.", "err")
            return
        self._lock(True)
        threading.Thread(target=self._single_worker,
                         args=(component, path), daemon=True).start()

    def _single_worker(self, component, path):
        host = self.host_in.text.strip()
        bind_to_wifi(self.log)
        version = version_from_name(path.name, component)
        if component == "rc":
            self.log("Sending fota_prepare...", "dim")
            pok, pdetail = prepare_fota(host)
            self.log(f"fota_prepare: {pdetail}", "ok" if pok else "warn")
            ok = self._upload_flow(host, path, version, component="",
                                   transaction_complete=False)
        else:
            ok = self._upload_flow(host, path, version, component)
        if ok and component == "fizzz":
            self.log("Accepted. HMI now pushes it to the addon over MSA "
                     "(~1-2 min). Do NOT cut power/Wi-Fi. Check 'ver' on HC.", "warn")
        self.log("SUCCESS" if ok else "FAILED", "ok" if ok else "err")
        self._lock(False)

    # ── COMBO (HMI -> reboot -> ADDON) ───────────────────────────────────────
    def start_combo(self):
        if self._busy:
            return
        self.scan_folder()
        if not self.hmi_path or not self.hmi_path.exists():
            self.log("[ERROR] No HMI .bin found in the folder.", "err")
            return
        if not self.addon_path or not self.addon_path.exists():
            self.log("[ERROR] No ADDON .bin found in the folder.", "err")
            return
        self._lock(True)
        threading.Thread(target=self._combo_worker, daemon=True).start()

    def _combo_worker(self):
        host = self.host_in.text.strip()
        bind_to_wifi(self.log)
        self.log("=== STEP 1/2: HMI ===", "warn")
        v = version_from_name(self.hmi_path.name, "hmi")
        if not self._upload_flow(host, self.hmi_path, v, "hmi"):
            self.log("HMI step failed - aborting.", "err")
            self._lock(False)
            return
        self.log("HMI accepted. The bar reboots into the new firmware now.", "ok")
        if not self._wait_for_bar(host):
            self._lock(False)
            return
        self.log("=== STEP 2/2: ADDON ===", "warn")
        v2 = version_from_name(self.addon_path.name, "fizzz")
        if self._upload_flow(host, self.addon_path, v2, "fizzz"):
            self.log("ADDON accepted. HMI now pushes it to the STM32 over MSA "
                     "(~1-2 min). Do NOT cut power/Wi-Fi. Check 'ver' on HC.", "warn")
            self.log("SUCCESS", "ok")
        else:
            self.log("FAILED", "err")
        self._lock(False)

    # ── CORE FLOW (shared) ───────────────────────────────────────────────────
    def _upload_flow(self, host, path, version, component,
                     transaction_complete=None) -> bool:
        path = Path(path)
        if not path.exists():
            self.log(f"[ERROR] Missing file: {path}", "err")
            return False
        self.log(f"Hashing {path.name}...", "dim")
        sha = sha256_of_file(path)
        self.log(f"  sha256 = {sha}", "dim")

        ok, detail = ping_bar(host)
        if not ok:
            self.log(f"[ERROR] Bar not reachable: {detail}. Connect to its Wi-Fi.", "err")
            return False

        tc = (self.tc_btn.state == "down") if transaction_complete is None \
            else transaction_complete
        comp_label = component if component else "(none)"
        attempts = (RETRY_ON_500 + 1) if self.retry_btn.state == "down" else 1
        size_kb = path.stat().st_size / 1024
        self.log(f"Uploading {path.name} ({size_kb:.0f} KB) "
                 f"component={comp_label} version={version}", "dim")

        for i in range(1, attempts + 1):
            self.log(f"Attempt {i}/{attempts}...", "dim")
            code, body, good = upload_once(host, path, version, sha, component, tc)
            if good:
                self.log(f"HTTP {code}: {body}", "ok")
                return True
            if code == 500 and i < attempts:
                self.log(f"HTTP {code}: {body} - bar busy/MSA down. "
                         f"Retry in {RETRY_WAIT_SEC}s...", "warn")
                time.sleep(RETRY_WAIT_SEC)
                continue
            if code is None and i < attempts:
                self.log(f"Connection failed ({body}). Bar may be rebooting. "
                         f"Retry in {RETRY_WAIT_SEC}s...", "warn")
                time.sleep(RETRY_WAIT_SEC)
                continue
            if code == 400:
                self.log(f"HTTP 400: {body} - wrong component/params.", "err")
            else:
                self.log(f"HTTP {code}: {body}", "err")
            return False

        self.log("All attempts failed. Check the addon (HC) is connected and "
                 "MSA shows 'connected', then retry.", "err")
        return False

    def _wait_for_bar(self, host) -> bool:
        self.log("Waiting for the bar to reboot...", "dim")
        time.sleep(10)
        deadline = time.time() + REBOOT_WAIT_SEC
        while time.time() < deadline:
            bind_to_wifi()  # re-bind: AP may drop during reboot
            ok, _ = ping_bar(host)
            if ok:
                self.log("Bar is back online.", "ok")
                time.sleep(5)
                return True
            self.log("  ...still rebooting, retry in 5s", "dim")
            time.sleep(5)
        self.log("Timed out. Reconnect Wi-Fi to the bar and try again.", "err")
        return False


if __name__ == "__main__":
    OtaApp().run()
