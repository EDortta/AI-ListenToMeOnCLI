# RESUME — ListenToMeOnCLI

Última sessão: 2026-05-18

---

## Estado atual

- **Branch ativa:** `feature/calibration-accuracy-test`
- **main** está limpo e estável — serviço rodando em produção
- **Serviço:** `systemctl --user status listen` — active, auto-start com a sessão gráfica

---

## O que foi feito nesta sessão

### Commits em main (cronológico)
| Hash | O quê |
|------|-------|
| `810df97` | README completo em PT/EN/ES com hotkeys, tray, pause workflow |
| `875c41f` | Limite de 30s de gravação ativa com beeps de aviso + estado pendente |
| `e2ac22a` | Remove blink do ícone — estado pendente usa ícone ⏸ estático |
| `f60d84c` | Fix `UnboundLocalError recording_paused` dentro de `run()` |
| `ef09c03` | docs/index.html atualizado |

### Branch `feature/calibration-accuracy-test` (commit `28253bc`)
- Adicionada **Etapa 3** na calibração: mede acurácia do Whisper lendo um texto fixo
- Textos de domínio público ~15s: Dom Casmurro (PT), Alice in Wonderland (EN), Dom Quixote (ES)
- Calcula WER (word error rate) via edit distance palavra-a-palavra
- Mostra diff colorido (verde=certo, vermelho=erro, amarelo=inserção) + barra percentual
- **Esta branch NÃO foi mergeada em main ainda**

---

## Próximos passos

1. **Testar** a Etapa 3 de calibração rodando `listentomecli --calibrate` e verificando:
   - O texto aparece bem formatado no terminal
   - O diff colorido é legível
   - A acurácia faz sentido com o modelo `tiny` vs `small`

2. **Mergear** `feature/calibration-accuracy-test` → `main` se o teste for satisfatório

3. **Ideia pendente:** o usuário mencionou que o modelo `tiny` perde acentos ("voc est", "decim"). Considerar:
   - Sugerir `--model small` automaticamente se acurácia < 70% na Etapa 3
   - Ou guardar no perfil qual modelo alcançou boa acurácia e usá-lo como padrão

4. **Branch `feature/noisereduce-calibrate`** — existe remotamente, verificar se já foi mergeada ou pode ser deletada

---

## Arquitetura atual do listen.py

```
Estados:   IDLE ←→ RECORDING ←→ PAUSED
           IDLE ← PENDING (texto pronto, aguardando janela destino)

Globais-chave:
  armed              bool   — False=IDLE, True=RECORDING/PAUSED
  recording_paused   bool   — True=PAUSED (só válido quando armed=True)
  _pending_text      [str]  — texto transcrito aguardando Ctrl+Space para colar
  _auto_stop_flag    [bool] — sinaliza auto-stop por limite de 30s
  _transcribing_flag [bool] — bloqueia novo Ctrl+Space enquanto transcreve
  _send_fn           [fn]   — send_text injetado por run()
  REC_LIMIT_SECS     = 30   — limite de gravação ativa em segundos

Hotkeys:
  Ctrl+Space           → inicia / para+envia / (se pendente) cola na janela atual
  Ctrl+Shift+Space     → pausa / retoma (buffer acumula)
  Ctrl+Esc             → cancela sessão ou descarta texto pendente

Beeps (via aplay, PCM gerado em runtime):
  10s → 1 beep  |  20s → 2 beeps  |  25s → 3 rápidos  |  30s → 1 longo (440Hz)

Tray (XApp.StatusIcon — nativo MATE):
  idle      → audio-input-microphone
  recording → media-record
  paused    → media-playback-pause   (também usado para estado pendente)
```

---

## Instalação / deploy

```bash
# Qualquer mudança em listen.py:
cp listen.py ~/.local/bin/listentomecli
systemctl --user restart listen

# Ou via install.sh (copia tudo + assets + recarrega service):
bash install.sh
```

---

## Comandos úteis

```bash
systemctl --user status listen          # status do serviço
journalctl --user -u listen -f          # logs em tempo real
listentomecli --calibrate               # calibrar (inclui teste de acurácia)
listentomecli --model small             # mais preciso, ~5s latência
```
