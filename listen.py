#!/usr/bin/env python3
"""
listen.py — Voice input for Claude CLI / Cursor / Codex / any window (Linux/X11)

Reconhecimento via faster-whisper + noisereduce (offline, sem nuvem).

Uso básico:
  python3 listen.py --calibrate   # calibra microfone e voz (faça primeiro)
  python3 listen.py               # usa perfil salvo, Ctrl+Space para gravar

Toggle: Ctrl+Space | Enter neste terminal | kill -USR1 $(cat /tmp/listen.pid)
"""

import argparse
import array
import json
import math
import os
import signal
import subprocess
import sys
import time
import threading
import pyaudio
import numpy as np

# Prefer AppIndicator (MATE/Ubuntu) over plain GTK StatusIcon (deprecated/hidden in modern DEs)
def _best_pystray_backend() -> str:
    try:
        import gi
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: F401
        return "appindicator"
    except Exception:
        pass
    try:
        import gi
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3  # noqa: F401
        return "appindicator"
    except Exception:
        pass
    return "gtk"

os.environ.setdefault("PYSTRAY_BACKEND", _best_pystray_backend())

PID_FILE    = "/tmp/listen.pid"
PROFILE_DIR = os.path.expanduser("~/.config/listentomecli")
PROFILE_FILE = os.path.join(PROFILE_DIR, "profile.json")
NOISE_FILE   = os.path.join(PROFILE_DIR, "noise_profile.npy")

SAMPLERATE   = 16000
BLOCK_SIZE   = 1024
DEVICE_INDEX = 3
SILENCE_SECS = 2.0
SILENCE_RMS  = 200
CLAUDE_CMD   = os.path.expanduser("~/.local/bin/claude")

LANG_LABELS = {
    "pt": "Português (Brasil)",
    "en": "English (US)",
    "es": "Español (UY/ES)",
}
LANG_WHISPER = {"pt": "pt", "en": "en", "es": "es"}

SWITCH_TARGETS = {
    "switch to english":     "en",
    "switch to spanish":     "es",
    "switch to portuguese":  "pt",
    "trocar para inglês":    "en",
    "trocar para espanhol":  "es",
    "trocar para português": "pt",
    "cambiar a español":     "es",
    "cambiar a inglés":      "en",
    "cambiar a portugués":   "pt",
}

DEFAULT_PROMPTS = {
    "pt": "Transcrição em português brasileiro com acentuação correta:",
    "en": "Transcription in English:",
    "es": "Transcripción en español con acentuación correcta:",
}

# ── Estado global ─────────────────────────────────────────────────────────────
# States: IDLE (armed=False) → RECORDING (armed=True, paused=False)
#                            ↔ PAUSED    (armed=True, paused=True)
#         RECORDING/PAUSED  → IDLE via Ctrl+Space (send) or Ctrl+Esc (cancel)
armed              = False
recording_paused   = False
armed_lock         = threading.Lock()
target_window      = None
target_window_name = ""
tray_icon          = None
_base_img          = None

RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
PURPLE = "\033[35m"
GRAY   = "\033[90m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ── Perfil ────────────────────────────────────────────────────────────────────

def load_profile() -> dict:
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_profile(data: dict):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(PROFILE_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_noise_profile() -> np.ndarray | None:
    if os.path.exists(NOISE_FILE):
        try:
            return np.load(NOISE_FILE)
        except Exception:
            pass
    return None


def save_noise_profile(arr: np.ndarray):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    np.save(NOISE_FILE, arr)


# ── Áudio ─────────────────────────────────────────────────────────────────────

def rms(data: bytes) -> float:
    samples = array.array('h', data)
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0


def record_seconds(stream, seconds: float) -> bytes:
    """Grava N segundos do stream e retorna bytes PCM int16."""
    buf = bytearray()
    n_blocks = int(seconds * SAMPLERATE / BLOCK_SIZE)
    for _ in range(n_blocks):
        buf.extend(stream.read(BLOCK_SIZE, exception_on_overflow=False))
    return bytes(buf)


def pcm_to_float(audio_bytes: bytes) -> np.ndarray:
    return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def normalize(samples: np.ndarray) -> np.ndarray:
    peak = np.abs(samples).max()
    return samples / peak * 0.95 if peak > 0.001 else samples


# ── Transcrição ───────────────────────────────────────────────────────────────

def transcribe(model, audio_bytes: bytes, lang: str,
               noise_profile: np.ndarray | None = None,
               initial_prompt: str = "") -> str:
    if not audio_bytes:
        return ""

    samples = pcm_to_float(audio_bytes)
    samples = normalize(samples)

    if noise_profile is not None:
        try:
            import noisereduce as nr
            samples = nr.reduce_noise(
                y=samples,
                sr=SAMPLERATE,
                y_noise=noise_profile,
                prop_decrease=0.8,
                stationary=True,
            )
        except Exception as e:
            print(f"{YELLOW}noisereduce falhou ({e}){RESET}")

    prompt = initial_prompt or DEFAULT_PROMPTS.get(lang, "")

    segments, _ = model.transcribe(
        samples,
        language=LANG_WHISPER.get(lang, lang),
        initial_prompt=prompt,
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=False,
        temperature=0,
    )
    return " ".join(s.text.strip() for s in segments).strip()


# ── Calibração ────────────────────────────────────────────────────────────────

def calibrate(device: int, lang: str, whisper_model: str):
    print(f"\n{BOLD}=== CALIBRAÇÃO ==={RESET}")
    print(f"Microfone: device {device} | Idioma: {LANG_LABELS[lang]} | Modelo: {whisper_model}\n")

    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLERATE,
        input=True,
        input_device_index=device,
        frames_per_buffer=BLOCK_SIZE,
    )

    # ── Etapa 1: perfil de ruído ──────────────────────────────────────────────
    print(f"{BOLD}Etapa 1/2 — Perfil de ruído{RESET}")
    print("Fique em SILÊNCIO (sem falar) por 3 segundos...")
    for i in range(3, 0, -1):
        print(f"  {i}...", end=" ", flush=True)
        time.sleep(1)
    print("gravando!", flush=True)

    noise_bytes   = record_seconds(stream, 3.0)
    noise_samples = pcm_to_float(noise_bytes)
    save_noise_profile(noise_samples)
    noise_rms = math.sqrt(np.mean(noise_samples ** 2)) * 32768
    print(f"{GREEN}✓ Perfil de ruído salvo (RMS ambiente: {noise_rms:.0f}){RESET}\n")

    # ── Etapa 2: perfil de voz ────────────────────────────────────────────────
    print(f"{BOLD}Etapa 2/2 — Perfil de voz{RESET}")
    print("Fale normalmente por ~15 segundos.")
    print("Pode contar o que quiser: o que você faz, um projeto, qualquer coisa.")
    print(f"Pressione {BOLD}Enter{RESET} quando terminar (ou aguarde o silêncio).\n")

    input(f"  [{BOLD}Enter para começar a gravar{RESET}]")
    print(f"\n{BOLD}{GREEN}● GRAVANDO — fale agora…{RESET}", flush=True)

    voice_buf     = bytearray()
    last_speech_t = time.time()
    stop_event    = threading.Event()

    def _stdin_stop():
        input()
        stop_event.set()
    threading.Thread(target=_stdin_stop, daemon=True).start()

    while not stop_event.is_set():
        data  = stream.read(BLOCK_SIZE, exception_on_overflow=False)
        level = rms(data)
        voice_buf.extend(data)
        bar = "█" * min(int(level / 400), 20)
        print(f"\r{PURPLE}🎙 {bar:<20}{RESET} {int(level):4.0f}  ", end="", flush=True)
        if level > SILENCE_RMS:
            last_speech_t = time.time()
        elif time.time() - last_speech_t > 4.0 and len(voice_buf) > SAMPLERATE * 3:
            print(f"\n{GRAY}[silêncio detectado — encerrando gravação]{RESET}")
            break

    print(f"\n{GRAY}Transcrevendo…{RESET}")

    stream.stop_stream()
    stream.close()
    pa.terminate()

    print(f"Carregando Whisper [{whisper_model}]…")
    from faster_whisper import WhisperModel
    model = WhisperModel(whisper_model, device="cpu", compute_type="int8")

    transcribed = transcribe(model, bytes(voice_buf), lang,
                             noise_profile=noise_samples)

    if not transcribed:
        print(f"{YELLOW}Não foi possível transcrever. Tente falar mais perto do microfone.{RESET}")
        transcribed = DEFAULT_PROMPTS[lang]

    print(f"\n{PURPLE}Transcrição captada:{RESET}")
    print(f"  {transcribed}\n")

    profile = {
        "lang":           lang,
        "model":          whisper_model,
        "device":         device,
        "initial_prompt": transcribed,
        "noise_rms":      float(noise_rms),
    }
    save_profile(profile)

    print(f"{GREEN}✓ Perfil salvo em {PROFILE_FILE}{RESET}")
    print(f"{GRAY}  Na próxima execução, será carregado automaticamente.{RESET}\n")
    print("Para recalibrar a qualquer momento: python3 listen.py --calibrate")


# ── Helpers de UI ─────────────────────────────────────────────────────────────

def notify(title: str, body: str):
    try:
        subprocess.run(["notify-send", "-t", "2000", title, body],
                       capture_output=True)
    except Exception:
        pass


def get_active_window() -> tuple:
    try:
        from Xlib import display as xdisplay, X
        dpy = xdisplay.Display()
        win = dpy.get_input_focus().focus
        if win in (X.PointerRoot, X.NONE):
            return "", "?"
        while True:
            name = win.get_wm_name()
            if name:
                return str(win.id), str(name)
            parent = win.query_tree().parent
            if parent == dpy.screen().root or parent is None:
                break
            win = parent
        return str(win.id), "?"
    except Exception:
        return "", "?"


def xdotype(text: str, clipboard: bool = True):
    if clipboard:
        try:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode(), check=True)
            time.sleep(0.08)
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"],
                           check=True)
            return
        except Exception as e:
            print(f"{YELLOW}clipboard falhou ({e}), tentando type…{RESET}")
    cmd = ["xdotool", "type", "--clearmodifiers", "--delay", "20", "--", text]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print(f"{RED}xdotool não encontrado: sudo apt install xdotool{RESET}")
        print(f"{CYAN}Texto: {text}{RESET}")
    except subprocess.CalledProcessError as e:
        print(f"{RED}xdotool erro: {e}{RESET}")


def ask_claude_print(text: str, is_first: bool):
    cmd = [CLAUDE_CMD, "--print"]
    if not is_first:
        cmd.append("--continue")
    cmd.append(text)
    print(f"\n{CYAN}▶ {text}{RESET}")
    print(f"{YELLOW}─────────────────────────────{RESET}")
    subprocess.run(cmd, text=True)
    print(f"{YELLOW}─────────────────────────────{RESET}\n")


# ── System tray ───────────────────────────────────────────────────────────────

def find_asset(name: str) -> str | None:
    candidates = [
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", name),
        os.path.join(PROFILE_DIR, "assets", name),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _load_base_image():
    global _base_img
    if _base_img is not None:
        return _base_img
    path = find_asset("icone-ai-listen-to-me-on-cli.png")
    if path is None:
        return None
    try:
        from PIL import Image
        _base_img = Image.open(path).convert("RGBA").resize((64, 64), Image.LANCZOS)
        return _base_img
    except Exception:
        return None


def make_tray_image(state: str = "idle"):
    """state: 'idle' | 'recording' | 'paused'"""
    base = _load_base_image()
    try:
        from PIL import Image, ImageDraw
        colors = {
            "recording": ((220, 50,  50,  110), (220, 50,  50,  255)),
            "paused":    ((220, 160,  0,  110), (220, 160,  0,  255)),
        }
        if base is None:
            img = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
            draw = ImageDraw.Draw(img)
            dot = {"recording": (220,50,50,255), "paused": (220,160,0,255)}.get(state, (60,180,60,255))
            draw.ellipse([8, 8, 56, 56], fill=dot)
            return img
        img = base.copy()
        if state in colors:
            overlay_color, dot_color = colors[state]
            overlay = Image.new("RGBA", img.size, overlay_color)
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
            draw.ellipse([46, 2, 62, 18], fill=dot_color,
                         outline=(255, 255, 255, 200), width=1)
        return img
    except Exception:
        return None


def _build_argv(lang: str, model: str, clipboard: bool) -> list:
    script = os.path.realpath(__file__)
    argv = [sys.executable, script, f"--lang={lang}", f"--model={model}"]
    if clipboard:
        argv.append("--clipboard")
    return argv


_GLib = None
_xapp_icon = None

# Symbolic icon names for each state — guaranteed present in any GTK theme
_ICON_NAMES: dict = {
    "idle":      "audio-input-microphone",
    "recording": "media-record",
    "paused":    "media-playback-pause",
}

def _install_icons():
    """Install custom PNGs into hicolor theme for other uses (not used for status icon)."""
    try:
        icon_dir = os.path.expanduser("~/.local/share/icons/hicolor/64x64/apps")
        os.makedirs(icon_dir, exist_ok=True)
        for state, sysname in _ICON_NAMES.items():
            img = make_tray_image(state)
            if img:
                img.save(os.path.join(icon_dir, f"listentomecli-{state}.png"))
        subprocess.run(["gtk-update-icon-cache", "-f",
                        os.path.expanduser("~/.local/share/icons/hicolor")],
                       capture_output=True)
    except Exception:
        pass


def _build_gtk_menu(lang_ref: list, model_ref: list, clipboard_ref: list):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib
    except Exception:
        return None

    menu = Gtk.Menu()

    # ── Status (disabled label) ───────────────────────────────────────────────
    def _status_text():
        if armed and recording_paused:
            return "⏸ Pausado"
        elif armed:
            return "● Gravando"
        else:
            return "○ Aguardando"

    status_item = Gtk.MenuItem(label=f"{_status_text()}  [{lang_ref[0].upper()} · {model_ref[0]}]")
    status_item.set_sensitive(False)
    menu.append(status_item)
    menu.append(Gtk.SeparatorMenuItem())

    # ── Calibrar ──────────────────────────────────────────────────────────────
    def _calibrate(_item):
        script = os.path.realpath(__file__)
        for cmd in [
            ["x-terminal-emulator", "-e", f"{sys.executable} {script} --calibrate --lang {lang_ref[0]}"],
            ["xterm", "-e", f"{sys.executable} {script} --calibrate --lang {lang_ref[0]}"],
        ]:
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue
        notify("Calibrar", "Execute: listentomecli --calibrate")

    cal_item = Gtk.MenuItem(label="Calibrar microfone")
    cal_item.connect("activate", _calibrate)
    menu.append(cal_item)

    # ── Modelo ────────────────────────────────────────────────────────────────
    model_item = Gtk.MenuItem(label="Modelo")
    model_sub = Gtk.Menu()
    model_group = []
    for label, key in [("tiny  — rápido (~1s)", "tiny"), ("small — preciso (~5s)", "small")]:
        rb = Gtk.RadioMenuItem.new_with_label(model_group, label)
        model_group = rb.get_group()
        if model_ref[0] == key:
            rb.set_active(True)
        def _on_model(item, m=key):
            if item.get_active() and model_ref[0] != m:
                model_ref[0] = m
                notify("Modelo", f"Reiniciando com {m}…")
                GLib.idle_add(_do_restart, lang_ref[0], m, clipboard_ref[0])
        rb.connect("toggled", _on_model)
        model_sub.append(rb)
    model_item.set_submenu(model_sub)
    menu.append(model_item)

    # ── Idioma ────────────────────────────────────────────────────────────────
    lang_item = Gtk.MenuItem(label="Idioma")
    lang_sub = Gtk.Menu()
    lang_group = []
    for label, key in [("Português (Brasil)", "pt"), ("English (US)", "en"), ("Español", "es")]:
        rb = Gtk.RadioMenuItem.new_with_label(lang_group, label)
        lang_group = rb.get_group()
        if lang_ref[0] == key:
            rb.set_active(True)
        def _on_lang(item, lg=key):
            if item.get_active() and lang_ref[0] != lg:
                lang_ref[0] = lg
                print(f"\n{GREEN}⇄  Idioma: {LANG_LABELS[lg]}{RESET}\n", flush=True)
                notify("Idioma", LANG_LABELS[lg])
        rb.connect("toggled", _on_lang)
        lang_sub.append(rb)
    lang_item.set_submenu(lang_sub)
    menu.append(lang_item)

    menu.append(Gtk.SeparatorMenuItem())

    # ── Reiniciar / Sair ──────────────────────────────────────────────────────
    def _on_restart(_item):
        GLib.idle_add(_do_restart, lang_ref[0], model_ref[0], clipboard_ref[0])

    def _on_quit(_item):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)

    restart_item = Gtk.MenuItem(label="Reiniciar")
    restart_item.connect("activate", _on_restart)
    menu.append(restart_item)

    quit_item = Gtk.MenuItem(label="Sair")
    quit_item.connect("activate", _on_quit)
    menu.append(quit_item)

    menu.show_all()
    return menu


def _do_restart(lang: str, model: str, clipboard: bool):
    from gi.repository import Gtk
    Gtk.main_quit()
    os.execv(sys.executable, _build_argv(lang, model, clipboard))
    return False


def start_tray(lang_ref: list, model_ref: list, clipboard_ref: list):
    global tray_icon, _GLib, _xapp_icon
    try:
        import gi
        gi.require_version("XApp", "1.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import XApp, Gtk, GLib
        _GLib = GLib
    except Exception as e:
        print(f"{GRAY}XApp não disponível — tray desativado ({e}){RESET}", flush=True)
        return

    _install_icons()
    menu = _build_gtk_menu(lang_ref, model_ref, clipboard_ref)

    si = XApp.StatusIcon()
    si.set_name("listentomecli")
    si.set_icon_name(_ICON_NAMES.get("idle", "audio-input-microphone"))
    si.set_tooltip_text("ListenToMeOnCLI")
    si.set_visible(True)
    if menu:
        si.set_secondary_menu(menu)

    _xapp_icon = si
    tray_icon   = si   # keep shared reference for _set_tray_state

    def _loop():
        Gtk.main()

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    time.sleep(0.3)
    print(f"{GREEN}✓ Tray iniciado (XApp){RESET}", flush=True)


def _set_tray_state(state: str):
    if _xapp_icon is None or _GLib is None:
        return
    name = _ICON_NAMES.get(state)
    if name:
        _GLib.idle_add(_xapp_icon.set_icon_name, name)


def toggle_armed(lang_ref: list):
    """Ctrl+Space: start session (IDLE→RECORDING) or send (RECORDING/PAUSED→IDLE)."""
    global armed, recording_paused, target_window, target_window_name
    with armed_lock:
        armed = not armed
        new_val = armed
        if not new_val:
            recording_paused = False
    label = LANG_LABELS.get(lang_ref[0], lang_ref[0])
    if new_val:
        _set_tray_state("recording")
        print(f"\n{BOLD}{GREEN}● GRAVANDO [{label}]{RESET}"
              f"  {GRAY}Ctrl+Shift+Space = pausar | Ctrl+Esc = cancelar{RESET}", flush=True)
        notify("🎙 Gravando", label)
    else:
        _set_tray_state("idle")
        wid, wname = get_active_window()
        target_window      = wid
        target_window_name = wname
        print(f"\n{GRAY}○ Enviando → {CYAN}{wname}{RESET}", flush=True)
        notify("⏳ Transcrevendo…", wname)


def toggle_paused(lang_ref: list):
    """Ctrl+Shift+Space: pause/resume within an active session."""
    global recording_paused
    with armed_lock:
        if not armed:
            return
        recording_paused = not recording_paused
        is_paused = recording_paused
    label = LANG_LABELS.get(lang_ref[0], lang_ref[0])
    if is_paused:
        _set_tray_state("paused")
        print(f"\n{BOLD}{YELLOW}⏸ PAUSADO [{label}]{RESET}"
              f"  {GRAY}Ctrl+Shift+Space = retomar | Ctrl+Space = enviar{RESET}", flush=True)
        notify("⏸ Pausado", "Ctrl+Shift+Space para retomar")
    else:
        _set_tray_state("recording")
        print(f"\n{BOLD}{GREEN}● GRAVANDO [{label}]{RESET}"
              f"  {GRAY}(retomado — buffer acumulando){RESET}", flush=True)
        notify("🎙 Retomado", label)


_cancel_flag = [False]   # shared between cancel_session() and the audio loop


def cancel_session():
    """Ctrl+Esc: discard buffer and return to IDLE."""
    global armed, recording_paused
    with armed_lock:
        was_armed = armed
        armed            = False
        recording_paused = False
    if was_armed:
        _cancel_flag[0] = True
        _set_tray_state("idle")
        print(f"\n{GRAY}✗ Sessão cancelada — buffer descartado{RESET}", flush=True)
        notify("✗ Cancelado", "Buffer descartado")


def list_devices():
    p = pyaudio.PyAudio()
    print("\nDispositivos de entrada disponíveis:")
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0:
            marker = f" {CYAN}◀ padrão{RESET}" if i == DEVICE_INDEX else ""
            print(f"  [{i:2d}] {d['name']}{marker}")
    p.terminate()
    print()


# ── Hotkey Ctrl+Space via XGrabKey ────────────────────────────────────────────

def start_xlib_hotkey(lang_ref: list) -> bool:
    try:
        from Xlib import X, XK, display as xdisplay
        from Xlib.error import BadAccess

        dpy  = xdisplay.Display()
        root = dpy.screen().root

        code_space = dpy.keysym_to_keycode(XK.string_to_keysym("space"))
        code_esc   = dpy.keysym_to_keycode(XK.string_to_keysym("Escape"))

        # Extra modifier variants to handle NumLock / CapsLock transparently
        _extra = [0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask]

        combos = [
            # (keycode, base_mods)
            (code_space, X.ControlMask),                    # Ctrl+Space  → start/send
            (code_space, X.ControlMask | X.ShiftMask),      # Ctrl+Shift+Space → pause/resume
            (code_esc,   X.ControlMask),                    # Ctrl+Esc → cancel
        ]

        grabbed = False
        for code, base in combos:
            for extra in _extra:
                try:
                    root.grab_key(code, base | extra, False,
                                  X.GrabModeAsync, X.GrabModeAsync)
                    grabbed = True
                except BadAccess:
                    pass

        if not grabbed:
            print(f"{YELLOW}XGrabKey: teclas já em uso por outro app{RESET}")
            return False

        dpy.flush()

        def _loop():
            while True:
                ev = dpy.next_event()
                if ev.type != X.KeyPress:
                    continue
                state = ev.state & (X.ControlMask | X.ShiftMask | X.Mod1Mask)
                sym   = dpy.keycode_to_keysym(ev.detail, 0)
                if sym == XK.XK_space and (state & X.ShiftMask):
                    toggle_paused(lang_ref)
                elif sym == XK.XK_space:
                    toggle_armed(lang_ref)
                elif sym == XK.XK_Escape:
                    cancel_session()

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        time.sleep(0.4)
        if not t.is_alive():
            print(f"{RED}XGrabKey: thread de eventos morreu{RESET}")
            return False
        return True

    except Exception as e:
        print(f"{GRAY}XGrabKey falhou: {e}{RESET}")
        return False


def start_stdin_toggle(lang_ref: list):
    def _loop():
        while True:
            try:
                input()
                toggle_armed(lang_ref)
            except EOFError:
                break
    threading.Thread(target=_loop, daemon=True).start()


# ── Loop principal ────────────────────────────────────────────────────────────

def run(device: int, initial_lang: str, silence: float, confirm: bool,
        print_mode: bool, clipboard: bool, whisper_model: str,
        noise_profile: np.ndarray | None, initial_prompt: str,
        model_ref: list | None = None):

    if model_ref is None:
        model_ref = [whisper_model]
    clipboard_ref = [clipboard]

    print(f"Carregando Whisper [{whisper_model}] …")
    from faster_whisper import WhisperModel
    model = WhisperModel(whisper_model, device="cpu", compute_type="int8")

    if noise_profile is not None:
        print(f"{GREEN}✓ Perfil de ruído carregado{RESET}")
    if initial_prompt:
        preview = initial_prompt[:60] + ("…" if len(initial_prompt) > 60 else "")
        print(f"{GREEN}✓ Perfil de voz: \"{preview}\"{RESET}")
    print()

    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLERATE,
        input=True,
        input_device_index=device,
        frames_per_buffer=BLOCK_SIZE,
    )

    lang_ref = [initial_lang]

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    signal.signal(signal.SIGUSR1, lambda s, f: toggle_armed(lang_ref))

    if not print_mode:
        start_tray(lang_ref, model_ref, clipboard_ref)

    if print_mode:
        global armed
        with armed_lock:
            armed = True
        print(f"{GRAY}Modo --print ativo. Ctrl+C para sair.{RESET}\n")
    else:
        hotkey_ok = start_xlib_hotkey(lang_ref)
        start_stdin_toggle(lang_ref)
        if hotkey_ok:
            print(f"{BOLD}Ctrl+Space{RESET}       → gravar / enviar")
            print(f"{BOLD}Ctrl+Shift+Space{RESET} → pausar / retomar (buffer acumula)")
            print(f"{BOLD}Ctrl+Esc{RESET}          → cancelar e descartar")
        else:
            print(f"{YELLOW}Ctrl+Space indisponível{RESET} — use Enter neste terminal ou kill -USR1 $(cat {PID_FILE})")
        print(f"{GRAY}PID: {os.getpid()} | Ctrl+C para encerrar{RESET}\n")

    is_first      = True
    audio_buf     = bytearray()
    last_speech_t = None
    was_armed     = False
    vol_chars     = " ▁▂▃▄▅▆▇█"

    def do_transcribe(buf: bytes) -> str:
        return transcribe(model, buf, lang_ref[0],
                          noise_profile=noise_profile,
                          initial_prompt=initial_prompt)

    def send_text(text: str):
        nonlocal is_first
        if not text:
            return
        target = SWITCH_TARGETS.get(text.lower().rstrip(".!?"))
        if target:
            if target != lang_ref[0]:
                lang_ref[0] = target
                print(f"\n{GREEN}⇄  Idioma: {LANG_LABELS[target]}{RESET}\n")
            return
        if confirm:
            print(f"\nEnviar: \"{text}\" ? [Enter=sim / texto=editar / n=descartar] ", end="", flush=True)
            ans = input()
            if ans.lower() == "n":
                print("Descartado.\n")
                return
            if ans.strip():
                text = ans.strip()
        if print_mode:
            ask_claude_print(text, is_first)
            is_first = False
        else:
            wname = target_window_name or "janela focada"
            print(f"\n{CYAN}↳ {wname}: {text}{RESET}")
            xdotype(text, clipboard=clipboard)

    try:
        while True:
            data = stream.read(BLOCK_SIZE, exception_on_overflow=False)

            with armed_lock:
                cur_armed  = armed
                cur_paused = recording_paused

            # Session ended (Ctrl+Space to send, or cancel_session)
            if was_armed and not cur_armed:
                print(f"\r{' '*60}\r", end="", flush=True)
                cancelled = _cancel_flag[0]
                _cancel_flag[0] = False
                if audio_buf and not cancelled:
                    text = do_transcribe(bytes(audio_buf))
                    audio_buf.clear()
                    last_speech_t = None
                    if text:
                        print(f"{PURPLE}◉ {text}{RESET}")
                        send_text(text)
                    else:
                        print(f"{GRAY}(nada reconhecido){RESET}")
                else:
                    audio_buf.clear()
                    last_speech_t = None
                    if not cancelled:
                        print(f"{GRAY}(sem áudio gravado){RESET}")

            was_armed = cur_armed

            if not cur_armed or cur_paused:
                continue

            audio_buf.extend(data)

            level = rms(data)
            buf_secs = len(audio_buf) / (SAMPLERATE * 2)
            if level > SILENCE_RMS:
                last_speech_t = time.time()
                bar_idx = min(int(level / 500), len(vol_chars) - 1)
                print(f"\r{PURPLE}🎙 {vol_chars[bar_idx]*8}{RESET} {int(level):4.0f}"
                      f"  {GRAY}{buf_secs:.0f}s{RESET}  ", end="", flush=True)
            else:
                print(f"\r{GRAY}🎙 {'·'*8}{RESET}      "
                      f"  {GRAY}{buf_secs:.0f}s{RESET}  ", end="", flush=True)

            if (last_speech_t and
                    time.time() - last_speech_t > silence and
                    len(audio_buf) > SAMPLERATE * 0.5):
                print(f"\r{' '*60}\r", end="", flush=True)
                print(f"{GRAY}[silêncio — transcrevendo…]{RESET}")
                text = do_transcribe(bytes(audio_buf))
                audio_buf.clear()
                last_speech_t = None
                if text:
                    print(f"{PURPLE}◉ {text}{RESET}")
                    send_text(text)

    except KeyboardInterrupt:
        print(f"\n{GRAY}Encerrando…{RESET}")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Voz → janela focada | faster-whisper + noisereduce | Linux/X11",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Primeira vez:
  python3 listen.py --calibrate       # calibra mic e voz (~2 min)

Uso normal:
  python3 listen.py                   # carrega perfil salvo automaticamente
  python3 listen.py --model small     # melhor precisão (~5s latência)
  python3 listen.py --lang en         # inglês
  python3 listen.py --no-profile      # ignora perfil salvo
  python3 listen.py --list-devices    # lista microfones
        """,
    )
    ap.add_argument("--calibrate",    action="store_true",
                    help="Calibra perfil de ruído e voz para este microfone/ambiente")
    ap.add_argument("--lang",         default=None, choices=["pt","en","es"],
                    help="Idioma (padrão: do perfil salvo, ou pt)")
    ap.add_argument("--model",        default=None, choices=["tiny","small"],
                    help="Modelo Whisper (padrão: do perfil salvo, ou tiny)")
    ap.add_argument("--device",       type=int,   default=None,
                    help=f"Índice do microfone (padrão: do perfil salvo, ou {DEVICE_INDEX})")
    ap.add_argument("--silence",      type=float, default=SILENCE_SECS)
    ap.add_argument("--confirm",      action="store_true")
    ap.add_argument("--print",        dest="print_mode", action="store_true")
    ap.add_argument("--clipboard",    action="store_true",
                    help="Cola via xclip+Ctrl+Shift+V")
    ap.add_argument("--no-profile",   action="store_true",
                    help="Ignora perfil salvo, usa padrões")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    # Carrega perfil salvo (se existir e --no-profile não for passado)
    profile = {} if args.no_profile else load_profile()
    if profile:
        print(f"{GRAY}Perfil carregado: {PROFILE_FILE}{RESET}")

    lang          = args.lang   or profile.get("lang",   "pt")
    whisper_model = args.model  or profile.get("model",  "tiny")
    device        = args.device if args.device is not None else profile.get("device", DEVICE_INDEX)
    initial_prompt = profile.get("initial_prompt", "")
    noise_profile  = None if args.no_profile else load_noise_profile()

    if args.calibrate:
        calibrate(device=device, lang=lang, whisper_model=whisper_model)
        return

    run(
        device         = device,
        initial_lang   = lang,
        silence        = args.silence,
        confirm        = args.confirm,
        print_mode     = args.print_mode,
        clipboard      = args.clipboard,
        whisper_model  = whisper_model,
        noise_profile  = noise_profile,
        initial_prompt = initial_prompt,
    )


if __name__ == "__main__":
    main()
