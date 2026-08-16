# Política de segurança

## Como relatar uma vulnerabilidade

Não publique tokens, credenciais, bancos de clientes, comprovantes ou detalhes
exploráveis em uma issue pública. Use **Security > Report a vulnerability** no
GitHub para abrir um aviso privado ao mantenedor.

Inclua somente o necessário para reproduzir o problema em dados fictícios:
versão, área afetada, impacto, passos e correção sugerida. Nunca anexe
`config.ini`, banco operacional, backup ou código de recuperação.

## Escopo atual

A versão 1.1.x é mantida. Atualização online permanece desativada por padrão e
só deve ser habilitada com repositório HTTPS, metadados TUF válidos e artefatos
assinados. Instalações piloto devem usar um pacote offline cuja origem e hashes
tenham sido verificados.

## Segredos e dados

- credenciais ficam apenas na configuração local;
- bancos, backups, filas, logs e builds não pertencem ao repositório;
- o catálogo versionado contém somente produtos, sem usuários ou histórico;
- exemplos públicos devem manter todos os campos sensíveis vazios.
