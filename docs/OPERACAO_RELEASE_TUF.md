# Operação de atualização online — TrigoPDV

## Estado da arquitetura

- Bootstrap: TrigoPDV 1.2.1, sequência 4, schema 9.
- Canal inicial: `pilot`, destinado ao único caixa da padaria.
- Repositório: GitHub Pages em
  `https://aaotaviano-pixel.github.io/TrigoPDV/updates/`.
- Autenticidade: TUF com raiz offline 2-de-3 e chaves online separadas para
  targets, snapshot e timestamp.
- Empacotamento/aplicação: Velopack 1.2.0, pacote completo sem delta.
- Raiz do programa: `%LOCALAPPDATA%\TrigoDeMinas.TrigoPDV.V2`, distinta da
  raiz Inno/CMD 1.1 e da raiz de dados `%LOCALAPPDATA%\TrigoPDV`.
- Política offline-first: falha de internet nunca bloqueia login ou venda.

O primeiro 1.2.1 precisa ser instalado pelo pacote confiável do pen drive. Uma
versão anterior não conhece a raiz TUF pública e, por construção, não pode
aceitar uma atualização online.

## Fluxo no computador da padaria

1. O app abre normalmente e inicia a verificação em segundo plano.
2. No máximo a cada seis horas, o cliente consulta os metadados HTTPS.
3. Manifesto, tamanho e SHA-256 de cada arquivo são aceitos somente após a
   cadeia TUF ser validada.
4. O pacote completo é baixado para `%LOCALAPPDATA%\TrigoPDV\updates`.
5. O administrador vê `Pronta para instalar` em Configurações.
6. `Instalar agora` só funciona com caixa financeiro fechado.
7. Antes de chamar o Velopack, o PDV cria e valida o backup SQLite.
8. O app reinicia, confere versão, sequência, schema e integridade do banco.

O operador não precisa de navegador, Python, Git ou conta do GitHub.

## Publicar uma nova versão piloto

1. Altere somente `release/version.toml`, aumentando a versão e a sequência.
2. Execute `python tools/render_release_metadata.py`.
3. Rode testes, `compileall`, auditoria de dependências e o release gate.
4. Faça merge na branch protegida `main`.
5. No GitHub, execute o workflow **Publish authenticated update** com canal
   `pilot`, rollout inicialmente pequeno e `mandatory=false`. A execução
   agendada renova a validade dos metadados sem reutilizar versões TUF.
6. O workflow recompila do zero, valida a fonte, cria o pacote Velopack, assina
   TUF, verifica com o cliente real e só então publica o site.
7. O workflow autentica e preserva todos os canais já publicados, incrementa
   os contadores TUF e monta também o pacote USB com manifesto SHA-256.
8. Confirme o deployment do GitHub Pages e faça o ensaio em uma cópia do banco
   antes de instalar no caixa.

Nunca coloque chaves privadas, senha da cerimônia, banco, `config.ini` ou dados
do cliente no repositório ou no site. As três chaves online ficam somente nos
GitHub Actions Secrets. As chaves de raiz ficam cifradas em duas mídias sob
custódia do responsável e não participam da publicação cotidiana.

## Falha e recuperação

- Sem internet/timeout: continuar vendendo; o monitor tenta novamente depois.
- Metadado expirado, assinatura/hash incorreto ou replay: não instalar e manter
  a versão local.
- Download interrompido: não aplicar arquivo parcial.
- Caixa aberto: fechar corretamente e repetir `Instalar agora`.
- Falha de saúde após reinício: bloquear nova aplicação e preservar backup e
  diagnóstico redigido para análise.
- Perda de uma chave online: substituir o secret e publicar metadados
  autorizados; não expor as chaves offline.
- Comprometimento/rotação da raiz: executar cerimônia offline com o limiar
  exigido e testar o encadeamento de roots antes de publicar.

## Limites deliberados

O canal `stable` continua bloqueado enquanto executável e Setup não tiverem
assinatura Authenticode válida com timestamp e enquanto o rollback não tiver
sido ensaiado em uma VM Windows e na máquina física. O piloto é autenticado por
TUF, mas não transforma um binário sem Authenticode em release stable.
