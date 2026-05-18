#!/usr/bin/env python3
"""
listen.py — Voice input for Claude CLI / Cursor / Codex / any window

Toggle (armar/desarmar microfone) — três formas, use a que funcionar:
  1. SIGNAL:   kill -USR1 $(cat /tmp/listen.pid)   ← mais confiável
  2. HOTKEY:   Ctrl+F8  (via pynput/Xlib)
  3. STDIN:    pressione Enter neste terminal (fallback sempre disponível)

Modo --print (legado): passa texto direto para `claude --print`.
Idiomas: pt (padrão) | en | es — trocáveis por voz.
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
armed      = False
armed_lock = threading.Lock()

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


def xdotype(text: str):
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "20", "--", text],
            check=True
        )
    except FileNotFoundError:
        print(f"{RED}xdotool não encontrado. Instale: sudo apt install xdotool{RESET}")
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
    global armed
    with armed_lock:
        armed = not armed
        new_val = armed
    label = LANG_LABELS.get(lang_ref[0], lang_ref[0])
    if new_val:
        print(f"\n{BOLD}{GREEN}● ARMADO [{label}] — fale agora{RESET}  ", flush=True)
        notify("🎙 Ouvindo", f"Idioma: {label}")
    else:
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


# ── Thread: toggle por hotkey Xlib (Ctrl+F8) ────────────────────────────────

def start_hotkey_listener(lang_ref: list):
    """Tenta iniciar listener pynput. Falha silenciosamente se não funcionar."""
    try:
        from pynput import keyboard as kb

        pressed = set()

        def on_press(key):
            pressed.add(key)
            ctrl  = kb.Key.ctrl in pressed or kb.Key.ctrl_l in pressed or kb.Key.ctrl_r in pressed
            is_f8 = key == kb.Key.f8
            if ctrl and is_f8:
                toggle_armed(lang_ref)

        def on_release(key):
            pressed.discard(key)

        listener = kb.Listener(on_press=on_press, on_release=on_release, daemon=True)
        listener.start()
        # testa se listener está vivo após 0.5s
        time.sleep(0.5)
        if listener.is_alive():
            print(f"{GRAY}Hotkey Ctrl+F8 ativa (pynput){RESET}")
            return True
    except Exception as e:
        print(f"{GRAY}pynput indisponível: {e}{RESET}")
    return False


# ── Thread: toggle por Enter no stdin ────────────────────────────────────────

def start_stdin_listener(lang_ref: list):
    def _loop():
        while True:
            try:
                input()   # bloqueia até Enter
                toggle_armed(lang_ref)
            except EOFError:
                break
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ── Loop principal de áudio ──────────────────────────────────────────────────

def run(device: int, initial_lang: str, silence: float, confirm: bool,
        preload: bool, print_mode: bool):

    # Valida modelos
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

    # Escreve PID para controle externo
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Registra SIGUSR1 como toggle
    signal.signal(signal.SIGUSR1, lambda s, f: toggle_armed(lang_ref))

    if print_mode:
        global armed
        with armed_lock:
            armed = True
        print(f"{GRAY}Modo --print: envia direto para claude CLI. Ctrl+C para sair.{RESET}\n")
    else:
        hotkey_ok = start_hotkey_listener(lang_ref)
        start_stdin_listener(lang_ref)

        print(f"\n{BOLD}Como armar/desarmar:{RESET}")
        print(f"  {GREEN}1.{RESET} {BOLD}kill -USR1 $(cat {PID_FILE}){RESET}  ← de qualquer terminal / alias")
        if hotkey_ok:
            print(f"  {GREEN}2.{RESET} Ctrl+F8  ← hotkey global (pynput ativo)")
        else:
            print(f"  {YELLOW}2.{RESET} Ctrl+F8 não disponível (pynput/X11 sem acesso a /dev/input)")
        print(f"  {GREEN}3.{RESET} Enter neste terminal")
        print(f"\n{GRAY}PID: {os.getpid()} | arquivo: {PID_FILE}{RESET}")
        print(f"{GRAY}Ctrl+C para encerrar.{RESET}\n")

    is_first     = True
    pending_text = []
    last_ts      = None
    was_armed    = False

    try:
        while True:
            data = stream.read(BLOCK_SIZE, exception_on_overflow=False)

            with armed_lock:
                cur_armed = armed

            if was_armed and not cur_armed:
                if pending_text:
                    print(f"\n{GRAY}[descartado]{RESET}")
                pending_text.clear()
                last_ts = None
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
                    print(f"{CYAN}↳ digitando: {final}{RESET}")
                    xdotype(final)

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
        description="Voz → janela focada (xdotool) ou claude --print",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Toggle (armar mic):
  kill -USR1 $(cat {PID_FILE})   ← de qualquer terminal
  Ctrl+F8                         ← hotkey global (se pynput funcionar)
  Enter neste terminal            ← sempre funciona

Exemplos:
  python3 listen.py               # modo xdotool, pt-BR
  python3 listen.py --lang en     # inglês
  python3 listen.py --preload-all # carrega pt+en+es na RAM
  python3 listen.py --print       # modo legado claude --print
  python3 listen.py --confirm     # pede confirmação antes de digitar
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
