#!/usr/bin/env python3
"""
listen.py — Voice input for Claude CLI / Cursor / Codex / any window (Linux/X11)

Reconhecimento via faster-whisper (muito superior ao VOSK para pt/en/es).

Uso:
  Ctrl+Space  →  arma (começa a gravar)
  Ctrl+Space  →  desarma (transcreve e digita na janela capturada)

Fallbacks de toggle: Enter neste terminal  |  kill -USR1 $(cat /tmp/listen.pid)
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
import tempfile
import wave
import pyaudio
import numpy as np

PID_FILE = "/tmp/listen.pid"

SAMPLERATE   = 16000
BLOCK_SIZE   = 1024           # ~64ms por chunk — boa resolução p/ VAD
DEVICE_INDEX = 3              # XWF-1080P USB
SILENCE_SECS = 2.0            # silêncio para auto-enviar enquanto armado
SILENCE_RMS  = 200            # abaixo disso = silêncio (int16 RMS)
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def rms(data: bytes) -> float:
    samples = array.array('h', data)
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0


def notify(title: str, body: str):
    try:
        subprocess.run(["notify-send", "-t", "2000", title, body],
                       capture_output=True)
    except Exception:
        pass


def get_active_window() -> tuple:
    """Captura janela focada via python-xlib direto — sem subprocess, sem sleep."""
    try:
        from Xlib import display as xdisplay, X
        dpy  = xdisplay.Display()
        win  = dpy.get_input_focus().focus
        if win in (X.PointerRoot, X.NONE):
            return "", "?"
        # Sobe até a janela toplevel (que tem WM_NAME)
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


def xdotype(text: str, window_id: str | None = None, clipboard: bool = False):
    if clipboard:
        try:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode(), check=True)
            time.sleep(0.05)
            cmd = ["xdotool", "key", "--clearmodifiers"]
            if window_id:
                cmd += ["--window", window_id]
            cmd.append("ctrl+shift+v")
            subprocess.run(cmd, check=True)
            return
        except Exception as e:
            print(f"{YELLOW}clipboard falhou ({e}), tentando type…{RESET}")

    cmd = ["xdotool", "type", "--clearmodifiers", "--delay", "20"]
    if window_id:
        cmd += ["--window", window_id]
    cmd += ["--", text]
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


def transcribe(model, audio_bytes: bytes, lang: str) -> str:
    """Converte bytes PCM int16 → float32 e transcreve com Whisper."""
    if not audio_bytes:
        return ""
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(
        samples,
        language=LANG_WHISPER.get(lang, lang),
        vad_filter=True,          # filtra silêncio interno automaticamente
        beam_size=5,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def toggle_armed(lang_ref: list):
    global armed, target_window, target_window_name
    with armed_lock:
        armed = not armed
        new_val = armed
    label = LANG_LABELS.get(lang_ref[0], lang_ref[0])
    if new_val:
        wid, wname = get_active_window()
        target_window      = wid
        target_window_name = wname
        print(f"\n{BOLD}{GREEN}● GRAVANDO [{label}]{RESET} → {CYAN}{wname}{RESET} — fale e mude de janela à vontade", flush=True)
        notify("🎙 Gravando", f"{label} → {wname}")
    else:
        print(f"\n{GRAY}○ Parado — transcrevendo…{RESET}", flush=True)
        notify("⏳ Transcrevendo…", target_window_name)


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
        preload: bool, print_mode: bool, clipboard: bool, whisper_model: str):

    print(f"Carregando Whisper [{whisper_model}] …")
    from faster_whisper import WhisperModel
    model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
    print(f"{GREEN}Pronto.{RESET}")

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
    audio_buf     = bytearray()   # acumula PCM enquanto armado
    last_speech_t = None
    was_armed     = False
    vol_chars     = " ▁▂▃▄▅▆▇█"

    def send_text(text: str):
        nonlocal is_first
        if not text:
            return
        lang = lang_ref[0]
        # Verifica troca de idioma
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
            wname = target_window_name or "janela ativa"
            print(f"\n{CYAN}↳ {wname}: {text}{RESET}")
            xdotype(text, target_window, clipboard)

    try:
        while True:
            data = stream.read(BLOCK_SIZE, exception_on_overflow=False)

            with armed_lock:
                cur_armed = armed

            # Transição desarmado → transcrevendo e enviando
            if was_armed and not cur_armed:
                print(f"\r{' '*60}\r", end="", flush=True)
                if audio_buf:
                    text = transcribe(model, bytes(audio_buf), lang_ref[0])
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

            # Acumula áudio
            audio_buf.extend(data)

            # Indicador de volume em tempo real
            level = rms(data)
            if level > SILENCE_RMS:
                last_speech_t = time.time()
                bar_idx = min(int(level / 500), len(vol_chars) - 1)
                bar = vol_chars[bar_idx] * 8
                print(f"\r{PURPLE}🎙 {bar}{RESET} {int(level):4.0f}  ", end="", flush=True)
            else:
                print(f"\r{GRAY}🎙 {'·'*8}{RESET}       ", end="", flush=True)

            # Auto-envio por silêncio prolongado (enquanto armado)
            if (last_speech_t and
                    time.time() - last_speech_t > silence and
                    len(audio_buf) > SAMPLERATE * 0.5):  # mínimo 0.5s de áudio
                print(f"\r{' '*60}\r", end="", flush=True)
                print(f"{GRAY}[silêncio — transcrevendo…]{RESET}")
                text = transcribe(model, bytes(audio_buf), lang_ref[0])
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
        description="Voz → janela focada | faster-whisper | Linux/X11",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modelos Whisper (CPU):
  tiny   ~0.6s latência  (padrão — boa qualidade, rápido)
  small  ~2.8s latência  (melhor qualidade, mais lento)

Exemplos:
  python3 listen.py                        # pt-BR, modelo tiny
  python3 listen.py --model small          # mais preciso
  python3 listen.py --lang en              # inglês
  python3 listen.py --clipboard            # cola via Ctrl+Shift+V
  python3 listen.py --confirm              # pede confirmação antes de digitar
  python3 listen.py --print                # envia para claude --print
  python3 listen.py --list-devices
        """,
    )
    ap.add_argument("--lang",         default="pt", choices=["pt","en","es"])
    ap.add_argument("--model",        default="tiny", choices=["tiny","small"],
                    help="Modelo Whisper: tiny (rápido) ou small (mais preciso)")
    ap.add_argument("--device",       type=int,   default=DEVICE_INDEX)
    ap.add_argument("--silence",      type=float, default=SILENCE_SECS,
                    help=f"Segundos de silêncio para auto-enviar (padrão: {SILENCE_SECS})")
    ap.add_argument("--confirm",      action="store_true")
    ap.add_argument("--print",        dest="print_mode", action="store_true")
    ap.add_argument("--clipboard",    action="store_true",
                    help="Cola via xclip+Ctrl+Shift+V (melhor para TUIs)")
    ap.add_argument("--preload-all",  action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--list-models",  action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    run(
        device        = args.device,
        initial_lang  = args.lang,
        silence       = args.silence,
        confirm       = args.confirm,
        preload       = args.preload_all,
        print_mode    = args.print_mode,
        clipboard     = args.clipboard,
        whisper_model = args.model,
    )


if __name__ == "__main__":
    main()
