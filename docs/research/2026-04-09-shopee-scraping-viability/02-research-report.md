# Relatório de Pesquisa — Shopee Brasil Scraping

**Data:** 2026-04-09 | **Workers:** 5 paralelos | **Fontes:** 14 únicas

---

## 1. Endpoint Principal Identificado

O endpoint de busca da Shopee Brasil é:

```
GET https://shopee.com.br/api/v4/search/search_items
```

### Parâmetros da query:

| Parâmetro | Valores | Descrição |
|-----------|---------|-----------|
| `keyword` | string | Termo de busca |
| `limit` | 10/20/30/40 | Resultados por página |
| `newest` | 0 | Offset de paginação |
| `by` | `relevancy`, `sales`, `price`, `ctime` | Ordenação |
| `order` | `asc`, `desc` | Direção |
| `page_type` | `search` | Tipo de página |
| `scenario` | `PAGE_OTHERS` | Cenário |
| `version` | `2` | Versão da API |

**Exemplo de URL completa:**
```
https://shopee.com.br/api/v4/search/search_items?by=sales&keyword=rtx+4060&limit=30&newest=0&order=desc&page_type=search&scenario=PAGE_OTHERS&version=2
```

---

## 2. Proteção Anti-Bot — O Grande Obstáculo

A Shopee opera **dois sistemas simultâneos** de proteção:

### Camada 1: Cloudflare (dificuldade 8/10)
- Validação de IP, TLS fingerprint, JS challenges

### Camada 2: Akamai Bot Manager (dificuldade 9/10)
- **Mais agressivo que Cloudflare**
- Análise comportamental em tempo real
- Machine learning que se adapta continuamente
- Fingerprinting de hardware, browser, plugins, timezone, tela

### Headers dinâmicos obrigatórios:

| Header | Descrição |
|--------|-----------|
| `af-ac-enc-dat` | Autenticação de dispositivo criptografada |
| `af-ac-enc-sz-token` | Token de tamanho criptografado |
| `x-csrftoken` | Token CSRF |
| `x-sap-access-f` | Fingerprint SAP |
| `x-sap-access-s` | Assinatura de sessão SAP |
| `x-sap-access-t` | Token temporal SAP |
| `x-sap-ri` | Hash de integridade da request |
| `x-sz-sdk-version` | Versão do SDK |

> **Problema crítico:** Esses headers são gerados por **algoritmos criptográficos em JavaScript** no lado do cliente. Não é possível replicá-los estaticamente — eles incorporam fingerprint do dispositivo, estado da sessão, timing da request e assinaturas criptográficas.

### Resultado prático:
- `requests` / `urllib` → **bloqueado imediatamente** (403 ou resultado vazio)
- `cloudscraper` → **parcialmente eficaz** apenas contra Cloudflare, **não** contra Akamai
- Qualquer chamada estática é detectada em segundos

---

## 3. Proteções da API Mobile

O app Shopee usa o **mesmo endpoint** `/api/v4/search/search_items` mas com:
- Cookie de autenticação: **`SPC_ST`** (obtido após login)
- Menor friction de segurança que a versão web
- Para interceptar: requer JADX (decompilação APK) + Frida (bypass SSL pinning) + Burp Suite
- Performance documentada: **1000+ requests/hora, 98% sucesso, <0.05% detecção** — mas requer Android emulator

> **Para app desktop .exe: inviável.** Absurdamente complexo de manter e distribuir.

---

## 4. Bibliotecas Python e Projetos GitHub

### Ativos em 2024-2025:

| Projeto | Stars | Último commit | Método | Busca por keyword |
|---------|-------|---------------|--------|-------------------|
| `dtungpka/shopee-scraper` | 19 | **Dez 2024** | Selenium | ✅ `-k 'termo'` |
| `paulodarosa/shopee-scraper` | 46 | Jan 2022 | API pública | ✅ Sim |
| `duyet/pricetrack` | 136 | Ativo | Firebase+React | Monitoramento |
| `SuspiciousLookingOwl/shopee-api` | N/D | N/D | Node.js wrapper | ✅ Sim |

### Conclusões sobre projetos existentes:
- Todos os projetos que "funcionam" usam Selenium ou navegador real
- Projetos API-based (`paulodarosa`) podem estar desatualizados (2022)
- `dtungpka` é o mais recente e ativo, mas **exige login manual** para resolver CAPTCHA inicial
- Nenhuma biblioteca Python pura (sem browser) funciona de forma confiável

---

## 5. Affiliate API Oficial — Opção Mais Promissora

### O que existe:
- **URL:** `affiliate.shopee.com.br/open_api`
- **Documentação:** `open.shopee.com`
- **Autenticação:** OAuth 2.0 + SHA256 (App ID + Secret Key)

### Endpoints relevantes:
| Endpoint | Função |
|----------|--------|
| `Product Offer List` | Busca produtos por keyword, shop ID, ou item ID |
| `Shop Offer List` | Lista lojas com comissões diferenciadas |

### Requisitos de acesso:
1. Ter conta ativa na Shopee como afiliado
2. Solicitar credenciais (App ID + Secret)
3. Aprovação: **até 2 semanas**
4. Não há custo declarado (incluído no programa de afiliados)

### Limitações conhecidas:
- Rate limits existem (valores exatos não publicados)
- Não está claro se retorna **todos** os produtos do marketplace ou apenas os do programa de afiliados
- O `Product Offer List` suporta busca por keyword — **isso é o que precisamos**

### Exemplo de chamada (referência, não produção):
```python
import hmac
import hashlib
import time

# Parâmetros de autenticação
app_id = "SEU_APP_ID"
secret = "SEU_SECRET"
timestamp = str(int(time.time()))

# Assinatura SHA256
base_string = f"{app_id}{timestamp}"
signature = hmac.new(secret.encode(), base_string.encode(), hashlib.sha256).hexdigest()

headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}

params = {
    "keyword": "rtx 4060",
    "limit": 20,
    "page": 1
}
```

---

## 6. Serviços de Terceiros (Scrapeless / Apify)

### Como funciona:
```python
import requests

payload = {
    "actor": "scraper.shopee",
    "input": {
        "action": "shopee.search",
        "url": "https://shopee.com.br/api/v4/search/search_items?by=sales&keyword=rtx+4060&limit=30&newest=0&order=desc&page_type=search&scenario=PAGE_OTHERS&version=2"
    }
}

response = requests.post(
    "https://api.scrapeless.com/api/v1/scraper/request",
    headers={"x-api-token": "SEU_TOKEN", "Content-Type": "application/json"},
    json=payload
)
```

### Prós:
- Simples de implementar (1 POST request)
- Eles mantêm o bypass de anti-bot
- Funciona imediatamente sem aprovação

### Contras:
- **Serviço pago** com custo por request
- Dependência de terceiro (pode mudar preços / encerrar)
- Latência adicional (request passa pelo servidor deles)
- **Inviável para distribuir** como .exe gratuito — o token da API ficaria exposto

---

## 7. Playwright Stealth (Automação de Browser)

### Como funcionaria:
```python
from playwright.sync_api import sync_playwright

def buscar_shopee(keyword):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Interceptar a chamada de API ao navegar
        results = []
        def handle_response(response):
            if "search_items" in response.url:
                results.append(response.json())
        
        page.on("response", handle_response)
        page.goto(f"https://shopee.com.br/search?keyword={keyword}")
        page.wait_for_timeout(3000)
        browser.close()
        return results
```

### Avaliação para TechOfertas:
- ✅ Funciona (browser real = headers dinâmicos válidos)
- ❌ Chromium adiciona **~150-300MB** ao bundle .exe
- ❌ Lento: 5-15 segundos por busca vs 1-2s das outras lojas
- ❌ Requer manutenção quando Shopee atualiza detecção
- ⚠️ Possível com `playwright-stealth` para reduzir detecção

---

## 8. Fontes Consultadas

| URL | Credibilidade | Ângulo |
|-----|---------------|--------|
| bluetickconsultants.com/how-to-scrape-shopee-at-scale | HIGH | Anti-bot, headers |
| scrapeops.io/websites/shopee/ | HIGH | Proteção (Cloudflare + Akamai) |
| docs.scrapeless.com/en/scraping-api/features/scrape-shopee/ | HIGH | Endpoint + params |
| github.com/dtungpka/shopee-scraper | HIGH | Python lib, Dez 2024 |
| github.com/paulodarosa/shopee-scraper | HIGH | API-based Python |
| github.com/SuspiciousLookingOwl/shopee-api | HIGH | Node.js wrapper |
| affiliate.shopee.com.br/open_api | HIGH | API oficial |
| affiliateshopee.com.br/documentacao | MEDIUM | Docs afiliado |
| bluetickconsultants.medium.com (mobile interception) | HIGH | Mobile API |
| bluetickconsultants.com/shopee-scraping-via-mobile-emulation | HIGH | Mobile emulation |
| github.com/duyet/pricetrack | HIGH | Price tracker multi-plat |
| github.com/limyuquan/pricetracker | MEDIUM | Flask + Selenium |
| api2cart.com/api-technology/shopee-api/ | MEDIUM | API overview |
| github.com/kevinjon27/ShopeeAPI | MEDIUM | Python wrapper |
