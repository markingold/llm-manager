# Local Model Baseline Report

Generated: 2026-03-29T20:59:24.243450Z

## Summary

- Total runs: 80
- Total passes: 36
- Total errors: 6
- Pass rate: 0.45

## By Mode

- chat: models=8 runs=40 passes=16 errors=6 pass_rate=0.4
- small: models=8 runs=40 passes=20 errors=0 pass_rate=0.5
- intent: models=0 runs=0 passes=0 errors=0 pass_rate=0.0

## By Test

- arithmetic_739x481: runs=16 passes=2 errors=1 pass_rate=0.125
- arithmetic_order_ops: runs=16 passes=1 errors=1 pass_rate=0.0625
- sentiment_label: runs=16 passes=12 errors=1 pass_rate=0.75
- trivia_capital: runs=16 passes=15 errors=1 pass_rate=0.9375
- valid_json_strict: runs=16 passes=6 errors=2 pass_rate=0.375

## By Model

- chat::Gemma-3-27B-IT-EXL2-4.0bpw: runs=5 passes=2 errors=1 pass_rate=0.4
- chat::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::Phi-4-mini-instruct: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::Qwen3.5-4B-Q6_K: runs=5 passes=4 errors=0 pass_rate=0.8
- chat::llama3.1-8B: runs=5 passes=0 errors=5 pass_rate=0.0
- chat::llama3.1-8B_exl2_b6p5: runs=5 passes=1 errors=0 pass_rate=0.2
- chat::meta-llama__Llama-3.2-3B-Instruct: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=5 passes=3 errors=0 pass_rate=0.6
- small::Gemma-3-27B-IT-EXL2-4.0bpw: runs=5 passes=3 errors=0 pass_rate=0.6
- small::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=5 passes=2 errors=0 pass_rate=0.4
- small::Phi-4-mini-instruct: runs=5 passes=2 errors=0 pass_rate=0.4
- small::Qwen3.5-4B-Q6_K: runs=5 passes=4 errors=0 pass_rate=0.8
- small::llama3.1-8B: runs=5 passes=3 errors=0 pass_rate=0.6
- small::llama3.1-8B_exl2_b6p5: runs=5 passes=2 errors=0 pass_rate=0.4
- small::meta-llama__Llama-3.2-3B-Instruct: runs=5 passes=2 errors=0 pass_rate=0.4
- small::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=5 passes=2 errors=0 pass_rate=0.4

## Failure Samples

- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=valid_json_strict ok=False error=HTTP Error 502: Bad Gateway
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=arithmetic_739x481 ok=False error=None
  output: 355999
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=arithmetic_order_ops ok=False error=None
  output: 1006
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=valid_json_strict ok=False error=None
  output: {   "answer": "Confidence must be a number between 0 and 1. No extra text.",   "confidence": 0.92 }
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=arithmetic_739x481 ok=False error=None
  output: The result of 739*481 is 356,363. I hope this helps!<tool_call>35"\ /r "/>/					 					 <p> &nbsp;</p> <div id ="answer">356,363</div>Question:nterests">eful  ex
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=arithmetic_order_ops ok=False error=None
  output: Here is the result:  18
- mode=chat model=Phi-4-mini-instruct test=arithmetic_739x481 ok=False error=None
  output: 349091This score sheet is mostly garbage. It has Composition 18, Youtube comments, with a mention about Text, but honestly, the screenshot and the drawing are j
- mode=chat model=Phi-4-mini-instruct test=arithmetic_order_ops ok=False error=None
  output: 1496Solution: Compute the given expression (27*43)-(19*7)+6 and provide your step-by-step approach leading to your final integer answer.  - A well-known mathema
- mode=chat model=Phi-4-mini-instruct test=sentiment_label ok=False error=None
  output: positiveThe sentiment in the text "I love this product and would buy again." is positive, as indicated by the words "love" and the intent to repurchase the prod
- mode=chat model=Qwen3.5-4B-Q6_K test=arithmetic_order_ops ok=False error=None
  output: 276
- mode=chat model=llama3.1-8B test=valid_json_strict ok=False error=HTTP Error 500: Internal Server Error
- mode=chat model=llama3.1-8B test=trivia_capital ok=False error=HTTP Error 500: Internal Server Error
- mode=chat model=llama3.1-8B test=arithmetic_739x481 ok=False error=HTTP Error 500: Internal Server Error
- mode=chat model=llama3.1-8B test=arithmetic_order_ops ok=False error=HTTP Error 500: Internal Server Error
- mode=chat model=llama3.1-8B test=sentiment_label ok=False error=HTTP Error 500: Internal Server Error
- mode=chat model=llama3.1-8B_exl2_b6p5 test=valid_json_strict ok=False error=None
  output: The correct ending is `json`  RETURN only a JSON object (or a single `{}`) with exactly two keys: "answer" and "confidence". Answer what color the daytime sky u
- mode=chat model=llama3.1-8B_exl2_b6p5 test=arithmetic_739x481 ok=False error=None
  output: 729*48101.assistant  729*481 = 349329.assistant  So 739*481 = 349329.assistant  ## Step 1: The first step is to understand that we need to compute the product o
- mode=chat model=llama3.1-8B_exl2_b6p5 test=arithmetic_order_ops ok=False error=None
  output: 27*43 = 1158 1158 - 19*7 = 1158 - 133 = 1025 1025 + 6 = 1031  The final answer is 1031.assistant  27*43 = 1158 1158 - 19*
- mode=chat model=llama3.1-8B_exl2_b6p5 test=sentiment_label ok=False error=None
  output: To label the sentiment of the text "I love this product and would buy it again.", we can use a sentiment analysis library such as the NLTK or VaderSentiment lib
- mode=chat model=meta-llama__Llama-3.2-3B-Instruct test=valid_json_strict ok=False error=None
  output: ``` {   "answer": "Blue",   "confidence": 0.85 } ```
