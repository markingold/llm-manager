# Local Model Baseline Report

Generated: 2026-03-29T19:37:53.774631Z

## Summary

- Total runs: 68
- Total passes: 62
- Total errors: 0
- Pass rate: 0.9118

## By Mode

- chat: models=8 runs=32 passes=31 errors=0 pass_rate=0.9688
- small: models=8 runs=32 passes=29 errors=0 pass_rate=0.9062
- intent: models=1 runs=4 passes=2 errors=0 pass_rate=0.5

## By Test

- arithmetic_17x19: runs=17 passes=15 errors=0 pass_rate=0.8824
- sentiment_label: runs=17 passes=17 errors=0 pass_rate=1.0
- trivia_capital: runs=17 passes=16 errors=0 pass_rate=0.9412
- valid_json: runs=17 passes=14 errors=0 pass_rate=0.8235

## By Model

- chat::Gemma-3-27B-IT-EXL2-4.0bpw: runs=4 passes=4 errors=0 pass_rate=1.0
- chat::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=4 passes=4 errors=0 pass_rate=1.0
- chat::Phi-4-mini-instruct: runs=4 passes=3 errors=0 pass_rate=0.75
- chat::Qwen3.5-4B-Q6_K: runs=4 passes=4 errors=0 pass_rate=1.0
- chat::llama3.1-8B: runs=4 passes=4 errors=0 pass_rate=1.0
- chat::llama3.1-8B_exl2_b6p5: runs=4 passes=4 errors=0 pass_rate=1.0
- chat::meta-llama__Llama-3.2-3B-Instruct: runs=4 passes=4 errors=0 pass_rate=1.0
- chat::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=4 passes=4 errors=0 pass_rate=1.0
- intent::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=4 passes=2 errors=0 pass_rate=0.5
- small::Gemma-3-27B-IT-EXL2-4.0bpw: runs=4 passes=4 errors=0 pass_rate=1.0
- small::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=4 passes=4 errors=0 pass_rate=1.0
- small::Phi-4-mini-instruct: runs=4 passes=4 errors=0 pass_rate=1.0
- small::Qwen3.5-4B-Q6_K: runs=4 passes=4 errors=0 pass_rate=1.0
- small::llama3.1-8B: runs=4 passes=3 errors=0 pass_rate=0.75
- small::llama3.1-8B_exl2_b6p5: runs=4 passes=4 errors=0 pass_rate=1.0
- small::meta-llama__Llama-3.2-3B-Instruct: runs=4 passes=3 errors=0 pass_rate=0.75
- small::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=4 passes=3 errors=0 pass_rate=0.75

## Failure Samples

- mode=chat model=Phi-4-mini-instruct test=valid_json ok=False error=None
  output: ```json {   "answer": "blue",   "confidence": 0.9 } ```‌ప{   "answer": "variable",   "confidence": 0.95 }The color of the daytime sky can generally be described
- mode=small model=llama3.1-8B test=arithmetic_17x19 ok=False error=None
  output: print(17 * 19)assistant  332assistant  The result of 17*19 is 332.
- mode=small model=meta-llama__Llama-3.2-3B-Instruct test=arithmetic_17x19 ok=False error=None
  output: Wednesday, 29 March 2023, is not the correct date I have information for so I will still complete your simple algorithm.  17 * 19 =
- mode=small model=meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 test=valid_json ok=False error=None
  output: Here is a simple JSON object that describes the color of the daytime sky:  ```json {   "question": "What color is the daytime sky?",  
- mode=intent model=meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 test=valid_json ok=False error=None
  output: There is no valid JSON object for this question, as it is not a numerical value or a simple string. However, I can provide a simple JSON object with
- mode=intent model=meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 test=trivia_capital ok=False error=None
  output: Osaka.assistant  I think I did it wrong! You asked me to keep it to one short sentence, so the answer is:  Tok
