# Manual de uso — TrigoPDV 1.2.1

**Padaria Trigo de Minas**
**Revisado em 05/09/2026**

Este manual foi feito para o único caixa da padaria. O sistema vende produtos
já cadastrados mesmo sem internet. Use o usuário de cada pessoa; não compartilhe
senhas.

## 1. Instalação pelo pen drive — responsável

1. No Windows 10 ou 11, instale primeiro o leitor e a impressora térmica.
2. Copie a pasta completa do pen drive para o computador.
3. Execute `instalador\Instalar_TrigoPDV.cmd` e aguarde o atalho **TrigoPDV**.
4. Na primeira abertura, crie a conta administrativa e guarde o código de
   recuperação fora do computador e longe do caixa.
5. Crie uma conta individual para cada operador em **Administração → Usuários**.

Não existe senha padrão. O programa fica em
`%LOCALAPPDATA%\TrigoDeMinas.TrigoPDV.V2`; banco, configuração e backups ficam
em `%LOCALAPPDATA%\TrigoPDV`. Nunca apague essa pasta para corrigir um erro.

## 2. Preparar antes da primeira venda real — responsável

Faça os testes antes do inventário definitivo:

- selecione a impressora em **Administração → Impressora** e imprima um teste;
- leia um produto comum e um produto por peso;
- abra um caixa de treinamento, faça uma venda curta em dinheiro e confira o
  troco e o comprovante;
- faça uma entrada e uma retirada pequenas;
- teste PIX e cartão, lembrando que a confirmação é feita pelo operador;
- cancele a venda de teste com um administrador e feche o caixa;
- feche e abra o aplicativo para confirmar que não ficou nenhum processo preso.

Depois dos testes, entre como administrador e abra **Administração → Caixa e
manutenção**. Clique uma única vez em **Limpar testes e iniciar produção**,
digite `INICIAR PRODUCAO` e confirme. O sistema:

- cria e verifica um backup antes da limpeza;
- remove vendas, caixas, movimentações, comprovantes e auditoria de treinamento;
- mantém produtos, preços, configurações e usuários;
- deixa todo estoque em zero e sem bloqueio;
- impede uma segunda limpeza para proteger vendas reais.

Somente depois dessa limpeza, revise preços, faça o inventário real e ative o
controle de estoque dos produtos que devem bloquear venda sem saldo.

## 3. Abrir o caixa — operador

1. Ligue computador, leitor e impressora.
2. Abra o TrigoPDV e entre com seu próprio usuário.
3. Clique em **Abrir caixa**.
4. Conte o dinheiro físico da gaveta e informe esse valor como fundo inicial.
5. Confira se a tela mostra **Caixa aberto**.

Existe somente um caixa financeiro aberto por vez. Se outro turno estiver
aberto, chame o administrador para retomar ou fechar corretamente.

## 4. Fazer uma venda

1. Leia o código de barras. Também é possível pesquisar o produto.
2. Confira nome, quantidade, preço e subtotal na tela.
3. Em produtos por peso, informe quilogramas com até três casas decimais.
4. Escolha **Dinheiro**, **PIX** ou **Cartão**.
5. Em dinheiro, informe o valor recebido e confira o troco.
6. Em PIX ou cartão, confira a aprovação no banco ou na maquininha antes de
   concluir no TrigoPDV.
7. Entregue o comprovante. Se não imprimir, use **Segunda via**; não repita a
   venda.

### Produto não encontrado

- Com internet, aguarde a consulta por GTIN.
- Se nome e marca forem encontrados, informe o preço correto antes de cadastrar.
- Sem internet ou sem resultado, use o cadastro manual autorizado.
- Produtos já cadastrados continuam disponíveis sem internet.

### Cancelar uma venda

Use **Cancelar última venda**, informe o motivo e peça autorização do
administrador. O estoque local é recomposto uma única vez. O cancelamento no
TrigoPDV **não estorna PIX ou cartão**: o responsável deve fazer o estorno no
banco ou na adquirente e guardar o comprovante.

## 5. Entradas, retiradas e fechamento

- Registre toda entrada ou retirada de dinheiro com uma observação clara.
- Não retire dinheiro sem lançar a movimentação.
- No fim do turno, conte a gaveta sem consultar o valor esperado e informe
  somente o valor físico.
- Se houver diferença, confira novamente e escreva uma justificativa objetiva.
- Fechar o caixa encerra o turno financeiro, mas mantém o usuário conectado.
- Ao terminar, clique em **Sair** para encerrar a conta do operador.

O fechamento inicia um backup automático. Se aparecer falha, o administrador
deve corrigir o destino e repetir o backup antes de desligar o computador.

## 6. Impressora e leitor

O administrador escolhe a fila instalada em **Administração → Impressora**. Use
sempre **Testar impressão** depois de trocar cabo, driver, porta ou impressora.

Se faltar papel ou a impressão falhar, a venda continua registrada. Reponha o
papel, confira a fila do Windows e use **Segunda via**. Nunca registre outra
venda apenas para obter novo comprovante.

O leitor deve funcionar como teclado e enviar Enter depois do código. Se houver
leituras duplicadas, confira a configuração do leitor e não finalize até revisar
as quantidades.

## 7. Atualização segura

O canal inicial é **piloto**. O aplicativo verifica atualizações em segundo plano
e as vendas locais continuam funcionando sem internet. Quando aparecer
**Pronta para instalar**:

1. termine a venda atual;
2. feche o caixa corretamente;
3. entre como administrador;
4. abra **Administração → Visão geral → Atualização segura do sistema**;
5. clique em **Instalar agora**.

Antes de atualizar, o próprio PDV cria e valida um backup. Não desligue o
computador durante a aplicação. O instalador ainda pode exibir aviso do Windows
enquanto não houver assinatura Authenticode empresarial.

## 8. Senha, backup e solução rápida

- Guarde o código de recuperação administrativa fora do computador.
- Depois de cinco tentativas administrativas erradas, use a recuperação. O
  código antigo é substituído ao concluir.
- Faça backup diário para outro disco, pen drive guardado ou pasta segura
  sincronizada; backup no mesmo computador não protege contra perda da máquina.
- Se o sistema avisar que já está aberto, procure a janela existente e aguarde
  alguns segundos. Se não houver janela, encerre apenas `TrigoPDV.exe` no
  Gerenciador de Tarefas e abra novamente.
- Nunca apague o banco, `config.ini` ou arquivo de bloqueio para resolver erros.
- Ao pedir suporte, informe horário, tela, mensagem e o que estava sendo feito;
  não envie senha, código de recuperação nem banco sem autorização do dono.

## 9. Checklist diário curto

### Abrir

- [ ] leitor e impressora ligados;
- [ ] usuário individual conectado;
- [ ] dinheiro da gaveta contado;
- [ ] caixa aberto com fundo correto.

### Fechar

- [ ] entradas e retiradas registradas;
- [ ] gaveta contada;
- [ ] diferença conferida e justificada;
- [ ] caixa fechado;
- [ ] backup concluído;
- [ ] usuário desconectado.

## 10. Limites conhecidos

- PIX e cartão são confirmados e estornados fora do TrigoPDV.
- Consulta de GTIN novo depende da internet e pode não encontrar todo produto.
- A impressora depende do driver e da fila configurados no Windows.
- O canal `stable` permanece bloqueado até assinatura Authenticode com timestamp
  e ensaio de rollback na máquina física.
