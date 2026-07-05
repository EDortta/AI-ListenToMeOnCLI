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
                     xclip → Ctrl+Shift+V → janela focada
```

1. O serviço roda em background (systemd — sem terminal aberto).
2. Pressiona **Ctrl+Space** para começar a gravar — pode estar em qualquer janela.
3. Fala à vontade. **Pode pausar, retomar e mudar de janela** enquanto fala.
4. Navega até a janela de destino.
5. **Ctrl+Space de novo** → Whisper transcreve o bloco inteiro e cola na janela ativa.

O Whisper recebe o bloco de áudio inteiro ao invés de processar palavra por palavra — por isso a qualidade é muito superior a soluções de streaming como VOSK.

---

## 🇧🇷 Português (Brasil)

### Requisitos

- Python 3.8+
- Linux com X11 — testado no Ubuntu 22.04 / MATE
- Microfone (USB ou integrado)

```bash
# Pacotes de sistema
sudo apt-get install -y xdotool xclip python3-pyaudio portaudio19-dev \
                        gir1.2-ayatanaappindicator3-0.1

# Pacotes Python
pip install faster-whisper pyaudio python-xlib noisereduce
```

> `faster-whisper` baixa os modelos automaticamente na primeira execução (~150 MB para `tiny`, ~480 MB para `small`).

### Instalação como serviço (recomendado)

Inicia automaticamente com a sessão gráfica, sem precisar de terminal aberto:

```bash
bash install.sh
```

Após instalar, gerencie com:

```bash
systemctl --user status listen        # status
systemctl --user stop listen          # parar
systemctl --user start listen         # iniciar
journalctl --user -u listen -f        # logs em tempo real
systemctl --user disable listen       # remover do autostart
```

### Ícone de bandeja (system tray)

Ao iniciar, aparece um ícone na bandeja do sistema que muda conforme o estado:

| Estado | Ícone |
|--------|-------|
| Aguardando | 🎙 microfone |
| Gravando | 🔴 círculo vermelho |
| Pausado | ⏸ pause |

**Menu (clique direito no ícone):**
- Status atual (idioma e modelo)
- **Calibrar microfone** — abre terminal de calibração
- **Modelo** → tiny / small (reinicia com novo modelo)
- **Idioma** → Português / English / Español
- **Reiniciar** / **Sair**

> No MATE: o ícone aparece no applet **xapp-status** do painel. Se não aparecer, adicione o "Indicator Applet Complete" ao painel (botão direito no painel → Adicionar ao painel).

### Calibração (recomendado na primeira vez)

```bash
listentomecli --calibrate
# ou via menu: clique direito no ícone → Calibrar microfone
```

Fase 1 (3s): fica em silêncio → captura perfil de ruído ambiente.
Fase 2 (15s): fale normalmente → calibra seu perfil de voz.

O perfil é salvo em `~/.config/listentomecli/` e usado automaticamente em todas as sessões seguintes.

### Atalhos de teclado

| Tecla | Ação |
|-------|------|
| **Ctrl+Space** | Inicia sessão de gravação |
| **Ctrl+Space** (2ª vez) | Para e envia tudo que foi gravado |
| **Ctrl+Shift+Space** | Pausa / retoma dentro da sessão (buffer acumula) |
| **Ctrl+Esc** | Cancela sessão e descarta o buffer |

> **Detalhe importante:** a janela de destino é capturada no momento de **parar** (segundo Ctrl+Space). Você pode iniciar em qualquer janela, pausar, mudar de janela, retomar, e só precisa estar na janela certa na hora de parar.

**Se Ctrl+Space não funcionar** (outro app com o grab):
- Pressione **Enter** no terminal do listen.py
- `kill -USR1 $(cat "${XDG_RUNTIME_DIR:-$HOME/.config/listentomecli}"/listentomecli.pid)` de qualquer terminal

### Fluxo típico com pausa

```
[Ctrl+Space] → grava...
[Ctrl+Shift+Space] → pausa (para ler o que vai ditar a seguir)
[Ctrl+Shift+Space] → retoma gravando (buffer acumula)
... repete quantas vezes quiser ...
[navega até a janela de destino]
[Ctrl+Space] → transcreve tudo e cola
```

### Uso

```bash
listentomecli                    # carrega perfil salvo automaticamente
listentomecli --model small      # melhor precisão (~5s latência)
listentomecli --lang en          # inglês
listentomecli --no-profile       # ignora perfil salvo para esta sessão
listentomecli --list-devices     # lista microfones disponíveis
listentomecli --calibrate        # calibra microfone e voz
```

### Modelos Whisper disponíveis

| Modelo  | Tamanho | Latência (CPU, 5s de fala) | Qualidade |
|---------|---------|---------------------------|-----------|
| `tiny`  | ~150 MB | ~1 s                      | Boa — perde alguns acentos em pt-BR |
| `small` | ~480 MB | ~5 s                      | Ótima — acentuação quase perfeita   |

> **Nota sobre acentuação:** o modelo `tiny` reconhece o conteúdo corretamente mas pode omitir acentos e cedilhas ("voce" em vez de "você", "nao" em vez de "não"). Se a acentuação for importante use `--model small` ou calibre com `--calibrate`.

### Troca de idioma por voz

Fale a frase enquanto gravando:

| Frase | Troca para |
|-------|-----------|
| `"trocar para inglês"` | EN |
| `"trocar para espanhol"` | ES |
| `"switch to english"` | EN |
| `"cambiar a español"` | ES |
| `"trocar para português"` / `"switch to portuguese"` / `"cambiar a portugués"` | PT |

### Opções completas

```
--lang {pt,en,es}    Idioma inicial (padrão: do perfil, ou pt)
--model {tiny,small} Modelo Whisper (padrão: do perfil, ou tiny)
--device N           Índice do microfone (padrão: do perfil, ou 3)
--silence SECS       Segundos de silêncio para auto-enviar (padrão: 2.0)
--confirm            Mostra texto reconhecido e pede confirmação
--clipboard          Cola via xclip+Ctrl+Shift+V (melhor para TUIs)
--print              Modo legado: envia para `claude --print`
--list-devices       Lista microfones e sai
--calibrate          Calibra microfone e voz, salva perfil
--no-profile         Ignora perfil salvo para esta sessão
```

---

## 🇺🇸 English

### Requirements

- Python 3.8+
- Linux with X11 — tested on Ubuntu 22.04 / MATE
- A microphone (USB or built-in)

```bash
# System packages
sudo apt-get install -y xdotool xclip python3-pyaudio portaudio19-dev \
                        gir1.2-ayatanaappindicator3-0.1

# Python packages
pip install faster-whisper pyaudio python-xlib noisereduce
```

> `faster-whisper` downloads models automatically on first run (~150 MB for `tiny`, ~480 MB for `small`).

### Install as a service (recommended)

Starts automatically with the desktop session — no terminal required:

```bash
bash install.sh
```

Manage with:

```bash
systemctl --user status listen        # status
systemctl --user stop listen          # stop
systemctl --user start listen         # start
journalctl --user -u listen -f        # live logs
systemctl --user disable listen       # remove from autostart
```

### System tray icon

A tray icon shows the current state:

| State | Icon |
|-------|------|
| Idle | 🎙 microphone |
| Recording | 🔴 red circle |
| Paused | ⏸ pause |

**Right-click menu:**
- Current status (language and model)
- **Calibrate microphone** — opens a calibration terminal
- **Model** → tiny / small (restarts with new model)
- **Language** → Português / English / Español
- **Restart** / **Quit**

> On MATE: the icon appears in the **xapp-status** panel applet. If it doesn't appear, add "Indicator Applet Complete" to the panel (right-click panel → Add to Panel).

### Calibration (recommended on first run)

```bash
listentomecli --calibrate
# or via tray: right-click icon → Calibrate microphone
```

Phase 1 (3s): stay silent → captures ambient noise profile.
Phase 2 (15s): speak normally → calibrates your voice profile.

Profile is saved to `~/.config/listentomecli/` and applied automatically from then on.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+Space** | Start recording session |
| **Ctrl+Space** (2nd press) | Stop and send everything recorded |
| **Ctrl+Shift+Space** | Pause / resume within session (buffer accumulates) |
| **Ctrl+Esc** | Cancel session and discard buffer |

> **Key detail:** the destination window is captured at **stop** time (second Ctrl+Space). You can start anywhere, pause, switch windows, resume, and only need to be on the target window when you stop.

**Ctrl+Space fallbacks:**
- Press **Enter** in the listentomecli terminal
- `kill -USR1 $(cat "${XDG_RUNTIME_DIR:-$HOME/.config/listentomecli}"/listentomecli.pid)` from any terminal

### Typical pause workflow

```
[Ctrl+Space] → recording...
[Ctrl+Shift+Space] → pause (read what you want to dictate next)
[Ctrl+Shift+Space] → resume (buffer accumulates)
... repeat as many times as needed ...
[switch to destination window]
[Ctrl+Space] → transcribes everything and pastes
```

### How it works

```
Microphone → PyAudio → PCM buffer
                            ↓  (Ctrl+Space or 2s silence)
                     faster-whisper (CPU, fully offline)
                            ↓
                     xclip → Ctrl+Shift+V → focused window
```

Unlike streaming solutions (VOSK), Whisper receives the entire audio block at once — this produces much higher accuracy, especially for non-English languages.

### Usage

```bash
listentomecli                    # loads saved profile automatically
listentomecli --model small      # better accuracy, ~5s latency
listentomecli --lang en          # English
listentomecli --no-profile       # ignore saved profile for this session
listentomecli --list-devices     # list microphones
listentomecli --calibrate        # calibrate mic and voice
```

### Whisper models

| Model   | Size    | CPU latency (5s speech) | Quality |
|---------|---------|-------------------------|---------|
| `tiny`  | ~150 MB | ~1 s                    | Good — may miss some accents |
| `small` | ~480 MB | ~5 s                    | Great — near-perfect accuracy |

### Language switching by voice

| Phrase | Switches to |
|--------|-------------|
| `"switch to english"` | EN |
| `"switch to spanish"` | ES |
| `"trocar para português"` / `"cambiar a portugués"` / `"switch to portuguese"` | PT |

> **Why not Windows/macOS?** Windows 10/11 has `Win+H` built-in and macOS has double-tap `Fn` / Globe key. This project targets Linux, where no native equivalent exists.

---

## 🇺🇾 Español

### Requisitos

- Python 3.8+
- Linux con X11 — probado en Ubuntu 22.04 / MATE
- Micrófono (USB o integrado)

```bash
# Paquetes de sistema
sudo apt-get install -y xdotool xclip python3-pyaudio portaudio19-dev \
                        gir1.2-ayatanaappindicator3-0.1

# Paquetes Python
pip install faster-whisper pyaudio python-xlib noisereduce
```

### Instalar como servicio (recomendado)

Se inicia automáticamente con la sesión gráfica, sin necesidad de terminal abierta:

```bash
bash install.sh
```

Gestionar con:

```bash
systemctl --user status listen        # estado
systemctl --user stop listen          # detener
systemctl --user start listen         # iniciar
journalctl --user -u listen -f        # logs en tiempo real
systemctl --user disable listen       # quitar del autostart
```

### Ícono de bandeja (system tray)

El ícono cambia según el estado:

| Estado | Ícono |
|--------|-------|
| Esperando | 🎙 micrófono |
| Grabando | 🔴 círculo rojo |
| Pausado | ⏸ pausa |

**Menú (clic derecho en el ícono):**
- Estado actual (idioma y modelo)
- **Calibrar micrófono** — abre terminal de calibración
- **Modelo** → tiny / small (reinicia con nuevo modelo)
- **Idioma** → Português / English / Español
- **Reiniciar** / **Salir**

> En MATE: el ícono aparece en el applet **xapp-status** del panel. Si no aparece, añade "Indicator Applet Complete" al panel (clic derecho en el panel → Agregar al panel).

### Calibración (recomendado la primera vez)

```bash
listentomecli --calibrate
# o desde el menú: clic derecho en el ícono → Calibrar micrófono
```

Fase 1 (3s): quédate en silencio → captura perfil de ruido ambiente.
Fase 2 (15s): habla normalmente → calibra tu perfil de voz.

El perfil se guarda en `~/.config/listentomecli/` y se aplica automáticamente en las sesiones siguientes.

### Atajos de teclado

| Tecla | Acción |
|-------|--------|
| **Ctrl+Space** | Inicia sesión de grabación |
| **Ctrl+Space** (2ª vez) | Detiene y envía todo lo grabado |
| **Ctrl+Shift+Space** | Pausa / reanuda dentro de la sesión (buffer acumula) |
| **Ctrl+Esc** | Cancela sesión y descarta el buffer |

> **Detalle clave:** la ventana destino se captura al **detener** (segundo Ctrl+Space). Puedes iniciar en cualquier ventana, pausar, cambiar de ventana, reanudar, y solo necesitas estar en la ventana correcta al detener.

**Fallbacks para Ctrl+Space:**
- Presiona **Enter** en la terminal de listentomecli
- `kill -USR1 $(cat "${XDG_RUNTIME_DIR:-$HOME/.config/listentomecli}"/listentomecli.pid)` desde cualquier terminal

### Flujo típico con pausa

```
[Ctrl+Space] → grabando...
[Ctrl+Shift+Space] → pausa (leer lo que vas a dictar)
[Ctrl+Shift+Space] → reanuda (buffer acumula)
... repite las veces que necesites ...
[navega a la ventana destino]
[Ctrl+Space] → transcribe todo y pega
```

### Cómo funciona

```
Micrófono → PyAudio → buffer PCM
                           ↓  (Ctrl+Space o 2s de silencio)
                    faster-whisper (CPU, 100% offline)
                           ↓
                    xclip → Ctrl+Shift+V → ventana enfocada
```

### Uso

```bash
listentomecli                    # carga perfil guardado automáticamente
listentomecli --model small      # mayor precisión, ~5s latencia
listentomecli --lang es          # español explícito
listentomecli --no-profile       # ignorar perfil para esta sesión
listentomecli --list-devices     # listar micrófonos
listentomecli --calibrate        # calibrar micrófono y voz
```

### Modelos Whisper

| Modelo  | Tamaño  | Latencia CPU (5s de habla) | Calidad |
|---------|---------|---------------------------|---------|
| `tiny`  | ~150 MB | ~1 s                      | Buena — puede perder algunos acentos |
| `small` | ~480 MB | ~5 s                      | Excelente — precisión casi perfecta  |

> **Nota sobre acentuación:** el modelo `tiny` puede omitir tildes y la ñ. Si la acentuación correcta es importante usa `--model small` o calibra con `--calibrate`.

### Cambio de idioma por voz

| Frase | Cambia a |
|-------|---------|
| `"cambiar a inglés"` | EN |
| `"cambiar a portugués"` | PT |
| `"switch to english"` | EN |
| `"trocar para português"` | PT |

> **¿Por qué no Windows/macOS?** Windows 10/11 tiene `Win+H` nativo y macOS tiene doble toque `Fn` / Globe. Este proyecto cubre la brecha en Linux.

---

## License

MIT
