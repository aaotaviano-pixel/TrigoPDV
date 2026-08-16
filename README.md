# PDV Trigo de Minas

Sistema de ponto de venda local para padaria, pensado para operação rápida de caixa no Windows. O projeto usa Python, Tkinter/ttk, SQLite e funciona sem rede para vender produtos que já estejam no cadastro local.

## Início rápido no Windows

Abra o PowerShell e execute:

```powershell
cd C:\TRIGOPDV
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python init_db.py
python main.py
```

Também é possível abrir `run_pdv.bat` depois de instalar as dependências.

No primeiro início são criadas a configuração `config.ini` e uma base operacional nova no computador. O catálogo inicial contém somente produtos; a tela de configuração inicial permite que o responsável crie a própria conta administrativa, senha e recuperação. Não existe credencial padrão no código ou no pen drive.

## Configuração

Edite `config.ini` antes de operar em produção. Os pontos mais importantes são:

- `[store]`: nome, documento e cabeçalho do comprovante;
- `[pix]`: chave PIX, nome do recebedor e cidade;
- `[paths] backup_path`: pasta local ou pasta sincronizada com nuvem para backups;
- `[printing]`: fila local e compatibilidade com impressora térmica. A seleção
  normalmente é feita na aba **Administração → Impressora**, que consulta o
  spooler do Windows e grava o nome escolhido no `config.ini` da máquina.

Exemplo de impressora Windows instalada:

```ini
[printing]
enabled = true
driver = win32raw
printer_name = NOME SALVO PELA TELA DE IMPRESSORA
host =
printer_port = 9100
queue_dir = data/print_queue
cut_paper = true
```

Exemplo de impressora de rede ESC/POS:

```ini
[printing]
enabled = true
driver = network
printer_name =
host = 192.168.0.50
printer_port = 9100
queue_dir = data/print_queue
```

Para testar uma fila IPP direta, como uma impressora virtual na rede, use
somente no `config.ini` local:

```ini
[printing]
enabled = true
driver = ipp
printer_name = virtual
uri = http://ENDERECO_DA_IMPRESSORA:PORTA/p/virtual
cut_paper = true
queue_dir = data/print_queue
```

O modo IPP envia o comprovante como `application/octet-stream` e confirma o
status do job no protocolo. Para impressoras de produção instaladas no
Windows, prefira a seleção automática da aba Administração → Impressora;
nesse caso o driver fica `win32raw` e o Windows administra USB/rede/fila.

Com `enabled = false`, a venda continua normal e o comprovante é salvo em `data\print_queue` para conferência/reimpressão. Uma falha de impressora nunca desfaz uma venda confirmada.

Na aba **Administração → Impressora**, use **Atualizar impressoras** para
detectar USB, rede e compartilhadas, selecione uma opção, clique em **Salvar
seleção** e faça **Testar impressão**. O padrão do Windows é mostrado apenas
como referência/fallback; uma seleção específica do PDV tem prioridade. Se a
impressora for removida, a tela informa “Não encontrada” e permite atualizar ou
trocar sem editar código. O corte de papel só é enviado quando `cut_paper =
true` (desative-o para modelos sem cortador).

## Operação do caixa

1. Entre com seu usuário e abra o caixa, informando o fundo inicial.
2. Bipe ou digite o GTIN. A busca é local primeiro; depois consulta o cache de resultados e só então Open Food Facts/Cosmos, com timeout. O caixa continua vendendo offline.
3. Produto sem preço abre a precificação rápida. Para um usuário caixa, preço/cadastro novo exige credencial de administrador e fica auditado.
4. Use os itens rápidos de balcão para produtos por unidade ou por kg. Produtos em kg pedem o peso antes de entrar no carrinho.
5. Finalize com dinheiro, PIX ou cartão. Dinheiro calcula troco em tempo real; PIX oferece QR Code e código copia-e-cola; cartão exige confirmação da maquininha.
6. Em **Caixa → Operações**, use **Adicionar dinheiro**, **Retirar dinheiro** ou inicie o fechamento de caixa.

Atalhos disponíveis durante a venda:

| Tecla | Ação |
| --- | --- |
| `F1` | Buscar produto por nome, marca ou código |
| `F5` | Cancelar item selecionado (autorização/auditoria) |
| `F10` | Abrir pagamento |
| `Esc` | Cancelar modal ou voltar o foco ao GTIN |

O campo de GTIN volta ao foco após os fluxos de venda.

## Perfis e controles

- **Caixa:** venda, próprio caixa, adição e retirada de dinheiro e fechamento de caixa. O operador não recebe o valor esperado durante a conferência.
- **Admin:** cadastro/ajuste de produto e estoque, itens de balcão, validade, relatórios, fechamentos, logs de auditoria, backup e manutenção.
- Senhas usam `bcrypt` (com fallback seguro `scrypt` somente se a dependência ainda não estiver instalada).
- A camada de serviços valida o perfil; não é apenas uma restrição visual.
- Venda, itens, baixa de estoque e auditoria são gravados na mesma transação SQLite.

No fechamento de caixa, o operador informa apenas o dinheiro físico contado. A aplicação calcula internamente a diferença e exige justificativa quando ela for diferente de zero. O valor esperado só fica disponível ao administrador no histórico, salvo configuração administrativa explícita.

## Administração

O painel administrativo oferece:

- cadastro de produtos, preço, estoque, validade, unidade `UN`/`KG`, item rápido de balcão e estoque controlado;
- categoria/subcategoria e embalagem/peso/volume para manter o catálogo organizado;
- códigos industriais ficam como `GTIN`; produtos feitos na padaria usam `PLU-...` e aparecem como código interno, nunca como EAN;
- alertas visuais de produtos próximos do vencimento;
- visão do dia, relatórios por período, totais por forma de pagamento e produtos mais vendidos;
- histórico de fechamentos e logs de auditoria;
- **Compactar banco** e **Reorganizar índices**, com backup prévio. Esses nomes simples correspondem às rotinas técnicas VACUUM e REINDEX.

O estoque controlado não pode ficar negativo. Produtos que já têm histórico de venda são inativados em vez de apagados, preservando o histórico do cupom.

Na aba **Usuários** do painel de administração, o administrador cria contas de perfil `caixa` ou `admin`. A senha inicial exige no mínimo oito caracteres, é armazenada somente como hash e deve ser trocada no primeiro login da nova conta.

Um produto incluído pelo cadastro rápido ou pelo Open Food Facts nasce sem controle de estoque, porque esse fluxo só conhece identificação e preço — não o saldo físico da loja. O administrador pode ativar o controle e informar o saldo no editor assim que o produto for conferido.

O cadastro guarda o estado do código separadamente: **válido estruturalmente** significa que o dígito verificador foi conferido; **confirmado** significa que uma fonte cadastrou aquele GTIN junto do produto; **pendente** significa que ainda precisa de revisão. Um retorno 429/sem internet não transforma um código em produto confirmado.

## Backup e banco

O backup é disparado após um fechamento de caixa confirmado e usa a API de backup do SQLite, segura mesmo quando o banco opera em modo WAL. Os arquivos vão para `backup_path` e passam por verificação de integridade.

Não copie apenas o arquivo `.sqlite3` enquanto o sistema estiver aberto; use o backup do sistema. Antes de **Compactar banco** ou **Reorganizar índices**, confirme que não há vendas em andamento.

## Testes

Para validar o núcleo do sistema:

```powershell
cd C:\TRIGOPDV
python -m unittest discover -s tests -v
python -m compileall -q .
```

Os testes cobrem GTIN/cache (inclusive cache negativo), PLU interno, venda com baixa transacional, idempotência contra duplo clique, fechamento cego, PIX, backup, fila de impressão, descoberta do spooler Windows, persistência da seleção, impressora removida/indisponível e impressão fora da thread da interface.

## Pacote para instalação em outra máquina

Na máquina de montagem, execute `build_release.bat` para gerar `dist\TrigoPDV\TrigoPDV.exe`, um aplicativo standalone que não depende de Python na máquina do caixa. O build usa versões e hashes fixos e exige CPython 3.13.14 ou superior da linha 3.13, que possui instalador oficial para Windows. Para o pen drive, `TrigoPDV_Instalacao_PenDrive\instalador\Instalar_TrigoPDV.cmd` copia o app e o catálogo somente de produtos; o banco operacional é criado localmente e nunca é sobrescrito. O instalador gráfico exige Inno Setup 6. Uma publicação online real também precisa passar `python tools\release_gate.py --production`.

O aplicativo instalado cria `config.ini`, banco, backups e fila de impressão em `%LOCALAPPDATA%\TrigoPDV`, não em `Program Files`. Isso mantém os dados graváveis sem pedir permissão de administrador e evita que uma desinstalação comum apague a operação. Defina `TRIGOPDV_DATA_DIR` somente se quiser usar outra pasta de dados local.

## Estrutura

```text
C:\TRIGOPDV
├── config/          leitura e validação de configuração
├── db/              SQLite, schema e migrações
├── integrations/    Open Food Facts
├── services/        autenticação, produtos, venda, caixa, PIX, backup e auditoria
├── printing/        comprovante e adaptadores ESC/POS/Windows
├── ui/              interface nativa, modais e painel administrativo
├── tests/           testes automatizados
├── desktop_controller.py
├── init_db.py
└── main.py
```

## Decisões técnicas e complementos

A interface usa Tkinter/ttk nativo em vez de uma janela HTML: para um PDV Windows local isso reduz dependências, elimina a necessidade de runtime de navegador e mantém o leitor de código de barras/atalhos responsivos mesmo offline. A camada visual foi estilizada para manter contraste e operação de teclado.

Além do schema mínimo, foram incluídos snapshot de nome/unidade nos itens vendidos, log de auditoria, idempotência de confirmação de venda, produto inativo em vez de exclusão histórica, estoque controlado, itens rápidos, migração de schema e fila local de comprovantes. Esses acréscimos existem para preservar histórico, evitar duplicidade e tornar a operação recuperável em caso de falha de rede, impressora ou clique repetido.
