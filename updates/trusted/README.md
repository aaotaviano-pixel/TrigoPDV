# Raiz de confiança TUF

Antes de uma montagem destinada à produção, coloque aqui `root.json`, assinado
na cerimônia de chaves e sem qualquer chave privada. O arquivo é imutável no
pacote e funciona como âncora para rotação das próximas raízes.

O gate de produção recusa raiz ausente, inválida ou expirada. Nunca coloque
chaves privadas, senhas, tokens de hospedagem ou certificado de assinatura
neste diretório.

