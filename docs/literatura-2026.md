# Trabalhos recentes: perguntar e acionar skills (2026)

Criado em 10/11/2026 para o gate de 12/11. Situa o E2 e a matriz E3 (`registro-metodologico.md`
§3) frente a seis artigos lidos no arXiv em 10/11/2026 (resumo, método e resultados principais;
não lemos cada artigo inteiro). Os trabalhos já cruzados estão em `agenda-experimentos-toolkit.md`
§3 e não se repetem aqui. Só citamos números que lemos no artigo.

**Nosso estudo, em uma linha.** Um agente real (Claude Code headless, em container) mantém
infraestrutura AWS em 5 condições de toolkit. No Floci: com a skill acionada, o agente pergunta em
18/18 pedidos ambíguos, contra 10–12/18 sem ela; o sucesso total não muda; surgem perguntas a mais
em pedidos vagos marcados como claros; os tokens sobem 19–33%. O Haiku só aciona a skill com a
regra no system prompt (30/105 sem ela, 105/105 com ela); o Sonnet a aciona sozinho (99/105).

## Perguntar antes de agir

**Ambig-IaC (Yang et al. 2026) [1].** 300 tarefas Terraform reescritas para ficarem ambíguas
(167 tipos de recurso AWS). O método gera 10 candidatos, acha onde discordam (recursos, topologia,
atributos) e faz perguntas sim/não a um usuário simulado. Com GPT-4o-mini e 10 perguntas, a
estrutura acerta 50,98%, contra 44,38% ao gerar perguntas direto. **Relação:** concorda que
perguntar ajuda em IaC ambíguo. Mas só gera código e mede contra uma config de referência; ninguém
aplica nada. Todas as tarefas são ambíguas, então não se mede pergunta a mais em pedido claro.

**ClarEval (Li et al. 2026) [2].** 2.250 instâncias de código (HumanEval e LiveCodeBench) com três
tipos de ambiguidade injetada; 11 agentes. Sem esclarecer, o Pass@1 cai de 89,02% para 8,94%. As
métricas punem conversa longa (desconto por turno). **Relação:** concorda que a ambiguidade derruba
o acerto. Difere de nós: o agente só gera código, sem ambiente, e o usuário é um roteiro fixo. Não
há pedidos claros para medir pergunta desnecessária.

**Ask or Assume? (Edwards e Schuster 2026) [3].** SWE-bench Verified com issues resumidas para
faltar informação; OpenHands com Claude Sonnet 4.5 e Kimi K2.6; usuário simulado por GPT-5.1. Com
o issue completo, o Sonnet resolve 70,80%; sem a informação, 54,80%; com um segundo agente que
vigia o que falta (UA-Multi), 69,40%. O Kimi pergunta demais (8,71 perguntas por tarefa, contra
3,06 do Sonnet). **Relação:** é o mais próximo de nós: agente real que executa e testa. Mas os
scaffolds só rodam nos issues incompletos; o custo de perguntar em pedido completo não é medido.
Concorda conosco que o modelo pesa (o Kimi pergunta demais; nosso Haiku depende da regra).

**SAGE (Suri et al. 2026) [6].** Escolhe a pergunta pelo valor da informação sobre os parâmetros
das ferramentas. No ClarifyBench (716 exemplos, 5 domínios, sem nuvem), com GPT-4o, cobre 59,73%
nas ambíguas (+7,39 pp) com 1,39 pergunta a menos por tarefa. Inclui pedidos explícitos (acerto da
ferramenta 93,65%). **Relação:** mede pedidos claros e ambíguos juntos, como nós, mas com chamadas
de ferramenta simuladas, fora de código e de nuvem.

## Skills: ajudam e são acionadas?

**SkillsBench (Li et al. 2026) [4].** 84 tarefas em 11 domínios; 7 combinações de harness e
modelo, 7.308 trajetórias. Skills curadas dão +16,2 pp em média, mas só +4,5 pp em engenharia de
software, e 16 de 84 tarefas pioram. Skills que o próprio agente escreve dão −1,3 pp. No Claude
Code: Haiku 4.5 de 11,0% para 27,7%; Sonnet 4.5 de 17,3% para 31,8%. As skills entram como
contexto de sistema antes da tarefa. **Relação:** difere de nós: não vimos ganho no total. O mais
próximo deles é a nossa `own-rules` (regra no system prompt), que também não ganhou. Concorda que
algumas tarefas pioram (nossas task-118, 231 e 373). Sobre acionamento, eles só relatam, sem
número, que o Codex CLI muitas vezes ignora as skills; nós medimos o acionamento por modelo.

**SkillApt (Guo 2026) [5].** Decide se vale carregar uma skill já recuperada, a partir de pares de
execução com e sem ela. No SRA-Bench, mantém o acerto do BM25 Top-1 (0,838) carregando a skill em
31,5% dos casos em vez de 100%, com 74,3% menos tokens. Diz que a utilidade da skill varia por
modelo. **Relação:** concorda que carregar skill custa tokens e que o efeito depende do modelo.
Mas o acionamento é decidido por um controlador externo; não mede se o agente aciona a skill sozinho.

## O que este trabalho acrescenta

- **Agente real mudando estado de nuvem.** Ambig-IaC e ClarEval só geram código; Ask or Assume
  age num repositório, não em nuvem. Nós conferimos o estado final da infraestrutura (Floci na
  matriz E3; AWS real nas rodadas de modalidades) e o efeito colateral.
- **Custo de perguntar demais em pedido claro, no mesmo experimento.** Os três trabalhos de código
  e IaC só usam pedidos incompletos. Nós medimos as duas coisas juntas: +6–8 ambíguas com pergunta,
  ao preço de 3–6 perguntas nas claras (contra 0–1 sem toolkit) e +19–33% de tokens.
- **Acionamento espontâneo da skill, por modelo.** SkillsBench injeta a skill no contexto e só
  relata a omissão sem número; SkillApt decide por fora. Nós medimos quando o próprio agente aciona
  a skill: Haiku 30/105 sem regra, Sonnet 99/105.
- **Comparação com o toolkit de um fornecedor.** Nenhum dos seis compara com um toolkit oficial;
  o `aws-core` não muda as perguntas nem o sucesso e custa +39–40% de tokens.

**Limites desta nota.** Lemos resumo, método e resultados, não cada artigo inteiro. Todos os seis
usam usuário simulado ou benchmark; nós também (estratos de autoria nossa, juiz conferido por
outro modelo).

## Referências

1. Zhenning Yang et al. *Interactive Clarification for Cloud Infrastructure-as-Code Synthesis*
   (Ambig-IaC). 2026. arXiv:2604.02382. https://arxiv.org/abs/2604.02382
2. Jialin Li et al. *ClarEval: A Benchmark for Evaluating Clarification Skills of Code Agents
   under Ambiguous Instructions*. 2026. arXiv:2603.00187. https://arxiv.org/abs/2603.00187
3. Nicholas Edwards e Sebastian Schuster. *Ask or Assume? Uncertainty-Aware Clarification-Seeking
   in Coding Agents*. 2026. arXiv:2603.26233. https://arxiv.org/abs/2603.26233
4. Xiangyi Li et al. *SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks*.
   2026. arXiv:2602.12670. https://arxiv.org/abs/2602.12670
5. Shuang Guo. *SkillApt: Learning When to Activate Agent Skills from Counterfactual Evidence*.
   2026. arXiv:2609.26863. https://arxiv.org/abs/2609.26863
6. Manan Suri et al. *Structured Uncertainty guided Clarification for LLM Agents* (SAGE). 2025
   (v2 em 2026). arXiv:2511.08798. https://arxiv.org/abs/2511.08798
