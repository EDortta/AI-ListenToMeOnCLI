# ListenToMeOnCLI

> Fale em vez de digitar — voz para qualquer janela no Linux (Claude CLI, Cursor, Codex, browser, qualquer app).

**Platform:** Linux / X11.
Windows já tem `Win+H` e macOS tem `Fn` duplo; este projeto preenche a lacuna no Linux e serve quem quer reconhecimento **100% offline**, sem nenhum dado de áudio saindo da máquina.

---

## Como funciona

```
Microfone → PyAudio → buffer PCM
                            ↓  (Ctrl+Space ou silêncio de 2s)
                     faster-whisper (CPU, offline)
                            ↓
                     xdotool type → janela focada
```

1. O script roda em background num terminal qualquer.
2. Você **clica na janela de destino** (Claude CLI, Cursor, terminal SSH, browser…).
3. Pressiona **Ctrl+Space** — o script captura o ID dessa janela e começa a gravar.
4. Você fala à vontade.
5. **Ctrl+Space de novo** (ou silêncio de 2 s) → o áudio vai para o Whisper, que transcreve tudo de uma vez e digita o texto na janela capturada via `xdotool`.

O Whisper recebe o bloco de áudio inteiro ao invés de processar palavra por palavra — por isso a qualidade é muito superior a soluções de streaming como VOSK.

---

## 🇧🇷 Português (Brasil)

### Requisitos

- Python 3.8+
- Linux com X11 — testado no Ubuntu 22.04
- Microfone (USB ou integrado)

```bash
# Pacotes de sistema
sudo apt-get install -y xdotool python3-pyaudio portaudio19-dev

# Pacotes Python
pip install faster-whisper pyaudio python-xlib
```

> `faster-whisper` baixa os modelos automaticamente na primeira execução (~150 MB para `tiny`, ~480 MB para `small`).

### Uso

```bash
# Inicia com modelo tiny (padrão — rápido, boa qualidade)
python3 listen.py

# Modelo small: melhor precisão de acentuação, ~3s de latência por bloco
python3 listen.py --model small

# Inglês
python3 listen.py --lang en

# Espanhol
python3 listen.py --lang es

# Cola via Ctrl+Shift+V em vez de simular teclado (melhor para TUIs)
python3 listen.py --clipboard

# Pede confirmação antes de enviar cada frase
python3 listen.py --confirm

# Modo legado: envia texto direto para `claude --print`
python3 listen.py --print

# Lista microfones disponíveis
python3 listen.py --list-devices
```

### Fluxo de uso com múltiplas janelas

```
Terminal A (listen.py rodando em background)
Terminal B (claude CLI)         ← você quer falar aqui
Terminal C (Cursor/Codex)       ← ou aqui
```

1. Clique em Terminal B (ou C).
2. Pressione **Ctrl+Space** — aparece `● GRAVANDO → Terminal B` + notificação desktop.
3. Fale sua mensagem completa, sem pressa.
4. Pressione **Ctrl+Space** novamente → transcrição aparece e é digitada em Terminal B.
5. Para mandar para outra janela: clique nela e repita.

**Se Ctrl+Space não funcionar** (outro app com o grab):
- Pressione **Enter** no terminal do listen.py
- `kill -USR1 $(cat /tmp/listen.pid)` de qualquer terminal

### Modelos Whisper disponíveis

| Modelo  | Tamanho | Latência (CPU, 5s de fala) | Qualidade |
|---------|---------|---------------------------|-----------|
| `tiny`  | ~150 MB | ~1 s                      | Boa — perde alguns acentos em pt-BR |
| `small` | ~480 MB | ~5 s                      | Ótima — acentuação quase perfeita   |

### Troca de idioma por voz

Fale a frase enquanto gravando:

| Frase                    | Troca para |
|--------------------------|-----------|
| `"trocar para inglês"`   | EN        |
| `"trocar para espanhol"` | ES        |
| `"switch to english"`    | EN        |
| `"switch to spanish"`    | ES        |
| `"cambiar a español"`    | ES        |
| `"trocar para português"` / `"switch to portuguese"` / `"cambiar a portugués"` | PT |

### Opções completas

```
--lang {pt,en,es}    Idioma inicial (padrão: pt)
--model {tiny,small} Modelo Whisper (padrão: tiny)
--device N           Índice do microfone (padrão: 3)
--silence SECS       Segundos de silêncio para auto-enviar (padrão: 2.0)
--confirm            Mostra texto reconhecido e pede confirmação
--clipboard          Cola via xclip+Ctrl+Shift+V (melhor para TUIs)
--print              Modo legado: envia para `claude --print`
--list-devices       Lista microfones e sai
```

---

## 🇺🇸 English

### Requirements

- Python 3.8+
- Linux with X11 — tested on Ubuntu 22.04
- A microphone (USB or built-in)

```bash
# System packages
sudo apt-get install -y xdotool python3-pyaudio portaudio19-dev

# Python packages
pip install faster-whisper pyaudio python-xlib
```

> `faster-whisper` downloads models automatically on first run (~150 MB for `tiny`, ~480 MB for `small`).

### How it works

```
Microphone → PyAudio → PCM buffer
                            ↓  (Ctrl+Space or 2s silence)
                     faster-whisper (CPU, fully offline)
                            ↓
                     xdotool type → focused window
```

Unlike streaming solutions (VOSK), Whisper receives the entire audio block at once — this produces much higher accuracy, especially for non-English languages.

### Usage

```bash
python3 listen.py                  # pt-BR, tiny model
python3 listen.py --lang en        # English
python3 listen.py --model small    # better accuracy, ~5s latency
python3 listen.py --clipboard      # paste via Ctrl+Shift+V (better for TUIs)
python3 listen.py --list-devices   # list microphones
```

### Multi-window workflow

1. Run `python3 listen.py` (minimize or leave in a side terminal).
2. **Click** the destination window (Claude CLI, Cursor, Codex, browser…).
3. Press **Ctrl+Space** → shows `● RECORDING → <window name>` + desktop notification.
4. Speak your full message at a natural pace.
5. Press **Ctrl+Space** again (or wait 2 s of silence) → Whisper transcribes the whole block and types it into the captured window.

**Ctrl+Space fallbacks:**
- Press **Enter** in the listen.py terminal
- `kill -USR1 $(cat /tmp/listen.pid)` from any terminal

### Whisper models

| Model   | Size    | CPU latency (5s speech) | Quality |
|---------|---------|-------------------------|---------|
| `tiny`  | ~150 MB | ~1 s                    | Good — may miss some accents |
| `small` | ~480 MB | ~5 s                    | Great — near-perfect accuracy |

### Language switching by voice

| Phrase                  | Switches to |
|-------------------------|-------------|
| `"switch to english"`   | EN          |
| `"switch to spanish"`   | ES          |
| `"trocar para português"` / `"cambiar a portugués"` / `"switch to portuguese"` | PT |

> **Why not Windows/macOS?** Windows 10/11 has `Win+H` built-in and macOS has double-tap `Fn` / Globe key. This project targets Linux, where no native equivalent exists.

---

## 🇺🇾 Español

### Requisitos

- Python 3.8+
- Linux con X11 — probado en Ubuntu 22.04
- Micrófono (USB o integrado)

```bash
# Paquetes de sistema
sudo apt-get install -y xdotool python3-pyaudio portaudio19-dev

# Paquetes Python
pip install faster-whisper pyaudio python-xlib
```

### Cómo funciona

```
Micrófono → PyAudio → buffer PCM
                           ↓  (Ctrl+Space o 2s de silencio)
                    faster-whisper (CPU, 100% offline)
                           ↓
                    xdotool type → ventana enfocada
```

A diferencia de soluciones de streaming (VOSK), Whisper recibe el bloque de audio completo de una vez — esto produce precisión mucho mayor, especialmente en español y portugués.

### Uso

```bash
python3 listen.py                  # español, modelo tiny
python3 listen.py --lang es        # español explícito
python3 listen.py --model small    # mayor precisión, ~5s latencia
python3 listen.py --clipboard      # pegar vía Ctrl+Shift+V (mejor para TUIs)
python3 listen.py --list-devices   # listar micrófonos
```

### Flujo con múltiples ventanas

1. Ejecuta `python3 listen.py` (minimiza o déjalo en una terminal lateral).
2. **Haz clic** en la ventana destino (Claude CLI, Cursor, Codex, navegador…).
3. Presiona **Ctrl+Space** → aparece `● GRABANDO → <nombre ventana>` + notificación.
4. Habla tu mensaje completo a ritmo natural.
5. Presiona **Ctrl+Space** de nuevo (o espera 2 s de silencio) → Whisper transcribe todo el bloque y lo escribe en la ventana capturada.

**Fallbacks para Ctrl+Space:**
- Presiona **Enter** en la terminal de listen.py
- `kill -USR1 $(cat /tmp/listen.pid)` desde cualquier terminal

### Modelos Whisper

| Modelo  | Tamaño  | Latencia CPU (5s de habla) | Calidad |
|---------|---------|---------------------------|---------|
| `tiny`  | ~150 MB | ~1 s                      | Buena — puede perder algunos acentos |
| `small` | ~480 MB | ~5 s                      | Excelente — precisión casi perfecta  |

### Cambio de idioma por voz

| Frase                    | Cambia a  |
|--------------------------|-----------|
| `"cambiar a inglés"`     | EN        |
| `"cambiar a portugués"`  | PT        |
| `"switch to english"`    | EN        |
| `"trocar para português"`| PT        |

> **¿Por qué no Windows/macOS?** Windows 10/11 tiene `Win+H` nativo y macOS tiene doble toque `Fn` / Globe. Este proyecto cubre la brecha en Linux.

---

## License

MIT
