# TrigoPDV — Atualização online segura e offline-first

## Objetivo

Permitir instalação inicial local e futuras atualizações remotas sem visitar o cliente, sem colocar internet no caminho crítico da venda e sem aceitar pacote adulterado.

## Arquitetura escolhida

Usar **TUF 7.0.0 + Velopack 1.2.0 + coordenador pequeno do TrigoPDV**.

- TUF fornece raiz de confiança independente, metadados assinados, expiração, hash/tamanho e proteção contra replay/rollback.
- Velopack troca a pasta versionada no Windows fora do processo principal e mantém caminho estável.
- O coordenador conhece caixa aberto, banco SQLite, backup, migração, health gate e política de rollout.

Velopack isolado foi descartado porque o feed e o hash viriam do mesmo canal. TUF isolado foi descartado porque não resolve arquivos travados e troca de versão no Windows. Atualizador inteiramente próprio foi descartado pelo risco de instalação híbrida.

## Separação de dados

```text
%LOCALAPPDATA%\TrigoDeMinas.TrigoPDV\  programa/Velopack (`packId`)
  current\                                conteúdo versionado
  packages\, Update.exe e stub           infraestrutura Velopack
%LOCALAPPDATA%\TrigoPDV\config.ini
%LOCALAPPDATA%\TrigoPDV\data\
%LOCALAPPDATA%\TrigoPDV\updates\
%LOCALAPPDATA%\TrigoPDV\state\
%LOCALAPPDATA%\TrigoPDV\backups\pre-update\
%LOCALAPPDATA%\TrigoPDV\logs\
```

O identificador imutável do pacote é `TrigoDeMinas.TrigoPDV` e o título visível é
`TrigoPDV`. Eles não podem ser trocados entre releases. O `packId` distinto é uma
invariante de segurança: o Velopack instala e desinstala sua própria raiz, portanto
`TrigoPDV` não pode ser usado como `packId` enquanto os dados persistentes estiverem
em `%LOCALAPPDATA%\TrigoPDV`.

Atualização nunca substitui configuração, banco, WAL/SHM, fila de impressão ou backups.
Diretórios do atualizador são derivados da raiz de dados da instalação, inclusive
quando `TRIGOPDV_DATA_DIR` estiver definido; eles nunca são derivados de `current\`.

## Pré-condição de schema

Na data desta especificação, o código integrado está em schema **v7**. A identidade
adiciona a v8 e o checkout manual adiciona a v9. O atualizador pode ser desenvolvido
com adaptadores isolados, mas integração de startup, build, instalador, publicação e
qualquer rótulo de produção ficam bloqueados até:

1. `SCHEMA_VERSION` ser 9;
2. migrações v7 → v8 → v9 e saltos suportados passarem;
3. seed/instalação limpa já terminar em v9;
4. versão central, pacote e manifesto declararem schema 9.

## Fonte única de versão

`release/version.toml` define SemVer, sequência monotônica de release e schema-alvo. `config/version.py` expõe esses dados ao runtime e ao recurso PE. Versão proposta para este pacote: `1.1.0`, sequência `2`, schema `9`.

## Repositório e confiança

O cliente recebe somente `root.json` público, empacotado dentro do conteúdo versionado
e assinado do programa. O runtime lê seus bytes e os fornece como `bootstrap` obrigatório
ao `tuf.ngclient.Updater`. A raiz inicial não é lida do cache gravável, de `config.ini`
nem de um caminho arbitrário informado pelo usuário. Ausência ou erro da raiz desabilita
updates antes de qualquer acesso à rede.

Chaves privadas TUF e certificado Authenticode ficam fora do projeto/cliente. O cache
de metadados e targets é gravável e, por isso, nunca substitui a âncora inicial de confiança.

```text
<UPDATE_BASE_URL>/metadata/*.json
<UPDATE_BASE_URL>/targets/channels/<channel>.json
<UPDATE_BASE_URL>/targets/packages/<hash>.nupkg
<UPDATE_BASE_URL>/targets/feeds/<hash>.json
```

O manifesto de canal, protegido pelo TUF, contém versão, sequência, canal, pacote/feed, schema, compatibilidade, rollout, versões bloqueadas e notas. O cliente baixa feed/pacote via TUF; depois entrega um repositório local verificado ao Velopack.

O bridge TUF → Velopack materializa o feed verificado com o nome esperado
`releases.<channel>.json` e o pacote com o nome exato referenciado nesse feed. Antes de
entregá-los ao Velopack, valida caminho relativo seguro, nome, tipo full, SHA-256 e tamanho
contra manifesto, metadado TUF e feed. O repositório local não contém URL remota nem arquivo
que não tenha sido baixado e verificado pelo TUF.

Com URL HTTPS vazia, raiz ausente, política inválida ou build local sem capacidade de update, o atualizador fica desabilitado e o PDV abre normalmente. Não existe fallback HTTP, `verify=False` ou pacote sem metadados válidos.

A política de rede usa `FetcherInterface` com orçamento total limitado para conexão,
leitura, repetição e tamanho. Repetições da biblioteca não podem multiplicar o timeout
configurado a ponto de bloquear a experiência offline.

Snapshot offline é um fluxo diferente: `stage_offline_snapshot(path)` aceita somente um
diretório local explicitamente selecionado, passa pela mesma raiz TUF e pelo mesmo bridge
e nunca habilita `file://` ou caminho local na configuração de atualização online.

## Fluxo

1. O entrypoint importa apenas biblioteca padrão e Velopack, executa
   `velopack.App().set_auto_apply_on_startup(False).run()` e só então importa o TrigoPDV.
2. O runtime carrega configuração/estado e executa `resume_pending_update()` antes de
   construir `PDVService` ou qualquer serviço que chame `Database.initialize()`.
3. Se não há aplicação pendente, o PDV abre imediatamente com a última versão boa.
4. Thread com timeout consulta TUF somente após a UI estar utilizável.
5. Canal/coorte determinam oferta; download usa `.partial` exclusivo e só vira
   `VERIFIED` após validação e `os.replace` atômico.
6. Aplicação ocorre sem carrinho, venda ou caixa aberto, preferencialmente após o
   fechamento normal.
7. Coordenador cria backup SQLite verificado, grava estado atômico e marca `APPLY_PENDING`.
8. `wait_exit_then_apply_updates()` é chamado somente quando o processo pode encerrar
   imediatamente e de forma limpa; o PDV não aguarda o limite que levaria a encerramento forçado.
9. Velopack substitui a versão após a saída do processo.
10. Nova versão inicia no health gate, migra sob lock e valida banco/configuração antes
    de construir `PDVService`.
11. Só após `COMMITTED` a UI comercial é liberada.
12. Antes de qualquer escrita comercial, falha restaura backup e agenda downgrade pelo
    pacote anterior verificado.

Após liberação comercial, banco antigo nunca é restaurado automaticamente, pois isso poderia apagar venda nova; a recuperação passa a ser correção para frente.

## Estados e observabilidade

`IDLE → CHECKING → DOWNLOADING → VERIFIED → BACKUP_OK → APPLY_PENDING → MIGRATING → HEALTH_PENDING → COMMITTED`, com transições de falha/rollback.

Logs JSONL rotativos registram tentativa, fase, versão, schema, duração e erro sanitizado. Não registram token, configuração completa, usuário, produto, preço, venda ou documento. Telemetria remota não será habilitada nesta etapa.

## Canais e rollout

`internal`, `pilot` e `stable`. A instalação recebe UUID aleatório; a coorte é hash de UUID + seed assinado. Promoção reutiliza o mesmo hash. Metadados podem pausar, bloquear ou reduzir rollout.

O UUID vem de `installation_state`; o atualizador não cria uma segunda identidade. A
sequência de release mais alta já vista é persistida e nunca diminui. Fluxo normal cria
`UpdateOptions` sem downgrade. `AllowVersionDowngrade` só pode ser ativado internamente
para rollback explícito, usando pacote anterior já verificado e sem liberar essa opção na UI.

## Transição do instalador legado

O Inno Setup atual não cria `Update.exe`, `sq.version`, `current\` ou o locator Velopack.
Por isso não é tratado como instalação Velopack existente. A 1.1.0 deve definir um único
instalador canônico Velopack e uma transição de uma vez que:

1. instala o programa na raiz do `packId` distinto;
2. continua usando a raiz de dados existente sem copiá-la ou removê-la;
3. mantém o bloqueio de instância pelo mesmo banco durante a convivência;
4. valida a nova instalação antes de aposentar atalhos/entrada do Inno;
5. nunca chama o desinstalador legado com opção que remova dados do TrigoPDV.

O Inno pode permanecer apenas como artefato legado de transição; não pode existir um
segundo Setup anunciado como atualização stable.

## Build e publicação

- PyInstaller obrigatoriamente `onedir`.
- Velopack gera Setup e pacotes completos; deltas ficam desabilitados nesta fase.
- Release `stable` falha sem TUF, certificado Authenticode/timestamp e parâmetros de publicação.
- Scripts leem segredos apenas de ambiente/secret store; nunca gravam chave no projeto ou logs.
- O pendrive recebe o mesmo Setup assinado e snapshot TUF offline. Pacote offline passa pelo mesmo verificador.
- `vpk` e binding Python usam exatamente 1.2.0 e `vpk pack --delta none`.
- Assinatura ocorre dentro do fluxo Velopack antes de hashes/metadados TUF.
- Publicação envia pacote/feed e metadados targets/snapshot antes de publicar `timestamp.json`
  por último. Falha parcial preserva o timestamp anterior.
- O build não apaga `TrigoPDV.spec`; a fonte única gera recurso PE, pacote e metadados.

## Critérios de aceite

- Falha de internet/servidor não atrasa nem impede venda.
- Metadado adulterado, expirado, replay, downgrade, pacote truncado/hash errado são rejeitados.
- Dois updaters não executam simultaneamente.
- Configuração, impressora e banco sobrevivem a update/rollback.
- Atualização fica pronta em background e é aplicada fora do processo comercial.
- Falha de migração/health antes da primeira escrita restaura banco e versão anterior no adaptador testado.
- Build local sem assinaturas não se apresenta como produção nem habilita update.
- Instalar/atualizar/desinstalar o programa não altera a raiz de dados.
- Uma instalação Inno não é confundida com instalação Velopack.
- Auto-apply permanece desligado e nenhum caminho cria `PDVService` antes do health gate.
- Feed e pacote entregues ao Velopack possuem nomes, hash e tamanho verificados pelo TUF.

## Bloqueios externos de produção

Implementação e testes locais não autorizam publicação stable. Produção continua bloqueada
sem host HTTPS, cerimônia/custódia/rotação de chaves TUF, `root.json` público definitivo,
certificado Authenticode com timestamp, procedimento validado de transição do Inno e testes
em Windows/VM/hardware real. Ferramentas locais não devem ser presumidas instaladas; scripts
as detectam, validam versões e falham com mensagem segura.

## Referências oficiais

- [Python-TUF 7.0.0 — Updater](https://theupdateframework.readthedocs.io/en/latest/api/tuf.ngclient.updater.html)
- [Python-TUF — implantação da raiz confiável](https://theupdateframework.readthedocs.io/en/stable/INSTALLATION.html)
- [Especificação TUF](https://theupdateframework.github.io/specification/)
- [Velopack — início com Python](https://docs.velopack.io/getting-started/python)
- [Velopack App 1.2.0](https://docs.velopack.io/reference/py/App)
- [Velopack UpdateManager 1.2.0](https://docs.velopack.io/reference/py/UpdateManager)
- [Velopack — estrutura Windows](https://docs.velopack.io/packaging/operating-systems/windows)
- [Velopack — preservação de arquivos](https://docs.velopack.io/integrating/preserved-files)
- [Velopack — fontes de atualização](https://docs.velopack.io/integrating/update-sources)
- [Velopack — assinatura de código](https://docs.velopack.io/packaging/signing)
- [Velopack — deltas](https://docs.velopack.io/packaging/deltas)
