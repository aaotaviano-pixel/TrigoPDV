# Gate Integrado de Produção — TrigoPDV 1.1.0

> Este plano orquestra os planos detalhados de identidade, checkout, operação de
> caixa/impressão e atualização. Ele não substitui suas tarefas; define a ordem
> vinculante e os testes que atravessam os limites entre elas.

**Objetivo:** impedir que instalador, pendrive ou atualização anunciem uma versão/schema que não foi exercitada ponta a ponta, e entregar a auditoria final solicitada sem declarar produção prematuramente.

## Ordem vinculante

1. Motor de migrações v6 (concluído).
2. Identidade: provisionamento v7 (estado integrado atual), primeiro uso/troca, rate limit v8 e instância única.
3. Checkout e operação: cotação Decimal, migração v9 única, caixa aberto global,
   idempotência, cancelamento, scanner, outbox de impressão e UI responsiva.
4. Catálogo/instalação: SQLite distribuído contém somente os 192 produtos;
   cada máquina cria banco operacional fresh v9 e UUID próprio.
5. Atualizador: versão central declara schema 9 e consome o `installation_state`
   único criado no banco local, nunca uma identidade clonada do pacote.
6. Build/pendrive/release somente depois dos gates abaixo.

Nenhuma etapa pode publicar artefato intermediário como produção.

Enquanto o schema real estiver em v7 ou v8, componentes do atualizador podem existir
somente atrás de adaptadores/testes isolados. Integração no entrypoint, Setup, publicação,
pendrive e rótulo 1.1.0 permanecem bloqueados até v9.

A implementação operacional segue obrigatoriamente a
[especificação](../specs/2026-08-15-operacao-caixa-impressao-producao-design.md)
e o [plano TDD](./2026-08-15-operacao-caixa-impressao-producao.md). Não existe
uma segunda migração v9 independente.

## Task 0 — Gate de arquitetura do atualizador

- [ ] Fixar `packId = TrigoDeMinas.TrigoPDV` e título `TrigoPDV`, provando que a raiz de programa não coincide com `%LOCALAPPDATA%\TrigoPDV` nem com `TRIGOPDV_DATA_DIR`.
- [ ] Desligar auto-apply e provar que o bootstrap Velopack ocorre antes dos imports do TrigoPDV.
- [ ] Provar que `resume_pending_update()`/health termina antes da construção de `PDVService`.
- [ ] Ler primeiro o estado do atualizador sem abrir o banco comercial. Update
  pendente tem prioridade e bloqueia o bootstrap do catálogo; somente sem pending e
  sem DB/WAL/SHM o fresh v9 pode ser criado sob `SingleInstanceGuard`.
- [ ] Empacotar a raiz TUF pública como bootstrap; cache/configuração não podem substituir a âncora.
- [ ] Validar bridge local por nome/hash/tamanho e permitir downgrade somente no rollback interno.
- [ ] Separar snapshot offline da política HTTPS online e limitar toda rede por orçamento total.
- [ ] Definir transição Inno → único Setup Velopack sem excluir dados nem manter atalhos concorrentes.

## Task 1 — Gate único de schema e release

**Arquivos:**

- Create: `tests/test_release_schema_gate.py`
- Modify: `config/version.py`, `release/version.toml` somente pela fonte única do plano do atualizador

- [ ] Verificar que `SCHEMA_VERSION`, runtime, manifesto, Inno Setup, PyInstaller e pacote concordam com v9.
- [ ] Migrar cópias temporárias v4, v6, v7 e v8 até v9, inclusive saltando versões, e preservar produtos, usuários e histórico.
- [ ] Antes da primeira DDL v9, detectar mais de um caixa `ABERTO` global e
  abortar sem alteração, fechamento ou exclusão automática. Banco válido recebe
  índice parcial único global e concorrência prova no máximo um aberto.
- [ ] Confirmar na mesma v9 os campos/índices de movimento idempotente,
  fingerprint/cancelamento da venda, status de backup e outbox de comprovantes
  `PENDENTE/IMPRESSO/FALHOU`.
- [ ] Recusar schema futuro sem escrever e abortar quando faltar uma migração intermediária.
- [ ] Confirmar backup íntegro antes de cada origem não vazia e ausência de backup no fresh.
- [ ] Exigir catálogo distribuído sem schema operacional e provar que a instalação
  limpa constrói banco local v9; atualização só aceita `schema_target` suportado pelo binário.
- [ ] Recusar geração/publicação do atualizador enquanto `SCHEMA_VERSION != 9`, mesmo que TOML ou manifesto declarem 9.

## Task 2 — E2E instalação, update e venda

**Arquivos:**

- Create: `tests/test_e2e_production.py`

- [ ] Cenário A: v6 com usuário legado → v9 → login/troca/rate limit → venda normal e manual.
- [ ] Cenário B: v6 sem usuário → v9 → provisionamento único → venda manual de R$ 50,00.
- [ ] Cenário C: update falha em v7, v8 e v9 antes da primeira escrita comercial; banco/configuração/impressora são preservados e rollback restaura a versão anterior.
- [ ] Cenário D: update conclui e venda manual acima do limite exige administrador/motivo, persiste auditoria completa e não altera catálogo.
- [ ] Em todos, verificar WAL/SHM, fila de impressão, backups e estado do atualizador sem acessar banco real.
- [ ] Cenário E: instalação Inno existente recebe o Setup Velopack de transição, reutiliza apenas a raiz de dados, deixa um único atalho canônico e não trata o legado como locator Velopack.
- [ ] Cenário F: programa em `%LOCALAPPDATA%\TrigoDeMinas.TrigoPDV` é atualizado/desinstalado em sandbox e sentinelas na raiz de dados permanecem byte a byte.
- [ ] Cenário G: duas raízes de dados partem do mesmo catálogo, geram UUIDs
  distintos e terminam v9/`UNINITIALIZED` com os mesmos 192 produtos e zero
  usuários, histórico e cache.
- [ ] Cenário H: sem DB, WAL ou SHM órfão bloqueia o bootstrap sem remoção;
  crash antes do rename não publica banco parcial e banco preexistente não é aberto.
- [ ] Cenário I: dois usuários tentam abrir caixa; somente um vence. O dono
  retoma, um administrador retoma com motivo/auditoria, suprimento/sangria são
  idempotentes e fechar mantém a conta autenticada. Backup lento/falho ocorre
  depois do commit, não congela a UI e não reabre o caixa.
- [ ] Cenário J: com impressão ativa, venda confirmada cria outbox no mesmo commit; falha do adaptador
  ou encerramento deixa `PENDENTE/FALHOU`, reinício retoma em worker serial e
  segunda via é marcada/auditada sem nova baixa de estoque. Modo desativado não
  cria job automático e continua permitindo segunda via posterior.
- [ ] Cenário K: retry de venda com fingerprint idêntico reproduz a resposta e
  qualquer divergência conflita. Cancelamento admin+motivo em caixa aberto
  recompõe estoque uma vez e avisa que não realiza estorno PIX/cartão.
- [ ] Cenário L: modos de impressora `SELECIONADA`, `PADRAO_WINDOWS` e
  `DESATIVADA` persistem sem fallback silencioso; indisponibilidade e erro/busy
  restauram a UI; PIX permanece confirmação manual explícita.

## Task 3 — Matriz operacional e segurança

**Arquivos:**

- Create: `tests/test_operational_matrix.py`
- Create: `docs/MATRIZ_PERMISSOES.md`
- Create: `docs/QA_WINDOWS.md`

- [ ] Scanner/HID: válido, inexistente, incompleto, inválido, duplicado, Enter extra, duas leituras, repetição em massa, desconexão/reconexão simulada, GTIN/PLU/digitação.
- [ ] Checkout: misto, sem código, preço zero/negativo/extremo, UN/KG, muitos itens, cliques repetidos, remoção, cancelamento e pagamento.
- [ ] Caixa: abertura global concorrente, migração recusando duplicidade antiga,
  retomada do dono/admin, sangria/suprimento idempotentes, diferença, falta de
  caixa, fechamento concorrente sem logout e backup pós-commit não bloqueante.
- [ ] Venda idempotente: fingerprint cobre ator, caixa, linhas/ordem, quantidades,
  preços, pagamento, recebido e autorização; replay exato não duplica e chave
  divergente conflita.
- [ ] Cancelamento: venda confirmada em caixa aberto, admin+motivo, estoque de
  catálogo recomposto uma vez, manual sem estoque e aviso de devolução física e
  ausência de estorno automático PIX/cartão.
- [ ] Auditoria manual: operador, data/hora, produto relacionado, preço original/novo, motivo e tipo; nenhuma senha/código/hash em evento ou erro.
- [ ] Rede indisponível/lenta: lookup e updater nunca bloqueiam venda local nem apagam carrinho.
- [ ] Startup: auto-apply desligado, bootstrap Velopack antes dos imports e nenhum
  `PDVService`/`Database.initialize()` comercial antes do health gate. O bootstrap
  fresh do catálogo é permitido somente quando o estado local prova que não há
  update pendente nem DB/WAL/SHM.
- [ ] TUF/Velopack: bootstrap empacotado, feed `releases.<channel>.json`, pacote full com nome/hash/tamanho exatos e nenhum arquivo remoto não verificado.
- [ ] Offline: snapshot entra somente por API local explícita e mesma raiz TUF; configuração online continua HTTPS-only.
- [ ] Impressão por adaptador: outbox no mesmo commit, worker serial, retomada de
  `PENDENTE`, estados `IMPRESSO/FALHOU`, retry e segunda via; USB, rede, removida,
  driver/fila/timeout; falha nunca reverte venda confirmada.
- [ ] Configuração de impressora: modos persistentes selecionada/padrão Windows/
  desativada, sem fallback silencioso, refresh e teste com restauração após
  busy/erro, além de golden receipts 58/80 mm.
- [ ] UI operacional: diálogos, justificativas e mensagens totalmente acessíveis
  em 640×480/720×520, reflow/scroll/teclado e estados assíncronos claros. PIX
  exige confirmação manual e não anuncia integração bancária.
- [ ] Dependências/segurança: executar auditoria de dependências e varreduras locais disponíveis; corrigir apenas achados comprovados e registrar exceções.
- [ ] `docs/QA_WINDOWS.md` cobre testes manuais que exigem VM/hardware real: DPI/resoluções, permissões limitadas, antivírus, falta de espaço, arquivo bloqueado, reboot e Setup/atalho.

## Task 4 — Operação TUF e publicação

**Arquivos:**

- Create: `docs/OPERACAO_RELEASE_TUF.md`
- Extend: `tests/test_update_repository.py`, `tests/test_update_coordinator.py`, `tests/test_release_scripts.py`

- [ ] Documentar custódia/threshold, expiração, rotação/revogação de raiz, timestamp/snapshot/targets, pausa e bloqueio de release.
- [ ] Testar rotação/revogação com chaves somente temporárias e a matriz 404/500/orçamento total de timeout/truncado/hash/assinatura/replay/downgrade/disco/lock/crash/reboot.
- [ ] Stable falha fechada sem HTTPS, TUF válido, Authenticode/timestamp e diretório de publicação.
- [ ] Build sem certificado permanece claramente não-produção e update online desabilitado.
- [ ] Confirmar `vpk`/binding 1.2.0, `--delta none`, `packId` distinto, assinatura antes dos metadados TUF e `timestamp.json` publicado por último.
- [ ] Detectar ferramentas e versões no script; ausência é falha explícita, nunca evidência presumida de disponibilidade.

## Task 5 — Benchmark, relatório e primeiro cliente

**Arquivos:**

- Create: `docs/BENCHMARK_PDV.md`
- Create: `docs/RELATORIO_AUDITORIA_1.1.0.md`
- Create: `docs/CHECKLIST_PRIMEIRO_CLIENTE.md`
- Update: segundo cérebro, somente após evidência final

- [ ] Benchmark usa apenas fontes públicas e separa práticas observadas de decisões próprias.
- [ ] Relatório final cobre as 29 seções pedidas, com comandos, contagens, falhas, correções e riscos remanescentes.
- [ ] Checklist bloqueia produção em qualquer Critical conhecido, migração/update/rollback não reproduzível ou teste físico obrigatório ainda não executado.
- [ ] Checklist inclui impressão real USB e rede, papel 58/80 mm, acentos,
  quebra/alinhamento/total, corte suportado e não suportado, spooler parado,
  impressora removida e restart com job pendente.
- [ ] Checklist inclui telas 640×480, 720×520 e 1920×1080 em DPI
  100%/125%/150%, usuário Windows limitado e fechamento com backup lento/falho
  sem congelamento perceptível.
- [ ] Registrar limitações externas: certificado Authenticode, chaves/cerimônia TUF, host HTTPS e hardware/VM que não estejam disponíveis.
- [ ] Registrar também a transição Inno, o `root.json` público definitivo e a validação de SmartScreen/antivírus como bloqueios enquanto não exercitados.
- [ ] Atualizar pendrive por staging+manifesto somente depois do gate; preservar arquivos fora do escopo e validar hashes da cópia final.

## Matriz mínima cruzada do atualizador

| Limite | Evidência obrigatória |
|---|---|
| Programa × dados | `packId` distinto; update/uninstall não toca config, DB, WAL/SHM, fila ou backups |
| Entrypoint × banco | auto-apply off; Velopack antes dos imports; pending resume/health antes de bootstrap/`PDVService`; fresh somente sem pending/DB/sidecars |
| Catálogo × operacional | distribuído somente produtos; fresh v9 por máquina; UUID único; banco existente nunca recebe reimportação |
| TUF × Velopack | root bootstrap empacotada; bridge local com nome/hash/tamanho; pacote full somente |
| Normal × rollback | sequência nunca diminui; downgrade false no normal e true somente no rollback privado |
| Online × offline | HTTPS e budget total online; snapshot local por API separada e mesma confiança |
| Inno × Velopack | legado não é locator; uma transição; um Setup/atalho canônico; dados preservados |
| Build × publicação | schema real v9; assinatura/timestamp; targets/snapshot antes de timestamp |

## Matriz TDD do catálogo e instalação

| Contrato | Evidência bloqueante |
|---|---|
| Catálogo distribuído | única tabela de negócio `produtos`; 192 linhas e 20 campos; nenhum usuário/schema operacional |
| Códigos e preços | 167 GTINs estruturalmente válidos, 25 PLUs internos, zero duplicidades e 192 preços positivos |
| Preservação | digest lógico canônico dos 20 campos idêntico antes/depois da separação |
| Identidade | duas instalações do mesmo catálogo produzem UUIDs válidos e distintos |
| Estado inicial | ambas v9/`UNINITIALIZED`, com 192 produtos e zero usuários, caixas, vendas, itens, movimentos, auditoria e cache |
| Atomicidade | staging no mesmo volume, validação completa e rename; crash não expõe banco parcial |
| Sidecars | DB ausente com WAL/SHM órfão falha sem criar, apagar ou sobrescrever arquivos |
| Preservação local | DB/WAL/SHM existentes ficam byte a byte inalterados por instalador, bootstrap e update |
| Prioridade do updater | pending resume/health ocorre antes de qualquer bootstrap; fresh só no estado comprovadamente vazio |

## Matriz TDD operacional bloqueante

| Contrato | RED obrigatório | Evidência de saída |
|---|---|---|
| Caixa único/migração | abertura concorrente e v8 com dois abertos | um aberto; duplicidade antiga aborta antes de DDL |
| Retomada/movimentos | outro operador, admin e chave repetida/divergente | mesmo caixa; auditoria; replay ou conflito correto |
| Fechamento/backup | fechar duas vezes; backup lento, falho e reiniciado | um fechamento, sessão ativa, UI livre e status durável |
| Venda idempotente | alterar cada campo sob a mesma chave | apenas fingerprint integral idêntico reproduz resposta |
| Cancelamento | retry, catálogo/manual, caixa fechado, PIX/cartão | estoque uma vez; recusa segura; aviso sem estorno automático |
| Outbox/segunda via | impressão ativa, falha commit→thread, adaptador e restart | venda+`PENDENTE` atômicos; retomada serial; cópia auditada; desativada sem job |
| Impressora | três modos, alvo removido, busy/erro | persistência sem fallback; controles sempre recuperados |
| PIX | tentar confirmar sem declaração manual | bloqueio claro e nenhuma alegação de confirmação bancária |
| Layout | 640×480/720×520, DPI e texto longo | ações e mensagens 100% alcançáveis |
| Térmica | golden 58/80 mm e corte condicional | valores sem truncar; prova física antes da release |

## Bloqueios locais observados na auditoria da Task 8

- O banco-base atual ainda é operacional schema 4, contém 192 produtos e um
  usuário; não pode ser distribuído na release nova.
- O CMD atual verifica apenas o arquivo principal e não trata WAL/SHM órfão;
  o Inno atual não transporta o catálogo.
- O pacote possui `_internal\_internal`, uma cópia `app\TrigoPDV` e lançador
  legado de recuperação. Tudo deve sair somente após snapshot verificável.
- O executável do pacote antecede as mudanças correntes e o Setup Inno não foi
  gerado. Nenhum artefato intermediário pode receber rótulo de produção.
- Nenhum volume removível estava montado durante a auditoria; sincronização do
  pendrive depende de staging, identificação explícita do alvo e manifesto final.

## Bloqueios externos

O gate não presume ferramentas, serviços ou credenciais instalados. Stable continua bloqueada
sem host HTTPS, cerimônia/threshold/custódia/rotação TUF, raiz pública final, certificado
Authenticode/timestamp, transição Inno exercitada e QA em Windows/VM/hardware real. Testes
locais usam somente diretórios temporários, chaves de teste efêmeras, fetchers falsos e
adaptadores injetados.

O QA físico inclui impressoras Windows USB e rede, papel 58/80 mm, corte quando
suportado, indisponibilidade/spooler, reinício com fila pendente, resoluções/DPI e
usuário limitado. O gate não inventa conta, webhook ou API de banco/adquirente:
PIX continua confirmado manualmente pelo operador, e cancelamento local não é
evidência de estorno financeiro externo.

## Comandos mínimos do gate

```text
python -m unittest discover -s tests -v
python -m compileall -q config db services runtime ui updates tests
```

Também executar os scripts de verificação do release, smoke do build isolado e as matrizes automatizadas. Resultado parcial nunca recebe o rótulo “pronto para produção”.
