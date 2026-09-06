# Checklist do primeiro cliente — TrigoPDV 1.2.1

## Antes de sair

- [x] Metadados e instaladores exigem ProductVersion 1.2.1.
- [x] Catálogo distribuível com 192 produtos e sem banco operacional clonado.
- [ ] Suíte completa aprovada no workflow Windows do commit de produção.
- [x] Setup e CMD exercitados em instalação isolada.
- [x] Manifesto e ZIP validados.
- [ ] Copiar para o pen drive físico; o instalador confere o SHA-256 de todos os
  arquivos automaticamente.
- [ ] Levar segunda cópia confiável do pacote e destino para backup externo.

## No computador da padaria

- [ ] Confirmar Windows 10/11, espaço livre, data/hora e usuário de instalação.
- [ ] Instalar driver do leitor e da impressora antes de configurar o PDV.
- [ ] Instalar 1.2.1, criar administrador no primeiro uso e guardar o código de
  recuperação fora do computador.
- [ ] Criar usuário operador e testar bloqueios administrativos.
- [ ] Fazer o teste físico mínimo descrito em `docs/QA_WINDOWS.md`: leitor,
  papel, corte, PIX, maquininha, backup externo e um ciclo de encerramento.
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
