# Publicação segura do TrigoPDV

A versão, sequência e schema vêm de `version.toml`. Rode:

```powershell
python tools\release_gate.py
```

Esse comando valida a fonte, o lock com hashes e o catálogo sem identidade.

Uma publicação real também precisa de infraestrutura externa que não pode ser
inventada no código: certificado Authenticode com timestamp, raiz e metadados
TUF assinados por chaves mantidas fora da máquina do caixa, endpoint HTTPS e o
empacotador Velopack (`vpk`). Depois de preparar e assinar os artefatos, rode:

```powershell
$env:TRIGOPDV_UPDATE_BASE_URL = "https://seu-host-seguro/"
python tools\release_gate.py --production
```

O modo de produção falha fechado se qualquer um desses itens estiver ausente,
expirado ou sem assinatura válida. Nunca coloque chave privada, token ou senha
neste diretório. O endpoint publica somente metadata/targets TUF; dados locais,
`config.ini`, banco, backups e fila de impressão não entram no pacote.

Ordem operacional:

1. Faça a cerimônia de chaves TUF fora do repositório e copie somente a raiz
   pública para `updates/trusted/root.json`.
2. Use CPython 3.13.14+ da linha 3.13, execute `build_release.bat` e assine o
   executável/instalador com Authenticode e timestamp.
3. Gere o pacote Velopack com o pack ID `TrigoDeMinas.TrigoPDV` e mantenha
   `Releases.win.json` junto dos pacotes produzidos.
4. Rode `tools/create_update_manifest.py` para copiar os artefatos e gerar o
   manifesto de canal. Adicione todos esses arquivos como targets do TUF e
   assine timestamp/snapshot/targets com as chaves offline.
5. Publique `metadata/` e `targets/` por HTTPS e só então rode o gate de
   produção. Comece no canal `internal`, avance para `pilot` e depois `stable`.
