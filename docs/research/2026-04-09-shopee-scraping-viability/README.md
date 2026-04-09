# Shopee Brasil — Viabilidade de Integração no TechOfertas

**Data:** 2026-04-09  
**Pesquisa:** Tech Search (5 workers paralelos, 2 waves)  
**Cobertura:** 78% | Fontes HIGH credibility: 8

---

## TL;DR

A Shopee possui proteção anti-bot **extremamente agressiva** (Cloudflare 8/10 + Akamai Bot Manager 9/10). Scraping direto via requests/urllib é **inviável**. Existem **4 abordagens** com viabilidades distintas para o TechOfertas:

| # | Abordagem | Viabilidade | Complexidade | Custo |
|---|-----------|-------------|--------------|-------|
| 1 | **Affiliate API Oficial** | ✅ ALTA | MÉDIA | Grátis (requer aprovação) |
| 2 | **Playwright stealth** | ⚠️ MÉDIA | ALTA | Grátis (pesado) |
| 3 | **Scrapeless/Apify API** | ✅ ALTA | BAIXA | **Pago** |
| 4 | **requests direto / cloudscraper** | ❌ INVIÁVEL | — | — |

**Recomendação:** Affiliate API Oficial → melhor equilíbrio estabilidade/complexidade para app desktop.

---

## Arquivos

- [00-query-original.md](00-query-original.md) — Pergunta original e contexto
- [01-deep-research-prompt.md](01-deep-research-prompt.md) — Sub-queries e estratégia
- [02-research-report.md](02-research-report.md) — Achados completos por ângulo
- [03-recommendations.md](03-recommendations.md) — Recomendações e próximos passos
