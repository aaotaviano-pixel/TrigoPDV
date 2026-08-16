# TrigoPDV — relatório final para testes em produção

**Data:** 09/08/2026  
**Cenário:** uma estação de caixa em padaria, Windows, banco SQLite local.

## Resultado executivo

O aplicativo que está no pendrive foi atualizado e recompilado após a última
alteração visual. O executável do build e o executável distribuído são iguais,
com SHA-256:

`87CDC3D08EAB388A32FED3D5EB9ADA0362DF30E5099681CDD1C4A2D1DF391FC9`

O pacote inclui manual atualizado, instalador por `cmd` e banco-base sem
histórico operacional. A impressão térmica física ainda precisa ser conferida
na máquina da padaria, pois não há impressora térmica instalada neste ambiente.

## Conteúdo do banco-base do pendrive

- 150 produtos ativos e precificados;
- todos os GTINs passam na validação do dígito verificador;
- 1 conta administrativa técnica;
- 0 vendas;
- 0 itens de venda;
- 0 caixas abertos ou fechados;
- 0 movimentações de caixa;
- 0 logs de auditoria de operação;
- `PRAGMA integrity_check`: `ok`;
- saldo de estoque inicial zero e controle de estoque desligado até o inventário.

Os preços são referências. O administrador deve revisar preço de venda,
unidade, validade e estoque físico antes do atendimento real.

## Validações executadas

| Verificação | Resultado |
|---|---|
| Suíte automatizada | 28 testes, OK |
| Compilação de código-fonte | OK |
| Smoke test do executável recompilado | Processo abriu e permaneceu ativo; encerrado intencionalmente |
| Instalador `Instalar_TrigoPDV.cmd` em pasta isolada | Código 0 |
| Banco preparado pelo instalador isolado | 150 produtos, integridade `ok` |
| Consulta de GTIN e busca local | OK nos testes automatizados |
| Fechamento cego e justificativa | OK nos testes automatizados |
| Cadastro de usuário e hash | OK nos testes automatizados |
| Mensagens longas e rolagem | OK no teste de acessibilidade |
| Impressora virtual | Não usada como validação térmica; teste administrativo de fila OK |

## Melhorias aplicadas

- Manual de operação separado por perfil, com roteiro para o dia de produção.
- Orientação explícita de que o pen drive instala o programa; os dados de
  operação ficam em `%LOCALAPPDATA%\\TrigoPDV`.
- Documentação sem senha inicial, token ou chave de integração.
- Consulta de GTIN local primeiro, Ctrl+F11 e cadastro manual para itens sem
  retorno externo.
- Itens de produção própria podem usar código interno e não dependem de API.
- Estado do caixa passou a usar verde para aberto e âmbar para fechado.
- Instalador deixou de copiar uma pasta aninhada antiga do aplicativo em novas
  instalações.

## Cuidados obrigatórios antes de vender

1. Trocar a credencial administrativa inicial e criar contas individuais.
2. Conferir preços e cadastrar o estoque real dos produtos vendidos.
3. Configurar nome da loja, PIX, backup e impressora na máquina do caixa.
4. Imprimir um teste físico e fazer uma venda curta em Dinheiro, PIX e Cartão.
5. Testar uma sangria, uma justificativa de diferença e o fechamento do caixa.
6. Confirmar que o backup foi criado antes de encerrar o treinamento.
7. Não apagar o banco nem trabalhar diretamente a partir do pendrive.

## Pendências externas

- Teste físico da impressora térmica, dependente do equipamento e do driver da
  padaria.
- Instalador gráfico Inno Setup, opcional; o instalador por `cmd` já está pronto.
- Não há branch Git porque esta cópia de `C:\TRIGOPDV` não é um repositório.

Backup criado antes das alterações:

`C:\TRIGOPDV\backups\pre_producao_manual_20260809_200948`

## Atualização posterior — fechamento e saldo do caixa

- O fechamento agora exibe “Caixa fechado com sucesso” e mantém a conta
  conectada na tela do PDV; somente o turno financeiro é encerrado.
- Administradores visualizam “Saldo atual” no topo enquanto o caixa está
  aberto. Para o perfil caixa, o fechamento cego continua ocultando o esperado
  por padrão; a visualização só aparece se habilitada administrativamente.
- Os botões técnicos foram traduzidos para **Compactar banco** e
  **Reorganizar índices**, com explicação antes da confirmação.
- A suíte final passou com **31 testes**, e o executável foi recompilado e
  sincronizado no pendrive. SHA-256 final:
  `808A202A32D0F96EB47147473719E8646CBD5ACFA0E3290A04B8E7D84D7236B8`.
- Backup adicional antes desta alteração:
  `C:\TRIGOPDV\backups\pre_cash_ui_20260809_204915`.

## Auditoria completa do catálogo e pacote — 2026-08-10

- Backup criado antes da migração e da atualização do catálogo:
  `C:\TRIGOPDV\backups\pre_auditoria_completa_20260810_220423`.
- O schema foi migrado de forma aditiva para a versão 4: categoria,
  subcategoria, embalagem/peso/volume, tipo de código, estado de validação e
  cache persistente de GTIN. Nenhuma venda ou relacionamento existente foi
  apagado.
- O banco-base do pen drive passou de 150 para **192 produtos ativos**:
  **167 GTINs** e **25 PLUs internos** de produção própria. Todos estão
  precificados, com estoque inicial zero e controle de estoque desligado.
- Os 150 GTINs legados foram verificados individualmente quanto a formato e
  dígito verificador: **150 válidos estruturalmente**. Isso não foi promovido a
  confirmação de produto/embalagem; a base legada ainda tem 150 marcas vazias,
  12 grupos de nomes duplicados e precisa de conferência da embalagem física.
- Foram adicionados **17 GTINs industriais** confirmados em páginas públicas do
  Cosmos e **25 PLUs** sem aparência de EAN. A curadoria não incluiu o código
  informado para Coca-Cola sem confirmação; a fonte pública mostra outros
  códigos para as embalagens correspondentes.
- A API Cosmos respondeu HTTP 429 durante a tentativa de revalidar os 150
  códigos, compatível com o limite diário do plano básico. O sistema registra o
  estado pendente e não troca código por palpite. Relatório linha a linha:
  `C:\TRIGOPDV\RELATORIO_AUDITORIA_CATALOGO_2026-08-10.md`.
- A busca agora segue catálogo local → cache positivo/negativo → Open Food
  Facts/Cosmos, com timeout. Resultados encontrados são persistidos; códigos
  não encontrados ficam em cache temporário; falha de rede não bloqueia a
  próxima tentativa.
- A interface ganhou campos de categoria/embalagem, tabelas com rolagem
  horizontal quando necessário e abertura centralizada adaptada ao monitor.
- Suíte automatizada: **34 testes, OK**. Compilação: OK. Smoke do executável:
  processo permaneceu ativo. Instalação isolada: código 0, 192 produtos,
  integridade `ok`, 0 vendas, 0 caixas, 0 movimentações e 0 logs.
- Executável onedir sincronizado em `dist` e no pendrive; SHA-256:
  `6FBEDE8D272068B3E105B42532A086AE2ABAA551C561DDE8B9A939F30E4D11CA`.
- O manual foi reexportado com o catálogo de 192 itens, PLU, cache e estados
  de validação. A renderização visual do DOCX continua pendente porque
  LibreOffice não está instalado nesta máquina; a inspeção estrutural do XML
  do documento passou.

## Auditoria completa das impressoras — 2026-08-10

- Causa raiz confirmada: a tela anterior apenas exibia `printer_name` fixo do
  `config.ini`; não consultava o spooler do Windows, não permitia escolher ou
  persistir a fila pela interface e o teste era síncrono.
- Criado `printing/discovery.py`: enumeração via `win32print.EnumPrinters`
  (impressoras locais/USB, conexões de rede e compartilhadas), fallback
  PowerShell `Get-Printer`, indicação da padrão do Windows e status amigável.
- A aba **Administração → Impressora** agora tem Atualizar impressoras,
  combobox com nomes reais, Salvar seleção, Testar impressão e Desativar
  impressão. A escolha é gravada atomically no `config.ini` local, sem
  substituir credenciais ou outras seções, e continua após reiniciar.
- Impressão RAW/ESC-POS foi preservada e centralizada. `cut_paper` permite
  enviar corte somente quando o modelo suporta; USB, rede e compartilhadas
  instaladas no Windows usam o mesmo caminho. O modo ESC/POS direto por IP
  continua compatível para instalações legadas.
- Testes de teste e descoberta executam fora da thread Tk. Após a venda
  confirmada, o envio ativo ao spooler ocorre em worker daemon; falhas mantêm
  a venda e o comprovante na fila local. Mensagens cobrem impressora removida,
  offline, driver ausente, timeout e falha do spooler.
- Auditoria de pontos de impressão: havia somente finalização de venda e o
  teste administrativo; ambos passaram a usar o mesmo `ReceiptPrinter`. Não
  existem rotinas separadas de impressão para relatório, fechamento, pedido
  ou comanda no código atual.
- Nesta máquina a enumeração real encontrou Microsoft Print to PDF, Fax,
  Microsoft XPS Document Writer e OneNote for Windows 10; a padrão foi
  identificada como Microsoft Print to PDF. O teste físico com a térmica da
  padaria ainda depende do equipamento.
- Backup antes do empacotamento: `C:\TRIGOPDV\backups\pre_impressora_20260810_230109`.
- Suíte final: **41 testes, OK**; compilação OK. Build onedir e pacote do
  pendrive sincronizados, com SHA-256 do executável:
  `22B72AC06FD68AB28D2FF05220A895B7FFA3822FC19E3B02859F6D608CB0C4DB`.
- Instalação isolada do pendrive: código 0; executável iniciou e respondeu;
  banco íntegro com 192 produtos, 0 vendas e 0 caixas. Manual DOCX e instruções
  de configuração da impressora também foram atualizados no pacote.

## Validação da impressora virtual IPP — 2026-08-10

- O endereço informado foi consultado diretamente por IPP. A impressora
  respondeu HTTP 200/IPP 0, identificou-se como `virtual`/`A402D IppVirtual`,
  estava em estado ocioso, aceitando trabalhos e com fila inicial vazia.
- O envio real de um `Print-Job` retornou HTTP 200/IPP 0; o trabalho de teste
  recebeu `job-id` 7 e estado `processing` na fila virtual.
- O `ReceiptPrinter` do TrigoPDV também enviou um comprovante de teste real
  para o mesmo endereço e retornou `printed=True`, com a mensagem
  “Comprovante enviado para a impressora.”
- Foi adicionado o adaptador `printing/ipp.py`, com timeout, validação do
  protocolo, mensagens amigáveis e suporte a `driver=ipp`/`uri=`. O caminho
  normal de impressoras USB/rede instaladas no Windows continua usando o
  spooler RAW/ESC-POS e não foi substituído.
- A versão v6 foi recompilada incluindo explicitamente o módulo IPP e
  sincronizada para `dist\TrigoPDV` e para
  `TrigoPDV_Instalacao_PenDrive\instalador\app`. SHA-256:
  `8835E2A7B043DF5ED037592F4DEAE5CFD5F7CBFC142DB810D134AA213296CA10`.
- Smoke do instalador v6 em pasta isolada: código 0, executável respondeu,
  SQLite `integrity_check=ok`, 192 produtos, 0 vendas e 0 caixas.
- A suíte completa passou com **44 testes**, e `compileall` passou. A
  impressora virtual valida transporte IPP e aceitação do trabalho, mas não
  substitui o teste físico de papel, alinhamento, acentos, corte e resposta de
  uma térmica ESC/POS real; esse item continua pendente na padaria.
