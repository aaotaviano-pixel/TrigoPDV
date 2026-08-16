# Identidade, Banco e Instalação Segura Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar credenciais previsíveis, provisionar primeiro admin com recuperação, migrar com backup atômico e impedir concorrência de processos.

**Architecture:** Migrações numeradas são coordenadas antes dos serviços; `ProvisioningService` controla o primeiro uso; autenticação usa bloqueios temporários; `SingleInstanceGuard` protege o banco antes de inicializar; `CatalogBootstrapService` cria atomicamente o banco operacional fresh por máquina a partir de um catálogo exclusivo de produtos.

**Tech Stack:** Python 3.12, SQLite Backup API, bcrypt/scrypt, Win32 `CreateFileW`, Tkinter, `unittest`.

## Global Constraints

- Migrações aditivas e dados legados preservados.
- Backup verificado antes de DDL em banco existente.
- Nenhuma credencial em produção, documentação, log ou artefato distribuído.
- Catálogo final: SQLite exclusivo de produtos, com 192 linhas, sem schema ou
  dados operacionais. Cada máquina cria seu próprio banco operacional fresh v9.
- Projeto sem Git: snapshot timestamped antes das mudanças.

---

### Task 1: Snapshot e baseline recuperável

**Files:**
- Create: `backups/pre_update_total_<timestamp>/manifesto-sha256.txt`

- [x] Copiar somente fontes, configurações-modelo, instaladores e banco-base legado para pasta timestamped.
- [x] Calcular SHA-256 do banco-base legado e dos arquivos-fonte no snapshot.
- [x] Rodar baseline `python -m unittest discover -s tests -v` e registrar a falha intermitente de Tk separadamente se reaparecer.

### Task 2: Motor de migrações e backup

**Files:**
- Create: `db/migrations.py`
- Create: `tests/test_migrations.py`
- Modify: `db/database.py`
- Modify: `db/schema.py`
- Modify: `services/backup.py`

**Interfaces:**
- Produces: `MigrationResult`, `MigrationManager.migrate(backup_dir)` e `Database.backup_to(path)`.

- [x] Escrever testes de banco vazio, v4→v6, schema futuro, backup falho, DDL falho e duas inicializações.
- [x] Rodar e confirmar falhas por ausência do motor.
- [x] Implementar leitura de versão, backup/integrity, lock de escrita, migrações ordenadas e update de versão no fim.
- [x] Corrigir fechamento do destino de backup antes de remover parcial.
- [x] Rodar `python -m unittest tests.test_migrations -v`.

### Task 3: Estado de instalação e provisionamento

**Files:**
- Create: `services/provisioning.py`
- Create: `tests/test_provisioning.py`
- Create: `tests/support.py`
- Modify: `services/auth.py`
- Modify: `services/pdv_service.py`
- Modify: `desktop_controller.py`

**Interfaces:**
- Produces: `ProvisioningStatus`, `status`, `generate_recovery_code`, `provision_initial_admin`.

- [x] Escrever testes de zero conta padrão, criação única/concorrrente, hashes e auditoria sem segredo.
- [x] Rodar e confirmar que a conta padrão ainda nasce.
- [x] Remover constantes/bootstrap padrão; implementar migração 7 e provisionamento transacional.
- [x] Criar helper de testes que provisiona credenciais exclusivamente no banco temporário.
- [x] Migrar testes existentes do literal antigo para o helper e rodar backend/controlador.

### Task 4: UI obrigatória de primeiro uso

**Files:**
- Create: `ui/setup.py`
- Modify: `ui/app.py`
- Modify: `ui/contracts.py`
- Modify: `main.py`
- Create: `tests/test_ui_setup.py`

- [x] Escrever testes de instalação não inicializada, impossibilidade de cancelar para o caixa, confirmação do código e retorno ao login.
- [x] Implementar assistente rolável, mostrar código uma vez, exigir repetição e não iniciar sessão automaticamente.
- [x] Remover impressão de credencial de `main.py`/`init_db.py` e rodar testes UI.

### Task 5: Troca obrigatória atômica

**Files:**
- Modify: `services/errors.py`
- Modify: `services/security.py`
- Modify: `services/auth.py`
- Modify: `services/pdv_service.py`
- Modify: `desktop_controller.py`
- Modify: `tests/test_backend.py`

- [ ] Escrever testes bloqueando caixa, venda, administração e impressora para flag ativa.
- [ ] Escrever teste de verificação+troca na mesma transação e revalidação do usuário em cada chamada.
- [ ] Implementar `PasswordChangeRequiredError`, exceção explícita apenas na troca própria e atualizar sessão após commit.
- [ ] Rodar backend/controlador/impressão.

### Task 6: Rate limit e recuperação

**Files:**
- Create: `services/rate_limit.py`
- Create: `tests/test_auth_rate_limit.py`
- Modify: `db/migrations.py`
- Modify: `services/auth.py`
- Modify: `ui/recovery.py`
- Modify: `main.py`

- [ ] Escrever testes com relógio injetado para cinco falhas/15 min, bloqueio 15 min, recovery 30 min e contador compartilhado com aprovação.
- [ ] Escrever teste removendo a recuperação local sem código.
- [ ] Implementar migração 8, janelas UTC, mensagens neutras e rotação de recovery.
- [ ] Tornar argumento legado de recuperação somente informativo, sem mutação.
- [ ] Rodar `python -m unittest tests.test_auth_rate_limit -v`.

### Task 7: Instância única

**Files:**
- Create: `runtime/__init__.py`
- Create: `runtime/single_instance.py`
- Create: `tests/test_single_instance.py`
- Modify: `main.py`
- Modify: `init_db.py`

- [ ] Escrever testes multiprocesso para mesmo/diferente banco e liberação após crash.
- [ ] Implementar `CreateFileW` com caminho canônico e compartilhamento zero.
- [ ] Adquirir lock antes de `Database`/migração e rodar testes.

### Task 8: Catálogo de produtos e instaladores equivalentes

**Files:**
- Create: `TrigoPDV_Instalacao_PenDrive/dados-iniciais/catalogo-produtos.sqlite3`
- Create: `services/catalog_bootstrap.py`
- Create: `tests/test_catalog_bootstrap.py`
- Modify: `TrigoPDV_Instalacao_PenDrive/instalador/Instalar_TrigoPDV.cmd`
- Modify: `TrigoPDV.spec`
- Modify: `installer/TrigoPDV.iss`
- Modify: `main.py`
- Modify: `init_db.py`
- Modify: `tests/test_backend.py`
- Modify: `TrigoPDV_Instalacao_PenDrive/INSTALAR.txt`
- Remove after snapshot: `TrigoPDV_Instalacao_PenDrive/Recuperar_Acesso_Administrador.cmd`

- [ ] Fazer backup timestamped e verificável do banco-base anterior, catálogo,
  scripts e pacote; registrar SHA-256, `integrity_check`, FK e manifesto antes de
  substituir ou remover qualquer artefato.
- [ ] Escrever RED exigindo que `catalogo-produtos.sqlite3` tenha somente
  `produtos`, 192 linhas/20 campos, 167 GTINs válidos, 25 PLUs, preços positivos,
  zero duplicidades e o mesmo digest lógico canônico do catálogo anterior.
- [ ] Escrever RED de duas instalações a partir do mesmo catálogo: ambas terminam
  no schema 9, `UNINITIALIZED`, com UUIDs válidos e distintos, 192 produtos e
  zero usuários, caixas, vendas, itens, movimentos, auditoria e cache.
- [ ] Escrever RED para idempotência, catálogo truncado/adulterado, crash antes e
  depois do rename, disputa concorrente e preservação byte a byte de banco
  existente. DB ausente com `-wal` ou `-shm` órfão deve falhar sem criar/apagar nada.
- [ ] Escrever RED da ordem de startup: update pendente executa resume/health antes
  de qualquer bootstrap; sem update e sem DB/sidecars, `SingleInstanceGuard`
  antecede a criação do staging e o `PDVService` nasce somente depois do commit.
- [ ] Construir o catálogo em arquivo SQLite novo a partir de leitura immutable dos
  produtos. Não higienizar por `DELETE`: arquivo novo evita transportar páginas que
  já contiveram identidade. Fechar journal/sidecars e validar antes de publicar.
- [ ] Implementar bootstrap fresh v9 em staging no mesmo volume, importar em uma
  transação e publicar por rename atômico. O UUID nasce do fresh schema local;
  não existe sentinel, personalização posterior nem backdoor.
- [ ] Ajustar PyInstaller/CMD/Inno para transportar programa+catálogo, nunca copiar
  SQLite operacional. Catálogo novo não é reaplicado a banco existente.
- [ ] Após snapshot, remover `_internal\_internal`, a cópia `app\TrigoPDV`, o
  lançador de recuperação legado e textos de credencial entregue; manter uma única
  árvore canônica.
- [ ] Rodar testes focais, smoke de duas instalações isoladas e comparar
  contagens/digest/manifesto/hashes do pacote final.

### Task 9: Gate integrado

- [ ] Rodar `python -m unittest discover -s tests -v`.
- [ ] Rodar `python -m compileall -q config db services runtime ui tests`.
- [ ] Verificar por AST que produção não contém senha/login padrão nem impressão de credencial.
- [ ] Simular v4→v9 em cópia, primeiro uso, cinco falhas, recovery, duas instâncias e banco já existente.
- [ ] Simular duas instalações fresh pelo mesmo catálogo e provar UUIDs distintos,
  digest idêntico dos produtos e zero identidade/histórico/cache antes do setup.
- [ ] Provar prioridade de update pendente, bloqueio de WAL/SHM órfão,
  atomicidade do staging/rename e preservação byte a byte de dados existentes.
