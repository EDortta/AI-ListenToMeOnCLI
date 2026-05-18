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
armed              = False
armed_lock         = threading.Lock()
target_window      = None
target_window_name = ""

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


def toggle_armed(lang_ref: list):
    global armed, target_window, target_window_name
    with armed_lock:
        armed = not armed
        new_val = armed
    label = LANG_LABELS.get(lang_ref[0], lang_ref[0])
    if new_val:
        print(f"\n{BOLD}{GREEN}● GRAVANDO [{label}]{RESET} — fale agora, mude de janela à vontade", flush=True)
        notify("🎙 Gravando", label)
    else:
        # Capture the focused window NOW — this is where the text will be typed
        wid, wname = get_active_window()
        target_window      = wid
        target_window_name = wname
        print(f"\n{GRAY}○ Parado — transcrevendo → {CYAN}{wname}{RESET}", flush=True)
        notify("⏳ Transcrevendo…", wname)


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
        code = dpy.keysym_to_keycode(XK.string_to_keysym("space"))

        grabbed = False
        for mod in [X.ControlMask,
                    X.ControlMask | X.Mod2Mask,
                    X.ControlMask | X.LockMask,
                    X.ControlMask | X.Mod2Mask | X.LockMask]:
            try:
                root.grab_key(code, mod, False, X.GrabModeAsync, X.GrabModeAsync)
                grabbed = True
            except BadAccess:
                pass

        if not grabbed:
            print(f"{YELLOW}XGrabKey: Ctrl+Space já está em uso por outro app{RESET}")
            return False

        dpy.flush()

        def _loop():
            while True:
                ev = dpy.next_event()
                if ev.type == X.KeyPress:
                    toggle_armed(lang_ref)

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
        noise_profile: np.ndarray | None, initial_prompt: str):

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

    if print_mode:
        global armed
        with armed_lock:
            armed = True
        print(f"{GRAY}Modo --print ativo. Ctrl+C para sair.{RESET}\n")
    else:
        hotkey_ok = start_xlib_hotkey(lang_ref)
        start_stdin_toggle(lang_ref)
        if hotkey_ok:
            print(f"{BOLD}Ctrl+Space{RESET} para gravar/transcrever")
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
                cur_armed = armed

            if was_armed and not cur_armed:
                print(f"\r{' '*60}\r", end="", flush=True)
                if audio_buf:
                    text = do_transcribe(bytes(audio_buf))
                    audio_buf.clear()
                    last_speech_t = None
                    if text:
                        print(f"{PURPLE}◉ {text}{RESET}")
                        send_text(text)
                    else:
                        print(f"{GRAY}(nada reconhecido){RESET}")
                else:
                    print(f"{GRAY}(sem áudio gravado){RESET}")

            was_armed = cur_armed

            if not cur_armed:
                continue

            audio_buf.extend(data)

            level = rms(data)
            if level > SILENCE_RMS:
                last_speech_t = time.time()
                bar_idx = min(int(level / 500), len(vol_chars) - 1)
                print(f"\r{PURPLE}🎙 {vol_chars[bar_idx]*8}{RESET} {int(level):4.0f}  ", end="", flush=True)
            else:
                print(f"\r{GRAY}🎙 {'·'*8}{RESET}       ", end="", flush=True)

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
