# Checklist da instalação no caixa

## Levar

- computador Windows 10 ou 11, com usuário que possa instalar programas;
- pen drive com a pasta completa do TrigoPDV 1.2.0;
- teclado, mouse e leitor de código de barras USB configurado como teclado;
- impressora térmica já instalada no Windows, papel e cabo USB/rede;
- valores e contagem reais do estoque para revisar preços e saldos;
- um segundo pen drive ou pasta segura para testar o primeiro backup.

O TrigoPDV não exige Python, SQLite, Java, navegador ou servidor de banco. O
banco é local e é criado automaticamente em `%LOCALAPPDATA%\TrigoPDV`.

## Instalar

1. Copie a pasta completa do pen drive para o computador.
2. Execute `TrigoPDV-Setup.exe`. Se necessário, use
   `instalador\Instalar_TrigoPDV.cmd` como alternativa.
3. Abra pelo atalho. Na primeira tela, crie o administrador e guarde o código
   de recuperação fora do computador e longe do caixa.
4. Entre com o administrador e crie o usuário operador. Não compartilhe senhas.
5. Abra Configurações > Impressão, atualize a lista, escolha a fila instalada
   no Windows e faça uma impressão física de teste.
6. Revise os preços do catálogo e faça inventário antes de ativar bloqueio de
   estoque. O catálogo começa com saldo físico zero.
7. Em Configurações, confirme que `Atualização segura do sistema` mostra o
   canal piloto configurado. A verificação não deve impedir vendas sem internet.
8. Feche o caixa de treinamento antes de testar `Instalar agora`; nunca force
   uma atualização durante uma venda.

## Teste antes da primeira venda real

- abra o caixa com valor inicial conhecido;
- leia um GTIN já cadastrado e confirme nome, preço e quantidade;
- teste um item por peso e confira arredondamento e total;
- finalize uma venda de treinamento e confira comprovante e valor em caixa;
- imprima segunda via e confirme que estoque/total não mudaram;
- cancele a venda de treinamento com administrador e confirme a devolução;
- faça retirada e entrada pequenas, com justificativa, e confira o saldo;
- feche o caixa e confirme que a conta continua conectada;
- gere um backup pelo sistema, feche e abra o aplicativo novamente;
- depois dos testes, remova somente os registros de treinamento pelo fluxo
  correto do sistema. Não apague o arquivo do banco manualmente.

## Limites que precisam de validação física

- a impressão real depende do driver e da fila instalados nessa máquina;
- a consulta de GTIN novo depende de internet e da configuração válida da API;
- produtos já cadastrados, vendas e caixa continuam locais sem internet;
- atualização online segura fica desativada até o fornecedor configurar
  servidor HTTPS e assinatura. Atualize amanhã somente pelo pacote confiável.
