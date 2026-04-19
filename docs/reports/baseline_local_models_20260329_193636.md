# Local Model Baseline Report

Generated: 2026-03-29T19:35:59.386771Z

## Summary

- Total runs: 68
- Total passes: 0
- Total errors: 68
- Pass rate: 0.0

## By Mode

- chat: models=8 runs=32 passes=0 errors=32 pass_rate=0.0
- small: models=8 runs=32 passes=0 errors=32 pass_rate=0.0
- intent: models=1 runs=4 passes=0 errors=4 pass_rate=0.0

## By Test

- arithmetic_17x19: runs=17 passes=0 errors=17 pass_rate=0.0
- sentiment_label: runs=17 passes=0 errors=17 pass_rate=0.0
- trivia_capital: runs=17 passes=0 errors=17 pass_rate=0.0
- valid_json: runs=17 passes=0 errors=17 pass_rate=0.0

## By Model

- chat::Gemma-3-27B-IT-EXL2-4.0bpw: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::Phi-4-mini-instruct: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::Qwen3.5-4B-Q6_K: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::llama3.1-8B: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::llama3.1-8B_exl2_b6p5: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::meta-llama__Llama-3.2-3B-Instruct: runs=4 passes=0 errors=4 pass_rate=0.0
- chat::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=4 passes=0 errors=4 pass_rate=0.0
- intent::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=4 passes=0 errors=4 pass_rate=0.0
- small::Gemma-3-27B-IT-EXL2-4.0bpw: runs=4 passes=0 errors=4 pass_rate=0.0
- small::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=4 passes=0 errors=4 pass_rate=0.0
- small::Phi-4-mini-instruct: runs=4 passes=0 errors=4 pass_rate=0.0
- small::Qwen3.5-4B-Q6_K: runs=4 passes=0 errors=4 pass_rate=0.0
- small::llama3.1-8B: runs=4 passes=0 errors=4 pass_rate=0.0
- small::llama3.1-8B_exl2_b6p5: runs=4 passes=0 errors=4 pass_rate=0.0
- small::meta-llama__Llama-3.2-3B-Instruct: runs=4 passes=0 errors=4 pass_rate=0.0
- small::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=4 passes=0 errors=4 pass_rate=0.0

## Failure Samples

- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=valid_json ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=trivia_capital ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=arithmetic_17x19 ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=sentiment_label ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=valid_json ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=trivia_capital ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=arithmetic_17x19 ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=sentiment_label ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Phi-4-mini-instruct test=valid_json ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Phi-4-mini-instruct test=trivia_capital ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Phi-4-mini-instruct test=arithmetic_17x19 ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Phi-4-mini-instruct test=sentiment_label ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Qwen3.5-4B-Q6_K test=valid_json ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Qwen3.5-4B-Q6_K test=trivia_capital ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Qwen3.5-4B-Q6_K test=arithmetic_17x19 ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Qwen3.5-4B-Q6_K test=sentiment_label ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=llama3.1-8B test=valid_json ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=llama3.1-8B test=trivia_capital ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=llama3.1-8B test=arithmetic_17x19 ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=llama3.1-8B test=sentiment_label ok=False error=HTTP Error 502: Bad Gateway
