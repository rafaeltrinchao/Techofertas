# Recomendações — Integração da Shopee no TechOfertas

**Contexto:** TechOfertas é um app desktop Python bundlado como .exe. Usa urllib, cloudscraper e requests. Precisa de busca por keyword retornando produtos com nome, preço, imagem e URL.

---

## Veredicto Direto

> **requests/urllib direto com a Shopee = impossível em 2025-2026.**  
> A proteção Cloudflare + Akamai Bot Manager derruba qualquer requisição estática em segundos.  
> Há apenas 3 caminhos viáveis — em ordem de recomendação para o TechOfertas:

---

## Caminho 1 — Affiliate API Oficial ⭐ RECOMENDADO

**Por quê é o melhor para o TechOfertas:**
- API oficial = sem risco de bloqueio
- Implementação em Python puro (sem browser, sem peso extra no .exe)
- Dados estruturados (JSON limpo, igual à KaBuM)
- Custo zero (programa de afiliados gratuito)
- Manutenção mínima (API estável, versionada)

**O endpoint `Product Offer List` suporta busca por keyword** — exatamente o que precisamos.

### Processo para viabilizar:
1. Criar conta de afiliado Shopee Brasil em `affiliate.shopee.com.br`
2. Solicitar acesso à Open API (formulário no portal)
3. Aguardar aprovação: **até 2 semanas**
4. Receber App ID + Secret Key
5. Implementar OAuth 2.0 + SHA256 signing em Python

### Incerteza a validar:
- O `Product Offer List` retorna produtos de todo o marketplace ou só do programa de afiliados?
  → Se retornar todo o marketplace: **perfeito**, implementação direta
  → Se restringir ao programa: **ainda útil** (cobre produtos populares com comissão)

### Complexidade: MÉDIA
- OAuth 2.0 já existe em muitas libs Python
- SHA256 signing é padrão (`hmac` + `hashlib` nativos)
- ~100-150 linhas de código

---

## Caminho 2 — Playwright Stealth (Headless Browser) ⚠️ CONTINGÊNCIA

**Quando usar:** Se a Affiliate API for negada ou restritiva demais.

**Como funciona:** Browser Chromium headless intercepta as chamadas `/api/v4/search/search_items` naturalmente — sem precisar replicar headers dinâmicos (o browser os gera por si só).

### Trade-offs para o TechOfertas:

| Aspecto | Impacto |
|---------|---------|
| Bundle size | +150-300MB no .exe |
| Velocidade | 5-15s por busca (vs 1-2s dos outros) |
| Detecção | Baixa com `playwright-stealth` |
| Manutenção | Média (pode precisar atualizar ao mudar detecção) |
| Login | Opcional — funciona sem conta |

### Estratégia de implementação:
- Usar `playwright` com interceptação de network response
- Não navegar pela página inteira — interceptar o JSON da API ao carregar a busca
- Executar em thread separada com timeout de 15s
- Mostrar "Shopee: buscando..." com loader diferenciado (mais lento)

---

## Caminho 3 — Scrapeless/Apify API ❌ NÃO RECOMENDADO para .exe distribuído

**Por quê não:**
- Serviço pago — custo por request repassado ao usuário
- Token de API no .exe = exposto (decompilável)
- Dependência de terceiro para função core da app
- Latência extra (request intermediário)

**Quando faz sentido:** Apenas se o TechOfertas evoluir para modelo SaaS com backend próprio, onde o token fica no servidor.

---

## O que NÃO fazer

| Abordagem | Por quê não |
|-----------|-------------|
| `requests` direto na API | Bloqueado imediatamente (Akamai) |
| `cloudscraper` | Resolve Cloudflare, não Akamai |
| Mobile API (Frida + JADX) | Absurdamente complexo, inviável para distribuição |
| Headers estáticos "copiados" | Headers expiram em minutos, criptografados dinamicamente |
| BeautifulSoup no HTML | Shopee é 100% JS-rendered, HTML sem dados |

---

## Matriz de Decisão Final

```
                        ESTABILIDADE
                    Alta          Baixa
                ┌─────────────┬─────────────┐
     COMPLEXIDADE│ Affiliate   │             │
     Baixa       │ API ⭐      │  requests   │
                 │             │  (falha)    │
                ├─────────────┼─────────────┤
     COMPLEXIDADE│             │ Playwright  │
     Alta        │             │ stealth ⚠️  │
                └─────────────┴─────────────┘
```

---

## Próximos Passos Recomendados

### Fase 1 — Validação (1-2 semanas)
1. Criar conta afiliado Shopee Brasil
2. Solicitar acesso à Open API
3. Enquanto aguarda: prototipar o módulo `buscar_shopee()` com a estrutura de resposta esperada

### Fase 2 — Implementação (após aprovação)
1. Implementar autenticação OAuth 2.0 + SHA256
2. Implementar `buscar_shopee(query, vm, vmax)` seguindo o padrão das outras lojas
3. Adicionar Shopee ao `buscadores` dict e ao selector de lojas na UI
4. Testar com queries reais de hardware (RTX 4060, processadores, etc.)

### Fase 3 — Fallback (se API negada)
1. Avaliar viabilidade do Playwright no bundle .exe
2. Decidir se o peso adicional (+300MB) é aceitável para os usuários

---

## Referência de Implementação (estrutura esperada)

```python
# Padrão atual das outras lojas — Shopee deve seguir o mesmo
def buscar_shopee(query, preco_min=None, preco_max=None):
    """
    Retorna lista de dicts: {nome, preco, link, imagem, loja}
    """
    # Opção A: via Affiliate API (OAuth 2.0)
    # Opção B: via Playwright stealth (fallback)
    pass
```

A estrutura de dados de saída deve ser idêntica às outras lojas para integrar com o sistema de ranking/comparação existente.
