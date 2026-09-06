# Checklist da instalação no caixa

## Levar

- computador Windows 10 ou 11, com usuário que possa instalar programas;
- pen drive com a pasta completa do TrigoPDV 1.2.1;
- teclado, mouse e leitor de código de barras USB configurado como teclado;
- impressora térmica já instalada no Windows, papel e cabo USB/rede;
- valores e contagem reais do estoque para revisar preços e saldos;
- um segundo pen drive ou pasta segura para testar o primeiro backup.

O TrigoPDV não exige Python, SQLite, Java, navegador ou servidor de banco. O
banco é local e é criado automaticamente em `%LOCALAPPDATA%\TrigoPDV`.

## Instalar

1. Copie a pasta completa do pen drive para o computador.
2. Execute `instalador\Instalar_TrigoPDV.cmd`. Ele confere automaticamente
   todos os arquivos, preserva a pasta de dados, migra uma instalação antiga e
   valida a estrutura do atualizador.
3. Abra pelo atalho. Na primeira tela, crie o administrador e guarde o código
   de recuperação fora do computador e longe do caixa.
4. Entre com o administrador e crie o usuário operador. Não compartilhe senhas.
5. Abra Configurações > Impressão, atualize a lista, escolha a fila instalada
   no Windows e faça uma impressão física de teste.
6. Em `Administração → Visão geral`, confirme que `Atualização segura do sistema`
   mostra o canal piloto configurado. A verificação não deve impedir vendas sem
   internet.
7. Feche o caixa de treinamento antes de testar `Instalar agora`; nunca force
   uma atualização durante uma venda.

## Testes que precisam da máquina e dos equipamentos reais

Os cálculos, permissões, banco, backup, limpeza de treinamento, duplicidade de
venda, atualização autenticada e falhas simuladas já são validados pela suíte
automática. No caixa da padaria, teste somente o que depende do ambiente físico:

- leia um GTIN com o leitor USB e confirme que o Enter e a quantidade chegam
  uma única vez;
- faça uma venda curta com dinheiro, um item por peso e a impressora térmica;
- confira acentos, largura e corte no papel e simule falta de papel antes de
  usar **Segunda via**;
- confirme um PIX no banco e um cartão na maquininha de treinamento;
- gere um backup em outro disco ou pen drive e confirme que o arquivo aparece;
- feche e abra o aplicativo uma vez e confirme no Gerenciador de Tarefas que
  `TrigoPDV.exe` encerrou;
- entre como administrador, use uma única vez `Caixa e manutenção` →
  `Limpar testes e iniciar produção` e guarde o backup informado;
- revise os preços, faça o inventário real e só então ative o controle de
  estoque dos produtos que devem bloquear venda sem saldo.

## Limites físicos e externos

- impressão, leitor, PIX, cartão e destino externo de backup dependem dos
  equipamentos e contas configurados nessa máquina;
- a consulta de GTIN novo depende da internet e das credenciais locais da API;
- sem certificado Authenticode, o piloto pode gerar aviso do Windows e o canal
  `stable` permanece bloqueado;
- produtos cadastrados, vendas e caixa continuam locais sem internet.
