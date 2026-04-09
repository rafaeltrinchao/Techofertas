# Query Original

**Pergunta:** Encontrar a forma mais viável de buscar produtos e preços da Shopee Brasil (shopee.com.br) para integrar no TechOfertas — app Python desktop que já scrapa KaBuM, Pichau, Terabyte, Mercado Livre, Magalu e Amazon.

## Contexto da Aplicação

- **Stack atual:** Python + urllib + cloudscraper + requests
- **Bundle:** Executável .exe (PyInstaller)
- **Método de busca existente:** keyword → lista de produtos com nome, preço, imagem, URL
- **Gold standard de performance:** KaBuM (1 request, JSON direto via `__NEXT_DATA__`)

## Ângulos de Pesquisa

1. API pública ou mobile API sem autenticação
2. Endpoints internos (GraphQL, REST, JSON)
3. Bibliotecas Python existentes
4. Proteções anti-bot da Shopee
5. Projetos GitHub funcionando em 2024-2025
6. Affiliate API / Partner API oficial
7. Comparativo de abordagens por viabilidade

## Contexto Inferido

- Focus: técnico
- Temporal: recente (2024-2025)
- Domain: Python, scraping, REST API, anti-bot
