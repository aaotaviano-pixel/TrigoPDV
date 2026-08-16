# Checkout Manual Confiável Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o checkout aceitar GTIN, PLU, pesquisa e item avulso com valores idênticos na tela, pagamento e banco, scanner serial confiável e autorização auditada.

**Architecture:** `services.checkout` produz uma cotação autoritativa em `Decimal`; `SaleService` recalcula dentro da transação; a UI mantém apenas linhas decimais e uma fila serial com geração. Exceções são tipadas e autorização é revalidada no serviço.

**Tech Stack:** Python 3.12, Tkinter/ttk, SQLite, `Decimal`, `unittest`.

**Operational dependency:** este plano compartilha schema 9 e gate final com
[`2026-08-15-operacao-caixa-impressao-producao.md`](./2026-08-15-operacao-caixa-impressao-producao.md).
Não criar migrações v9 paralelas: campos do checkout, caixa único, idempotência,
cancelamento e outbox de impressão entram na mesma migração atômica.

## Global Constraints

- Item manual total de até R$ 50,00 por venda não exige administrador; acima disso exige.
- Preço excepcional de produto cadastrado sempre exige administrador e justificativa.
- Migração somente aditiva; nenhum banco real é usado em teste.
- Nenhuma senha entra em código, log, documentação ou banco em claro.
- Não alterar catálogo/estoque por causa de item avulso.
- Projeto sem Git: usar snapshot timestamped e relatórios de revisão.

---

### Task 1: Cotação decimal autoritativa

**Files:**
- Create: `services/checkout.py`
- Create: `tests/test_checkout.py`
- Modify: `services/money.py`

**Interfaces:**
- Produces: `LineKind`, `CheckoutLine`, `SaleQuote`, `quote_lines(connection, items)`.
- Payload numérico: strings decimais canônicas.

- [ ] Escrever testes para `12.50 * 0.250 == 3.13`, `UN` fracionada rejeitada, `KG` milésimos, `0.0004`, `NaN` e infinito rejeitados.
- [ ] Rodar `python -m unittest tests.test_checkout -v` e confirmar falhas por módulo ausente.
- [ ] Implementar dataclasses imutáveis, normalização `Decimal`, subtotal `money(price * quantity)` e agregados `total`/`manual_total`.
- [ ] Rodar o módulo e confirmar todos os testes verdes.

### Task 2: Migração 9 do checkout

**Files:**
- Modify: `db/schema.py`
- Modify: `db/migrations.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `MigrationManager` criado pelo plano de identidade.
- Produces: schema 9 com campos de exceção/manual e invariantes operacionais.

- [ ] Escrever teste v8→v9 preservando linhas antigas e aplicando `CATALOGO`, `total_manual=0`.
- [ ] Escrever teste em que dois caixas `ABERTO` preexistentes abortam antes da
  primeira DDL, sem fechar/apagar/escolher registro; um banco válido recebe o
  índice parcial único global.
- [ ] Escrever testes para chave idempotente de movimentos, fingerprint e
  cancelamento da venda, status de backup e outbox `PENDENTE/IMPRESSO/FALHOU`.
- [ ] Escrever teste de idempotência e de rollback por falha após o primeiro `ALTER TABLE`.
- [ ] Rodar os testes e confirmar ausência das colunas.
- [ ] Adicionar uma única migração 9 com `total_manual`, `autorizador_excecao_id`,
  `motivo_excecao`, `tipo_lancamento`, `codigo_informado`, `preco_original`,
  invariantes de caixa/idempotência/cancelamento/backup e outbox de impressão.
- [ ] Rodar `python -m unittest tests.test_migrations -v`.

### Task 3: Venda normal/manual/excepcional transacional

**Files:**
- Modify: `services/sales.py`
- Modify: `services/errors.py`
- Modify: `tests/test_checkout.py`

**Interfaces:**
- Produces: `AuthorizationRequirement` e `AuthorizationRequiredError`.
- Produces: `SaleService.quote(items, actor_id)` e `SaleService.finalize(..., exception_authorizer_id=None, exception_reason="")`.

- [ ] Escrever testes para venda manual com `gtin=None`, venda mista, ausência de baixa de estoque, limite 50,00/50,01 e duas linhas de 30,00.
- [ ] Escrever testes para preço cadastrado alterado, motivo de 8–250 caracteres e ID de caixa rejeitado como autorizador.
- [ ] Escrever teste de fingerprint completo: retry exato devolve a venda; a
  mesma chave com operador, caixa, linha/ordem, quantidade, preço, pagamento,
  recebido, autorizador ou motivo alterado produz conflito.
- [ ] Rodar e confirmar falhas por comportamento ausente.
- [ ] Integrar `quote_lines` ao `finalize`, revalidar catálogo/estoque/admin dentro do mesmo `BEGIN IMMEDIATE` e persistir campos.
- [ ] Registrar `ITEM_MANUAL_VENDIDO`, `PRECO_EXCEPCIONAL_APLICADO` e `EXCECAO_VENDA_AUTORIZADA` sem credenciais.
- [ ] Rodar `python -m unittest tests.test_checkout -v` e `python -m unittest tests.test_backend -v`.

### Task 4: Fachada e autorização tipada

**Files:**
- Modify: `services/pdv_service.py`
- Modify: `desktop_controller.py`
- Modify: `ui/contracts.py`
- Modify: `tests/test_desktop_controller.py`

**Interfaces:**
- Produces: `quote_sale(operator_id, items)`.
- `finalize_sale(..., manual_authorization=None)` aceita credenciais somente na chamada e converte para ID.

- [ ] Escrever teste de credencial inválida sem venda e teste de retry com mesma chave idempotente.
- [ ] Confirmar que autenticação/autorização é revalidada antes de devolver uma
  venda idempotente existente e que credenciais não entram no fingerprint/log.
- [ ] Rodar e confirmar que a fachada não suporta cotação/autorização.
- [ ] Implementar validação administrativa via `AuthService.verify_admin_credentials`, nunca repassar senha a `SaleService`.
- [ ] Atualizar contrato e rodar testes de controlador.

### Task 5: Carrinho decimal e edição

**Files:**
- Modify: `ui/views.py`
- Modify: `ui/contracts.py`
- Create: `ui/dialogs_checkout.py`
- Create: `tests/test_ui_checkout.py`

**Interfaces:**
- Produces: `ManualSaleItemDialog` e `CartItemEditDialog`.
- `CartLine` contém chave estável, tipo, código, descrição, unidade, `Decimal` de preço/quantidade e preço original.

- [ ] Escrever teste do subtotal visual 3,13 e payload canônico.
- [ ] Escrever teste F2 totalmente pelo teclado e F3 editando quantidade/preço.
- [ ] Rodar sob Tk e confirmar falhas esperadas.
- [ ] Converter `CartLine`, total e renderização; criar diálogos roláveis no padrão `BaseDialog`.
- [ ] Exibir F1/F2/F3/F5/Ctrl+F11/F10 no rodapé e rodar testes de UI.

### Task 6: PLU e código desconhecido

**Files:**
- Modify: `services/products.py`
- Modify: `services/pdv_service.py`
- Modify: `ui/views.py`
- Modify: `tests/test_backend.py`
- Modify: `tests/test_ui_checkout.py`

**Interfaces:**
- Produces: `ProductService.resolve_code(raw_code, actor_id)`.

- [ ] Escrever teste PLU local sem chamada externa, GTIN desconhecido com fallback e PLU desconhecido sem fallback externo.
- [ ] Escrever teste de carrinho preservado e alternativas pesquisar/tentar/manual/cadastro.
- [ ] Implementar resolução local antes da normalização estrita de GTIN e resposta `MANUAL_ENTRY_REQUIRED`.
- [ ] Integrar opções amigáveis e rodar módulos backend/UI.

### Task 7: Fila de scanner e barreira de pagamento

**Files:**
- Create: `ui/scan_queue.py`
- Create: `tests/test_scan_queue.py`
- Modify: `ui/views.py`
- Modify: `ui/app.py`

**Interfaces:**
- Produces: `ScanTicket` e `ScanQueue` com `enqueue`, `take_next`, `finish`, `advance_generation`, `has_pending`.

- [ ] Escrever testes de duas leituras, erro seguido de sucesso, descarte de geração antiga e ordem.
- [ ] Escrever teste UI que F10 não abre enquanto há leitura e resultado antigo não entra na venda seguinte.
- [ ] Implementar uma thread ativa, fila habilitada e geração por carrinho/tela.
- [ ] Invalidar geração em venda finalizada/logout/destroy e rodar testes.

### Task 8: Pagamento autoritativo e regressão

**Files:**
- Modify: `ui/dialogs.py`
- Modify: `ui/views.py`
- Modify: `tests/test_ui_checkout.py`
- Modify: `tests/test_printing.py`

**Interfaces:**
- PaymentDialog recebe total decimal canônico produzido por `quote_sale`.

- [ ] Escrever teste de dinheiro, PIX e cartão usando exatamente 3,13.
- [ ] Escrever teste de PIX exigindo confirmação textual manual e impedindo
  qualquer mensagem que alegue confirmação bancária/adquirente.
- [ ] Escrever teste 50,01 abrindo autorização com motivo e 50,00 sem diálogo.
- [ ] Implementar cotação antes do pagamento, confirmação manual de PIX e retry
  com a mesma chave/fingerprint completo.
- [ ] Rodar `python -m unittest tests.test_ui_checkout tests.test_printing -v`.
- [ ] Rodar `python -m unittest discover -s tests -v` e registrar contagem/tempo/qualquer falha real.
