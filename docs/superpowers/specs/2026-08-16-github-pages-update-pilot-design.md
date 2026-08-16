# TrigoPDV — distribuição online do piloto pelo GitHub Pages

## Objetivo

Permitir que o único caixa da padaria receba correções futuras sem uma visita
técnica. A internet nunca participa da venda: o cliente consulta e baixa em
segundo plano, e só troca a versão com caixa fechado, backup SQLite verificado e
reinício controlado.

## Decisão

O canal inicial será `pilot`, hospedado em
`https://aaotaviano-pixel.github.io/TrigoPDV/updates/`. O GitHub Pages serve
somente arquivos públicos estáticos; TUF autentica metadados e conteúdo antes de
qualquer byte chegar ao Velopack. Uma indisponibilidade, resposta 404/429/500 ou
publicação parcial mantém a versão local íntegra e não bloqueia o PDV.

GitHub Pages é adequado ao piloto de um caixa: o site publicado fica muito
abaixo de 1 GiB e 100 GiB/mês. Se o número de instalações crescer, a mesma árvore
`metadata/` e `targets/` poderá migrar para armazenamento de objetos com domínio
próprio, sem trocar o formato ou confiar em GitHub no cliente.

## Bootstrap e versões

A versão instalada atualmente não contém uma raiz TUF definitiva. Haverá uma
última instalação manual de transição, criada depois da cerimônia de chaves, que:

1. embute apenas `updates/trusted/root.json` público;
2. habilita por padrão o canal `pilot` e a URL HTTPS acima em instalações novas;
3. preserva `config.ini`, banco, WAL/SHM, impressora, fila e backups existentes;
4. cria a nova estrutura Velopack com `packId=TrigoDeMinas.TrigoPDV.V2`.

Depois desse bootstrap, releases com sequência maior são descobertas online.
Uma configuração local explicitamente desabilitada continua sendo respeitada.

## Modelo de confiança

- A raiz TUF usa três chaves Ed25519 e threshold 2. As chaves privadas de raiz
  ficam fora do Git e fora do computador do caixa; o repositório recebe somente
  `root.json` público.
- `targets`, `snapshot` e `timestamp` usam chaves distintas armazenadas como
  secrets protegidos no ambiente `github-pages` do GitHub.
- O workflow nunca imprime chaves e rejeita valores ausentes, metadados
  expirados, sequência não monotônica, artefato adulterado e canal inválido.
- Um checkpoint exato de continuidade (versão TUF e hashes de metadados e
  manifestos por canal) fica em artefato protegido do último deploy concluído,
  separado do Pages. O publicador exige correspondência integral entre ambos.
- Resposta 404 só inicia a árvore na cerimônia manual explícita da primeira
  publicação; perda posterior do Pages ou do checkpoint falha fechada.
- O canal `stable` falha fechado sem Authenticode e timestamp válidos. O piloto
  pode usar pacote ainda sem Authenticode, mas somente depois da validação TUF e
  com indicação explícita de que continua sendo piloto.
- A raiz não vem de `config.ini`, rede ou cache gravável. Rotação de raiz exige
  assinatura pelo threshold anterior e pelo novo.

## Pipeline de publicação

Um workflow manual recebe `channel`, `rollout_percent`, `mandatory` e o sinal
de inicialização vazia, usado uma única vez na primeira publicação.
Versão, sequência, schema e pack ID vêm de `release/version.toml`.

1. CI Windows instala o lock com hashes e executa a suíte/gate de fonte.
2. PyInstaller produz `onedir`.
3. Velopack 1.2 produz pacote full, feed do canal e Setup; deltas permanecem
   desabilitados.
4. Para `stable`, o workflow exige e valida Authenticode antes de continuar.
5. O manifesto assinado referencia feed e pacote por nome, tamanho e SHA-256.
6. O publicador TUF gera `targets`, `snapshot` e `timestamp`, copiando a raiz
   pública; `timestamp.json` é materializado por último no staging.
7. Um verificador independente abre o repositório como cliente TUF, baixa o
   manifesto e todos os artefatos, e compara hashes antes do deploy.
8. GitHub Pages publica uma única árvore estática. O cliente rejeita qualquer
   estado intermediário incoerente e tenta novamente no próximo intervalo.
9. Somente depois do deploy concluído, o workflow registra o novo checkpoint
   independente. A agenda de 12 horas apenas renova versão/expiração TUF,
   preservando byte a byte todos os targets, políticas e artefatos.
10. Cada candidato inclui o hash do checkpoint predecessor. Imediatamente antes
    do deploy, o job relê o último ledger concluído e o Pages autenticado. Uma
    reexecução obsoleta é recusada; se o Pages já contém exatamente o candidato,
    o job apenas conclui o ledger, sem reconstruir nem republicar arquivos.
    Site e candidato ficam retidos por 30 dias para essa recuperação.

Uma republicação na mesma sequência pode apenas ampliar uma política já
assinada e exige os mesmos nomes, tamanhos e SHA-256 dos artefatos. Bytes novos
exigem sequência de aplicação maior. Expansão de rollout usa o modo somente
política, que reaproveita os artefatos do predecessor autenticado e não executa
PyInstaller ou Velopack.

O workflow não lê banco, configuração, produtos operacionais, vendas, backups ou
fila de impressão.

## Cliente local

- `config.ini.example` passa a trazer `enabled=true`, `channel=pilot` e a URL
  pública somente quando `root.json` definitivo acompanha a montagem.
- A UI inicia uma verificação em thread depois de ficar utilizável. O intervalo
  local impede consultas repetidas; timeout/erro viram estado informativo, não
  modal bloqueante.
- Uma oferta elegível é baixada e autenticada em background. A UI mostra
  “Atualização pronta para instalar”.
- Aplicar exige administrador, ausência de carrinho/venda/caixa aberto, backup
  SQLite verificável e pacote ainda correspondente ao estado persistido.
- A oferta é reconstruída do manifesto autenticado após reinício da UI; ela não
  pode depender apenas de um objeto em memória.
- Velopack aplica fora do processo. Na nova inicialização, health-check ocorre
  antes de `PDVService`. Falha antes da primeira escrita comercial bloqueia a
  nova versão e preserva evidência/backup para rollback.

## Falhas que precisam ser fechadas no núcleo existente

1. Não há ferramenta para criar/verificar metadados TUF reais.
2. Não há workflow de build Velopack + Pages.
3. O cliente perde `_pending_update_offer` ao reiniciar a tela, embora o bundle
   continue no estado local.
4. Não há monitor periódico em background nem persistência de `last_check`.
5. O bootstrap aceita `root.json` opcional; uma montagem com updates habilitados
   deve falhar se a raiz não estiver empacotada.
6. O gate não verifica a árvore Pages completa nem diferencia piloto de stable.
7. O rollback de versão precisa de ensaio real em instalação Velopack isolada;
   testes unitários não bastam para declarar produção.

## Critérios de aceite

- Offline, timeout, 404/429/500 e metadado inválido não bloqueiam login, caixa ou
  venda.
- Root/targets/snapshot/timestamp expirados, adulterados ou fora de sequência são
  rejeitados.
- Feed ou pacote alterado, truncado, duplicado ou com nome inseguro é rejeitado.
- Publicação de sequência igual/menor falha.
- Cliente baixa uma sequência maior, persiste-a, sobrevive a reinício e consegue
  aplicar sem depender de objeto em memória.
- Caixa/carrinho/venda abertos bloqueiam aplicação, nunca download.
- Banco, config, impressora e fila ficam byte a byte preservados em atualização e
  rollback ensaiados.
- Stable sem Authenticode falha; piloto TUF autenticado permanece identificado
  como piloto.
- Suíte completa, compileall, release gate, verificador TUF e smoke Velopack
  passam antes da publicação.

## Limites externos

Sem certificado Authenticode não existe liberação `stable`. A primeira versão
com raiz definitiva é necessariamente instalada uma vez de forma manual. Testes
de SmartScreen, antivírus, reboot forçado e rollback do executável exigem VM
Windows; testes de impressão permanecem dependentes do equipamento físico.

