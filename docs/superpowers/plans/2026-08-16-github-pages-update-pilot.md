# GitHub Pages Update Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar releases TUF+Velopack no GitHub Pages e fazer o caixa baixar atualizações em segundo plano, aplicando-as com segurança após fechamento.

**Architecture:** Um publicador puro gera e verifica a árvore estática TUF a partir dos artefatos Velopack. O GitHub Actions faz build e deploy; o cliente usa a raiz pública empacotada, persiste oferta/bundle e nunca coloca rede no caminho comercial.

**Tech Stack:** Python 3.13, TUF 7, securesystemslib, Velopack 1.2, GitHub Actions/Pages, Tkinter, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-16-github-pages-update-pilot-design.md`

## Global Constraints

- Banco, config, WAL/SHM, fila e backups nunca entram no pacote ou repositório Pages.
- URL online é HTTPS e não contém credenciais.
- Stable falha sem Authenticode válido; piloto nunca é rotulado stable.
- Root privada nunca entra em Git/GitHub Actions/cliente.
- Metadados online usam secrets e não podem aparecer em logs.
- Rede é assíncrona e falha aberta para operação comercial, fechada para update.
- Todo comportamento novo segue RED → GREEN e usa apenas bancos/diretórios temporários.

---

### Task 1: Publicador e verificador TUF

**Files:**
- Create: `tools/tuf_repository.py`
- Create: `tests/test_tuf_repository_tool.py`
- Modify: `tools/create_update_manifest.py`

**Interfaces:**
- Produces: `publish_repository(input_root, output_root, signers, versions, expires) -> Path`
- Produces: `verify_repository(repository_root, bootstrap_root, channel) -> dict`

- [ ] **Step 1: escrever testes RED de metadados e adulteração**

```python
def test_repository_is_loadable_and_every_artifact_is_authenticated(self):
    repository = publish_test_repository(self.tempdir, sequence=3)
    verified = verify_repository(repository, repository / "metadata/root.json", "pilot")
    self.assertEqual(verified["sequence"], 3)

def test_tampered_target_is_rejected(self):
    repository = publish_test_repository(self.tempdir, sequence=3)
    (repository / "targets/releases/1.1.1/app.nupkg").write_bytes(b"tampered")
    with self.assertRaises(TufPublishError):
        verify_repository(repository, repository / "metadata/root.json", "pilot")
```

- [ ] **Step 2: rodar e observar RED pela ausência do módulo**

Run: `python -m unittest tests.test_tuf_repository_tool -v`
Expected: FAIL por `ModuleNotFoundError`.

- [ ] **Step 3: implementar metadados TUF canônicos**

Usar `tuf.api.metadata.Metadata`, `Root`, `Targets`, `Snapshot`, `Timestamp`,
`TargetFile` e signers Ed25519. Rejeitar paths absolutos/`..`, symlinks,
duplicidade, expiração menor que 24h, versão não positiva e target fora do
staging. Escrever por temporário+`os.replace`, com timestamp por último.

- [ ] **Step 4: cobrir expiração, replay, truncamento e segredo em erro**

```python
for mutation in (expire_targets, lower_snapshot_version, truncate_timestamp):
    repository = publish_test_repository(self.tempdir, sequence=4)
    mutation(repository)
    with self.assertRaisesRegex(TufPublishError, "repositório"):
        verify_repository(repository, root, "pilot")
```

- [ ] **Step 5: rodar focais**

Run: `python -m unittest tests.test_tuf_repository_tool tests.test_updates -v`
Expected: PASS.

### Task 2: Cerimônia e fronteira de secrets

**Files:**
- Create: `tools/tuf_ceremony.py`
- Create: `docs/OPERACAO_RELEASE_TUF.md`
- Create: `tests/test_tuf_ceremony.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `create_root(public_path, root_signers, role_public_keys) -> Path`
- Produces CLI que escreve somente root pública no repositório e chaves em caminho externo explícito.

- [ ] **Step 1: RED para threshold e ausência de chave privada no projeto**

```python
def test_root_requires_two_of_three_and_repository_contains_no_private_key(self):
    result = run_ceremony(external_key_dir, project_root)
    root = Metadata.from_file(result.public_root)
    self.assertEqual(root.signed.roles["root"].threshold, 2)
    self.assertFalse(any(project_root.rglob("*.private.*")))
```

- [ ] **Step 2: implementar geração somente em diretório externo**

Falhar se o diretório de chaves estiver dentro do projeto. Gerar três roots e
três roles online distintas; exportar online keys somente ao stdout quando
`--export-github-secrets` for solicitado e com redação no modo normal. Nunca
sobrescrever uma cerimônia existente.

- [ ] **Step 3: documentar backup/rotação/revogação**

O documento define cópia offline dupla, threshold 2/3, expirações, rotação de
root assinada por velha+nova, revogação de role online e restauração testada.

- [ ] **Step 4: rodar focal e varredura**

Run: `python -m unittest tests.test_tuf_ceremony -v`
Run: `git grep -n -I -E "PRIVATE KEY|private.*key" -- ':!tests/*' ':!docs/*'`
Expected: testes PASS; nenhuma chave materializada.

### Task 3: Pipeline Velopack e Pages

**Files:**
- Create: `tools/build_online_release.py`
- Create: `.github/workflows/publish-update.yml`
- Create: `tests/test_online_release_pipeline.py`
- Modify: `tools/release_gate.py`
- Modify: `release/README.md`

**Interfaces:**
- Consumes: `publish_repository`, `verify_repository`.
- Produces: `_site/updates/metadata/*` e `_site/updates/targets/*`.

- [ ] **Step 1: RED para modo piloto/stable e ordem de publicação**

```python
def test_stable_refuses_unsigned_binaries(self):
    with self.assertRaisesRegex(ReleaseBuildError, "Authenticode"):
        build_online_release(channel="stable", artifacts=unsigned)

def test_pilot_build_has_no_data_files_and_timestamp_is_last(self):
    result = build_online_release(channel="pilot", artifacts=fakes)
    self.assertNotIn("config.ini", result.file_names)
    self.assertEqual(result.write_order[-1], "metadata/timestamp.json")
```

- [ ] **Step 2: implementar orquestração fail-closed**

Detectar Python 3.13.14+, `vpk 1.2.0`, PyInstaller e schema 9. Executar `vpk
pack --delta none --channel <channel>` com argumentos em lista, nunca shell.
Validar feed/pacote/Setup, gerar manifesto, TUF e verificação independente.

- [ ] **Step 3: workflow manual protegido**

Workflow usa `workflow_dispatch`, branch `main`, ambiente `github-pages`,
permissões mínimas `contents: read`, `pages: write`, `id-token: write`, actions
fixadas por SHA, secrets das três roles e deploy somente depois do verificador.

- [ ] **Step 4: gate e testes**

Run: `python -m unittest tests.test_online_release_pipeline -v`
Run: `python tools/release_gate.py`
Expected: PASS. `--production --channel stable` continua FAIL sem certificado.

### Task 4: Bootstrap configurado e persistência de oferta

**Files:**
- Modify: `config/settings.py`
- Modify: `config.ini.example`
- Modify: `TrigoPDV.spec`
- Modify: `updates/models.py`
- Modify: `updates/state.py`
- Modify: `updates/coordinator.py`
- Modify: `desktop_controller.py`
- Modify: `tests/test_updates.py`

**Interfaces:**
- Produces: `UpdateState.offer_manifest` autenticado e `restore_downloaded_offer()`.

- [ ] **Step 1: RED para defaults seguros e reinício da UI**

```python
def test_packaged_root_enables_pilot_default(self):
    settings = load_settings(example_with_root=True)
    self.assertTrue(settings.updates_enabled)
    self.assertEqual(settings.update_channel, "pilot")

def test_downloaded_offer_can_be_applied_after_controller_restart(self):
    controller.admin_check_for_update()
    restarted = new_controller(same_state=True)
    self.assertTrue(restarted.admin_update_status()["can_apply"])
```

- [ ] **Step 2: persistir manifesto mínimo autenticado**

Guardar version/sequence/schema/channel/rollout/artefatos no estado atômico,
com limites de tamanho e allowlist de campos. Reconstruir `UpdateOffer` somente
quando phase `DOWNLOADED` e bundle ainda confere hash/tamanho.

- [ ] **Step 3: tornar raiz obrigatória quando enabled**

PyInstaller/build falham se uma montagem habilitada não contiver root pública.
Instalação existente explicitamente disabled permanece disabled.

- [ ] **Step 4: rodar focais**

Run: `python -m unittest tests.test_updates tests.test_desktop_controller -v`
Expected: PASS.

### Task 5: Monitor assíncrono e UX operacional

**Files:**
- Create: `updates/monitor.py`
- Create: `tests/test_update_monitor.py`
- Modify: `desktop_controller.py`
- Modify: `ui/app.py`
- Modify: `ui/views.py`

**Interfaces:**
- Produces: `UpdateMonitor.start()`, `stop()`, `check_once()`.

- [ ] **Step 1: RED para offline e consulta única**

```python
def test_timeout_never_blocks_or_retries_tight_loop(self):
    monitor = UpdateMonitor(check=lambda: raise_timeout(), clock=clock)
    started = monotonic()
    monitor.start()
    self.assertLess(monotonic() - started, 0.05)
    self.assertEqual(wait_event(), "offline")
    self.assertFalse(monitor.due_again(within_hours=6))
```

- [ ] **Step 2: implementar thread daemon com intervalo persistido**

Iniciar depois da UI, uma execução por processo, lock interno, stop event e
callback entregue via `after`. Erros ficam sanitizados e não mostram modal no
caixa; oferta baixada gera aviso não bloqueante.

- [ ] **Step 3: aplicação continua explícita e segura**

Botão só habilita para admin, phase DOWNLOADED, bundle válido, carrinho/venda e
caixa fechados. Fechamento do caixa atualiza o aviso, mas nunca fecha o app.

- [ ] **Step 4: testes UI/lifecycle**

Run: `python -m unittest tests.test_update_monitor tests.test_ui_accessibility tests.test_ui_checkout -v`
Expected: PASS sem callback Tcl pendente.

### Task 6: E2E, auditoria e publicação

**Files:**
- Create: `tests/test_online_update_e2e.py`
- Update: `docs/QA_WINDOWS.md`
- Update: `C:/mind/projetos/trigopdv/historico.md` append-only
- Update: `C:/mind/projetos/trigopdv/pendencias.md`

- [ ] **Step 1: E2E HTTP local e adulteração**

Gerar chaves efêmeras, servir `_site/updates` em localhost por adaptador HTTPS
de teste, baixar sequência maior, reiniciar controller, aplicar com Velopack
fake e provar backup/estado. Repetir offline, 404, 429, 500, metadado expirado,
replay, pacote truncado e hash errado.

- [ ] **Step 2: preservação**

Usar sentinelas temporárias para DB/config/impressora/fila, atualizar/rollback e
comparar bytes. Nunca abrir banco real, seed operacional ou config real.

- [ ] **Step 3: validação completa**

Run: `python -m unittest discover -s tests -v`
Run: `python -m compileall -q config db integrations printing runtime services ui updates tests tools main.py init_db.py desktop_controller.py`
Run: `python tools/release_gate.py`
Expected: tudo PASS.

- [ ] **Step 4: GitHub**

Criar branch, commit, push, PR, aguardar CI/CodeQL e mesclar somente com checks
verdes. Habilitar Pages via GitHub Actions. Não disparar publicação stable.

- [ ] **Step 5: memória e entrega**

Registrar implementação, testes, limitações e o fato de que o primeiro
bootstrap continua manual. Não registrar keys, secrets, URLs com credenciais ou
dados comerciais.

