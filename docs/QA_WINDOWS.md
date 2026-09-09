# QA físico Windows — TrigoPDV 1.2.1

O workflow Windows valida automaticamente regras de venda, caixa, estoque,
permissões, backup, migrações, impressão simulada, atualização TUF, instância
única e integridade do pacote. O instalador também confere o SHA-256 de todos os
arquivos antes de abrir o Setup.

Registrar somente máquina, versão do Windows, resolução/DPI, impressora/driver,
largura do papel, leitor e resultado dos testes físicos, sem copiar dados
pessoais.

## Matriz física mínima

| Área | Cenários |
|---|---|
| Tela | resolução e DPI usados no caixa; botões, rolagem e teclado visíveis |
| Leitor | leitura única com Enter; leitura rápida sem quantidade duplicada |
| Impressão | fila real; 58/80 mm; acentos; corte; falta de papel; segunda via |
| Pagamentos | confirmação real no banco PIX e na maquininha de treinamento |
| Backup | gravação em outro disco, pen drive ou pasta segura configurada |
| Encerramento | fechar uma vez; processo encerra; nova abertura funciona |
| Preparação | executar a limpeza única e guardar o backup antes do inventário |

## Critérios

- O papel sai legível, com largura e corte corretos.
- Falta de papel não leva o operador a repetir a venda.
- Leitor e teclado não duplicam item nem escondem controles da tela.
- PIX e cartão são conferidos no serviço externo antes da confirmação no PDV.
- O backup externo é criado e o processo encerra ao fechar a janela.
- Qualquer falha física impede a abertura comercial até correção ou uso da
  cópia offline íntegra.
