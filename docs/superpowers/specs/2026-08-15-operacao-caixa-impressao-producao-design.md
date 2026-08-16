# TrigoPDV — Operação de caixa e impressão pronta para produção

## Objetivo

Definir os invariantes operacionais obrigatórios para um único caixa físico: uma sessão financeira aberta por vez, venda e movimentos idempotentes, cancelamento auditado, impressão recuperável após falha ou reinício e interface utilizável em telas pequenas. Este documento complementa o checkout manual e integra a mesma migração aditiva de schema 9.

## Limites explícitos

- O PDV é local e opera com um único caixa físico. O bloqueio de instância do processo não substitui a unicidade financeira no banco.
- PIX permanece **manual**. O sistema pode mostrar o QR Code e registrar a confirmação do operador, mas não consulta banco, PSP ou adquirente e nunca anuncia conciliação automática.
- Cancelar uma venda no PDV não estorna PIX/cartão no banco ou na adquirente. A interface deve avisar isso antes da confirmação.
- Impressão física não é transacional: um desligamento depois de o papel sair e antes da confirmação no banco pode produzir repetição na retomada. A fila deve tornar esse risco visível, sem prometer exatamente uma impressão.
- Testes automatizados usam somente bancos temporários e adaptadores falsos. Papel, corte, driver, USB, rede, DPI e permissões exigem gate físico em Windows.

## Caixa único e retomada

- Deve existir no máximo um registro `ABERTO` globalmente, independentemente do usuário. A garantia é do SQLite, por índice parcial único global, além da validação amigável do serviço.
- A migração v8→v9 executa uma pré-condição antes de qualquer `ALTER`/`CREATE`: se houver mais de um caixa aberto, aborta toda a migração, não fecha, escolhe nem apaga registros e informa os IDs para saneamento administrativo com backup.
- `BEGIN IMMEDIATE` protege abrir, movimentar e fechar. Concorrência deve produzir um sucesso e um conflito claro, nunca duas sessões ou dois fechamentos.
- Ao encontrar caixa aberto, o dono pode retomá-lo. Outro operador não abre um segundo; um administrador autenticado pode retomar o caixa existente, com evento `CAIXA_RETOMADO_ADMIN` contendo caixa, operador anterior, administrador, data/hora e motivo, sem credenciais.
- Fechar caixa mantém a conta autenticada e volta ao estado “caixa fechado”. O saldo esperado segue: abertura + suprimentos − sangrias + vendas em dinheiro confirmadas e não canceladas.
- Suprimento e sangria recebem chave idempotente estável. Repetir a mesma requisição retorna o mesmo movimento; reutilizar a chave com tipo, caixa, valor, ator ou motivo diferente produz conflito.
- O fechamento grava `backup_status=PENDENTE` na mesma transação. Depois do commit, um worker não bloqueante cria e verifica o backup, atualizando para `CONCLUIDO` ou `FALHOU`. Falha de backup não reabre nem desfaz o caixa e deve ficar visível ao administrador para nova tentativa.

## Idempotência da venda

Cada tentativa possui uma chave idempotente e um fingerprint canônico persistido. O fingerprint inclui, no mínimo:

- operador e caixa;
- itens em ordem, tipo `CATALOGO`/`MANUAL`, produto/código, unidade, quantidade, preço aplicado e preço original;
- forma de pagamento, total e valor recebido/troco quando aplicável;
- autorizador e motivo das exceções.

Retry exato devolve a venda já confirmada sem nova baixa de estoque nem novo comprovante original. A mesma chave com qualquer campo de negócio diferente retorna conflito. A validação de ator/autorização precede a resposta idempotente; a interface não pode usar retry para atravessar permissões.

## Cancelamento de venda finalizada

- Somente administrador autenticado cancela uma venda `CONFIRMADA`, informando motivo de 8–250 caracteres e chave idempotente.
- O cancelamento ocorre em uma transação: muda para `CANCELADA`, registra administrador/motivo/data, recompõe exatamente uma vez o estoque dos itens de catálogo e registra auditoria. Itens manuais nunca movimentam estoque.
- A operação é permitida enquanto o caixa de origem estiver aberto, para que o saldo esperado seja recalculado sem reescrever fechamento já consolidado. Venda de caixa fechado é recusada com orientação clara; correção contábil pós-fechamento é fluxo separado e não será improvisada nesta versão.
- Antes de confirmar, a interface informa que dinheiro precisa ser devolvido fisicamente e que PIX/cartão não recebem estorno automático. O PDV apenas cancela o registro local e recompõe estoque.
- Retry idêntico retorna o mesmo cancelamento; chave reutilizada com venda/motivo/admin diferente produz conflito.

## Outbox transacional de impressão

Quando o modo for `SELECIONADA` ou `PADRAO_WINDOWS`, a confirmação da venda e a criação do comprovante original `PENDENTE` pertencem à mesma transação. Em `DESATIVADA`, a venda preserva todos os dados necessários ao comprovante, mas não cria trabalho automático; uma segunda via pode ser solicitada depois. A outbox guarda venda, tipo (`ORIGINAL` ou `SEGUNDA_VIA`), payload canônico sem credenciais, modo/alvo da impressora no momento da solicitação, chave idempotente, tentativas, status, erro resumido e timestamps.

Estados públicos obrigatórios:

- `PENDENTE`: persistido e aguardando o worker;
- `IMPRESSO`: adaptador confirmou o envio;
- `FALHOU`: tentativa terminou com erro conhecido e permite retry explícito.

Um único worker serial processa a fila para preservar ordem e evitar concorrência no spooler. Na inicialização, ele retoma registros `PENDENTE`; `FALHOU` não entra em loop automático ilimitado. Retry administrativo recoloca a solicitação em `PENDENTE`, incrementa tentativas e audita a ação.

Falha, ausência ou remoção da impressora nunca desfaz venda confirmada. A UI mostra o resultado real assíncrono e mantém acesso a “Tentar novamente”, “Escolher impressora” e “Segunda via”. Segunda via nasce de dados persistidos, usa uma nova chave idempotente, traz marcação visível e registra quem solicitou. Escrita auxiliar em arquivo usa temporário + rename atômico e nunca anuncia sucesso quando falhar.

## Estados da impressora

A configuração persistente usa exatamente um modo explícito:

- `SELECIONADA`: envia somente ao nome salvo. Se ele não existir, mostra indisponível; não troca silenciosamente.
- `PADRAO_WINDOWS`: resolve a impressora padrão do Windows no momento do envio. Ausência de padrão é erro operacional visível.
- `DESATIVADA`: não enfileira impressão automática. Não pode ser convertida em padrão por fallback.

USB, rede e compartilhada usam a mesma lista fornecida pelo Windows. Atualizar a lista, salvar e testar ocorre fora da thread da UI. Toda operação restaura botões e texto em bloco `finally`, inclusive erro e solicitação feita enquanto outra está ocupada. O nome salvo permanece após reinício; desaparecimento apenas muda o status para indisponível.

## Comprovante térmico

- Configuração explícita de papel `58 mm` ou `80 mm`; formatação, separadores e quebra de linha derivam do perfil, não de uma constante global única.
- Itens, quantidades, preços, total, pagamento, troco, data/hora, operador e identificação do estabelecimento precisam caber sem truncar valores.
- Acentos usam a página de código suportada pelo adaptador; falha de conversão produz substituição controlada e log, não crash.
- Corte é opcional e enviado somente quando adaptador/perfil declarar suporte.
- Preview e arquivos de diagnóstico usam o mesmo formatador da impressão, com indicação do perfil escolhido.

## Interface operacional

- Diálogos limitam tamanho à área útil da tela, centralizam novamente após o ajuste e oferecem rolagem vertical/teclado/roda quando o conteúdo exceder a altura.
- Mensagens quebram linha conforme a largura real; não usam `wraplength` fixo maior que o container. Ações principais continuam alcançáveis em 640×480 e 720×520, além de 1920×1080 e DPI 125%/150% no gate manual.
- Operações assíncronas possuem estados `idle`, `busy`, `success` e `error`. Clique durante `busy` recebe retorno claro ou entra em fila; nenhum caminho deixa botão desativado permanentemente.
- Pagamento PIX exige ação textual inequívoca, por exemplo “Confirmo que identifiquei o PIX no aplicativo/banco”. Isso registra uma confirmação **manual**, sem selo de validação bancária.
- O valor atual em caixa permanece visível após abertura, suprimento, sangria, venda em dinheiro, cancelamento e fechamento, com atualização derivada do serviço, não por soma paralela na UI.

## Persistência aditiva compartilhada — schema 9

Além dos campos do checkout manual, a migração 9 inclui:

- índice parcial único global de caixa aberto;
- `movimentacoes_caixa.chave_idempotencia` e índice único parcial para valores legados nulos;
- `vendas.fingerprint_requisicao`, `cancelada_em`, `cancelada_por`, `motivo_cancelamento` e `chave_cancelamento`;
- `caixas.backup_status`, `backup_erro` e `backup_arquivo`;
- tabela `comprovantes_impressao` com estados, alvo, payload, tentativas e chaves idempotentes;
- índices de fila por `status/id` e de cancelamento por chave.

Nenhuma coluna ou histórico é removido. Backfill não fabrica idempotência para operações antigas. A migração inteira é atômica e idempotente.

## Critérios de aceite de release

- Dois usuários concorrentes não conseguem abrir dois caixas; banco migrado com duplicidade preexistente é recusado sem alteração.
- Admin retoma caixa existente com motivo e auditoria, sem criar outro; fechamento não desloga.
- Movimento e venda repetidos exatamente uma vez não duplicam dinheiro, estoque ou comprovante; payload divergente conflita.
- Com impressão ativa, venda e comprovante `PENDENTE` são atômicos; a venda sobrevive a falha de thread, spooler, USB/rede removida e reinício, a pendência volta ao worker e a segunda via é auditada. Modo desativado não cria job automático.
- Cancelamento autorizado recompõe estoque uma vez e mostra aviso explícito de ausência de estorno bancário.
- Modos selecionada/padrão/desativada persistem e não fazem fallback silencioso.
- Falha e clique concorrente na tela de impressora sempre restauram controles.
- Comprovantes de 58/80 mm passam por golden tests; impressão/corte reais continuam bloqueios físicos até evidência em Windows.
