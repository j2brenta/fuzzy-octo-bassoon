Kirill R, [7 Oct 2026 at 12:13:30]:
Here's a map of people worth following, grouped by angle. Affiliations change often in this field, so it's worth checking their latest pages.

Introspection and model internals

- Jack Lindsey (Anthropic) leads what Anthropic calls its "model psychiatry" team. His paper on emergent introspective awareness tests whether models can genuinely observe their own internal states, rather than just imitating human self-reflection, by injecting concepts into the model and checking whether it notices. His conclusion is cautious: models show at least a limited, functional form of introspective awareness, but he stresses it is highly limited and context-dependent. He's arguably the most direct researcher on "metacognition" in the literal sense.
- Chris Olah and Josh Batson (Anthropic interpretability) work on the circuit-tracing research that found the "can't answer" default pathway I mentioned. Their publications at transformer-circuits.pub are the main source for mechanistic explanations of hallucination.
- Sam Marks has worked on how models represent truth internally (the "geometry of truth" line of work) and on auditing models for hidden objectives.

Uncertainty and hallucination detection

- Sebastian Farquhar and Yarin Gal are lead authors of the semantic entropy work published in Nature in 2024, the best-known method for detecting confabulations by sampling multiple answers. Gal leads the OATML group at Oxford.
- Adam Tauman Kalai (OpenAI) is lead author of the 2025 paper "Why Language Models Hallucinate," which argues that training and evaluation reward guessing over admitting uncertainty. It's essential for understanding the incentive problem.
- Owain Evans has done influential work on whether models know what they know and can describe their own learned behaviors, including the "Looking Inward" and "Tell me about yourself" papers. He co-authored early work on teaching models to express uncertainty in words, and leads the research nonprofit Truthful AI.

The cognitive science bridge

- Stephen Fleming (UCL) is a leading neuroscientist of human metacognition and author of the book "Know Thyself." He has increasingly applied metacognition research to AI, and he's useful for understanding what we even mean by the term, including the calibration-versus-discrimination distinction.

Agent reliability and evaluation science

- Arvind Narayanan and Sayash Kapoor (Princeton) wrote the agent reliability paper I cited, which borrowed methods from nuclear and aviation safety. Their newsletter is often skeptical and data-driven, a good counterweight to hype.
- METR (founded by Beth Barnes) measures how long a task agents can complete autonomously. Their reports are a key reference for the reliability-over-horizon question.
- Andrej Karpathy popularized the "march of nines" framing and writes clearly about what it takes to move from demos to products.
- Azalia Mirhoseini (Stanford) co-led the LLM-as-a-Verifier work on scaling verification as its own axis.

Engineering practice: evals in production

- Shreya Shankar (UC Berkeley) researches how to evaluate LLM pipelines, including the paper "Who Validates the Validators?" on aligning automated judges with human judgment, which addresses the verifier-reliability problem directly.
- Hamel Husain is one of the most practical voices on building evals for real products: error analysis, reading traces, and designing checks that catch real failures. His writing pairs well with Shankar's research, and they teach together.
- Eugene Yan writes accessible, practical pieces on LLM evaluation and production patterns.

How to follow efficiently


Rather than tracking everyone, a good minimal set is: Anthropic's interpretability publications for mechanisms, the Princeton newsletter for reliability science, METR for agent capability trends, and Husain and Shankar for engineering practice. Together they cover the full chain we've discussed, from why models don't know when they're wrong to how teams build systems that catch it anyway. And for the newest papers, searching arXiv for terms like "metacognition," "calibration," "selective prediction," and "process verifier" will surface new work, much of which, as you saw, has appeared in just the past few months.