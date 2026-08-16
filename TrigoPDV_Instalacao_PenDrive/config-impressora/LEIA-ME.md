# Configuração da impressora térmica

O TrigoPDV lista automaticamente as impressoras instaladas no Windows na aba **Administração → Impressora**. USB, rede e impressoras compartilhadas aparecem juntas; não é necessário editar o nome no código.

## Impressora instalada por USB/Windows

1. Conecte a impressora e instale o driver oficial do fabricante.
2. Abra **Administração → Impressora** e clique em **Atualizar impressoras**.
3. Escolha o nome exibido pelo Windows, clique em **Salvar seleção** e depois em **Testar impressão**.
4. A escolha fica gravada em `%LOCALAPPDATA%\TrigoPDV\config.ini` e continua após reiniciar o programa.

```ini
[printing]
enabled = true
driver = win32raw
printer_name = NOME EXATO MOSTRADO NO WINDOWS
host =
printer_port = 9100
queue_dir = data/print_queue
cut_paper = true
```

5. Feche e abra novamente o TrigoPDV; realize uma venda de treino para conferir o cupom.

## Impressora de rede ESC/POS

Use somente se a impressora tiver um IP já definido pelo roteador/rede local. Não configure o GTIN aqui - essa configuração é exclusiva de impressão.

```ini
[printing]
enabled = true
driver = network
printer_name =
host = 192.168.0.50
printer_port = 9100
queue_dir = data/print_queue
```

Esse modo antigo continua disponível somente para uma impressora ESC/POS ligada diretamente por IP. Para uma impressora adicionada ao Windows, prefira a lista automática da aba Administração. Se a impressão falhar, a venda continua salva e o comprovante fica na fila indicada por `queue_dir`.

## Impressora virtual IPP para teste

Se o responsável fornecer uma URL IPP (por exemplo, uma impressora virtual de
teste), ela pode ser usada somente no `config.ini` local:

```ini
[printing]
enabled = true
driver = ipp
printer_name = virtual
uri = http://ENDERECO:PORTA/p/virtual
queue_dir = data/print_queue
```

O teste confirma a resposta do protocolo IPP e o identificador do job. Para o
uso da padaria, mantenha a seleção automática do Windows e valide a térmica
real na aba Administração → Impressora.

## Se não imprimir

- Verifique papel, energia, cabo e se a impressora aparece no Windows.
- Confirme que o nome ou IP foi preenchido sem espaços extras.
- Deixe `enabled = false` até a instalação física estar pronta; os comprovantes serão guardados em `data\print_queue`.
- Não refaça uma venda apenas porque o comprovante não saiu.
- Desative `cut_paper` para impressoras que não suportam corte automático.
