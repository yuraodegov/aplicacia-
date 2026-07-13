[app]

# App identity
title = HMI FIZZZ WiFi OTA
package.name = hmiotawifi
package.domain = com.strausswater

# Source
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0

# Requirements: Kivy for UI, pyjnius for the Android network-binding calls.
# urllib/hashlib are stdlib (bundled automatically).
requirements = python3,kivy,pyjnius

orientation = portrait
fullscreen = 0

# --- Android permissions ---
# INTERNET + ACCESS_NETWORK_STATE: HTTP + reading network list to bind Wi-Fi.
# ACCESS_WIFI_STATE / CHANGE_WIFI_STATE / CHANGE_NETWORK_STATE: bind to the AP.
# ACCESS_FINE_LOCATION: required by Android to read Wi-Fi details on newer APIs.
# READ/WRITE/MANAGE_EXTERNAL_STORAGE: read non-media .bin files by path.
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_STATE,CHANGE_NETWORK_STATE,ACCESS_FINE_LOCATION,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# Allow plain-HTTP (cleartext) traffic to 192.168.4.1
android.allow_backup = True
android.manifest.uses_cleartext_traffic = True

# API levels: 34 target keeps it Play-compatible; 24 min covers most phones and
# supports ConnectivityManager.bindProcessToNetwork (API 23+).
android.api = 34
android.minapi = 24
android.archs = arm64-v8a,armeabi-v7a

# Accept the Android SDK license non-interactively on CI
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
