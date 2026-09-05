# Checklist do primeiro cliente — TrigoPDV 1.2.1

## Antes de sair

- [ ] Build e instaladores com ProductVersion 1.2.1.
- [x] Catálogo distribuível com 192 produtos e sem banco operacional clonado.
- [ ] Suíte completa aprovada no workflow Windows do commit de produção.
- [x] Setup e CMD exercitados em instalação isolada.
- [x] Manifesto e ZIP validados.
- [ ] Copiar para o pen drive físico e conferir o SHA-256 do ZIP.
- [ ] Levar segunda cópia confiável do pacote e destino para backup externo.

## No computador da padaria

- [ ] Confirmar Windows 10/11, espaço livre, data/hora e usuário de instalação.
- [ ] Instalar driver do leitor e da impressora antes de configurar o PDV.
- [ ] Instalar 1.2.1, criar administrador no primeiro uso e guardar o código de
  recuperação fora do computador.
- [ ] Criar usuário operador e testar bloqueios administrativos.
- [ ] Testar venda, peso, dinheiro/troco, PIX manual, segunda via, cancelamento,
  entrada/retirada, saldo, fechamento sem logout, backup e reinício.
- [ ] Fechar e reabrir o aplicativo 20 vezes na máquina real; confirmar no
  Gerenciador de Tarefas que o processo encerra e que duplo clique não abre duas
  instâncias.
- [ ] Como administrador, usar uma única vez `Caixa e manutenção` →
  `Limpar testes e iniciar produção` e guardar o backup criado.
- [ ] Depois da limpeza, revisar preços, realizar inventário real e só então
  ativar o controle de estoque onde fizer sentido.

## Bloqueios de produção online

O canal piloto usa TUF e HTTPS, mas o bootstrap 1.2.1 ainda deve vir do pacote
offline confiável porque versões anteriores não possuem a raiz pública. A
publicação stable continua bloqueada sem Authenticode e ensaio físico de
rollback. Não copiar configuração, banco ou credenciais da máquina de build.

Roteiro entregue ao usuário:
`TrigoPDV_Instalacao_PenDrive\manual-de-uso\CHECKLIST_INSTALACAO_AMANHA.md`.
