#!/usr/bin/env python3
"""
listen.py — Voice input for Claude CLI / Cursor / Codex / any window

Modo hotkey (padrão):
  1. Rode este script em background (qualquer terminal)
  2. Pressione Ctrl+Space para armar
  3. Fale — texto reconhecido aparece no terminal deste script
  4. Silêncio → texto é "digitado" (xdotool) na janela que estava focada
  5. Ctrl+Space novamente para desarmar sem enviar

Modo --print (legado):
  Passa texto direto para `claude --print` como subprocesso.

Idiomas: pt (padrão) | en | es   — troca em runtime dizendo as frases de switch.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import threading
import pyaudio
from vosk import Model, KaldiRecognizer
from pynput import keyboard as kb

# ── Configurações padrão ─────────────────────────────────────────────────────
SAMPLERATE   = 16000
BLOCK_SIZE   = 4000          # ~250ms por chunk
SILENCE_SECS = 1.8
DEVICE_INDEX = 3             # XWF-1080P USB
CLAUDE_CMD   = os.path.expanduser("~/.local/bin/claude")
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))

# Hotkey padrão: Ctrl+Space
HOTKEY_MODS = {kb.Key.ctrl}
HOTKEY_KEY  = kb.KeyCode.from_char(' ')

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

# ── Estado global compartilhado entre threads ─────────────────────────────────
armed       = False
armed_lock  = threading.Lock()
pressed_now = set()          # teclas modificadoras pressionadas

# ── Cores ANSI ───────────────────────────────────────────────────────────────
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
    """Digita texto na janela atualmente focada via xdotool."""
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "20", "--", text],
            check=True
        )
    except FileNotFoundError:
        print(f"{RED}xdotool não encontrado. Instale: sudo apt install xdotool{RESET}")
        print(f"{CYAN}Texto reconhecido: {text}{RESET}")
    except subprocess.CalledProcessError as e:
        print(f"{RED}xdotool erro: {e}{RESET}")


def ask_claude_print(text: str, is_first: bool):
    """Modo legado --print: passa texto para claude como subprocesso."""
    cmd = [CLAUDE_CMD, "--print"]
    if not is_first:
        cmd.append("--continue")
    cmd.append(text)
    print(f"\n{CYAN}▶ {text}{RESET}")
    print(f"{YELLOW}─────────────────────────────{RESET}")
    result = subprocess.run(cmd, text=True)
    print(f"{YELLOW}─────────────────────────────{RESET}\n")
    if result.returncode != 0:
        print(f"{RED}Erro claude: exit {result.returncode}{RESET}")


def set_armed(value: bool, lang: str):
    global armed
    with armed_lock:
        armed = value
    if value:
        label = LANG_LABELS.get(lang, lang)
        print(f"\n{BOLD}{GREEN}● ARMADO [{label}] — fale agora{RESET}")
        notify("🎙 Ouvindo", f"Idioma: {label}")
    else:
        print(f"\n{GRAY}○ Desarmado{RESET}")
        notify("🔇 Mudo", "")


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


def check_models(langs):
    missing = []
    for lang in langs:
        path = MODELS.get(lang)
        if not path or not os.path.isdir(path):
            missing.append((lang, MODELS.get(lang, "?")))
    return missing


def load_model(lang: str):
    path = MODELS[lang]
    print(f"  Carregando [{lang}] {LANG_LABELS[lang]} …")
    model = Model(path)
    rec   = KaldiRecognizer(model, SAMPLERATE)
    rec.SetWords(True)
    return model, rec


# ── Listener de hotkey (thread separada) ─────────────────────────────────────

def make_hotkey_listener(initial_lang_ref: list):
    """
    Retorna um pynput Listener que togla armed com Ctrl+Space.
    initial_lang_ref é uma lista de 1 elemento para acesso por referência.
    """
    def on_press(key):
        pressed_now.add(key)
        is_ctrl  = kb.Key.ctrl in pressed_now or kb.Key.ctrl_l in pressed_now or kb.Key.ctrl_r in pressed_now
        is_space = key == HOTKEY_KEY or (hasattr(key, 'char') and key.char == ' ')
        if is_ctrl and is_space:
            current = initial_lang_ref[0]
            with armed_lock:
                new_val = not armed
            set_armed(new_val, current)

    def on_release(key):
        pressed_now.discard(key)

    return kb.Listener(on_press=on_press, on_release=on_release, daemon=True)


# ── Loop principal de áudio ──────────────────────────────────────────────────

def run(device: int, initial_lang: str, silence: float, confirm: bool,
        preload: bool, print_mode: bool):

    langs_to_load = list(MODELS.keys()) if preload else [initial_lang]
    missing = check_models(langs_to_load)
    if missing:
        for lang, path in missing:
            print(f"{RED}Modelo [{lang}] não encontrado: {path}{RESET}")
        sys.exit(1)

    models: dict = {}
    recs:   dict = {}
    if preload:
        print("Pré-carregando todos os modelos:")
        for lang in MODELS:
            models[lang], recs[lang] = load_model(lang)
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

    current_lang_ref = [initial_lang]   # lista para mutabilidade entre closures

    if not print_mode:
        listener = make_hotkey_listener(current_lang_ref)
        listener.start()
        print(f"\n{BOLD}Pressione Ctrl+Space para armar/desarmar o microfone.{RESET}")
        print(f"{GRAY}Texto reconhecido será digitado na janela focada (xdotool).{RESET}")
        print(f"{GRAY}Ctrl+C para encerrar.{RESET}\n")
    else:
        set_armed(True, initial_lang)
        print(f"{GRAY}Modo --print: envia direto para claude CLI.{RESET}")
        print(f"{GRAY}Ctrl+C para encerrar.{RESET}\n")

    is_first_claude  = True
    pending_text     = []
    last_speech_ts   = None
    was_armed        = False

    try:
        while True:
            data = stream.read(BLOCK_SIZE, exception_on_overflow=False)

            with armed_lock:
                currently_armed = armed

            # Ao desarmar, descarta texto pendente sem enviar
            if was_armed and not currently_armed:
                if pending_text:
                    print(f"\n{GRAY}[descartado]{RESET}")
                pending_text.clear()
                last_speech_ts = None
            was_armed = currently_armed

            if not currently_armed:
                continue

            lang = current_lang_ref[0]
            if lang not in recs:
                # Carga sob demanda ao trocar idioma
                missing = check_models([lang])
                if missing:
                    print(f"{RED}Modelo [{lang}] ausente{RESET}")
                    current_lang_ref[0] = initial_lang
                    continue
                print(f"Carregando modelo [{lang}] sob demanda…")
                models[lang], recs[lang] = load_model(lang)

            rec = recs[lang]

            if rec.AcceptWaveform(data):
                res  = json.loads(rec.Result())
                text = res.get("text", "").strip()
                if text:
                    pending_text.append(text)
                    last_speech_ts = time.time()
                    print(f"\r{PURPLE}◉{RESET} {' '.join(pending_text)}   ", end="", flush=True)
            else:
                partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                if partial:
                    last_speech_ts = time.time()
                    print(f"\r{PURPLE}◉{RESET} {' '.join(pending_text)} {partial}   ", end="", flush=True)

            # Fim de fala por silêncio
            if pending_text and last_speech_ts and (time.time() - last_speech_ts) > silence:
                final = " ".join(pending_text).strip()
                pending_text.clear()
                last_speech_ts = None

                if not final:
                    continue

                print()

                # Troca de idioma por voz
                target = SWITCH_TARGETS.get(final.lower().rstrip(".!?"))
                if target:
                    if target != current_lang_ref[0]:
                        current_lang_ref[0] = target
                        print(f"{GREEN}⇄  Idioma: {LANG_LABELS[target]}{RESET}\n")
                    continue

                if confirm:
                    print(f"Enviar: \"{final}\" ? [Enter=sim / texto=editar / n=descartar] ", end="", flush=True)
                    answer = input()
                    if answer.lower() == "n":
                        print("Descartado.\n")
                        continue
                    if answer.strip():
                        final = answer.strip()

                if print_mode:
                    ask_claude_print(final, is_first_claude)
                    is_first_claude = False
                else:
                    print(f"{CYAN}↳ digitando: {final}{RESET}")
                    xdotype(final)

    except KeyboardInterrupt:
        print(f"\n{GRAY}Encerrando…{RESET}")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Voz → janela focada (xdotool) ou claude --print",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python3 listen.py                   # hotkey Ctrl+Space → digita na janela focada
  python3 listen.py --lang en         # inicia em inglês
  python3 listen.py --preload-all     # carrega pt+en+es na RAM de uma vez
  python3 listen.py --print           # modo legado: envia para claude --print
  python3 listen.py --confirm         # mostra texto e pede confirmação antes
  python3 listen.py --list-devices    # lista microfones
  python3 listen.py --list-models     # status dos modelos instalados
        """,
    )
    ap.add_argument("--lang",         default="pt", choices=["pt", "en", "es"])
    ap.add_argument("--device",       type=int,   default=DEVICE_INDEX)
    ap.add_argument("--silence",      type=float, default=SILENCE_SECS,
                    help=f"Segundos de silêncio para disparar (padrão: {SILENCE_SECS})")
    ap.add_argument("--confirm",      action="store_true",
                    help="Pede confirmação antes de digitar/enviar")
    ap.add_argument("--print",        dest="print_mode", action="store_true",
                    help="Modo legado: passa texto para claude --print em vez de xdotool")
    ap.add_argument("--preload-all",  action="store_true",
                    help="Pré-carrega os 3 modelos (~300MB RAM)")
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
            status = f"{GREEN}✓ instalado{RESET}" if ok else f"{RED}✗ ausente{RESET}"
            print(f"  [{lang}] {LANG_LABELS[lang]:28s} {status}")
            print(f"       {path}")
        print()
        return

    run(
        device      = args.device,
        initial_lang= args.lang,
        silence     = args.silence,
        confirm     = args.confirm,
        preload     = args.preload_all,
        print_mode  = args.print_mode,
    )


if __name__ == "__main__":
    main()
