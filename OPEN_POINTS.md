# Open points

Uma GitHub Action (`.github/workflows/claude-open-points.yml`) corre dia sim, dia não.
O Claude pega no primeiro `- [ ]` desta lista, faz-o e abre um PR para eu rever.
Quando o PR é aceite (merge), o ponto fica marcado `- [x]`.

- Para mudar a prioridade, muda a ordem.
- Escreve cada ponto com detalhe suficiente para ser feito sem perguntas.
- Se o Claude tiver dúvidas, abre um PR só com perguntas (uma linha `❓` debaixo do ponto).
  Responde no próprio ponto e fecha esse PR.

## Lista

- [x] Portfolio tracker: cartas que tenho, preço pago, valor hoje, lucro ou prejuízo depois das fees do Cardmarket.
      (Primeira parte feita: `python portfolio.py` no terminal.)
- [ ] Portfolio tracker na app do browser: separador com a tabela do `portfolio.py`, e botões para adicionar/remover cartas (`/api/portfolio`).
- [ ] Opportunity scanner: scan diário do price guide do Cardmarket à procura de sinais
      (por exemplo a média de 7 dias a subir rápido em comparação com a média de 30 dias).
- [ ] Fee calculator: o preço de venda que preciso para X% de lucro depois das fees do Cardmarket e dos portes.
- [ ] Trade journal: registo de compras e vendas, com resultados por mês.
