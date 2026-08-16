# TrigoPDV — Checkout manual e leitura confiável

## Objetivo

Permitir uma venda rápida com ou sem código de barras, manter o mesmo valor em tela, pagamento e banco, e auditar exceções sem transformar o catálogo em depósito de produtos temporários.

## Decisões aprovadas

- Item avulso é uma linha de venda com `gtin = NULL`, não um produto genérico.
- O limite sem administrador é de **R$ 50,00 somados por venda**. Isso evita contorno dividindo a mesma exceção em várias linhas.
- Até R$ 50,00: o caixa lança item avulso e a auditoria registra a operação.
- Acima de R$ 50,00: exige administrador e justificativa.
- Alterar, apenas naquela venda, o preço de produto cadastrado sempre exige administrador e justificativa; o preço do catálogo não é alterado.
- Cadastro permanente e mudança do preço do catálogo continuam operações administrativas separadas.
- Valores e quantidades trafegam como `Decimal`/strings canônicas. `float` é usado somente na fronteira SQLite legada.

## Fluxo do operador

- `Enter`: enfileira o código digitado/bipado e libera imediatamente o campo para a próxima leitura.
- `F1`: pesquisa por nome, marca, GTIN ou PLU.
- `F2`: abre “Item avulso” com descrição, unidade, quantidade, preço e código informado opcional.
- `F3`: edita quantidade; para item avulso também edita descrição/preço. Em produto cadastrado, preço excepcional fica pendente de autorização no pagamento.
- `F5`: remove item com a política administrativa existente.
- `Ctrl+F11`: consulta externamente o GTIN digitado.
- `F10`: abre pagamento somente quando não há leitura pendente.

Código desconhecido preserva o carrinho e oferece tentar novamente, pesquisar, lançar item avulso ou cadastrar produto permanente quando autorizado. PLU local é resolvido antes de qualquer chamada externa.

## Modelo de domínio

Criar `services/checkout.py` com `LineKind`, `CheckoutLine`, `SaleQuote` e `quote_lines(connection, items)`. A cotação autoritativa:

- valida `UN` integral e `KG` com até três casas;
- rejeita zero, negativo, `NaN`, infinito e quantidade que arredonda para zero;
- usa `ROUND_HALF_UP` por linha;
- identifica item manual e preço excepcional;
- soma `total`, `manual_total` e razões de autorização.

`CartLine` usa `Decimal` e produz payloads com strings. A mesma `SaleQuote.total` alimenta tela, PIX, cartão, dinheiro e persistência.

## Persistência aditiva

Schema final deste pacote: versão 9. A migração é única e compartilhada com a
[especificação operacional de caixa e impressão](./2026-08-15-operacao-caixa-impressao-producao-design.md).
Além dos invariantes operacionais descritos ali, ela adiciona:

- `vendas.total_manual`;
- `vendas.autorizador_excecao_id`;
- `vendas.motivo_excecao`;
- `itens_venda.tipo_lancamento` (`CATALOGO` ou `MANUAL`);
- `itens_venda.codigo_informado`;
- `itens_venda.preco_original`;
- índice por tipo de lançamento/venda.

Linhas antigas recebem `CATALOGO`, total manual antigo é zero, e nenhuma tabela/coluna existente é removida. Item manual não baixa estoque e não cria produto.

A mesma migração persiste um fingerprint canônico completo da requisição de venda.
A chave idempotente só reproduz uma resposta anterior quando operador, caixa,
itens, preços, quantidades, pagamento, valor recebido e autorização forem
idênticos; qualquer divergência produz conflito. A pré-condição de caixa único
global deve rodar antes da primeira alteração de schema e abortar sem escrever
quando encontrar duplicidade preexistente.

## Autorização e auditoria

Criar `AuthorizationRequiredError` tipado; a interface não analisará texto de exceção. `PDVService` valida login/senha administrativa e passa apenas o ID do autorizador ao serviço transacional.

Dentro da mesma transação da venda:

- `VENDA_CONFIRMADA` registra totais e quantidade de itens manuais;
- `ITEM_MANUAL_VENDIDO` registra item, quantidade, preço e subtotal;
- `PRECO_EXCEPCIONAL_APLICADO` registra preço original/aplicado;
- `EXCECAO_VENDA_AUTORIZADA` registra operador, administrador e justificativa;
- senhas nunca entram no banco, log ou exceção.

## Scanner concorrente

Criar `ui/scan_queue.py` com fila serial e geração. Uma leitura ativa por vez; próximas leituras aguardam em ordem. Finalizar venda, logout ou destruir a tela invalida resultados antigos. Erro no primeiro item não impede o segundo. Pagamento não abre enquanto a fila possuir item ativo ou pendente.

## Critérios de aceite

- `12,50 × 0,250` aparece, cobra e grava `3,13`.
- Duas leituras rápidas entram em ordem e nenhuma cruza para a próxima venda.
- Venda mista GTIN, PLU, pesquisa, KG e manual finaliza atomicamente.
- R$ 50,00 manuais passam sem senha; R$ 50,01 e duas linhas de R$ 30,00 exigem administrador.
- Preço excepcional de cadastrado exige administrador, preserva preço original e não muda catálogo.
- Código desconhecido nunca apaga carrinho nem mostra erro técnico.
- Retry idempotente com o mesmo payload não duplica estoque ou comprovante; a
  mesma chave com qualquer campo de negócio alterado é recusada.
- Com impressão ativa, a confirmação da venda cria o comprovante original
  `PENDENTE` na mesma transação, conforme a especificação operacional; modo
  desativado não cria job automático, e falha de impressão nunca apaga nem
  reverte a venda.
- PIX é confirmação manual inequívoca do operador. Esta versão não consulta
  banco/adquirente e não apresenta a operação como conciliada automaticamente.
