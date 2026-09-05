# QA manual Windows — TrigoPDV 1.2.1

Registrar máquina, versão do Windows, resolução/DPI, impressora/driver, largura
do papel, leitor e resultado de cada cenário sem copiar dados pessoais.

## Matriz mínima

| Área | Cenários |
|---|---|
| Tela | resolução real; DPI 100%, 125% e 150%; rolagem e teclado |
| Leitor | GTIN conhecido, desconhecido, repetido rápido, Enter extra |
| Caixa | abrir, retomar, entrada, retirada, fechar, reabrir |
| Venda | unidade, KG, preço excepcional, R$ 50 de itens manuais, troco |
| Segurança | troca obrigatória, cinco erros, recuperação, código rotacionado |
| Impressão | USB/rede, padrão/específica, 58/80 mm, acento, falta de papel |
| Falhas | impressora removida, spooler parado, internet fora, duplo clique |
| Persistência | reinício preserva usuário, preço, caixa, impressora e backup |
| Preparação | backup, limpeza única dos testes, estoque zerado, segunda tentativa bloqueada |
| Encerramento | fechar/reabrir 20 vezes; processo encerra; segunda instância é recusada |

## Critérios

- Uma falha de impressão não desfaz nem duplica venda.
- Segunda via não altera estoque ou caixa.
- PIX/cartão não são confirmados nem estornados pelo banco no TrigoPDV.
- Consulta de GTIN externa fora do ar não bloqueia catálogo/venda local.
- Nenhuma ação fecha ou substitui banco existente sem backup verificável.
- A limpeza de treinamento mantém produtos, preços e usuários, exige caixa
  fechado e não pode ser repetida depois da abertura comercial.
- Qualquer erro crítico impede abertura comercial até correção ou rollback para
  a cópia offline íntegra.
