# Operação de Caixa e Impressão — Implementation Plan

> Este plano depende do checkout manual e compartilha sua migração v9. Cada tarefa começa por teste vermelho em banco temporário/adaptador falso; nenhum teste automatizado usa banco, impressora, URL ou configuração reais.

**Goal:** tornar o caixa único, cancelamento, idempotência, impressão e UI recuperáveis o suficiente para o gate de um único PDV físico.

**Architecture:** SQLite impõe invariantes financeiros e mantém outboxes duráveis; serviços executam transações `BEGIN IMMEDIATE`; um worker serial trata impressão e backup fora da thread Tk; a UI representa estados explícitos e não presume confirmação bancária.

**Tech Stack:** Python 3.12, Tkinter/ttk, SQLite, `Decimal`, win32print/IPP por adaptadores, `unittest`.

## Restrições globais

- Migração somente aditiva e unificada no schema 9.
- Pré-condição de duplicidade de caixa aberto roda antes da primeira escrita e aborta sem saneamento automático.
- Nenhuma credencial, token ou segredo em código, teste, log, payload de outbox ou documentação.
- Cancelamento local não promete estorno PIX/cartão e não existe integração bancária nesta entrega.
- Falha de impressão/backup nunca reverte venda/fechamento já confirmados.
- Release e pendrive permanecem bloqueados até os gates automatizados e físicos aplicáveis.

---

### Task 1: Ampliar a migração v9 com invariantes operacionais

**Files:**
- Modify: `db/schema.py`
- Modify: `db/migrations.py`
- Modify: `tests/test_migrations.py`

- [ ] RED: v8 com dois caixas abertos deve falhar antes de qualquer mudança; comparar schema e linhas byte/logicamente após rollback.
- [ ] RED: v8 válido deve receber índice único global, idempotência de movimentos/vendas/cancelamentos, status de backup e outbox de comprovantes.
- [ ] RED: migrar v8→v9 duas vezes e injetar falha após cada DDL crítico deve preservar atomicidade.
- [ ] Implementar a mesma migração 9 do plano de checkout, sem criar v9 concorrente nem renumerar parcialmente o schema.
- [ ] Criar índice único global de `ABERTO`; valores legados de chaves permanecem nulos e índices parciais ignoram nulos.
- [ ] Rodar `python -m unittest tests.test_migrations -v`.

### Task 2: Caixa global, retomada e movimentos idempotentes

**Files:**
- Modify: `services/cash.py`
- Modify: `services/pdv_service.py`
- Modify: `services/errors.py`
- Modify: `tests/test_backend.py`
- Create: `tests/test_cash_operational.py`

- [ ] RED: dois usuários/threads tentam abrir; exatamente um vence e a consulta global retorna um caixa.
- [ ] RED: dono retoma; outro operador recebe conflito; administrador retoma com motivo e evento sem credenciais.
- [ ] RED: repetir suprimento/sangria com a mesma chave retorna o mesmo movimento; conteúdo divergente conflita.
- [ ] RED: dois fechamentos concorrentes resultam em sucesso + conflito, preservam saldo e sessão autenticada.
- [ ] Implementar consultas globais dentro de `BEGIN IMMEDIATE`, autorização tipada de retomada e fingerprint do movimento.
- [ ] Manter fórmula autoritativa de valor em caixa e expor snapshot após cada operação.
- [ ] Rodar os módulos backend e operacional.

### Task 3: Backup pós-fechamento durável e não bloqueante

**Files:**
- Modify: `services/cash.py`
- Modify: `services/backup.py`
- Modify: `desktop_controller.py`
- Modify: `ui/views.py`
- Create: `tests/test_cash_backup_worker.py`

- [ ] RED: fechamento lento retorna à UI antes do adaptador de backup terminar e registra `PENDENTE` na transação.
- [ ] RED: sucesso atualiza `CONCLUIDO`; erro atualiza `FALHOU`, não reabre caixa e permite retry auditado.
- [ ] RED: reinício encontra backup pendente e o retoma sem duplicar fechamento.
- [ ] Implementar worker serial/encerramento cooperativo, status persistido e feedback no painel administrativo.
- [ ] Garantir que callbacks Tk usem `after` e que nenhum arquivo seja criado em caminhos reais durante testes.

### Task 4: Fingerprint completo e cancelamento transacional

**Files:**
- Modify: `services/sales.py`
- Modify: `services/pdv_service.py`
- Modify: `services/errors.py`
- Modify: `tests/test_checkout.py`
- Create: `tests/test_sale_cancellation.py`

- [ ] RED: retry de venda idêntica retorna a mesma venda; mudar operador, caixa, ordem/linha, quantidade, preço, pagamento, recebido, autorizador ou motivo produz conflito.
- [ ] RED: cancelamento exige admin e motivo 8–250, aceita apenas venda confirmada em caixa aberto e recompõe estoque de catálogo uma vez.
- [ ] RED: item manual não altera estoque; retry idêntico retorna o cancelamento; chave divergente conflita.
- [ ] RED: venda originada em caixa fechado é recusada sem alterar venda/estoque; PIX/cartão não chama adaptador bancário inexistente.
- [ ] Persistir fingerprint canônico antes de confirmar e cancelar em uma única transação com auditoria.
- [ ] Expor aviso obrigatório de devolução física/ausência de estorno automático no contrato da UI.

### Task 5: Outbox transacional e worker serial de impressão

**Files:**
- Modify: `services/sales.py`
- Modify: `printing/receipt_printer.py`
- Modify: `desktop_controller.py`
- Modify: `services/pdv_service.py`
- Modify: `tests/test_printing.py`
- Create: `tests/test_print_outbox.py`

- [ ] RED: com impressão selecionada/padrão, commit de venda contém comprovante original `PENDENTE`; falha entre commit e thread não deixa venda sem outbox. Modo desativado não cria job automático.
- [ ] RED: worker único processa em ordem, marca `IMPRESSO` ou `FALHOU` e retoma `PENDENTE` após reinício.
- [ ] RED: falha de USB/rede/driver/arquivo não altera venda e resultado assíncrono chega à UI.
- [ ] RED: retry usa a mesma solicitação sem duplicar em clique repetido; segunda via cria solicitação distinta, marcada e auditada.
- [ ] RED: preview/arquivo só anuncia sucesso após temporário + rename; erro de escrita permanece `FALHOU`.
- [ ] Implementar shutdown cooperativo com drenagem limitada; documentar possível repetição física após crash entre impressão e confirmação.

### Task 6: Modos persistentes da impressora e UI recuperável

**Files:**
- Modify: `config/settings.py`
- Modify: `printing/printer_discovery.py`
- Modify: `desktop_controller.py`
- Modify: `ui/views.py`
- Modify: `tests/test_desktop_controller.py`
- Modify: `tests/test_ui_regressions.py`

- [ ] RED: `SELECIONADA`, `PADRAO_WINDOWS` e `DESATIVADA` persistem após reinício e produzem comportamentos diferentes.
- [ ] RED: selecionada removida não cai para padrão; padrão ausente e desativada retornam mensagens claras.
- [ ] RED: salvar/testar/atualizar durante `busy` e em exceção sempre restaura botões/texto e permite nova tentativa.
- [ ] Manter USB/rede/compartilhada em uma lista do Windows, com descoberta fora da thread Tk.
- [ ] Mostrar status real, ações “Atualizar”, “Escolher outra” e “Tentar novamente”, sem nomes fixos em código.

### Task 7: Layout responsivo, PIX manual e papel 58/80 mm

**Files:**
- Modify: `ui/dialogs.py`
- Modify: `ui/views.py`
- Modify: `printing/receipt_printer.py`
- Modify: `tests/test_ui_regressions.py`
- Modify: `tests/test_printing.py`

- [ ] RED: Payment/Search/ProductEditor e confirmação de justificativa cabem em tela falsa 640×480/720×520 e mantêm ações alcançáveis por teclado/rolagem.
- [ ] RED: aviso longo quebra linha na largura efetiva, não em constante fixa.
- [ ] RED: PIX não finaliza sem confirmação textual manual e nunca exibe “confirmado pelo banco”.
- [ ] RED: golden receipts 58/80 mm validam linhas, valores, acentos, total, pagamento, troco e corte somente quando suportado.
- [ ] Limitar geometria à área útil, adicionar containers roláveis e reflow de ações sem alterar o fluxo de teclado do caixa.
- [ ] Persistir perfil 58/80 mm e usar o mesmo formatador em preview, arquivo e adaptadores.

### Task 8: Integração e gate de regressão

**Files:**
- Create: `tests/test_operational_matrix.py`
- Modify: `tests/test_e2e_production.py`
- Update: `docs/QA_WINDOWS.md`

- [ ] Rodar todos os cenários da matriz TDD abaixo em bancos temporários e mocks determinísticos.
- [ ] Rodar `python -m unittest discover -s tests -v` e `python -m compileall -q config db services printing runtime ui tests`.
- [ ] Confirmar que nenhum teste acessou impressora, URL, configuração ou banco reais.
- [ ] Executar checklist manual Windows com impressora USB e rede reais, 58/80 mm, corte suportado/não suportado, remoção, spooler parado, reinício e DPI 125%/150%.
- [ ] Não publicar instalador/pendrive enquanto qualquer cenário crítico ou bloqueio físico obrigatório estiver aberto.

## Matriz TDD bloqueante

| Contrato | Teste RED mínimo | Correção mínima | Evidência verde |
|---|---|---|---|
| Um caixa global | dois usuários abrem simultaneamente | transação + índice parcial global | um sucesso, um conflito, contagem 1 |
| Migração segura | v8 contém dois `ABERTO` | preflight antes de DDL | erro claro e zero alterações |
| Retomada admin | operador diferente encontra caixa | autorização + motivo + auditoria | mesmo caixa retomado, nenhum novo |
| Movimento idempotente | repetir/sobrescrever chave | fingerprint + índice único | replay igual; divergente conflita |
| Venda idempotente | mesma chave com payload alterado | fingerprint canônico completo | replay apenas quando idêntico |
| Cancelamento | repetir e variar admin/motivo | transação + chave + estoque | status/estoque/auditoria uma vez |
| Outbox atômica | impressão ativa e falha antes da thread | inserir `PENDENTE` com venda | venda e outbox no mesmo commit; desativada sem job |
| Restart da impressão | encerrar com `PENDENTE` | worker serial de retomada | fila processada após nova instância |
| Segunda via | clique repetido/restart | nova chave, marca e auditoria | uma solicitação por intenção |
| Modos de impressora | salvar/reabrir/remover alvo | enum persistente sem fallback | selecionada/padrão/desativada distintos |
| UI busy/error | exceção e clique concorrente | estado explícito + `finally` | controles reutilizáveis |
| Backup do fechamento | adaptador lento/falha/restart | status persistido + worker | UI livre; caixa segue fechado |
| PIX manual | tentar concluir sem declaração | confirmação textual explícita | registro manual, nenhuma alegação bancária |
| Layout | tela pequena/mensagem longa | cap/reflow/scroll | 100% das ações alcançáveis |
| Papel | conteúdo limite 58/80 mm | perfil de formatação/corte | golden tests + prova física |

## Bloqueios físicos antes de produção

- Impressão real em ao menos uma térmica USB e uma fila Windows de rede, com papel 58 e 80 mm quando disponíveis.
- Driver ausente, spooler parado, impressora removida/desligada e fila com erro sem congelar o PDV.
- Corte físico apenas em equipamento declarado compatível; ausência de corte não é falha do comprovante.
- Reinício do Windows com comprovante pendente e inspeção visual de possível duplicidade/estado mostrado ao operador.
- Telas 640×480, 720×520 e 1920×1080, DPI 100%/125%/150%, teclado/scanner e usuário sem privilégios administrativos.
- O gate não exige nem simula confirmação bancária de PIX/cartão; essa integração está fora do produto descrito.
