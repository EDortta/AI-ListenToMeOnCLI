# SUGGESTIONS — rota crítica para o ícone “sumir”

Contexto: o tray hoje é criado via `XApp.StatusIcon()` e o main loop GTK (`Gtk.main()`) roda em um thread `daemon` (ver `listen.py:723-756`). Isso é o principal cheiro de bug: GTK/GLib normalmente pressupõe **main loop no thread principal** e o host do tray pode se comportar de forma imprevisível quando o loop não está no main thread. Em paralelo, `StatusIcon`/XEmbed é “legacy tray” e pode ser invisível/instável dependendo do desktop environment (DE).

Este documento propõe uma rota de investigação → correção com o mínimo de risco, priorizando previsibilidade em Cinnamon/MATE/XFCE/KDE e um fallback claro para GNOME.

## 0) Objetivo e “Definition of Done”

- Ícone do ListenToMeOnCLI permanece visível por horas/dias sem desaparecer.
- Menu do tray abre consistentemente.
- Mudanças de estado (`idle/recording/paused`) atualizam o ícone sem travar.
- Ao reiniciar o serviço (`systemctl --user restart listen`), o ícone volta determinística e rapidamente.
- Diagnóstico: logs permitem diferenciar “caiu o processo” vs “tray host ocultou o ícone”.

## 1) Primeiro: separar as causas (sem adivinhação)

### 1.1. Quando o ícone some, o processo ainda está vivo?

Evidência exigida:
- `systemctl --user status listen.service`
- `journalctl --user -u listen.service -n 200 --no-pager`

Interpretação:
- Se o serviço reiniciou ou morreu: causa é crash/exit (bug runtime, dependências, ambiente).
- Se o serviço está “active (running)” mas o ícone sumiu: causa é UI/tray host/backend do indicador.

### 1.2. Qual DE e qual “tray implementation” real?

Você precisa registrar explicitamente (porque muda tudo):
- DE: GNOME / KDE / XFCE / Cinnamon / MATE / i3 / etc.
- Suporte a AppIndicator/KStatusNotifier (em GNOME, geralmente requer extensão).

Sem isso, qualquer correção pode “funcionar na máquina errada”.

## 2) Hipótese #1 (mais provável): `Gtk.main()` em thread daemon

Sinais típicos:
- Ícone aparece e some sem o processo morrer.
- Menu às vezes não abre, ou some após algum evento (sleep/resume, lock/unlock).

Problema estrutural atual:
- `Gtk.main()` é iniciado em thread `daemon` (`listen.py:752`).
- Em muitas stacks GTK, isso é comportamento indefinido; o host do tray pode deixar de receber eventos.

### Rota de correção (preferida, alta confiabilidade)

**Mover o GTK main loop para o thread principal** e jogar o loop de áudio para um worker thread.

Plano concreto (alto nível):
- Thread principal:
  - Inicializa GTK/XApp.
  - Cria `StatusIcon`.
  - Entra no `Gtk.main()`.
- Worker thread:
  - Faz `run()` do áudio + transcrição.
  - Quando quiser atualizar ícone, usa `GLib.idle_add(...)` (já existe `_set_tray_state()`).

Riscos:
- Reorganização de controle de fluxo (não é refactor cosmético; é mudança de arquitetura “single-process, multi-thread”).

Mitigação:
- Fazer a mudança em passos pequenos, com logs claros e rollback simples.

Critério de aceitação:
- Ícone não desaparece mais em sessões longas.
- Não há deadlock entre áudio thread e GTK thread.

## 3) Hipótese #2: `XApp.StatusIcon` / XEmbed “legacy tray” não é suportado/é filtrado

Sinais típicos:
- Em GNOME, ícone não aparece ou some aleatoriamente.
- Em KDE pode aparecer mas com comportamento irregular.

### Rota de correção (compatibilidade moderna)

Trocar de backend para um padrão mais aceito:

Opção A (recomendada): AppIndicator / AyatanaAppIndicator
- Já existe `_best_pystray_backend()` no topo do arquivo, mas **não está sendo usado** pelo tray atual (XApp).
- Rota: implementar tray via `pystray` com backend AppIndicator (quando disponível).
- Observação crítica: `pystray` em alguns ambientes cai em backends frágeis; tem que ser testado no DE alvo.

Opção B: StatusNotifierItem (KStatusNotifierItem) via bindings adequados
- Melhor para KDE.
- Mais trabalho e mais dependências.

Aceitação:
- GNOME: se não houver extensão de indicadores, documentar que não há tray (mas o hotkey funciona).
- KDE/XFCE/Cinnamon/MATE: ícone aparece e permanece.

## 4) Hipótese #3: ambiente do systemd user service está inconsistente

Sinais típicos:
- Só falha quando auto-start (boot/login), mas funciona quando rodado manualmente no terminal.
- Some após lock/unlock, troca de sessão, ou sleep/resume.

`listen.service` hoje usa `PassEnvironment=DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS ...`.

Crítica:
- `PassEnvironment` depende de quais variáveis estavam presentes **no momento em que systemd capturou** a unidade / sessão.
- Em setups com múltiplas sessões, isso pode apontar para um bus/display que não existe mais.

Rota de investigação:
- Comparar ambiente quando rodado manualmente vs via systemd (`/proc/<pid>/environ`).
- Confirmar que o DBus session address é válido.

Rota de correção (somente se confirmado):
- Injetar ambiente via `Environment=` explícito, ou usar mecanismos de importação de ambiente de sessão (`systemctl --user import-environment ...`) no login.
- Documentar isso no `README.md` e no `listen.service`.

## 5) Instrumentação mínima (para parar de “achar”)

Adicionar logs (curtos, sem spam) para:
- “GTK loop started” + thread name/id
- “StatusIcon set_visible(True) called”
- “_set_tray_state(state)” com throttling
- Se possível: callback/erro ao setar ícone/menu

Meta: quando o ícone some, o log diz se:
- o loop GTK morreu
- o processo continua vivo
- as atualizações de ícone continuam sendo chamadas

## 6) Sequência recomendada (rota de menor risco)

1) Coletar evidência: DE + se o serviço está vivo quando some (Seção 1).
2) Se o serviço está vivo: atacar Hipótese #1 (GTK main loop em daemon thread).
3) Se ainda houver sumiço: atacar Hipótese #2 (migrar para AppIndicator/pystray ou SNI).
4) Se o problema ocorre só em autostart/sleep: investigar Hipótese #3 (ambiente do systemd user).

## 7) O que eu NÃO recomendo (alto risco/baixa confiança)

- “Só colocar sleep/retry” quando o ícone some: mascara bug e piora previsibilidade.
- Trocar várias coisas ao mesmo tempo (threading + backend + systemd env): impede saber qual foi a causa real.
- Depender de PNG custom como “icon_name” do GTK theme para o StatusIcon: o `StatusIcon` atual usa `audio-input-microphone` etc; isso é bom. Manter.

