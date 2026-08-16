# TrigoPDV — relatório de validação pré-produção

**Data:** 07/08/2026  
**Escopo:** auditoria do aplicativo desktop, banco SQLite, catálogo/GTIN, caixa, usuários, impressão e empacotamento para pendrive.

## Resultado executivo

O código foi auditado e os ajustes de baixo risco foram aplicados. A suíte automatizada ficou com **28 testes passando**, a aplicação compilada abriu em modo isolado e o executável atualizado foi sincronizado para a pasta do pendrive. O banco de dados de distribuição contém **150 produtos ativos**, sem vendas, itens de venda, caixas, movimentações ou logs operacionais de desenvolvimento.

O projeto não possui repositório Git; portanto não foi possível criar uma branch ou registrar commit. O rollback está garantido pelo backup datado informado abaixo.

## Backup e rollback

Backup criado antes das alterações:

`C:\TRIGOPDV\backups\release_validacao_final_20260807_000202`

O backup contém o snapshot do código e cópias dos bancos de desenvolvimento, instalado e pendrive. Para rollback, pare o aplicativo e restaure os arquivos correspondentes a partir dessa pasta, preservando primeiro uma cópia do estado atual.

## Auditoria realizada

### Inventário técnico

- **Projeto:** TrigoPDV, `C:\TRIGOPDV`.
- **Runtime:** Python 3.12.10, Tkinter/ttk e executável Windows gerado com PyInstaller 6.10.
- **Persistência:** SQLite local, cliente próprio em `db/`, schema versionado (versão 3) e migrações aditivas.
- **Integrações:** Open Food Facts e Cosmos (credenciais somente por configuração local; nenhum segredo foi incluído neste relatório).
- **Impressão:** `pywin32`/fila raw do Windows ou `python-escpos` para rede.
- **Sistema auditado:** Windows 10/11 compatível; máquina de validação Windows 10 build 19045.
- **Dependências:** `bcrypt`, `requests`, `qrcode[pil]`, `python-escpos`, `pywin32` e PyInstaller para build.
- **Diretórios e entradas analisados:** `config/`, `db/`, `integrations/`, `printing/`, `services/`, `ui/`, `tests/`, `main.py`, `desktop_controller.py`, `init_db.py`, `config.ini`, `config.ini.example`, `README.md`, `build_release.bat`, `TrigoPDV.spec` e `TrigoPDV_Instalacao_PenDrive/`.
- **Tabelas verificadas:** `produtos`, `usuarios`, `caixas`, `vendas`, `itens_venda`, `movimentacoes_caixa`, `logs_auditoria` e `schema_meta`.

- Entrada da aplicação, configuração e seleção do diretório de dados.
- Camadas `services`, `db`, `integrations`, `printing`, `ui`, testes e controlador desktop.
- Fluxos de venda, consulta/cadastro de produto, abertura/fechamento e movimentação de caixa, usuários e recuperação de senha.
- Banco de origem, banco instalado e banco da distribuição no pendrive.
- Script de build e executável gerado.
- Impressoras Windows instaladas e possibilidade de teste físico.

## Correções aplicadas

### GTIN e busca de produto

- Validação do dígito verificador para GTIN-8, GTIN-12, GTIN-13 e GTIN-14.
- Mensagem clara para código inválido.
- Busca priorizada por GTIN exato, prefixo, nome e marca.
- Atalho e fluxo de consulta automática existentes foram preservados.

O cálculo segue a estrutura/dígito verificador definida nas especificações da GS1: [GS1 General Specifications](https://ref.gs1.org/standards/genspecs/15.0.0/).

### Caixa

- Rótulos de operação convertidos para linguagem de balcão: **Adicionar dinheiro** e **Retirar dinheiro**.
- Inclusão de motivo rápido e observação opcional na movimentação, sem migração destrutiva.
- Fechamento mostra conferência (esperado, contado e falta/sobra) quando autorizado.
- O valor esperado permanece oculto do operador por padrão; administradores podem visualizar, ou a exceção pode ser habilitada em `[cash] show_expected_to_operator = true`.
- Mensagens longas do fechamento e de justificativas passaram a quebrar linha corretamente.

### Usuários e interface

- Callbacks pendentes de diálogos são cancelados no fechamento, eliminando avisos `bgerror` observados nos testes de interface.
- Tamanho mínimo da janela reduzido para `900x600`, mantendo uso em estações Windows compactas.
- A aba administrativa ganhou visualização da configuração de impressão e botão **Imprimir teste**.

### Impressão

- Teste administrativo não cria venda nem altera estoque.
- O método utiliza o mesmo caminho de impressão de recibo da venda, permitindo preview quando a impressão está desabilitada.
- A enumeração de impressoras é compatível com a administração do Windows; o cmdlet [Get-Printer](https://learn.microsoft.com/en-us/powershell/module/printmanagement/get-printer?view=windowsserver2025-ps) lista as impressoras instaladas. O envio raw utiliza o spooler (`OpenPrinter`/`StartDocPrinter`), que é síncrono/bloqueante conforme [Microsoft StartDocPrinter](https://learn.microsoft.com/pt-br/windows/win32/printdocs/startdocprinter).

## Testes executados

| Verificação | Resultado |
|---|---|
| `python -m unittest discover -s tests -v` | 28 testes, OK |
| Testes de interface Tk | 7 testes, OK, sem `bgerror` |
| `python -m compileall -q` | OK |
| GTIN válido e inválido | OK |
| Busca por GTIN/nome priorizada | OK |
| Abertura/fechamento com falta/sobra | OK |
| Visibilidade do esperado por configuração | OK |
| Cadastro/fluxo de usuário | OK nos testes existentes |
| Teste de impressora sem venda | OK (preview/retorno controlado) |
| Smoke test do executável congelado | Processo abriu e permaneceu ativo; encerrado intencionalmente |

O projeto não possui linter ou verificador de tipos configurado; não foi inventado um resultado para essas ferramentas. Também não foi possível produzir uma comparação estatística de performance antes/depois, pois não havia uma medição histórica versionada. A busca foi exercitada no catálogo de distribuição (150 itens) e a suíte de regressão permaneceu verde.

## Banco e distribuição

Auditoria da base de distribuição:

- 150 produtos ativos.
- 0 vendas.
- 0 itens de venda.
- 0 caixas operacionais.
- 0 movimentações operacionais.
- Integridade SQLite: `ok`.
- GTINs do catálogo com dígito verificador válido.
- Nenhum token Cosmos não vazio foi encontrado nos arquivos de configuração empacotados.

Observação importante: a base de desenvolvimento em `C:\TRIGOPDV\data\trigo_de_minas.sqlite3` está separada e possui 0 produtos, 1 caixa e movimentação de testes. Ela não é a base distribuída. A base instalada e a base do pendrive são as que foram conferidas para a entrega e possuem o catálogo de 150 produtos sem histórico operacional.

Executável gerado:

`C:\TRIGOPDV\dist\TrigoPDV\TrigoPDV.exe`

Executável sincronizado para:

`C:\TRIGOPDV\TrigoPDV_Instalacao_PenDrive\instalador\app\TrigoPDV.exe`

SHA-256 dos dois executáveis: `945E618DFA31A23AF8B81ADF44DB43345422AC94DC1B695A85FD8488C600E1FF`.

O instalador Inno Setup não foi gerado porque o compilador `ISCC.exe` não está instalado nesta máquina. A árvore `dist\TrigoPDV` está pronta para ser usada pelo script do instalador assim que o Inno Setup for instalado.

## Limitações e pendências reais

- Não há repositório Git nesta cópia; branch/commit não podem ser criados sem inicializar ou fornecer um repositório.
- Não há impressora térmica física instalada (somente impressoras virtuais do Windows); o teste ESC/POS físico precisa ser feito no computador da padaria.
- O aplicativo atual é desktop Tkinter; não foi prometido suporte a telas menores que `900x600`.
- A arquitetura atual não contém módulos completos de clientes, fornecedores, compras, despesas, produção, ordem de serviço e contas a receber equivalentes a todos os ícones da imagem de referência. Esses itens devem ser tratados como evolução separada, não como telas fictícias.
- Não foi criado fluxo novo de estorno/devolução de venda; o cálculo do caixa cobre o modelo operacional existente (abertura + adições − retiradas + vendas confirmadas).
- O instalador final exige executar o Inno Setup em uma máquina que possua `ISCC.exe`.
