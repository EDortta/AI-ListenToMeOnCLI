#!/usr/bin/env python3
"""
listen.py — Voice input for Claude CLI / Cursor / Codex / any window (Linux/X11)

Toggle Ctrl+Space (armar/desarmar microfone):
  - Registrado via XGrabKey no X server — funciona de qualquer janela
  - Fallback: Enter neste terminal
  - Fallback: kill -USR1 $(cat /tmp/listen.pid)

Modo --print (legado): passa texto direto para `claude --print`.
Idiomas: pt (padrão) | en | es — trocáveis por voz durante a sessão.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import threading
import pyaudio
from vosk import Model, KaldiRecognizer

PID_FILE = "/tmp/listen.pid"

# ── Configurações padrão ─────────────────────────────────────────────────────
SAMPLERATE   = 16000
BLOCK_SIZE   = 4000
SILENCE_SECS = 1.8
DEVICE_INDEX = 3
CLAUDE_CMD   = os.path.expanduser("~/.local/bin/claude")
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))

MODELS = {
    "pt": os.path.join(SCRIPT_DIR, "vosk-model-small-pt-0.3"),
    "en": os.path.join(SCRIPT_DIR, "vosk-model-small-en-us-0.15"),
    "es": os.path.join(SCRIPT_DIR, "vosk-model-small-es-0.42"),
}
LANG_LABELS = {
    "pt": "Português (Brasil)",
    "en": "English (US)",
    "es": "Español (UY/ES)",
}
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

# ── Estado global ────────────────────────────────────────────────────────────
armed         = False
armed_lock    = threading.Lock()
target_window = None   # ID da janela capturada no momento do armar

RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
PURPLE = "\033[35m"
GRAY   = "\033[90m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ── Helpers ──────────────────────────────────────────────────────────────────

def notify(title: str, body: str):
    try:
        subprocess.run(["notify-send", "-t", "2000", title, body],
                       capture_output=True)
    except Exception:
        pass


def get_active_window() -> str | None:
    """Retorna o ID da janela atualmente focada, ou None se falhar."""
    try:
        r = subprocess.run(["xdotool", "getactivewindow"],
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return None


def xdotype(text: str, window_id: str | None = None):
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


def toggle_armed(lang_ref: list):
    global armed, target_window
    with armed_lock:
        armed = not armed
        new_val = armed
    label = LANG_LABELS.get(lang_ref[0], lang_ref[0])
    if new_val:
        # Captura a janela focada AGORA, antes de qualquer output no terminal
        target_window = get_active_window()
        win_info = f" [win:{target_window}]" if target_window else ""
        print(f"\n{BOLD}{GREEN}● ARMADO [{label}]{win_info} — fale agora{RESET}", flush=True)
        notify("🎙 Ouvindo", f"Idioma: {label}")
    else:
        target_window = None
        print(f"\n{GRAY}○ Desarmado{RESET}", flush=True)
        notify("🔇 Mudo", "")


def load_model(lang: str):
    path = MODELS[lang]
    print(f"  Carregando [{lang}] {LANG_LABELS[lang]} …")
    model = Model(path)
    rec   = KaldiRecognizer(model, SAMPLERATE)
    rec.SetWords(True)
    return model, rec


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


# ── Hotkey Ctrl+Space via XGrabKey (python-xlib) ─────────────────────────────

def start_xlib_hotkey(lang_ref: list) -> bool:
    """
    Registra Ctrl+Space no X server via XGrabKey.
    Roda em thread daemon; retorna True se conseguiu registrar.
    """
    try:
        from Xlib import X, XK, display as xdisplay
        from Xlib.error import BadAccess

        dpy  = xdisplay.Display()
        root = dpy.screen().root
        code = dpy.keysym_to_keycode(XK.string_to_keysym("space"))

        # Registra para todas as combinações de NumLock/CapsLock/etc.
        modifiers = [
            X.ControlMask,
            X.ControlMask | X.Mod2Mask,   # + NumLock
            X.ControlMask | X.LockMask,   # + CapsLock
            X.ControlMask | X.Mod2Mask | X.LockMask,
        ]
        grabbed = False
        for mod in modifiers:
            try:
                root.grab_key(code, mod, False, X.GrabModeAsync, X.GrabModeAsync)
                grabbed = True
            except BadAccess:
                pass

        if not grabbed:
            return False

        dpy.flush()

        def _loop():
            while True:
                event = dpy.next_event()
                if event.type == X.KeyPress:
                    toggle_armed(lang_ref)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        return True

    except Exception as e:
        print(f"{GRAY}XGrabKey falhou: {e}{RESET}")
        return False


# ── Thread: toggle por Enter no stdin ────────────────────────────────────────

def start_stdin_toggle(lang_ref: list):
    def _loop():
        while True:
            try:
                input()
                toggle_armed(lang_ref)
            except EOFError:
                break
    threading.Thread(target=_loop, daemon=True).start()


# ── Loop principal de áudio ──────────────────────────────────────────────────

def run(device: int, initial_lang: str, silence: float, confirm: bool,
        preload: bool, print_mode: bool):

    langs = list(MODELS.keys()) if preload else [initial_lang]
    missing = [l for l in langs if not os.path.isdir(MODELS[l])]
    if missing:
        for l in missing:
            print(f"{RED}Modelo [{l}] não encontrado: {MODELS[l]}{RESET}")
        sys.exit(1)

    models, recs = {}, {}
    if preload:
        print("Pré-carregando todos os modelos:")
        for l in MODELS:
            models[l], recs[l] = load_model(l)
    else:
        print("Carregando modelo:")
        models[initial_lang], recs[initial_lang] = load_model(initial_lang)

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
            print(f"\n{BOLD}Ctrl+Space{RESET} para armar/desarmar (registrado via XGrabKey)")
        else:
            print(f"\n{YELLOW}Ctrl+Space não disponível{RESET} — outro app pode ter o grab")
            print(f"  Alternativas: {BOLD}Enter{RESET} neste terminal  ou  {BOLD}kill -USR1 $(cat {PID_FILE}){RESET}")

        print(f"{GRAY}PID: {os.getpid()} | Ctrl+C para encerrar{RESET}\n")

    is_first     = True
    pending_text = []
    last_ts      = None
    was_armed    = False
    current_rec  = None   # recognizer ativo, para flush no disarm

    try:
        while True:
            data = stream.read(BLOCK_SIZE, exception_on_overflow=False)

            with armed_lock:
                cur_armed = armed

            # Transição armado → desarmado: envia o que foi reconhecido
            if was_armed and not cur_armed:
                if current_rec:
                    # Força flush do áudio ainda em buffer no VOSK
                    flushed = json.loads(current_rec.FinalResult()).get("text", "").strip()
                    if flushed:
                        pending_text.append(flushed)

                final = " ".join(pending_text).strip()
                pending_text.clear()
                last_ts = None
                print()

                if final:
                    if print_mode:
                        ask_claude_print(final, is_first)
                        is_first = False
                    else:
                        win = target_window
                        win_info = f" → win:{win}" if win else " → janela ativa"
                        print(f"{CYAN}↳ digitando{win_info}: {final}{RESET}")
                        xdotype(final, win)

            was_armed = cur_armed

            if not cur_armed:
                continue

            lang = lang_ref[0]
            if lang not in recs:
                if not os.path.isdir(MODELS[lang]):
                    print(f"{RED}Modelo [{lang}] ausente{RESET}")
                    lang_ref[0] = initial_lang
                    continue
                models[lang], recs[lang] = load_model(lang)

            rec = recs[lang]
            current_rec = rec

            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "").strip()
                if text:
                    pending_text.append(text)
                    last_ts = time.time()
                    print(f"\r{PURPLE}◉{RESET} {' '.join(pending_text)}   ", end="", flush=True)
            else:
                partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                if partial:
                    last_ts = time.time()
                    print(f"\r{PURPLE}◉{RESET} {' '.join(pending_text)} {partial}   ", end="", flush=True)

            if pending_text and last_ts and (time.time() - last_ts) > silence:
                final = " ".join(pending_text).strip()
                pending_text.clear()
                last_ts = None
                print()

                if not final:
                    continue

                target = SWITCH_TARGETS.get(final.lower().rstrip(".!?"))
                if target:
                    if target != lang_ref[0]:
                        lang_ref[0] = target
                        print(f"{GREEN}⇄  Idioma: {LANG_LABELS[target]}{RESET}\n")
                    continue

                if confirm:
                    print(f"Enviar: \"{final}\" ? [Enter=sim / texto=editar / n=descartar] ", end="", flush=True)
                    ans = input()
                    if ans.lower() == "n":
                        print("Descartado.\n")
                        continue
                    if ans.strip():
                        final = ans.strip()

                if print_mode:
                    ask_claude_print(final, is_first)
                    is_first = False
                else:
                    win = target_window
                    win_info = f" → win:{win}" if win else " → janela ativa"
                    print(f"{CYAN}↳ digitando{win_info}: {final}{RESET}")
                    xdotype(final, win)

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
        description="Voz → janela focada (xdotool) | Linux/X11",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python3 listen.py               # pt-BR, hotkey Ctrl+Space
  python3 listen.py --lang en     # inglês
  python3 listen.py --preload-all # carrega pt+en+es na RAM (~300 MB)
  python3 listen.py --print       # modo legado: envia para claude --print
  python3 listen.py --confirm     # pede confirmação antes de digitar
  python3 listen.py --list-devices
  python3 listen.py --list-models
        """,
    )
    ap.add_argument("--lang",         default="pt", choices=["pt","en","es"])
    ap.add_argument("--device",       type=int,   default=DEVICE_INDEX)
    ap.add_argument("--silence",      type=float, default=SILENCE_SECS)
    ap.add_argument("--confirm",      action="store_true")
    ap.add_argument("--print",        dest="print_mode", action="store_true")
    ap.add_argument("--preload-all",  action="store_true")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--list-models",  action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    if args.list_models:
        print("\nModelos VOSK:")
        for lang, path in MODELS.items():
            ok = os.path.isdir(path)
            s  = f"{GREEN}✓ instalado{RESET}" if ok else f"{RED}✗ ausente{RESET}"
            print(f"  [{lang}] {LANG_LABELS[lang]:28s} {s}")
            print(f"       {path}")
        print()
        return

    run(
        device       = args.device,
        initial_lang = args.lang,
        silence      = args.silence,
        confirm      = args.confirm,
        preload      = args.preload_all,
        print_mode   = args.print_mode,
    )


if __name__ == "__main__":
    main()
