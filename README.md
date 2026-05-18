# ListenToMeOnCLI

> Speak to your terminal instead of typing — voice input for Claude CLI, Cursor, Codex, or any focused window.

---

## 🇺🇸 English

### What is this?

**ListenToMeOnCLI** captures your voice via microphone, transcribes it offline using [VOSK](https://alphacephei.com/vosk/), and either:

- **types the recognized text into whatever window is focused** (via `xdotool`) — so it works with Claude CLI, Cursor, Codex, browser, or any app; or
- passes text directly to `claude --print` as a subprocess (legacy `--print` mode).

Speech recognition runs **100% offline** — no audio leaves your machine.

Supports three languages in the same session, switchable by voice:

| Code | Language          | VOSK model                   |
|------|-------------------|------------------------------|
| `pt` | Português (Brasil)| `vosk-model-small-pt-0.3`    |
| `en` | English (US)      | `vosk-model-small-en-us-0.15`|
| `es` | Español (UY/ES)   | `vosk-model-small-es-0.42`   |

### Requirements

- Python 3.8+
- Linux (X11) — tested on Ubuntu 22.04
- A microphone (USB or built-in)

```bash
# System packages
sudo apt-get install -y xdotool python3-pyaudio portaudio19-dev

# Python packages
pip install vosk pynput pyaudio
```

### Download VOSK models

```bash
cd AI-ListenToMeOnCLI

# Portuguese (Brazil) ~31 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip
unzip vosk-model-small-pt-0.3.zip && rm vosk-model-small-pt-0.3.zip

# English (US) ~40 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip

# Spanish ~33 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip
unzip vosk-model-small-es-0.42.zip && rm vosk-model-small-es-0.42.zip
```

### Usage

```bash
# Start in hotkey mode (recommended for multi-window workflows)
python3 listen.py

# Start in English
python3 listen.py --lang en

# Pre-load all 3 models into RAM for instant switching (~300 MB)
python3 listen.py --preload-all

# Ask for confirmation before typing each recognized phrase
python3 listen.py --confirm

# Legacy mode: pipe text directly to `claude --print`
python3 listen.py --print

# List available microphone devices
python3 listen.py --list-devices

# Show installed model status
python3 listen.py --list-models
```

### Hotkey mode workflow

1. Run `python3 listen.py` (minimize or leave in a side terminal).
2. **Click** the window you want to send text to (Claude CLI, Cursor, Codex, browser…).
3. Press **Ctrl+Space** → terminal shows `● ARMED` + desktop notification.
4. Speak — recognized text streams in real time.
5. Silence for ~1.8 s → text is typed into the focused window via `xdotool`.
6. Press **Ctrl+Space** again to disarm (pending text is discarded).

### Switching languages by voice

Say one of these phrases while armed:

| Phrase                  | Switches to |
|-------------------------|-------------|
| `"switch to english"`   | EN          |
| `"switch to spanish"`   | ES          |
| `"trocar para inglês"`  | EN          |
| `"trocar para espanhol"`| ES          |
| `"cambiar a español"`   | ES          |
| `"cambiar a inglés"`    | EN          |
| `"trocar para português"` / `"cambiar a portugués"` / `"switch to portuguese"` | PT |

### Microphone selection

```bash
python3 listen.py --list-devices
# [3] XWF-1080P: USB Audio  ◀ default

python3 listen.py --device 0   # use built-in mic instead
```

Default is device index `3` (USB camera mic). Edit `DEVICE_INDEX` at the top of `listen.py` to change the permanent default.

### All options

```
--lang {pt,en,es}     Initial language (default: pt)
--device N            Microphone device index (default: 3)
--silence SECS        Silence duration to trigger send (default: 1.8)
--confirm             Show recognized text and ask before typing
--print               Legacy mode: send to `claude --print` subprocess
--preload-all         Load all 3 models at startup (~300 MB RAM)
--list-devices        List audio input devices and exit
--list-models         Show installed VOSK models and exit
```

---

## 🇧🇷 Português (Brasil)

### O que é isso?

**ListenToMeOnCLI** captura sua voz pelo microfone, transcreve localmente com [VOSK](https://alphacephei.com/vosk/) (sem enviar áudio para nenhum servidor) e:

- **digita o texto reconhecido na janela que estiver focada** (via `xdotool`) — funciona com Claude CLI, Cursor, Codex, browser ou qualquer app; ou
- envia o texto diretamente para `claude --print` como subprocesso (modo `--print` legado).

Suporta três idiomas na mesma sessão, trocáveis por voz.

### Requisitos

- Python 3.8+
- Linux (X11) — testado no Ubuntu 22.04
- Microfone (USB ou integrado)

```bash
# Pacotes de sistema
sudo apt-get install -y xdotool python3-pyaudio portaudio19-dev

# Pacotes Python
pip install vosk pynput pyaudio
```

### Download dos modelos VOSK

```bash
cd AI-ListenToMeOnCLI

# Português (Brasil) ~31 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip
unzip vosk-model-small-pt-0.3.zip && rm vosk-model-small-pt-0.3.zip

# Inglês (EUA) ~40 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip

# Espanhol ~33 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip
unzip vosk-model-small-es-0.42.zip && rm vosk-model-small-es-0.42.zip
```

### Uso básico

```bash
# Modo hotkey (recomendado para múltiplas janelas)
python3 listen.py

# Iniciar em inglês
python3 listen.py --lang en

# Pré-carregar os 3 modelos na RAM (troca instantânea)
python3 listen.py --preload-all

# Ver microfones disponíveis
python3 listen.py --list-devices
```

### Fluxo com hotkey

1. Rode `python3 listen.py` (minimize ou deixe em terminal lateral).
2. **Clique** na janela de destino (terminal do Claude, Cursor, Codex, browser…).
3. Pressione **Ctrl+Space** → aparece `● ARMADO` + notificação desktop.
4. Fale — texto aparece em tempo real no terminal do script.
5. Silêncio de ~1.8 s → texto é digitado na janela focada via `xdotool`.
6. Pressione **Ctrl+Space** novamente para desarmar.

### Troca de idioma por voz

| Frase                    | Troca para |
|--------------------------|-----------|
| `"switch to english"`    | Inglês    |
| `"cambiar a español"`    | Espanhol  |
| `"trocar para inglês"`   | Inglês    |
| `"trocar para espanhol"` | Espanhol  |

---

## 🇺🇾 Español

### ¿Qué es esto?

**ListenToMeOnCLI** captura tu voz por el micrófono, la transcribe localmente con [VOSK](https://alphacephei.com/vosk/) (sin enviar audio a ningún servidor) y:

- **escribe el texto reconocido en la ventana que esté enfocada** (vía `xdotool`) — funciona con Claude CLI, Cursor, Codex, navegador o cualquier app; o
- envía el texto directamente a `claude --print` como subproceso (modo `--print` legacy).

Soporta tres idiomas en la misma sesión, cambiables por voz.

### Requisitos

- Python 3.8+
- Linux (X11) — probado en Ubuntu 22.04
- Micrófono (USB o integrado)

```bash
# Paquetes de sistema
sudo apt-get install -y xdotool python3-pyaudio portaudio19-dev

# Paquetes Python
pip install vosk pynput pyaudio
```

### Descarga de modelos VOSK

```bash
cd AI-ListenToMeOnCLI

# Portugués (Brasil) ~31 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip
unzip vosk-model-small-pt-0.3.zip && rm vosk-model-small-pt-0.3.zip

# Inglés (EE. UU.) ~40 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip

# Español ~33 MB
wget https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip
unzip vosk-model-small-es-0.42.zip && rm vosk-model-small-es-0.42.zip
```

### Uso básico

```bash
# Modo hotkey (recomendado para múltiples ventanas)
python3 listen.py

# Iniciar en inglés
python3 listen.py --lang en

# Precargar los 3 modelos en RAM (cambio instantáneo)
python3 listen.py --preload-all

# Ver micrófonos disponibles
python3 listen.py --list-devices
```

### Flujo con hotkey

1. Ejecuta `python3 listen.py` (minimiza o déjalo en una terminal lateral).
2. **Haz clic** en la ventana de destino (terminal de Claude, Cursor, Codex, navegador…).
3. Presiona **Ctrl+Space** → aparece `● ARMADO` + notificación de escritorio.
4. Habla — el texto aparece en tiempo real en la terminal del script.
5. Silencio de ~1.8 s → el texto es escrito en la ventana enfocada vía `xdotool`.
6. Presiona **Ctrl+Space** nuevamente para desarmar.

### Cambio de idioma por voz

| Frase                    | Cambia a  |
|--------------------------|-----------|
| `"switch to english"`    | Inglés    |
| `"trocar para português"`| Portugués |
| `"cambiar a inglés"`     | Inglés    |
| `"cambiar a portugués"`  | Portugués |

---

## License

MIT
