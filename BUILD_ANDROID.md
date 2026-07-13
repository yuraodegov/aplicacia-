# Сборка APK (HMI/FIZZZ WiFi OTA)

Buildozer работает только под Linux. На Windows — через WSL (Ubuntu). Файлы
`main.py` и `buildozer.spec` должны лежать в одной папке.

## Вариант A. WSL (Ubuntu) на твоём ПК

1. Установи WSL (один раз, в PowerShell от админа):
   ```powershell
   wsl --install -d Ubuntu
   ```
   Перезагрузись, задай логин/пароль Ubuntu.

2. В Ubuntu поставь зависимости:
   ```bash
   sudo apt update
   sudo apt install -y python3-pip python3-venv git zip unzip openjdk-17-jdk \
       autoconf libtool pkg-config zlib1g-dev libncurses-dev libffi-dev libssl-dev
   pip3 install --user buildozer cython
   echo 'export PATH=$PATH:~/.local/bin' >> ~/.bashrc && source ~/.bashrc
   ```

3. Скопируй проект в Linux-раздел (важно: НЕ в /mnt/c, иначе долго и с правами):
   ```bash
   mkdir -p ~/ota && cp /mnt/c/Users/Yura_Od/Desktop/ota/main.py ~/ota/
   cp /mnt/c/Users/Yura_Od/Desktop/ota/buildozer.spec ~/ota/
   cd ~/ota
   ```

4. Собери (первый раз качает SDK/NDK, ~20-40 мин):
   ```bash
   buildozer android debug
   ```
   Готовый APK: `~/ota/bin/hmiotawifi-1.0-debug.apk`.

5. Перекинь APK на телефон (или сразу установи по USB):
   ```bash
   cp ~/ota/bin/*.apk /mnt/c/Users/Yura_Od/Desktop/
   # либо, если телефон подключён и включён USB-debug:
   buildozer android deploy run
   ```

## Вариант B. GitHub Actions (без локального Linux)

Положи `main.py` + `buildozer.spec` в репозиторий и добавь
`.github/workflows/build.yml`:
```yaml
name: build-apk
on: [push, workflow_dispatch]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: digitallyinduced/kivy-buildozer-action@v1  # or ArtemSBulgakov/buildozer-action
        with:
          command: buildozer android debug
      - uses: actions/upload-artifact@v4
        with:
          name: apk
          path: bin/*.apk
```
APK скачаешь из Artifacts после сборки.

## На телефоне (первый запуск)

1. Установи APK (разреши "установку из неизвестных источников").
2. При старте приложение попросит:
   - разрешения (сеть/хранилище) — дай все;
   - "All files access" (Android 11+) — открой настройки, включи для приложения,
     вернись. Без этого не прочитает .bin по пути.
3. Положи прошивки в `/sdcard/Download/fota` (или укажи свою папку в поле Folder).
4. Подключи телефон к Wi-Fi точке прибора (SSID бара, 192.168.4.1). Android
   спросит "нет интернета, остаться?" — жми ОСТАТЬСЯ.
5. В приложении: **BIND WIFI** -> **CHECK LINK** (должно быть HTTP 200) ->
   **RESCAN** -> **HMI / ADDON / HMI+ADDON**.

## Если CHECK LINK не проходит

Это та самая проблема маршрутизации: Android гонит трафик через мобильные
данные. Порядок действий:
- нажми **BIND WIFI** ещё раз (лог должен сказать "Bound to Wi-Fi network");
- на время теста выключи мобильные данные (Settings -> SIM -> Mobile data off);
- убедись, что в системном диалоге Wi-Fi выбрал "Оставаться подключённым".
