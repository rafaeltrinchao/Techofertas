# TechOfertas — Documentação de Scraping por Loja

Referência técnica de como cada loja é acessada na aplicação, por que cada abordagem funciona,
e o que foi pesquisado/testado para lojas pendentes. Use como base para futuras integrações.

---

## Lojas Ativas

### KaBuM
- **Método:** API JSON interna (REST)
- **Endpoint:** `https://www.kabum.com.br/api/catalogsearch/result?query={termo}&sort=price_asc&pageSize=50&currentPage=1`
- **Headers necessários:** User-Agent Chrome padrão
- **Proteção:** Nenhuma relevante — API acessível diretamente
- **Por que funciona:** KaBuM expõe uma API paginada sem autenticação. Retorna JSON limpo com `data.catalogProducts` contendo nome, preço, imagem e URL.
- **Parcelamento:** Campo `installment` presente no JSON (ex: `"12x de R$ 50,00 sem juros"`)
- **Velocidade:** ~1–2s por busca
- **Notas:** Loja mais estável e rápida. Serve como referência de implementação para as demais.

---

### Pichau
- **Método:** API GraphQL
- **Endpoint:** `https://www.pichau.com.br/api/catalog` (POST com query GraphQL)
- **Headers necessários:** `Content-Type: application/json`, User-Agent Chrome
- **Proteção:** Sem proteção significativa na API GraphQL
- **Por que funciona:** Pichau usa GraphQL e o endpoint é acessível sem autenticação. A query solicita `products(search: "{termo}", sort: {price: ASC}, pageSize: 30)` retornando `items` com nome, preço, imagem e URL.
- **Parcelamento:** Disponível no retorno GraphQL via `price_installments`
- **Velocidade:** ~1–2s
- **Notas:** Loja estável. GraphQL garante resposta estruturada.

---

### Terabyte
- **Método:** API REST interna
- **Endpoint:** `https://www.terabyteshop.com.br/api/v1/search?term={termo}&order=priceasc&limit=30`
- **Headers necessários:** User-Agent Chrome padrão
- **Proteção:** Nenhuma relevante
- **Por que funciona:** API REST pública não autenticada. Retorna JSON com lista de produtos, preço à vista e parcelado.
- **Parcelamento:** Campo específico de parcelamento no JSON
- **Velocidade:** ~1–2s
- **Notas:** API bem documentada e estável.

---

### Mercado Livre
- **Método:** API oficial pública
- **Endpoint:** `https://api.mercadolibre.com/sites/MLB/search?q={termo}&sort=price_asc&limit=30`
- **Headers necessários:** Nenhum especial (API pública)
- **Proteção:** Rate limiting leve (sem bloqueio em uso normal)
- **Por que funciona:** ML disponibiliza API pública de busca sem autenticação para o marketplace. Retorna JSON rico com `results[]` contendo título, preço, link, thumbnail e vendedor.
- **Parcelamento:** Campo `installments` no JSON (parcelas, valor, taxa de juros)
- **Velocidade:** ~1–2s
- **Notas:** API mais robusta e documentada de todas as lojas. Altamente estável.

---

### Magazine Luiza (Magalu)
- **Método:** API interna (capturada via DevTools)
- **Endpoint:** `https://www.magazineluiza.com.br/busca/{termo}/?sort=price_asc` + parsing de `__NEXT_DATA__`
- **Headers necessários:** User-Agent Chrome, Accept-Language pt-BR
- **Proteção:** Cloudflare leve — resolvido com headers adequados e cloudscraper
- **Por que funciona:** Magalu injeta todos os produtos na página via `<script id="__NEXT_DATA__">` (Next.js SSR). O JSON em `props.pageProps.search.products` contém nome, preço, imagem e URL sem necessidade de JavaScript.
- **Parcelamento:** Disponível no `__NEXT_DATA__` via `installment` nos produtos
- **Velocidade:** ~2–4s (parsing de HTML mais lento que APIs JSON)
- **Notas:** Funciona com `urllib` + parsing de regex no `__NEXT_DATA__`. Sem necessidade de Selenium.

---

### Amazon
- **Método:** Parsing de HTML com User-Agent Chrome
- **Endpoint:** `https://www.amazon.com.br/s?k={termo}&s=price-asc-rank`
- **Headers necessários:** User-Agent Chrome completo, Accept-Language pt-BR, cookies básicos
- **Proteção:** Bot detection moderado — rotação de User-Agent + headers ajuda
- **Por que funciona:** A Amazon renderiza HTML server-side com produtos visíveis. Parsing via regex/BeautifulSoup nos `div[data-component-type="s-search-result"]`. Requer headers convincentes.
- **Parcelamento:** Não extraído (formato variável e inconsistente no HTML)
- **Velocidade:** ~3–5s
- **Notas:** Mais frágil das lojas ativas — estrutura HTML muda ocasionalmente. Usar `cloudscraper` ajuda na estabilidade.

---

### Shopee
- **Método:** Googlebot SSR bypass + JSON-LD schema.org
- **Endpoint busca:** `https://shopee.com.br/search?keyword={termo}&sortBy=price&order=asc`
- **Endpoint produto:** URL individual de cada produto
- **Headers necessários:** `User-Agent: Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)`
- **Proteção:** Cloudflare + proteção anti-bot robusta **para navegadores normais**. Googlebot recebe SSR completo.
- **Por que funciona:**
  - A Shopee serve HTML server-rendered (SSR) completo para Googlebot/crawlers SEO — sem bot protection
  - A página de busca retorna um `<script type="application/ld+json">` com `@type: ItemList` contendo nome, URL e imagem de cada produto
  - Cada página de produto retorna `@type: Product` com `offers.price` (preço real)
  - Busca: 1 request → lista de URLs de produtos
  - Preços: N requests paralelos para cada página de produto → preços via JSON-LD
- **Parcelamento:** Apenas disponível quando usuário está logado — não implementado
- **Velocidade:** ~5–10s (N requests paralelos de produto + 4 sorts em paralelo para ~30 produtos)
- **Implementação:**
  - 4 sorts em paralelo: `price`, `pop`, `ctime`, `sales` → ~30 produtos únicos (deduplicados por URL)
  - ThreadPoolExecutor para requests paralelos de produto
  - Fallback: se JSON-LD não encontra preço, tenta regex no HTML

---

## Casas Bahia

- **Método:** 2 APIs internas (REST) — sem Akamai no subdomínio de backend
- **Endpoint busca:** `https://api-partner-prd.casasbahia.com.br/api/v3/web/busca?Terms={termo}&Page=1&PageSize=20`
- **Endpoint preços:** `https://api.casasbahia.com.br/merchandising/oferta/v1/Preco/Produto/PrecoVenda/?idsProduto={ids}&composicao=DescontoFormaPagamento,MelhoresParcelamentos`
- **Headers necessários:** User-Agent Chrome + `apiKey: d081fef8c2c44645bb082712ed32a047`
- **Proteção:** Akamai está no `www.casasbahia.com.br` (domínio principal). Os subdomínios `api-partner-prd.casasbahia.com.br` e `api.casasbahia.com.br` não têm Akamai — curl_cffi com impersonação Chrome basta.
- **Por que funciona:** A chave `apiKey` e os endpoints internos estão expostos no `__NEXT_DATA__` da página de busca (campo `PRICE_API_KEY` e `SEARCH_API_V3`). A busca retorna produtos + IDs; os preços são buscados em lote numa segunda request.
- **Dados retornados:** Nome, preço à vista, preço PIX (quando há desconto), parcelamento (ex: "10x de R$892,00"), disponibilidade, URL, imagem
- **Velocidade:** ~0.5–1s (2 requests paralelas possíveis)
- **Como foi descoberto:** Análise do `__NEXT_DATA__` do HTML retornado via curl_cffi → bloco de config expõe todas as URLs e a chave de API → inspeção dos chunks JS Next.js confirma parâmetros corretos (`idsProduto`, `composicao`, header `apiKey`)

---

## Casas Bahia — Histórico de Pesquisa e Testes (Arquivado)

> **Status:** INTEGRADA em 2026-04-10. Seção abaixo mantida como referência histórica.

### Proteção da Casas Bahia

Casas Bahia usa **duas camadas** de proteção:

1. **Cloudflare** (camada externa) — resolve User-Agent e headers básicos
2. **Akamai Bot Manager** (camada interna) — proteção sofisticada com:
   - Cookies dinâmicos: `_abck`, `ak_bmsc`, `bm_sz`, `bm_ss`, `ak_geo`
   - TLS/JA3/JA4 fingerprinting
   - HTTP/2 fingerprinting
   - Behavioral analysis (tempo de resposta, padrões de request)

A proteção é em **dois níveis diferentes**:
- Páginas de busca: Akamai permissivo (aceita TLS fingerprint correto)
- Páginas de produto e APIs: Akamai estrito (exige cookies válidos gerados por browser real)

### O Que Foi Testado

#### 1. `requests` / `urllib` direto — FALHOU
- Bloqueado imediatamente com 403
- Akamai detecta TLS handshake de Python (`SSLv3/TLS_CLIENT_HELLO` não imita browser)

#### 2. `cloudscraper` — FALHOU
- Resolve Cloudflare mas não Akamai
- Ainda retorna 403 nas APIs da CB

#### 3. Headers estáticos copiados do browser — FALHOU
- Cookies Akamai expiram em minutos e são criptografados dinamicamente
- Copiar `_abck` e `ak_bmsc` de uma sessão real não funciona após ~2 min

#### 4. Googlebot SSR bypass (como na Shopee) — FALHOU PARCIALMENTE
- CB **não serve SSR completo para Googlebot** como a Shopee
- Retorna HTML mas **sem dados de preço** — preços carregados via JavaScript após render
- A busca retorna estrutura HTML vazia de produtos (sem JSON-LD com preços)

#### 5. `curl-cffi` com impersonação de browser — FUNCIONA PARCIALMENTE ⭐
- Biblioteca Python que usa libcurl com TLS/JA3/JA4 idêntico ao Chrome
- **Páginas de busca:** FUNCIONA — retorna HTML com lista de produtos (sem preço)
- **Páginas de produto:** FALHA com 403 — Akamai bloqueia com cookies inválidos
- **APIs VTEX:** FALHA — todas interceptadas e retornam home page HTML
- Versões testadas: `chrome110`, `chrome120`, `chrome124`, `safari17_0` (funcionam para busca)
- Versão `edge101` causa crash: `curl: (16) nghttp2 recv error`
- **Conclusão:** curl-cffi bypassou o fingerprint TLS, mas não gera os cookies Akamai dinâmicos necessários para APIs de preço

#### 6. VTEX APIs (plataforma da CB) — FALHOU
- CB roda VTEX (mesma plataforma da Dafiti, Reserva, etc.)
- APIs padrão VTEX testadas:
  - `/api/catalog_system/pub/products/search?fq=ft:{termo}`
  - `/api/intelligent-search/product_search/...`
  - Checkout simulation: `/api/checkout/pub/orderForms/simulation`
- Todas retornam home page HTML com `Content-Type: text/html` — interceptadas pelo Akamai

#### 7. Zoom.com.br como intermediário — FUNCIONA mas rejeitado
- Zoom.com.br agrega produtos de várias lojas incluindo CB
- Retorna `__NEXT_DATA__` com `initialReduxState.hits.hits` sem proteção anti-bot
- Filtrando por `bestOffer.merchantName == 'Casas Bahia'` → ~8-15 produtos por busca
- **Motivo da rejeição:** Usuário não quer dependência de site de terceiros

#### 8. Mobile API (Frida/JADX) — Não testado
- Risco de detecção elevado e complexidade inviável para distribuição em .exe

### Abordagens Promissoras para Continuação

#### Opção A — `curl-cffi` + session warming (MAIS PROMISSORA)
**Como funciona:**
1. Fazer request à home page da CB com curl-cffi (chrome124) → obtém cookies iniciais
2. Fazer request à página de busca → Akamai atualiza cookies
3. Usar cookies aquecidos para acessar página de produto → pode desbloquear

**Status:** Testado parcialmente. O session warming com 2-3 requests ajudou a reduzir bloqueios em produtos,
mas ainda não totalmente funcional. Vale investigar mais com:
- Delays realistas entre requests (500ms–2s)
- Ordem de headers idêntica ao Chrome (usando `curl_cffi.requests.Session`)
- Incluir Referer correto em cada request

**Código base:**
```python
from curl_cffi import requests as cffi_requests

session = cffi_requests.Session(impersonate="chrome124")
# Warm up
session.get("https://www.casasbahia.com.br/", timeout=15)
session.get("https://www.casasbahia.com.br/busca?strBusca=rtx+4060", timeout=15)
# Tentar produto
resp = session.get(product_url, timeout=15)
```

#### Opção B — Playwright Stealth (MAIS CONFIÁVEL, mas pesado)
- Browser Chromium headless real → Akamai não consegue distinguir de usuário real
- `playwright-stealth` para ocultar sinais de automação (`navigator.webdriver`, etc.)
- Interceptação de network response: capturar JSON da API quando a página carrega, sem parsear HTML
- **Trade-off:** +150–300MB no .exe bundlado
- **Velocidade:** 5–15s por busca
- **Implementação recomendada:**
  ```python
  from playwright.sync_api import sync_playwright
  # Interceptar resposta de API antes de renderizar a página inteira
  # Timeout de 15s com fallback
  ```

#### Opção C — nodriver (alternativa mais leve ao Playwright)
- `nodriver` (successor do `undetected-chromedriver`) — menor footprint
- Mantém instância do Chrome reutilizável entre buscas
- Menos overhead de inicialização que Playwright

### URL Correta de Busca
- **Correto:** `https://www.casasbahia.com.br/busca?strBusca=rtx+4060`
- **Errado:** `https://www.casasbahia.com.br/busca/rtx-4060` (retorna produtos errados — ex: "Carburador Fusca")

### Estrutura dos Dados (quando acessível)
Os produtos na CB têm preços carregados via JavaScript pelo endpoint VTEX. O HTML de busca contém apenas
estrutura de card sem valores numéricos. O JSON-LD presente é mínimo (sem `offers.price`).

---

## Resumo Comparativo

| Loja | Método | Proteção | Velocidade | Estabilidade |
|------|--------|----------|------------|--------------|
| KaBuM | API REST direta | Nenhuma | ~1s | Alta |
| Pichau | API GraphQL | Mínima | ~1s | Alta |
| Terabyte | API REST | Nenhuma | ~1s | Alta |
| Mercado Livre | API oficial | Rate limit leve | ~1s | Alta |
| Magalu | `__NEXT_DATA__` SSR | Cloudflare leve | ~3s | Média-Alta |
| Amazon | HTML parsing | Bot detection moderado | ~4s | Média |
| Shopee | Googlebot SSR + JSON-LD | Anti-bot (bypassado via Googlebot) | ~7s | Média |
| Casas Bahia | 2 APIs internas (curl_cffi) | Akamai no www (subdomínios sem proteção) | ~0.5–1s | Alta |

---

## Decisões de Design

- **Parcelamento:** Apenas KaBuM, Pichau, Terabyte e ML têm parcelamento extraído. Amazon e Shopee não (formato inconsistente / requer login).
- **Deduplicação Shopee:** 4 sorts paralelos (`price`, `pop`, `ctime`, `sales`) → deduplicação por URL → ~30 produtos únicos.
- **Timeout padrão:** 20s por loja, com ThreadPoolExecutor para paralelização.
- **`nome_compativel_com_busca()`:** Função de validação que filtra produtos que não correspondem à busca (evita falsos positivos como "Carburador Fusca" ao buscar "rtx 4060").

---

*Última atualização: 2026-04-10*
