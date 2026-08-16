# Atualização Online Segura Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instalar uma base offline-first de atualização TUF + Velopack com staging verificado, backup/health/rollback e publicação que falha fechada sem assinatura.

**Architecture:** TUF escolhe e baixa manifesto/feed/pacotes; um bridge materializa um repositório Velopack local somente com artefatos verificados; Velopack aplica fora do processo; `UpdateCoordinator` persiste estados e usa serviços de migração/backup/health do TrigoPDV antes de liberar `PDVService`.

**Tech Stack:** Python 3.12, `tuf==7.0.0`, `velopack==1.2.0`, `packaging`, PyInstaller onedir, PowerShell/SignTool.

## Global Constraints

- URL HTTPS e raiz TUF são obrigatórias para habilitar update.
- Chaves privadas e certificado nunca entram no projeto ou cliente.
- Internet/update nunca bloqueia venda normal.
- Pacotes completos apenas nesta fase.
- Banco/configuração/impressora ficam fora da pasta versionada.
- Stable não é publicado sem TUF e Authenticode válido.
- `packId` imutável `TrigoDeMinas.TrigoPDV`, título visível `TrigoPDV`; a raiz Velopack nunca coincide com `%LOCALAPPDATA%\TrigoPDV`.
- Auto-apply Velopack fica explicitamente desligado; `App().run()` ocorre antes de imports do TrigoPDV.
- `resume_pending_update()`/health ocorrem antes de construir `PDVService`.
- Estado atual do schema é v7; integração/build/release do atualizador ficam bloqueados até v8 e v9 estarem integrados e o gate v9 passar.
- Snapshot offline usa API local separada e nunca relaxa a exigência HTTPS da política online.

---

### Task 0: Pré-condições auditadas e gate v9

**Files:**
- Create: `.superpowers/sdd/atualizacao-online/task-0-audit.md`
- Reference: `docs/superpowers/plans/2026-08-15-gate-integracao-producao.md`

- [x] Auditar entrypoints, caminhos, empacotamento, TUF/Velopack e registrar fontes oficiais.
- [ ] Confirmar migração aditiva v7 → v8 → v9 e `SCHEMA_VERSION == 9`.
- [ ] Confirmar seed/instalação limpa v9 e suíte integrada do gate.
- [ ] Bloquear Task 7, Task 8, Task 9 e qualquer artefato de produção enquanto os dois itens anteriores falharem.
- [ ] Definir e testar a transição de uma vez do Inno legado para o único Setup Velopack canônico.

---

### Task 1: Versão central e dependências fixas

**Files:**
- Create: `release/version.toml`
- Create: `config/version.py`
- Modify: `requirements.txt`
- Modify: `requirements-build.txt`
- Modify: `TrigoPDV.spec`
- Modify: `installer/TrigoPDV.iss`
- Create: `tests/test_versioning.py`

- [ ] Escrever testes que runtime, schema, artefato legado Inno, recurso PE, Velopack e manifesto concordam com `1.1.0`, sequência 2, schema 9; o teste deve ficar RED enquanto o schema real ainda for v7/v8.
- [ ] Fixar `tuf==7.0.0`, `velopack==1.2.0` e versões testadas das demais dependências; fixar PyInstaller.
- [ ] Gerar/expor versão sem duplicar lógica, manter `TrigoPDV.spec` como entrada canônica sem apagá-lo e rodar teste.
- [ ] Testar `packId = "TrigoDeMinas.TrigoPDV"`, `packTitle = "TrigoPDV"` e rejeitar qualquer raiz de programa igual/contida na raiz de dados.

### Task 2: Política e estado local

**Files:**
- Create: `updates/__init__.py`
- Create: `updates/models.py`
- Create: `updates/state.py`
- Modify: `config/settings.py`
- Modify: `config.ini.example`
- Create: `tests/test_update_state.py`

**Interfaces:**
- Produces: `UpdatePolicy`, `UpdateOffer`, `UpdatePhase`, `UpdateStateStore`.

- [ ] Escrever testes de URL vazia, HTTP rejeitado, canal inválido, JSON truncado e escrita atômica.
- [ ] Implementar configuração `enabled`, URLs, canal, orçamento total de timeout e diretórios; raiz TUF vem somente do recurso empacotado; modo inválido fica desabilitado com motivo seguro.
- [ ] Implementar estado atômico, `highest_seen_sequence` monotônico e coorte determinística pelo UUID já existente em `installation_state`.
- [ ] Testar que diretórios derivam da raiz de dados, inclusive com `TRIGOPDV_DATA_DIR`, e nunca de `current\`.
- [ ] Implementar `stage_offline_snapshot(path)` como política separada que não aceita URL, `file://` ou alteração da configuração online.

### Task 3: Repositório TUF

**Files:**
- Create: `updates/repository.py`
- Create: `tests/test_update_repository.py`

**Interfaces:**
- Produces: `TufUpdateRepository.check(installed, channel, cohort)` e `stage(offer)`.

- [ ] Criar repositório TUF temporário de teste com chaves somente em `TemporaryDirectory`; nunca presumir raiz/chave/ferramenta de produção disponível.
- [ ] Testar bootstrap ausente/inválido, válido, adulterado, expirado, rotação, rollback, manifesto inválido, canal/coorte, tamanho/hash, travessia de caminho e download interrompido.
- [ ] Implementar `tuf.ngclient.Updater(..., bootstrap=<bytes da raiz empacotada>)` com metadata/targets locais, `FetcherInterface` injetável, orçamento total de timeout e `.partial` exclusivo.
- [ ] Validar SemVer, sequência monotônica, schema suportado, feed/package/rollback targets e rodar testes.
- [ ] Testar que repetição, 404/500, lentidão e leitura parcial respeitam o mesmo orçamento total e nunca atrasam a abertura/venda.

### Task 4: Adaptador Velopack

**Files:**
- Create: `updates/velopack_adapter.py`
- Create: `tests/test_velopack_adapter.py`

**Interfaces:**
- Produces: `prepare_local_repository`, `schedule_apply`, `schedule_rollback`.

- [ ] Escrever testes com camada nativa injetada para auto-apply desligado, feed/pacote verificados, apply fora do processo e downgrade permitido somente no rollback.
- [ ] Gerar repositório Velopack local exclusivamente dos targets TUF baixados: `releases.<channel>.json` e pacote full com nome exato do feed.
- [ ] Validar nome relativo seguro, canal, tipo full, tamanho e SHA-256 em manifesto TUF, feed e arquivo antes de construir `UpdateManager` local.
- [ ] Em fluxo normal usar `AllowVersionDowngrade=False` e deltas desabilitados; criar opção com downgrade somente no método privado de rollback.
- [ ] Usar `wait_exit_then_apply_updates` somente quando o app pode encerrar imediatamente; nunca substituir pasta com Python/robocopy nem aguardar encerramento forçado.

### Task 5: Health gate e coordenador

**Files:**
- Create: `updates/health.py`
- Create: `updates/coordinator.py`
- Create: `tests/test_update_coordinator.py`

**Interfaces:**
- Produces: `HealthReport`, `UpdateCoordinator.check_and_stage`, `prepare_apply`, `resume_pending_update`.

- [ ] Testar offline, timeout, download falho, disco insuficiente nos volumes de dados/programa, dois coordenadores, carrinho/caixa/venda pendente, backup falho, migração falha, health falho, commit e rollback.
- [ ] Implementar lock próprio, orçamento de espaço para staging/cópia/descompactação/backup, backup SQLite verificado e transições permitidas.
- [ ] Bloquear UI comercial até `COMMITTED` quando existe `APPLY_PENDING`.
- [ ] Restaurar backup/agendar downgrade somente antes de escrita comercial.
- [ ] Testar crash/reboot em cada transição e estado truncado; após apply sem estado recuperável, falhar fechado para a UI comercial.
- [ ] Preservar byte a byte configuração/fila e validar banco/WAL/SHM usando apenas sentinelas e SQLite temporários.

### Task 6: Logs técnicos sanitizados

**Files:**
- Create: `runtime/logging_config.py`
- Modify: `updates/coordinator.py`
- Create: `tests/test_technical_logging.py`

- [ ] Escrever teste de JSONL/rotação e teste que token, URL com query, documento, usuário, produto, preço, venda, conteúdo de configuração e caminho pessoal não aparecem.
- [ ] Implementar eventos técnicos por allowlist com attempt/version/schema/phase/duração/código de erro sanitizado.

### Task 7: Integração de startup e UI administrativa

**Files:**
- Modify: `main.py`
- Modify: `desktop_controller.py`
- Modify: `ui/app.py`
- Modify: `ui/views.py`
- Modify: `ui/contracts.py`
- Create: `tests/test_ui_updates.py`

- [ ] Escrever teste que `velopack.App().set_auto_apply_on_startup(False).run()` ocorre antes de qualquer import/bootstrap do TrigoPDV no processo empacotado.
- [ ] Escrever teste que `resume_pending_update()` e health terminam antes de construir `PDVService`/chamar `Database.initialize()` pelo serviço.
- [ ] Escrever teste de check assíncrono, retorno à thread Tk via `after`, modo offline, status e aplicação somente sem carrinho e sem caixa aberto.
- [ ] Integrar thread pós-UI e seção admin “Atualizações” com versão/status/verificar/aplicar ao sair.
- [ ] Garantir que exceção do updater só muda status/log e rodar testes UI.

### Task 8: Build, publicação e pendrive

**Files:**
- Create: `tools/build_release.ps1`
- Create: `tools/verify_release.ps1`
- Create: `tools/publish_release.ps1`
- Modify: `build_release.bat`
- Modify: `installer/TrigoPDV.iss` somente como transição legada, sem segundo Setup stable
- Modify: `TrigoPDV_Instalacao_PenDrive/LEIA-ME.txt`
- Create: `docs/ATUALIZACOES_ONLINE.md`
- Create: `tests/test_release_scripts.py`

- [ ] Testar estaticamente que stable falha sem schema 9, raiz empacotada, `vpk` 1.2.0, SignTool/certificado, HTTPS, diretório de publicação e transição Inno validada; scripts detectam ferramentas e não presumem instalação.
- [ ] Empacotar onedir pelo `.spec` preservado, chamar `vpk 1.2.0` com `--packId TrigoDeMinas.TrigoPDV`, `--packTitle TrigoPDV` e `--delta none`, exigir assinatura/timestamp e verificar Setup/Update/aplicativo.
- [ ] Gerar targets/metadados TUF somente depois do pacote final assinado; publicar pacote/feed, targets e snapshot antes de publicar `timestamp.json` por último.
- [ ] Usar variáveis de ambiente/secret store e mascarar comandos; nenhuma chave é criada dentro do projeto.
- [ ] Definir um único Setup Velopack canônico e testar migração do Inno sem apagar a raiz de dados, sem atalhos concorrentes e sem confundir instalação legada com locator Velopack.
- [ ] Preparar pendrive por staging + manifesto, preservando dados fora do escopo; snapshot offline entra apenas por `stage_offline_snapshot(path)` e pela mesma raiz TUF.

### Task 9: Falhas e gate do atualizador

- [ ] Rodar testes de updater completos.
- [ ] Injetar 404/500/orçamento total de timeout/truncado/hash/metadata/rotação/downgrade/disco/lock/migração/health/reboot-state com adaptadores locais.
- [ ] Rodar suíte completa e build PyInstaller.
- [ ] Executar pacote em diretório isolado com update desabilitado e confirmar abertura offline.
- [ ] Executar atualização local assinada por chave TUF de teste, confirmar backup/migração/health e rollback simulado.
- [ ] Marcar release como não-produção se Authenticode real não estiver disponível.

## Matriz TDD obrigatória

| Módulo | Contratos RED antes da implementação |
|---|---|
| Versão/schema | v7/v8 não podem produzir release 1.1.0; v9, TOML, PE, pacote e manifesto concordam |
| Caminhos | `packId` não colide com dados; update/uninstall preserva config, DB, WAL/SHM, fila e backups |
| Estado/coorte | gravação atômica, truncado, formato futuro, UUID existente, coorte estável, sequência nunca regride |
| TUF/rede | bootstrap empacotado, rotação/expiração/replay, hash/tamanho, path traversal e orçamento total de timeout |
| Bridge Velopack | feed/nupkg com nomes exatos e somente targets verificados; pacote full; sem URL remota |
| Apply/rollback | auto-apply off, encerramento limpo, downgrade somente rollback, falha antes da escrita restaura |
| Startup/health | Velopack antes dos imports; resume/health antes de `PDVService`; UI bloqueada no pending inseguro |
| Offline | falha de rede não chama UI; snapshot local usa API separada e mesma raiz TUF |
| Inno legado | não é locator Velopack; transição mantém dados e deixa um único instalador/atalho canônico |
| Release | ferramentas detectadas, versões exatas, assinatura antes do TUF e `timestamp.json` por último |

## Bloqueios externos para stable

- Host/domínio HTTPS e política de cache/publicação.
- Cerimônia, threshold, custódia, expiração, rotação/revogação e raiz pública TUF definitiva.
- Certificado Authenticode e timestamp válidos.
- Procedimento exercitado de transição do Inno já instalado.
- VM/Windows e hardware real para Setup, reinício, arquivo bloqueado, antivírus/SmartScreen e falta de espaço.

Esses itens não impedem implementação/testes com diretórios temporários, fetchers falsos,
chaves TUF exclusivamente de teste e camada Velopack injetada. Nenhuma ferramenta, chave,
certificado ou serviço externo é considerado instalado/disponível sem detecção no gate.

## Referências oficiais

- [Python-TUF 7.0.0 — Updater](https://theupdateframework.readthedocs.io/en/latest/api/tuf.ngclient.updater.html)
- [Python-TUF — raiz de confiança](https://theupdateframework.readthedocs.io/en/stable/INSTALLATION.html)
- [Velopack — Python](https://docs.velopack.io/getting-started/python)
- [Velopack App 1.2.0](https://docs.velopack.io/reference/py/App)
- [Velopack UpdateManager 1.2.0](https://docs.velopack.io/reference/py/UpdateManager)
- [Velopack — Windows](https://docs.velopack.io/packaging/operating-systems/windows)
- [Velopack — fontes locais](https://docs.velopack.io/integrating/update-sources)
- [Velopack — assinatura](https://docs.velopack.io/packaging/signing)
- [Velopack — deltas](https://docs.velopack.io/packaging/deltas)
