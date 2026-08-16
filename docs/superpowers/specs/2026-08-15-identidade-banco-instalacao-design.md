# TrigoPDV — Identidade, banco e instalação segura

## Objetivo

Remover credenciais previsíveis, tornar primeiro uso e recuperação seguros, garantir migrações recuperáveis e impedir duas instâncias de disputar o mesmo banco.

## Provisionamento inicial

- Banco operacional novo é criado no schema corrente, recebe o catálogo validado e nasce com **zero usuários**.
- `installation_state` guarda UUID aleatório e estado `UNINITIALIZED`/`READY`.
- Primeiro início mostra assistente obrigatório para nome, login, senha e código de recuperação.
- O código é gerado com `secrets`, mostrado uma vez e precisa ser confirmado; somente hash é persistido.
- Criação do primeiro admin e transição para `READY` acontecem na mesma transação.
- Instalações existentes com usuários ficam `READY` e nunca são reprovisionadas.
- Constantes/senhas padrão e recuperação local sem prova são removidas.

## Autenticação

- Cinco falhas de senha em 15 minutos bloqueiam a conta por 15 minutos, sem `sleep` na UI.
- Aprovação administrativa usa o mesmo contador de senha.
- Cinco falhas de recuperação bloqueiam esse fluxo por 30 minutos.
- Login bem-sucedido limpa o contador; recuperação bem-sucedida troca senha e gira o código.
- Mensagens não confirmam existência de login.
- `deve_trocar_senha` é aplicado no backend. A única operação permitida é troca atômica da própria senha e logout.
- Admin antigo sem código recebe aviso autenticado para configurá-lo, sem backdoor local.

## Migrações

Criar migrações numeradas, aditivas e imutáveis:

- v6: invariantes e reparo do schema legado (já implementada pela camada de migrações);
- v7: `installation_state`;
- v8: janelas/bloqueios de login e recuperação;
- v9: campos do checkout manual definidos na especificação correspondente.

Antes de migrar banco não vazio:

1. adquirir lock SQLite exclusivo;
2. recusar schema mais novo ou origem desconhecida;
3. criar backup pela API SQLite;
4. executar `integrity_check` do backup;
5. aplicar cada migração na mesma transação;
6. atualizar `schema_version` somente no fim;
7. executar health check.

Falha de backup impede qualquer DDL; falha intermediária faz rollback total. Clientes podem saltar de qualquer schema suportado até o atual.

## Instância única

Antes de abrir/migrar o banco, `SingleInstanceGuard` usa `CreateFileW` com compartilhamento zero em arquivo associado ao caminho canônico do banco. Crash libera o handle. Segunda instância exibe mensagem simples e não toca no banco.

## Catálogo distribuído, banco local e instalador

- O artefato offline é um SQLite exclusivo de catálogo, com somente a tabela
  `produtos`. Ele não é um banco operacional e nunca contém `usuarios`,
  `installation_state`, caixas, vendas, itens, movimentos, auditoria, cache ou
  sequências operacionais.
- O catálogo preserva exatamente os 20 campos e as 192 linhas atuais: 167 GTINs
  estruturalmente válidos e 25 PLUs internos. Um digest lógico canônico, ordenado
  por código, prova que nenhuma coluna de produto mudou durante a separação.
- O instalador copia somente programa e catálogo. Ele nunca copia um SQLite
  operacional para `%LOCALAPPDATA%\TrigoPDV`.
- Sob `SingleInstanceGuard`, e somente quando não existe update pendente nem banco,
  `-wal` ou `-shm`, o bootstrap valida o catálogo em modo somente leitura, cria um
  banco operacional fresh v9 em staging no mesmo volume e importa os produtos em
  uma transação.
- O fresh schema gera `installation_id` aleatório naquela máquina. Duas instalações
  derivadas do mesmo catálogo devem produzir UUIDs distintos, ambas em
  `UNINITIALIZED`, sem qualquer caminho de rotação de identidade ou backdoor.
- Antes da publicação local, o staging precisa passar digest, contagens,
  `integrity_check` e `foreign_key_check`; todas as conexões e sidecars são fechados
  e o arquivo completo entra no caminho definitivo por rename atômico.
- Se houver update pendente, `resume_pending_update()`/health tem prioridade e o
  bootstrap do catálogo não toca no banco. Banco ausente com WAL ou SHM órfão
  falha de forma segura, sem fabricar um banco novo.
- Banco existente jamais é aberto pelo bootstrap nem substituído por instalador,
  catálogo ou atualização. Catálogos de versões novas valem apenas para instalações
  futuras e não sobrescrevem produtos/preços locais.
- Programa fica separado de `%LOCALAPPDATA%\TrigoPDV`, onde vivem configuração, banco, logs e backups.
- Pendrive terá uma única árvore de aplicativo/Setup, sem `_internal\_internal` nem cópias antigas.
- Build fixa versões de runtime/build e nunca inclui `config.ini` local.
- Backup parcial é fechado antes de tentativa de remoção.

## Critérios de aceite

- Não existe credencial autenticável antes do provisionamento.
- Primeiro admin é criado uma única vez e nenhum segredo entra em auditoria.
- Usuário marcado para troca não abre caixa, vende, administra ou configura impressora.
- Cinco falhas bloqueiam temporariamente e o relógio injetado permite testar expiração.
- Migração v4→v9 preserva catálogo, usuários e histórico; falha injetada restaura o estado anterior.
- Duas instâncias para o mesmo banco não inicializam simultaneamente.
- O SQLite distribuído possui somente 192 produtos; duas instalações limpas
  chegam a v9 com UUIDs distintos, estado `UNINITIALIZED`, zero usuários,
  histórico/cache e o mesmo digest lógico dos 20 campos.
- DB, WAL e SHM existentes ficam byte a byte preservados; sidecar órfão bloqueia
  bootstrap e não é apagado automaticamente.
